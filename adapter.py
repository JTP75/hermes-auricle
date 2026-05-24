import asyncio
import logging
import os
import shlex
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from gateway.config import Platform
from gateway.platforms.base import (
    BasePlatformAdapter,
    MessageEvent,
    MessageType,
    SendResult,
)

from .consts import (
    ALL_ASSETS,
    AUDIO_RING_BUFFER_CHUNKS,
    TTS_ECHO_TAIL_SECONDS,
    ASSET_CLEARED,
    ASSET_DING,
    ASSET_ERROR,
    CHAT_ID,
    DEFAULT_ACTIVE_LISTEN_DURATION,
    DEFAULT_MIC_DEVICE,
    DEFAULT_MUTE,
    DEFAULT_OWW_EMBEDDING_MODEL_PATH,
    DEFAULT_OWW_MELSPEC_MODEL_PATH,
    DEFAULT_OWW_WAKEWORD_MODEL_PATH,
    DEFAULT_SESSION_RESUME,
    DEFAULT_TTS_VOICE,
    DEFAULT_VOSK_MODEL_PATH,
    EDGE_TTS_BIN,
    ENV_ACTIVE_LISTEN_DURATION,
    ENV_ALLOW_ALL_USERS,
    ENV_ALLOWED_USERS,
    ENV_HOME_CHANNEL,
    ENV_MIC_DEVICE,
    ENV_MUTE,
    ENV_OWW_EMBEDDING_MODEL_PATH,
    ENV_OWW_MELSPEC_MODEL_PATH,
    ENV_OWW_WAKEWORD_MODEL_PATH,
    ENV_SESSION_RESUME,
    ENV_TTS_VOICE,
    ENV_VOSK_MODEL_PATH,
    OWW_THRESHOLD,
    PLATFORM_HINT,
    PROACTIVE_PRE_SPEECH_PAUSE,
    PW_PLAY_BIN,
    PW_PLAY_TARGET,
    RETRY_DELAY_SECONDS,
    SAMPLE_RATE,
    STREAM_MESSAGE_ID,
    _CMD_CLEAR,
    _CMD_STOP,
)
from .audio_buffer import AudioBuffer
from .egress import EgressController
from .fsm import FSM, State
from .ingress import run_ingress_loop
from .providers import EdgeTTSProvider, VoskSTTProvider

logger = logging.getLogger(__name__)


# ── Adapter ────────────────────────────────────────────────────────────────

class AuricleAdapter(BasePlatformAdapter):
    """
    hermes-auricle: local voice platform adapter.

    Ingress: openWakeWord wakeword + vosk STT via shared arecord subprocess.
    Egress:  sentence-by-sentence edge-tts piped to pw-play.
    """

    REQUIRES_EDIT_FINALIZE = True

    def __init__(self, config) -> None:
        super().__init__(config, Platform("auricle"))

        self._fsm          = FSM()
        self._barge_in     = asyncio.Event()
        self._stop_event   = threading.Event()
        self._audio_buffer = AudioBuffer(AUDIO_RING_BUFFER_CHUNKS, tts_tail_seconds=TTS_ECHO_TAIL_SECONDS)
        self._loop: Optional[asyncio.AbstractEventLoop] = None

        self._stt = VoskSTTProvider(
            os.path.expanduser(os.getenv(ENV_VOSK_MODEL_PATH, DEFAULT_VOSK_MODEL_PATH))
        )
        self._tts    = EdgeTTSProvider(os.getenv(ENV_TTS_VOICE, DEFAULT_TTS_VOICE))
        self._egress = EgressController(self._tts, self._barge_in, self._audio_buffer)

        self._fsm.muted = _parse_bool(os.getenv(ENV_MUTE, str(DEFAULT_MUTE)))
        self._session_resume = _parse_bool(os.getenv(ENV_SESSION_RESUME, str(DEFAULT_SESSION_RESUME)))
        self._active_listen_duration = float(
            os.getenv(ENV_ACTIVE_LISTEN_DURATION, str(DEFAULT_ACTIVE_LISTEN_DURATION))
        )

        self._arecord_proc:   Optional[subprocess.Popen]  = None
        self._ingress_thread: Optional[threading.Thread]  = None
        self._retry_task:     Optional[asyncio.Task]      = None
        self._pending_clear:  bool                        = False
        self._source = None

    # ── Lifecycle ──────────────────────────────────────────────────────────

    async def connect(self) -> bool:
        self._loop = asyncio.get_running_loop()
        self._source = self.build_source(
            chat_id=CHAT_ID,
            chat_type="dm",
            chat_name="Local Speaker",
            user_id=CHAT_ID,
            user_name="user",
        )

        success = await asyncio.to_thread(self._connect_real)
        if success:
            if not self._session_resume:
                self._pending_clear = True
            return True

        self._retry_task = asyncio.create_task(self._retry_loop())
        return False

    def _connect_real(self) -> bool:
        """Synchronous: validate environment, load models, start subprocess and ingress thread."""
        # Binaries
        for binary in (PW_PLAY_BIN, EDGE_TTS_BIN, "arecord"):
            if not shutil.which(binary):
                msg = f"Required binary not found on PATH: {binary}"
                logger.error("[auricle] %s", msg)
                self._set_fatal_error("missing_binary", msg, retryable=False)
                return False

        # Audio assets
        missing = [str(a) for a in ALL_ASSETS if not a.exists()]
        if missing:
            msg = f"Missing audio assets (run scripts/generate_assets.py): {', '.join(missing)}"
            logger.error("[auricle] %s", msg)
            self._set_fatal_error("missing_assets", msg, retryable=False)
            return False

        # Model paths
        vosk_path = Path(os.path.expanduser(os.getenv(ENV_VOSK_MODEL_PATH, DEFAULT_VOSK_MODEL_PATH)))
        ww_path   = Path(os.path.expanduser(os.getenv(ENV_OWW_WAKEWORD_MODEL_PATH, DEFAULT_OWW_WAKEWORD_MODEL_PATH)))
        ms_path   = Path(os.path.expanduser(os.getenv(ENV_OWW_MELSPEC_MODEL_PATH, DEFAULT_OWW_MELSPEC_MODEL_PATH)))
        emb_path  = Path(os.path.expanduser(os.getenv(ENV_OWW_EMBEDDING_MODEL_PATH, DEFAULT_OWW_EMBEDDING_MODEL_PATH)))

        for p, label in [(vosk_path, "vosk"), (ww_path, "wakeword"), (ms_path, "melspec"), (emb_path, "embedding")]:
            if not p.exists():
                msg = f"Model not found ({label}): {p}"
                logger.error("[auricle] %s", msg)
                self._set_fatal_error("missing_model", msg, retryable=True)
                return False

        # Load vosk
        try:
            logger.info("[auricle] loading vosk model")
            self._stt._model_path = str(vosk_path)
            self._stt.load()
        except Exception as exc:
            msg = f"Failed to load vosk model: {exc}"
            logger.error("[auricle] %s", msg)
            self._set_fatal_error("model_load_failed", msg, retryable=True)
            return False

        # Load OWW
        try:
            logger.info("[auricle] loading openWakeWord model: %s", ww_path.name)
            from openwakeword.model import Model as OWWModel
            oww = OWWModel(
                wakeword_models=[str(ww_path)],
                melspec_model_path=str(ms_path),
                embedding_model_path=str(emb_path),
                inference_framework="onnx",
            )
            wakeword_key = ww_path.stem
        except Exception as exc:
            msg = f"Failed to load OWW model: {exc}"
            logger.error("[auricle] %s", msg)
            self._set_fatal_error("model_load_failed", msg, retryable=True)
            return False

        # Probe mic
        mic_device = os.getenv(ENV_MIC_DEVICE, DEFAULT_MIC_DEVICE)
        try:
            probe = subprocess.run(
                ["arecord", "-D", mic_device, "-f", "S16_LE", "-c", "1",
                 "-r", str(SAMPLE_RATE), "-t", "raw", "-d", "1", "-q"],
                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=3,
            )
            # returncode 0 or SIGTERM (-15) both mean the device exists
            if probe.returncode not in (0, -15):
                msg = f"Mic probe failed for {mic_device}: {probe.stderr.decode().strip()}"
                logger.error("[auricle] %s", msg)
                self._set_fatal_error("mic_unavailable", msg, retryable=True)
                return False
        except subprocess.TimeoutExpired:
            pass  # captured audio for full duration — device is fine

        # Start arecord
        self._arecord_proc = subprocess.Popen(
            ["arecord", "-D", mic_device, "-f", "S16_LE", "-c", "1",
             "-r", str(SAMPLE_RATE), "-t", "raw", "-q"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )

        # Start ingress thread
        self._stop_event.clear()
        self._ingress_thread = threading.Thread(
            target=run_ingress_loop,
            name="auricle-ingress",
            daemon=True,
            kwargs=dict(
                arecord_proc=self._arecord_proc,
                oww=oww,
                wakeword_key=wakeword_key,
                stt_provider=self._stt,
                egress=self._egress,
                audio_buffer=self._audio_buffer,
                fsm=self._fsm,
                loop=self._loop,
                dispatch_fn=self._dispatch,
                stop_event=self._stop_event,
                active_listen_duration=self._active_listen_duration,
                oww_threshold=OWW_THRESHOLD,
            ),
        )
        self._ingress_thread.start()

        self._fsm.transition(State.IDLE)
        self._mark_connected()
        logger.info("[auricle] connected — listening for wakeword")
        return True

    async def disconnect(self) -> None:
        logger.info("[auricle] disconnecting")

        if self._retry_task:
            self._retry_task.cancel()
            self._retry_task = None

        self._stop_event.set()

        if self._arecord_proc:
            try:
                self._arecord_proc.kill()
                self._arecord_proc.wait(timeout=2)
            except Exception:
                pass
            self._arecord_proc = None

        self._barge_in.set()
        if self._egress._worker_task:
            self._egress._worker_task.cancel()
        self._egress.kill_active()

        if self._ingress_thread and self._ingress_thread.is_alive():
            self._ingress_thread.join(timeout=3)
        self._ingress_thread = None

        self._fsm.transition(State.BOOTING)
        self._mark_disconnected()

    async def _retry_loop(self) -> None:
        while True:
            await asyncio.sleep(RETRY_DELAY_SECONDS)
            logger.info("[auricle] retrying connect…")
            success = await asyncio.to_thread(self._connect_real)
            if success:
                if not self._session_resume:
                    self._pending_clear = True
                break

    # ── Streaming egress ───────────────────────────────────────────────────

    async def send(
        self,
        chat_id: str,
        content: str,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SendResult:
        logger.info("[auricle] send(): %r", content[:80])
        if self._fsm.get() in (State.FATAL, State.BOOTING):
            await self._egress.play_file(ASSET_ERROR)
            return SendResult(success=False, error="adapter not connected")

        proactive = self._fsm.is_idle_for_proactive()

        self._egress.reset()
        self._egress.start_worker()

        if proactive:
            logger.info("[auricle] proactive message → ding")
            await self._egress.play_file(ASSET_DING)
            await asyncio.sleep(PROACTIVE_PRE_SPEECH_PAUSE)

        self._fsm.transition(State.SPEAKING)
        await self._egress.process_delta(content, finalize=False)
        return SendResult(success=True, message_id=STREAM_MESSAGE_ID)

    async def edit_message(
        self,
        chat_id: str,
        message_id: str,
        content: str,
        *,
        finalize: bool = False,
    ) -> SendResult:
        logger.info("[auricle] edit_message(finalize=%s): %r", finalize, content[:80])
        if self._barge_in.is_set():
            # Drop trailing chunk edit arrivals if the current session was barged in
            return SendResult(success=True, message_id=message_id)

        await self._egress.process_delta(content, finalize=finalize)
        if finalize:
            self._fsm.transition_if(State.SPEAKING, State.AWAITING_UTTERANCE)
            logger.info("[auricle] TTS complete → active-listen window open")
        return SendResult(success=True, message_id=message_id)

    async def play_tts(self, chat_id: str, audio_path: str, **kwargs) -> SendResult:
        """Play a pre-synthesized audio file (hermes TTS tool path — no file attachment)."""
        logger.info("[auricle] play_tts(): %s", audio_path)
        proc = await asyncio.create_subprocess_exec(
            PW_PLAY_BIN, f"--target={PW_PLAY_TARGET}", audio_path,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.wait()
        return SendResult(success=True)

    async def send_typing(self, chat_id: str, metadata=None) -> None:
        pass  # no typing indicator on a voice device

    async def get_chat_info(self, chat_id: str) -> Dict[str, Any]:
        return {"name": "Local Speaker", "type": "dm", "chat_id": CHAT_ID}

    # ── Internal dispatch ──────────────────────────────────────────────────

    async def _dispatch(self, text: str) -> None:
        """Route a transcript or internal command to the hermes gateway."""
        logger.info("[auricle] _dispatch: %r", text)
        if not self._message_handler:
            return

        if self._pending_clear:
            self._pending_clear = False
            await self.handle_message(self._make_event("/new", internal=True))

        if text == _CMD_CLEAR:
            await self._egress.play_file(ASSET_CLEARED)
            await self.handle_message(self._make_event("/new", internal=True))
            return

        if text == _CMD_STOP:
            await self.handle_message(self._make_event("/stop", internal=True))
            return

        await self.handle_message(self._make_event(text, internal=False))

    def _make_event(self, text: str, *, internal: bool) -> MessageEvent:
        return MessageEvent(
            text=text,
            message_type=MessageType.TEXT,
            source=self._source,
            internal=internal,
        )


# ── Plugin helpers ─────────────────────────────────────────────────────────

def _parse_bool(value: str) -> bool:
    return str(value).lower() in ("1", "true", "yes", "on")


def check_requirements() -> bool:
    try:
        import vosk          # noqa: F401
        import openwakeword  # noqa: F401
        import numpy         # noqa: F401
    except ImportError:
        return False
    return True


def validate_config(cfg) -> bool:
    errors = []
    vosk_path = Path(os.path.expanduser(os.getenv(ENV_VOSK_MODEL_PATH, DEFAULT_VOSK_MODEL_PATH)))
    if not vosk_path.exists():
        errors.append(f"Vosk model not found: {vosk_path}")
    for env, default, label in [
        (ENV_OWW_WAKEWORD_MODEL_PATH,  DEFAULT_OWW_WAKEWORD_MODEL_PATH,  "OWW wakeword model"),
        (ENV_OWW_MELSPEC_MODEL_PATH,   DEFAULT_OWW_MELSPEC_MODEL_PATH,   "OWW melspec model"),
        (ENV_OWW_EMBEDDING_MODEL_PATH, DEFAULT_OWW_EMBEDDING_MODEL_PATH, "OWW embedding model"),
    ]:
        p = Path(os.path.expanduser(os.getenv(env, default)))
        if not p.exists():
            errors.append(f"{label} not found: {p}")
    missing = [str(a) for a in ALL_ASSETS if not a.exists()]
    if missing:
        errors.append(f"Missing audio assets: {', '.join(missing)}")
    if errors:
        for error in errors:
            logger.warning("[auricle] validation error: %s", error)
        return False
    return True


def is_connected(adapter=None) -> bool:
    return adapter is not None and getattr(adapter, "is_connected", False)


def _env_enablement_fn():
    os.environ.setdefault(ENV_HOME_CHANNEL, CHAT_ID)
    return {
        "mic_device":   os.getenv(ENV_MIC_DEVICE, DEFAULT_MIC_DEVICE),
        "tts_voice":    os.getenv(ENV_TTS_VOICE,  DEFAULT_TTS_VOICE),
        "home_channel": {"chat_id": CHAT_ID},
    }


def _apply_yaml_config_fn(yaml_cfg, platform_cfg):
    auricle_cfg = yaml_cfg.get("gateway", {}).get("auricle", {})
    if not auricle_cfg:
        return None
    updates = {}
    mappings = [
        ("mic_device",               ENV_MIC_DEVICE,               DEFAULT_MIC_DEVICE),
        ("tts_voice",                ENV_TTS_VOICE,                DEFAULT_TTS_VOICE),
        ("active_listen_duration",   ENV_ACTIVE_LISTEN_DURATION,   str(DEFAULT_ACTIVE_LISTEN_DURATION)),
        ("session_resume",           ENV_SESSION_RESUME,           str(DEFAULT_SESSION_RESUME)),
        ("mute",                     ENV_MUTE,                     str(DEFAULT_MUTE)),
        ("vosk_model_path",          ENV_VOSK_MODEL_PATH,          DEFAULT_VOSK_MODEL_PATH),
        ("oww_wakeword_model_path",  ENV_OWW_WAKEWORD_MODEL_PATH,  DEFAULT_OWW_WAKEWORD_MODEL_PATH),
        ("oww_melspec_model_path",   ENV_OWW_MELSPEC_MODEL_PATH,   DEFAULT_OWW_MELSPEC_MODEL_PATH),
        ("oww_embedding_model_path", ENV_OWW_EMBEDDING_MODEL_PATH, DEFAULT_OWW_EMBEDDING_MODEL_PATH),
    ]
    for yaml_key, env_key, _ in mappings:
        if yaml_key in auricle_cfg and not os.getenv(env_key):
            val = str(auricle_cfg[yaml_key])
            os.environ[env_key] = val
            updates[yaml_key] = val
    return updates or None


async def _standalone_send(
    pconfig,
    chat_id: str,
    message: str,
    *,
    thread_id=None,
    media_files=None,
    force_document: bool = False,
) -> dict:
    """Out-of-process delivery for cron/notification jobs."""
    import re
    voice = os.getenv(ENV_TTS_VOICE, DEFAULT_TTS_VOICE)
    clean = re.sub(r'[*_`#\[\]()]', "", message)[:4000].strip()
    if not clean:
        return {"success": True}
    try:
        ding = await asyncio.create_subprocess_exec(
            PW_PLAY_BIN, f"--target={PW_PLAY_TARGET}", str(ASSET_DING),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        await ding.wait()
        await asyncio.sleep(PROACTIVE_PRE_SPEECH_PAUSE)
        cmd = (
            f"{shlex.quote(EDGE_TTS_BIN)} --voice {shlex.quote(voice)} "
            f"--text {shlex.quote(clean)} --write-media - | "
            f"{shlex.quote(PW_PLAY_BIN)} --target={shlex.quote(PW_PLAY_TARGET)} -"
        )
        proc = await asyncio.create_subprocess_shell(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        await proc.wait()
    except Exception as exc:
        return {"error": str(exc)}
    return {"success": True}


# ── register(ctx) entry point ──────────────────────────────────────────────

def register(ctx) -> None:
    ctx.register_platform(
        name="auricle",
        label="Auricle",
        adapter_factory=lambda cfg: AuricleAdapter(cfg),
        check_fn=check_requirements,
        validate_config=validate_config,
        is_connected=is_connected,
        env_enablement_fn=_env_enablement_fn,
        apply_yaml_config_fn=_apply_yaml_config_fn,
        standalone_sender_fn=_standalone_send,
        cron_deliver_env_var=ENV_HOME_CHANNEL,
        allowed_users_env=ENV_ALLOWED_USERS,
        allow_all_env=ENV_ALLOW_ALL_USERS,
        platform_hint=PLATFORM_HINT,
        emoji="🎙️",
        pii_safe=True,
        allow_update_command=False,
        install_hint=(
            "pip install vosk openwakeword numpy edge-tts\n"
            "System packages: alsa-utils (arecord), pipewire (pw-play)"
        ),
    )
