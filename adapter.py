import asyncio
import json
import logging
import os
import re
import time
from typing import Any, Dict, Optional

import websockets
import websockets.exceptions

from gateway.config import Platform
from gateway.platforms.base import (
    BasePlatformAdapter,
    MessageEvent,
    MessageType,
    SendResult,
)

from .classifier import SystemMessageClassifier
from .consts import (
    CHAT_ID,
    DEFAULT_ENGINE_WS_URL,
    DEFAULT_SESSION_AUTO_CLEAR,
    DEFAULT_SESSION_CLEAR_AFTER,
    DEFAULT_SESSION_RESUME,
    ENV_ALLOW_ALL_USERS,
    ENV_ALLOWED_USERS,
    ENV_ENGINE_WS_URL,
    ENV_HOME_CHANNEL,
    ENV_SESSION_AUTO_CLEAR,
    ENV_SESSION_CLEAR_AFTER,
    ENV_SESSION_RESUME,
    PLATFORM_HINT,
    RETRY_DELAY_SECONDS,
    STREAM_MESSAGE_ID,
    TTS_MAX_CHARS,
)

logger = logging.getLogger(__name__)

_MARKDOWN_RE = re.compile(r'[*_`#\[\]()]')


def _parse_bool(value: str) -> bool:
    return str(value).lower() in ("1", "true", "yes", "on")


class AuricleAdapter(BasePlatformAdapter):
    """
    Thin hermes connector for auricle-engine.

    Connects to the auricle-engine WebSocket server, forwards hermes responses
    as `speak` messages, and dispatches engine utterance events as hermes
    MessageEvents.
    """

    REQUIRES_EDIT_FINALIZE = False

    def __init__(self, config) -> None:
        super().__init__(config, Platform("auricle"))

        self._ws:             Optional[object]       = None
        self._client_id:      Optional[str]          = None
        self._receive_task:   Optional[asyncio.Task] = None
        self._retry_task:     Optional[asyncio.Task] = None
        self._source         = None
        self._classifier     = SystemMessageClassifier()

        self._engine_ws_url      = os.getenv(ENV_ENGINE_WS_URL, DEFAULT_ENGINE_WS_URL)
        self._session_resume     = _parse_bool(os.getenv(ENV_SESSION_RESUME, str(DEFAULT_SESSION_RESUME)))
        self._session_auto_clear = _parse_bool(os.getenv(ENV_SESSION_AUTO_CLEAR, str(DEFAULT_SESSION_AUTO_CLEAR)))
        self._session_clear_after = float(
            os.getenv(ENV_SESSION_CLEAR_AFTER, str(DEFAULT_SESSION_CLEAR_AFTER))
        )
        self._last_dispatch_time: Optional[float] = None
        self._pending_clear: bool = False

    # ── Lifecycle ──────────────────────────────────────────────────────────

    async def connect(self) -> bool:
        self._source = self.build_source(
            chat_id=CHAT_ID,
            chat_type="dm",
            chat_name="Local Speaker",
            user_id=CHAT_ID,
            user_name="user",
        )
        if not self._session_resume:
            self._pending_clear = True

        if await self._connect_ws():
            return True
        self._retry_task = asyncio.create_task(self._retry_loop())
        return False

    async def _connect_ws(self) -> bool:
        try:
            ws = await websockets.connect(self._engine_ws_url)
            raw = await asyncio.wait_for(ws.recv(), timeout=10)
            msg = json.loads(raw)
            if msg.get("t") != "ready":
                logger.error("[auricle] unexpected handshake from engine: %r", msg)
                await ws.close()
                return False
            self._ws        = ws
            self._client_id = msg["client_id"]
            self._receive_task = asyncio.create_task(self._receive_loop())
            self._mark_connected()
            logger.info("[auricle] connected to auricle-engine (client_id=%s)", self._client_id)
            return True
        except Exception as exc:
            logger.warning("[auricle] could not connect to engine at %s: %s", self._engine_ws_url, exc)
            return False

    async def _retry_loop(self) -> None:
        while True:
            await asyncio.sleep(RETRY_DELAY_SECONDS)
            logger.info("[auricle] retrying engine connection…")
            if await self._connect_ws():
                break

    async def disconnect(self) -> None:
        logger.info("[auricle] disconnecting")
        if self._retry_task:
            self._retry_task.cancel()
            self._retry_task = None
        if self._receive_task:
            self._receive_task.cancel()
            self._receive_task = None
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None
        self._mark_disconnected()

    # ── Egress ─────────────────────────────────────────────────────────────

    async def send(
        self,
        chat_id: str,
        content: str,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SendResult:
        logger.info("[auricle] send(): %r", content[:80])
        if self._ws is None:
            return SendResult(success=False, error="not connected to auricle-engine")

        verdict = self._classifier.classify(content)
        if verdict.is_suppression:
            logger.info("[auricle] suppressed (%s): %r", verdict.name, content[:80])
            return SendResult(success=True, message_id=STREAM_MESSAGE_ID)

        try:
            await self._ws.send(json.dumps({
                "t":         "speak",
                "client_id": self._client_id,
                "text":      content[:TTS_MAX_CHARS],
            }))
        except Exception as exc:
            logger.error("[auricle] failed to send to engine: %s", exc)
            return SendResult(success=False, error=str(exc))
        return SendResult(success=True, message_id=STREAM_MESSAGE_ID)

    async def edit_message(
        self,
        chat_id: str,
        message_id: str,
        content: str,
        *,
        finalize: bool = False,
    ) -> SendResult:
        return SendResult(success=True, message_id=message_id)

    async def send_typing(self, chat_id: str, metadata=None) -> None:
        pass  # no typing indicator on a voice device

    async def send_exec_approval(
        self,
        chat_id: str,
        command: str,
        session_key: str,
        description: str = "dangerous command",
        metadata=None,
    ) -> SendResult:
        from tools.approval import resolve_gateway_approval
        resolve_gateway_approval(session_key, "deny")
        return SendResult(success=True)

    async def get_chat_info(self, chat_id: str) -> Dict[str, Any]:
        return {"name": "Local Speaker", "type": "dm", "chat_id": CHAT_ID}

    # ── Ingress (receive loop) ──────────────────────────────────────────────

    async def _receive_loop(self) -> None:
        ws = self._ws
        try:
            async for raw in ws:
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                await self._handle_engine_event(msg)
        except websockets.exceptions.ConnectionClosed:
            logger.warning("[auricle] engine connection closed — scheduling reconnect")
            self._ws        = None
            self._client_id = None
            self._mark_disconnected()
            self._retry_task = asyncio.create_task(self._retry_loop())

    async def _handle_engine_event(self, msg: dict) -> None:
        t = msg.get("t")

        if t == "utterance":
            text = msg.get("text", "")
            if not text or not self._message_handler:
                return
            # Session auto-clear: if the user has been silent for too long,
            # clear the hermes session before dispatching the new utterance.
            now = time.monotonic()
            if (
                self._session_auto_clear
                and self._last_dispatch_time is not None
                and now - self._last_dispatch_time >= self._session_clear_after
            ):
                logger.info("[auricle] idle timeout exceeded — clearing session history")
                self._pending_clear = True
            self._last_dispatch_time = now

            if self._pending_clear:
                self._pending_clear = False
                self._classifier.expect_command_response()
                await self.handle_message(self._make_event("/new", internal=True))

            self._classifier.reset_pending()
            await self.handle_message(self._make_event(text, internal=False))

        elif t == "barge_in":
            # Engine already stopped audio; no action needed on the connector side.
            logger.info("[auricle] barge-in from engine")

        elif t == "cmd":
            name = msg.get("name")
            if name == "new":
                logger.info("[auricle] engine requests session clear → /new")
                if self._message_handler:
                    self._classifier.expect_command_response()
                    await self.handle_message(self._make_event("/new", internal=True))
            elif name == "stop":
                logger.info("[auricle] engine requests stop → /stop")
                if self._message_handler:
                    self._classifier.expect_command_response()
                    await self.handle_message(self._make_event("/stop", internal=True))

        elif t == "state":
            logger.debug(
                "[auricle] engine state: %s (sleeping=%s muted=%s)",
                msg.get("fsm"), msg.get("sleeping"), msg.get("muted"),
            )

        elif t == "error":
            logger.error("[auricle] engine error (%s): %s", msg.get("code"), msg.get("message"))
            self._set_fatal_error(
                msg.get("code", "engine_error"),
                msg.get("message", "unknown engine error"),
                retryable=True,
            )

    def _make_event(self, text: str, *, internal: bool) -> MessageEvent:
        return MessageEvent(
            text=text,
            message_type=MessageType.TEXT,
            source=self._source,
            internal=internal,
        )


# ── Plugin helpers ─────────────────────────────────────────────────────────

def check_requirements() -> bool:
    try:
        import websockets  # noqa: F401
    except ImportError:
        return False
    return True


def validate_config(cfg) -> bool:
    url = os.getenv(ENV_ENGINE_WS_URL, DEFAULT_ENGINE_WS_URL)
    if not url.startswith(("ws://", "wss://")):
        logger.warning("[auricle] AURICLE_ENGINE_WS_URL does not look like a WebSocket URL: %s", url)
        return False
    return True


def is_connected(adapter=None) -> bool:
    return adapter is not None and getattr(adapter, "is_connected", False)


def _env_enablement_fn():
    os.environ.setdefault(ENV_HOME_CHANNEL, CHAT_ID)
    return {"home_channel": {"chat_id": CHAT_ID}}


def _apply_yaml_config_fn(yaml_cfg, platform_cfg):
    auricle_cfg = platform_cfg if isinstance(platform_cfg, dict) else {}
    if not auricle_cfg:
        return None
    updates = {}
    mappings = [
        ("session_resume",    ENV_SESSION_RESUME,    str(DEFAULT_SESSION_RESUME)),
        ("session_auto_clear", ENV_SESSION_AUTO_CLEAR, str(DEFAULT_SESSION_AUTO_CLEAR)),
        ("session_clear_after", ENV_SESSION_CLEAR_AFTER, str(DEFAULT_SESSION_CLEAR_AFTER)),
        ("engine_ws_url",     ENV_ENGINE_WS_URL,     DEFAULT_ENGINE_WS_URL),
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
    """Cron/proactive delivery: connect to the engine and play notify + TTS."""
    clean = _MARKDOWN_RE.sub("", message)[:TTS_MAX_CHARS].strip()
    if not clean:
        return {"success": True}
    engine_url = os.getenv(ENV_ENGINE_WS_URL, DEFAULT_ENGINE_WS_URL)
    try:
        async with websockets.connect(engine_url) as ws:
            raw = await asyncio.wait_for(ws.recv(), timeout=10)
            msg = json.loads(raw)
            if msg.get("t") != "ready":
                return {"error": f"unexpected engine handshake: {msg}"}
            client_id = msg["client_id"]
            await ws.send(json.dumps({"t": "notify", "client_id": client_id, "text": clean}))
            try:
                done_raw = await asyncio.wait_for(ws.recv(), timeout=30)
                done_msg = json.loads(done_raw)
                if done_msg.get("t") != "notify_done":
                    logger.warning("[auricle] unexpected response to notify: %r", done_msg)
            except asyncio.TimeoutError:
                logger.warning("[auricle] notify_done not received within 30s timeout")
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
            "Requires the auricle-engine to be running and reachable.\n"
            "Default: ws://localhost:57310 (set AURICLE_ENGINE_WS_URL to override).\n"
            "See https://github.com/nousresearch/auricle-engine for engine setup."
        ),
    )
