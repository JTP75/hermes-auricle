# Connector-only constants. Audio, STT, TTS, OWW, and sleep constants have
# moved to the auricle-engine repo.

# ── Engine connection ────────────────────────────────────────────────────────
ENV_ENGINE_WS_URL     = "AURICLE_ENGINE_WS_URL"
DEFAULT_ENGINE_WS_URL = "ws://localhost:57310"

# ── Env var names ────────────────────────────────────────────────────────────
ENV_SESSION_RESUME       = "AURICLE_SESSION_RESUME"
ENV_SESSION_AUTO_CLEAR   = "AURICLE_SESSION_AUTO_CLEAR"
ENV_SESSION_CLEAR_AFTER  = "AURICLE_SESSION_CLEAR_AFTER"
ENV_ALLOWED_USERS        = "AURICLE_ALLOWED_USERS"
ENV_ALLOW_ALL_USERS      = "AURICLE_ALLOW_ALL_USERS"
ENV_HOME_CHANNEL         = "AURICLE_HOME_CHANNEL"

# ── Defaults ─────────────────────────────────────────────────────────────────
DEFAULT_SESSION_RESUME      = True
DEFAULT_SESSION_AUTO_CLEAR  = True
DEFAULT_SESSION_CLEAR_AFTER = 3600.0  # seconds

# ── Session ──────────────────────────────────────────────────────────────────
CHAT_ID           = "local"
STREAM_MESSAGE_ID = "auricle_voice_stream"

# ── TTS cap (mirrors engine; prevents oversized speak messages) ──────────────
TTS_MAX_CHARS = 3000

# ── Timing ───────────────────────────────────────────────────────────────────
RETRY_DELAY_SECONDS = 30

# ── Platform hint (injected into every session system prompt) ─────────────────
PLATFORM_HINT = (
    "You are speaking through auricle, the local voice interface for hermes. You respond aloud — "
    "keep everything short and conversational. Never use markdown, code fences, "
    "bullet lists, headers, emojis, or URLs. Do not narrate tools you use and do not think "
    "out loud; give the user a direct, natural-language answer. Prefer one to three sentences. This channel is "
    "NOT capable of approving sensitive tools; any attempts will be auto declined. Also, "
    "Try to keep responses timely; avoid runaway tool loops."
    ""
    "Input notes:"
    "The inputs you receive are interpreted by a model prone to mistakes. Its "
    "generally phonically accurate; for out-of-place or strange word combinations, "
    "try to interpret them phonically. If its too hard to interpret, reply with "
    "options for how to interpret it and let the user specify"
    ""
    "Here are some punctuation conventions: "
    "- Use commas (`,`), semicolons (`;`), or ellipses (`...`) to create natural breathing pauses."
    "- Use newlines to create longer pauses"
    "- Use question marks (`?`) and exclamation points (`!`) to alter pitch and tone."
    "- Avoid run-on sentences; use clear sentence-ending punctuation (`.`) to trigger a natural drop in pitch."
    "- Avoid emdashes (`—`); these behave inconsistently between different voices"
)
