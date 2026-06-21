import asyncio
import json
import logging
import os
import re
import time
import uuid
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
    DEFAULT_CONNECTOR_HOST,
    DEFAULT_CONNECTOR_PORT,
    DEFAULT_SESSION_AUTO_CLEAR,
    DEFAULT_SESSION_CLEAR_AFTER,
    DEFAULT_SESSION_RESUME,
    ENV_ALLOW_ALL_USERS,
    ENV_ALLOWED_USERS,
    ENV_CONNECTOR_HOST,
    ENV_CONNECTOR_PORT,
    ENV_HOME_CHANNEL,
    ENV_SESSION_AUTO_CLEAR,
    ENV_SESSION_CLEAR_AFTER,
    ENV_SESSION_RESUME,
    PLATFORM_HINT,
    STREAM_MESSAGE_ID,
    TTS_MAX_CHARS,
)

logger = logging.getLogger(__name__)

_MARKDOWN_RE = re.compile(r'[*_`#\[\]()]')


def _parse_bool(value: str) -> bool:
    return str(value).lower() in ("1", "true", "yes", "on")


class AuricleAdapter(BasePlatformAdapter):
    """
    Hermes connector for auricle-engine.

    Acts as a WebSocket server — the engine connects to it. Forwards hermes
    responses as `speak` messages and dispatches engine utterance events as
    hermes MessageEvents.
    """

    REQUIRES_EDIT_FINALIZE = False

    def __init__(self, config) -> None:
        super().__init__(config, Platform("auricle"))

        self._ws:        Optional[object]              = None
        self._client_id: Optional[str]                = None
        self._server:    Optional[object]              = None  # websockets.WebSocketServer
        self._source    = None
        self._classifier = SystemMessageClassifier()

        self._session_resume      = _parse_bool(os.getenv(ENV_SESSION_RESUME, str(DEFAULT_SESSION_RESUME)))
        self._session_auto_clear  = _parse_bool(os.getenv(ENV_SESSION_AUTO_CLEAR, str(DEFAULT_SESSION_AUTO_CLEAR)))
        self._session_clear_after = float(os.getenv(ENV_SESSION_CLEAR_AFTER, str(DEFAULT_SESSION_CLEAR_AFTER)))
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

        host = os.getenv(ENV_CONNECTOR_HOST, DEFAULT_CONNECTOR_HOST)
        port = int(os.getenv(ENV_CONNECTOR_PORT, str(DEFAULT_CONNECTOR_PORT)))

        try:
            self._server = await websockets.serve(
                self._handle_engine_connection, host, port
            )
        except OSError as exc:
            logger.error("[auricle] could not bind ws://%s:%d: %s", host, port, exc)
            self._set_fatal_error("port_unavailable", str(exc), retryable=False)
            return False

        self._mark_connected()
        logger.info("[auricle] listening for engine on ws://%s:%d", host, port)
        return True

    async def disconnect(self) -> None:
        logger.info("[auricle] disconnecting")
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None
        self._mark_disconnected()

    # ── Engine connection handler ───────────────────────────────────────────

    async def _handle_engine_connection(self, ws) -> None:
        """Called by websockets for each incoming engine connection."""
        if self._ws is not None:
            logger.warning("[auricle] rejecting second engine connection")
            await ws.close(1008, "Only one engine supported")
            return

        client_id = uuid.uuid4().hex[:8]
        self._ws        = ws
        self._client_id = client_id
        logger.info("[auricle] engine connected (client_id=%s)", client_id)

        await ws.send(json.dumps({"t": "ready", "client_id": client_id}))

        try:
            async for raw in ws:
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                await self._handle_engine_event(msg)
        except websockets.exceptions.ConnectionClosed:
            logger.info("[auricle] engine disconnected (client_id=%s)", client_id)
        finally:
            self._ws        = None
            self._client_id = None

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
            return SendResult(success=False, error="engine not connected")

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
        pass

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

    # ── Engine event handling ───────────────────────────────────────────────

    async def _handle_engine_event(self, msg: dict) -> None:
        t = msg.get("t")

        if t == "utterance":
            text = msg.get("text", "")
            if not text or not self._message_handler:
                return
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
    try:
        port = int(os.getenv(ENV_CONNECTOR_PORT, str(DEFAULT_CONNECTOR_PORT)))
        if not (1 <= port <= 65535):
            raise ValueError(f"port out of range: {port}")
    except ValueError as exc:
        logger.warning("[auricle] invalid connector port: %s", exc)
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
        ("connector_host",    ENV_CONNECTOR_HOST,    DEFAULT_CONNECTOR_HOST),
        ("connector_port",    ENV_CONNECTOR_PORT,    str(DEFAULT_CONNECTOR_PORT)),
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
    # Standalone delivery is not supported in the server model: the engine
    # connects to hermes, so there is no address to reach the engine directly
    # when the hermes gateway is not running.
    logger.warning("[auricle] standalone_send: not supported (engine-as-client model)")
    return {"error": "standalone delivery not supported — start the hermes gateway"}


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
            "Requires auricle-engine to connect in. Set AURICLE_CONNECTOR_HOST=0.0.0.0\n"
            "in the connector config to accept engine connections from remote machines."
        ),
    )
