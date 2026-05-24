from pathlib import Path
from typing import Tuple

# ── Paths ──────────────────────────────────────────────────────────────────
_PLUGIN_DIR = Path(__file__).parent
ASSETS_DIR  = _PLUGIN_DIR / "assets"
_MODELS_DIR = _PLUGIN_DIR / "models"

ASSET_PING    = ASSETS_DIR / "ping.wav"
ASSET_BONG    = ASSETS_DIR / "bong.wav"
ASSET_DING    = ASSETS_DIR / "ding.wav"
ASSET_CLEARED = ASSETS_DIR / "cleared.wav"
ASSET_ERROR   = ASSETS_DIR / "error.wav"
ALL_ASSETS: Tuple[Path, ...] = (ASSET_PING, ASSET_BONG, ASSET_DING, ASSET_CLEARED, ASSET_ERROR)

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
DEFAULT_OWW_WAKEWORD_MODEL_PATH  = str(_MODELS_DIR / "wakeword.tflite")
DEFAULT_OWW_MELSPEC_MODEL_PATH   = str(_MODELS_DIR / "melspectrogram.tflite")
DEFAULT_OWW_EMBEDDING_MODEL_PATH = str(_MODELS_DIR / "embedding_model.tflite")

# ── Audio ──────────────────────────────────────────────────────────────────
AUDIO_CHUNK_BYTES = 1280   # OWW hard requirement: 40ms at 16kHz 16-bit mono
SAMPLE_RATE       = 16000
OWW_THRESHOLD     = 0.5

# ── Session ────────────────────────────────────────────────────────────────
CHAT_ID           = "local"
STREAM_MESSAGE_ID = "auricle_voice_stream"

# ── Timing ─────────────────────────────────────────────────────────────────
RETRY_DELAY_SECONDS        = 30
PROACTIVE_PRE_SPEECH_PAUSE = 1.0   # seconds of silence after ding before TTS

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
    "You are auricle, the local voice interface for hermes. You respond aloud — "
    "keep everything short and conversational. Never use markdown, code fences, "
    "bullet lists, headers, or URLs. Do not narrate tools you use; give the user "
    "a direct, natural-language answer. Prefer one to three sentences."
)
