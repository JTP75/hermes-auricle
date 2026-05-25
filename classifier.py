import logging
import unicodedata
from enum import Enum, auto

logger = logging.getLogger(__name__)


class Classification(Enum):
    AGENT_RESPONSE             = auto()
    SUPPRESS_COMMAND_RESPONSE  = auto()
    SUPPRESS_EMOJI_PREFIX      = auto()
    SUPPRESS_KNOWN_LITERAL     = auto()
    SUPPRESS_EMPTY             = auto()

    @property
    def is_suppression(self) -> bool:
        return self is not Classification.AGENT_RESPONSE


# U+2022 BULLET is category Po (Punctuation, other); hermes uses it as a system
# bullet so we need an explicit allowlist — everything else is caught by So/Sk.
_SYSTEM_PUNCT_ALLOWLIST: frozenset[str] = frozenset({"•"})

# No-emoji system strings that slip past Stage 2. Extend as new cases are
# discovered in production logs.
KNOWN_SYSTEM_LITERALS: frozenset[str] = frozenset({
    "No active task to stop.",
    "Nothing to undo.",
})


def _first_non_whitespace(s: str) -> str | None:
    for ch in s:
        if not ch.isspace():
            return ch
    return None


def is_system_glyph(ch: str) -> bool:
    if ch in _SYSTEM_PUNCT_ALLOWLIST:
        return True
    return unicodedata.category(ch) in ("So", "Sk")


class SystemMessageClassifier:
    """
    Three-stage triage that decides whether adapter.send() content is a hermes
    system message (suppress silently) or a genuine agent response (play TTS).

    Stage 1 — pending-command credit: consumed when we forwarded /new or /stop
              and are expecting a system acknowledgement back.
    Stage 2 — emoji/system-glyph prefix: first non-whitespace char is So/Sk or
              in the explicit bullet allowlist. The LLM is instructed never to
              lead with emoji (PLATFORM_HINT), so this is a reliable signal.
    Stage 3 — known no-emoji literals: exact-match on a small static set for
              system strings that have no emoji prefix.
    Stage 4 — default: pass through to TTS playback.
    """

    def __init__(self) -> None:
        self._pending_credits: int = 0

    def expect_command_response(self, n: int = 1) -> None:
        self._pending_credits += n

    def reset_pending(self) -> None:
        if self._pending_credits:
            logger.debug(
                "[auricle] classifier: clearing %d stale credit(s)",
                self._pending_credits,
            )
        self._pending_credits = 0

    def classify(self, content: str) -> Classification:
        if self._pending_credits > 0:
            self._pending_credits -= 1
            return Classification.SUPPRESS_COMMAND_RESPONSE

        glyph = _first_non_whitespace(content)
        if glyph is None:
            return Classification.SUPPRESS_EMPTY
        if is_system_glyph(glyph):
            return Classification.SUPPRESS_EMOJI_PREFIX

        if content.strip() in KNOWN_SYSTEM_LITERALS:
            return Classification.SUPPRESS_KNOWN_LITERAL

        return Classification.AGENT_RESPONSE
