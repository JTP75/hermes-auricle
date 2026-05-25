from pathlib import Path
from typing import Tuple

# ── Paths ──────────────────────────────────────────────────────────────────
_PLUGIN_DIR = Path(__file__).parent
ASSETS_DIR  = _PLUGIN_DIR / "assets"
_MODELS_DIR = _PLUGIN_DIR / "models"

ASSET_WAKEUP   = ASSETS_DIR / "auricle-wakeup.wav"
ASSET_TOSLEEP  = ASSETS_DIR / "auricle-tosleep.wav"
ASSET_NOTIFY   = ASSETS_DIR / "auricle-notify.wav"
ASSET_CONFUSED = ASSETS_DIR / "auricle-confused.wav"
ALL_ASSETS: Tuple[Path, ...] = (ASSET_WAKEUP, ASSET_TOSLEEP, ASSET_NOTIFY, ASSET_CONFUSED)

TTS_CLEARED = "Session cleared."
TTS_ERROR   = "Something went wrong."

# ── Misinput guard ─────────────────────────────────────────────────────────
MISINPUT_MAX_CONSECUTIVE = 2

MISINPUT_PHRASES: frozenset[str] = frozenset({
    # Articles
    "the", "a", "an",
    # Possessive determiners
    "my", "your", "his", "her", "its", "their", "our",
    # Prepositions
    "of", "in", "on", "at", "to", "for", "from", "with", "by", "about",
    # Conjunctions
    "and", "or", "but", "if", "so",
    # Bare subject pronouns
    "i", "he", "she", "it", "we", "they",
    # Dangling contractions
    "it's", "he's", "she's", "that's", "there's", "what's", "who's",
    "they're", "we're", "i'm", "i've", "i'd", "i'll", "you're",
    # Two-word mid-sentence fragments (conjunction/preposition + article)
    "and the", "or the", "but the",
    "in the", "on the", "of the", "to the", "for the",
})

# ── Env var names ──────────────────────────────────────────────────────────
ENV_MIC_DEVICE               = "AURICLE_MIC_DEVICE"
ENV_TTS_VOICE                = "AURICLE_TTS_VOICE"
ENV_ACTIVE_LISTEN_DURATION   = "AURICLE_ACTIVE_LISTEN_DURATION"
ENV_SESSION_RESUME           = "AURICLE_SESSION_RESUME"
ENV_MUTE                     = "AURICLE_MUTE"
ENV_VOSK_MODEL_PATH          = "AURICLE_VOSK_MODEL_PATH"
ENV_OWW_WAKEWORD_MODEL_PATH  = "AURICLE_OWW_WAKEWORD_MODEL_PATH"
ENV_OWW_MELSPEC_MODEL_PATH   = "AURICLE_OWW_MELSPEC_MODEL_PATH"
ENV_OWW_EMBEDDING_MODEL_PATH = "AURICLE_OWW_EMBEDDING_MODEL_PATH"
ENV_ALLOWED_USERS            = "AURICLE_ALLOWED_USERS"
ENV_ALLOW_ALL_USERS          = "AURICLE_ALLOW_ALL_USERS"
ENV_HOME_CHANNEL             = "AURICLE_HOME_CHANNEL"

# ── Defaults ───────────────────────────────────────────────────────────────
DEFAULT_MIC_DEVICE               = "plughw:3,0"
DEFAULT_TTS_VOICE                = "en-GB-LibbyNeural"
DEFAULT_ACTIVE_LISTEN_DURATION   = 5       # seconds
DEFAULT_SESSION_RESUME           = True
DEFAULT_MUTE                     = False
DEFAULT_VOSK_MODEL_PATH          = str(_MODELS_DIR / "vosk-model")
DEFAULT_OWW_WAKEWORD_MODEL_PATH  = str(_MODELS_DIR / "wakeword.onnx")
DEFAULT_OWW_MELSPEC_MODEL_PATH   = str(_MODELS_DIR / "melspectrogram.onnx")
DEFAULT_OWW_EMBEDDING_MODEL_PATH = str(_MODELS_DIR / "embedding_model.onnx")

# ── Audio ──────────────────────────────────────────────────────────────────
AUDIO_CHUNK_BYTES        = 1280   # OWW hard requirement: 40ms at 16kHz 16-bit mono
SAMPLE_RATE              = 16000
OWW_THRESHOLD            = 0.5
AUDIO_RING_BUFFER_SECONDS = 2.0
AUDIO_RING_BUFFER_CHUNKS  = int(AUDIO_RING_BUFFER_SECONDS * SAMPLE_RATE * 2 / AUDIO_CHUNK_BYTES)  # 50
TTS_ECHO_TAIL_SECONDS     = 0.15

# ── Session ────────────────────────────────────────────────────────────────
CHAT_ID           = "local"
STREAM_MESSAGE_ID = "auricle_voice_stream"

# ── Auto-sleep ─────────────────────────────────────────────────────────────
ENV_SLEEP_TIMEOUT          = "AURICLE_SLEEP_TIMEOUT"
ENV_SLEEP_WAKE_SENSITIVITY = "AURICLE_SLEEP_WAKE_SENSITIVITY"
ENV_SLEEP_FLUX_THRESHOLD   = "AURICLE_SLEEP_FLUX_THRESHOLD"

DEFAULT_SLEEP_TIMEOUT          = 60      # seconds of IDLE silence before sleep
DEFAULT_SLEEP_WAKE_SENSITIVITY = 3.0     # × sleep_baseline → wake threshold
DEFAULT_SLEEP_FLUX_THRESHOLD   = 0.02    # normalized flux EMA "quiet" cutoff
SLEEP_EMA_ALPHA                = 0.01    # ~4-second smoothing at 40ms/chunk

# ── Timing ─────────────────────────────────────────────────────────────────
RETRY_DELAY_SECONDS        = 30
PROACTIVE_PRE_SPEECH_PAUSE = 1.0   # seconds of silence after notify before TTS

# ── Binaries / audio routing ───────────────────────────────────────────────
EDGE_TTS_BIN   = "edge-tts"
PW_PLAY_BIN    = "pw-play"
PW_PLAY_TARGET = "Jabra SPEAK 510 USB"

# ── Voice commands (exact whole-transcript match, case-insensitive) ─────────
CLEAR_COMMANDS: Tuple[str, ...] = ("clear", "reset")
STOP_COMMANDS:  Tuple[str, ...] = ("stop",)

# ── Internal dispatch sentinels ────────────────────────────────────────────
_CMD_CLEAR = "__AURICLE_CLEAR__"
_CMD_STOP  = "__AURICLE_STOP__"

# ── Platform hint (injected into every session system prompt) ──────────────
PLATFORM_HINT = (
    "You are speaking through auricle, the local voice interface for hermes. You respond aloud — "
    "keep everything short and conversational. Never use markdown, code fences, "
    "bullet lists, headers, emojis, or URLs. Do not narrate tools you use; give the user "
    "a direct, natural-language answer. Prefer one to three sentences. This channelt is "
    "NOT capable of approving sensitive tools; any attempts will be auto declined."
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
    "- Avoid emdashes (`—`); these behave inconsistenly between different voices"
)
