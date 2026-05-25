"""
Unit tests for SystemMessageClassifier.

The primary corpus is loaded from the live hermes-agent locale file and
gateway source so the tests track upstream changes automatically. If the
hermes-agent repo is not present, upstream-derived cases are skipped and
a hard-coded fallback corpus covers the known cases.
"""
import pathlib
import re
import sys

import pytest

# Allow importing the package without a full hermes install
sys.path.insert(0, str(pathlib.Path(__file__).parents[1]))

from classifier import (  # noqa: E402
    Classification,
    KNOWN_SYSTEM_LITERALS,
    SystemMessageClassifier,
    is_system_glyph,
)


# ── Upstream corpus ────────────────────────────────────────────────────────────

_HERMES_ROOT = pathlib.Path.home() / "misc/hermes-agent"
_LOCALE_FILE = _HERMES_ROOT / "locales/en.yaml"
_RUN_PY      = _HERMES_ROOT / "gateway/run.py"
_BASE_PY     = _HERMES_ROOT / "gateway/platforms/base.py"

_HERMES_AVAILABLE = _LOCALE_FILE.exists()


def _walk_yaml(d, prefix=""):
    """Recursively yield (key_path, string_value) from a nested dict."""
    for k, v in d.items():
        path = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            yield from _walk_yaml(v, path)
        elif isinstance(v, str):
            yield path, v


def _load_gateway_locale_strings():
    """Return all string values nested under the 'gateway' key of en.yaml."""
    import yaml
    data = yaml.safe_load(_LOCALE_FILE.read_text())
    return [(k, v) for k, v in _walk_yaml(data.get("gateway", {}), "gateway")]


def _extract_tool_progress_prefixes():
    """
    Grep run.py and base.py for tool-progress/still-working format strings
    and return just the static leading prefix (before any { placeholder).
    These are sent via adapter.send() and must be suppressed.
    """
    prefixes = []
    pattern = re.compile(r"""["']((?:🔧|⏳|⚙️)[^"'\n{]*)""")
    for path in (_RUN_PY, _BASE_PY):
        if not path.exists():
            continue
        for m in pattern.finditer(path.read_text()):
            prefixes.append(m.group(1).strip())
    return prefixes


# ── Hard-coded fallback corpus (used when hermes-agent is not present) ─────────

_FALLBACK_SYSTEM_STRINGS = [
    "✨ Session reset! Starting fresh.",
    "✨ New session started!",
    "⚡ Stopped. You can continue this session.",
    "⚡ Stopped. The agent hadn't started yet — you can continue this session.",
    "⚠️ Approval expired (agent is no longer waiting). Ask the agent to try again.",
    "⏳ Draining 1 active agent(s) before restart...",
    "✅ Command approved. The agent is resuming...",
    "❌ Command denied.",
    "🔧 Read: /some/path",
    "⏳ Still working...",
    "◆ Model: gpt-4o",
    "• `none` — (no personality overlay)",
    "↻ Resumed session **my-session** (3 messages). Conversation restored.",
    "↩️ Undid 2 message(s).\nRemoved: \"hello\"",
]

_FALLBACK_AGENT_STRINGS = [
    "The capital of France is Paris.",
    "Once upon a time, a star fell from the sky and landed in a quiet forest.",
    "Sure, here's a summary of what we discussed.",
    "I'm sorry, I didn't catch that. Could you repeat it?",
    "No problem! Let me look that up for you.",
]


# ── Tests ──────────────────────────────────────────────────────────────────────

class TestIsSystemGlyph:
    """Unit tests for the glyph classifier helper."""

    @pytest.mark.parametrize("ch", [
        "✨", "⚡", "🔧", "⏳", "🤖", "✅", "❌",
        "⚠",   # U+26A0 WARNING SIGN — note: bare char, not the ⚠️ emoji sequence
        "◆",   # U+25C6 BLACK DIAMOND — category So
        "✓",   # U+2713 CHECK MARK — category So
        "✗",   # U+2717 BALLOT X — category So
        "⑂",   # U+2442 OCR FORK — category So (branch symbol)
        "↻",   # U+21BB CLOCKWISE OPEN CIRCLE ARROW — category So
        "♻",   # U+267B BLACK UNIVERSAL RECYCLING SYMBOL — category So
        "⏱",   # U+23F1 STOPWATCH — category So
        "•",   # U+2022 BULLET — category Po, in explicit allowlist
    ])
    def test_system_glyphs_are_detected(self, ch):
        assert is_system_glyph(ch), f"{ch!r} should be a system glyph"

    @pytest.mark.parametrize("ch", ["H", "T", ".", ",", "S", "N", "a", "1"])
    def test_plain_chars_not_system(self, ch):
        assert not is_system_glyph(ch), f"{ch!r} should not be a system glyph"


class TestClassificationVerdicts:
    """Pin the exact verdict for canonical inputs."""

    def setup_method(self):
        self.clf = SystemMessageClassifier()

    @pytest.mark.parametrize("content,expected", [
        ("✨ Session reset! Starting fresh.", Classification.SUPPRESS_EMOJI_PREFIX),
        ("⚡ Stopped. You can continue this session.", Classification.SUPPRESS_EMOJI_PREFIX),
        ("◆ Model: gpt-4o", Classification.SUPPRESS_EMOJI_PREFIX),
        ("• `none` — (no personality overlay)", Classification.SUPPRESS_EMOJI_PREFIX),
        ("🔧 Read: /home/user/file.py", Classification.SUPPRESS_EMOJI_PREFIX),
        ("⏳ Still working...", Classification.SUPPRESS_EMOJI_PREFIX),
        ("No active task to stop.", Classification.SUPPRESS_KNOWN_LITERAL),
        ("Nothing to undo.", Classification.SUPPRESS_KNOWN_LITERAL),
        ("  No active task to stop.  ", Classification.SUPPRESS_KNOWN_LITERAL),  # leading/trailing ws
        ("", Classification.SUPPRESS_EMPTY),
        ("   \n  ", Classification.SUPPRESS_EMPTY),
        ("Hello, world.", Classification.AGENT_RESPONSE),
        ("Once upon a time, a star fell from the sky.", Classification.AGENT_RESPONSE),
        ("The capital of France is Paris.", Classification.AGENT_RESPONSE),
        ("Sure, I can help with that.", Classification.AGENT_RESPONSE),
    ])
    def test_exact_verdict(self, content, expected):
        assert self.clf.classify(content) == expected


class TestPendingCommandCredits:
    """Stage 1 lifecycle: set, consume, stale-clear."""

    def setup_method(self):
        self.clf = SystemMessageClassifier()

    def test_credit_suppresses_next_send(self):
        self.clf.expect_command_response()
        assert self.clf.classify("anything at all") == Classification.SUPPRESS_COMMAND_RESPONSE

    def test_credit_is_consumed_after_one_use(self):
        self.clf.expect_command_response()
        self.clf.classify("first send")
        result = self.clf.classify("Hello, I can help.")
        assert result == Classification.AGENT_RESPONSE

    def test_two_credits_two_suppressions(self):
        self.clf.expect_command_response(n=2)
        assert self.clf.classify("first").is_suppression
        assert self.clf.classify("second").is_suppression
        assert self.clf.classify("Hello.") == Classification.AGENT_RESPONSE

    def test_reset_pending_clears_stale_credits(self):
        self.clf.expect_command_response()
        self.clf.reset_pending()
        assert self.clf.classify("Hello.") == Classification.AGENT_RESPONSE

    def test_stage1_beats_stage2(self):
        """A pending credit suppresses even a clean agent response."""
        self.clf.expect_command_response()
        # "Hello." would normally be AGENT_RESPONSE — but credit wins
        assert self.clf.classify("Hello.") == Classification.SUPPRESS_COMMAND_RESPONSE

    def test_stage1_beats_stage4(self):
        """A pending credit suppresses even an emoji-less sentence (not just emoji msgs)."""
        self.clf.expect_command_response()
        result = self.clf.classify("No active task to stop.")
        assert result == Classification.SUPPRESS_COMMAND_RESPONSE


class TestKnownLiterals:
    """KNOWN_SYSTEM_LITERALS coverage."""

    def setup_method(self):
        self.clf = SystemMessageClassifier()

    def test_all_known_literals_suppressed(self):
        for literal in KNOWN_SYSTEM_LITERALS:
            verdict = self.clf.classify(literal)
            assert verdict == Classification.SUPPRESS_KNOWN_LITERAL, (
                f"{literal!r} should be SUPPRESS_KNOWN_LITERAL, got {verdict}"
            )

    def test_substring_not_matched(self):
        """Substring of a known literal must NOT be suppressed as a literal."""
        verdict = self.clf.classify("to stop")
        assert verdict == Classification.AGENT_RESPONSE


# ── Upstream-sourced corpus tests ──────────────────────────────────────────────

@pytest.mark.skipif(not _HERMES_AVAILABLE, reason="hermes-agent repo not at ~/misc/hermes-agent")
class TestUpstreamLocaleStrings:
    """
    Locale strings from gateway namespaces that auricle can actually receive
    must all be suppressed. Namespaces tied to Telegram/Discord/voice-platform
    features that auricle never invokes (topic, usage, verbose, voice, etc.)
    are intentionally excluded — those strings will never arrive here.

    If this test fails it means hermes added a system message in one of the
    auricle-relevant namespaces that slips through the classifier — extend
    KNOWN_SYSTEM_LITERALS or adjust glyph detection.
    """

    # Only the namespaces directly triggered by auricle's two hardcoded
    # voice commands: _CMD_CLEAR → /new (reset), _CMD_STOP → /stop (stop).
    # Everything else (agents, branch, goal, topic, voice, usage, etc.)
    # requires slash commands auricle never dispatches, so those strings are
    # out of scope — if they ever arrive unexpectedly, Stage 2 (emoji prefix)
    # or Stage 3 (known literals) will handle them.
    _AURICLE_NAMESPACES: tuple[str, ...] = (
        "gateway.reset",
        "gateway.stop",
    )

    def setup_method(self):
        self.clf = SystemMessageClassifier()

    def _classify_template(self, template: str) -> Classification:
        """Fill {format} placeholders with 'X' and classify the result."""
        filled = re.sub(r"\{[^}]+\}", "X", template)
        return self.clf.classify(filled)

    def _is_relevant(self, key: str) -> bool:
        return any(key.startswith(ns) for ns in self._AURICLE_NAMESPACES)

    def test_auricle_relevant_locale_strings_suppressed(self):
        failures = []
        checked = 0
        for key, template in _load_gateway_locale_strings():
            if not self._is_relevant(key):
                continue
            if not template.strip():
                continue
            checked += 1
            verdict = self._classify_template(template)
            if not verdict.is_suppression:
                failures.append((key, template[:60], verdict.name))

        assert checked > 0, "No auricle-relevant locale strings found — check AURICLE_NAMESPACES"

        if failures:
            lines = [f"  [{key}] {tmpl!r} → {v}" for key, tmpl, v in failures]
            pytest.fail(
                f"{len(failures)} auricle-relevant gateway string(s) classified as AGENT_RESPONSE:\n"
                + "\n".join(lines)
                + "\n\nExtend KNOWN_SYSTEM_LITERALS or add a glyph to the allowlist."
            )

    def test_tool_progress_prefixes_suppressed(self):
        prefixes = _extract_tool_progress_prefixes()
        if not prefixes:
            pytest.skip("No tool-progress format strings found in source")
        for prefix in prefixes:
            verdict = self.clf.classify(prefix)
            assert verdict.is_suppression, (
                f"Tool progress prefix {prefix!r} was not suppressed (got {verdict.name})"
            )


@pytest.mark.skipif(not _HERMES_AVAILABLE, reason="hermes-agent repo not at ~/misc/hermes-agent")
class TestFallbackCorpusWhenHermesPresent:
    """When hermes is present, also validate the hard-coded fallback strings."""

    def setup_method(self):
        self.clf = SystemMessageClassifier()

    @pytest.mark.parametrize("content", _FALLBACK_SYSTEM_STRINGS)
    def test_fallback_system_strings_suppressed(self, content):
        assert self.clf.classify(content).is_suppression, (
            f"{content!r} should be suppressed"
        )

    @pytest.mark.parametrize("content", _FALLBACK_AGENT_STRINGS)
    def test_fallback_agent_strings_play(self, content):
        assert self.clf.classify(content) == Classification.AGENT_RESPONSE, (
            f"{content!r} should be an agent response"
        )


@pytest.mark.skipif(_HERMES_AVAILABLE, reason="hermes-agent present; upstream tests run instead")
class TestFallbackCorpusStandalone:
    """Offline validation using the hard-coded corpus when hermes is absent."""

    def setup_method(self):
        self.clf = SystemMessageClassifier()

    @pytest.mark.parametrize("content", _FALLBACK_SYSTEM_STRINGS)
    def test_system_strings_suppressed(self, content):
        assert self.clf.classify(content).is_suppression, (
            f"{content!r} should be suppressed"
        )

    @pytest.mark.parametrize("content", _FALLBACK_AGENT_STRINGS)
    def test_agent_strings_play(self, content):
        assert self.clf.classify(content) == Classification.AGENT_RESPONSE, (
            f"{content!r} should be an agent response"
        )
