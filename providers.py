import asyncio
import json
import re
import shlex
import subprocess
from abc import ABC, abstractmethod
from typing import Optional, Tuple

from .consts import EDGE_TTS_BIN, PW_PLAY_BIN, SAMPLE_RATE

_MARKDOWN_RE = re.compile(r'[*_`#\[\]()]')


# ── Abstract interfaces ────────────────────────────────────────────────────

class STTProvider(ABC):
    @abstractmethod
    def load(self) -> None:
        """Load model into memory. Called once during adapter boot."""

    @abstractmethod
    def feed(self, pcm_bytes: bytes) -> Tuple[Optional[str], Optional[str]]:
        """Feed a PCM chunk. Returns (final_text, partial_text); at most one is non-None."""

    @abstractmethod
    def reset(self) -> None:
        """Reset internal state (call on wakeword detection and utterance completion)."""


class TTSProvider(ABC):
    @abstractmethod
    async def synthesize(self, sentence: str, target: str) -> asyncio.subprocess.Process:
        """Spawn synthesis+playback. Returns the process handle so barge-in can kill it."""


# ── Vosk STT ───────────────────────────────────────────────────────────────

class VoskSTTProvider(STTProvider):
    def __init__(self, model_path: str) -> None:
        self._model_path = model_path
        self._model      = None
        self._rec        = None

    def load(self) -> None:
        from vosk import Model, KaldiRecognizer
        self._model = Model(self._model_path)
        self._rec   = KaldiRecognizer(self._model, SAMPLE_RATE)

    def feed(self, pcm_bytes: bytes) -> Tuple[Optional[str], Optional[str]]:
        assert self._rec is not None, "VoskSTTProvider not loaded"
        if self._rec.AcceptWaveform(pcm_bytes):
            text = json.loads(self._rec.Result()).get("text", "").strip()
            return (text or None), None
        partial = json.loads(self._rec.PartialResult()).get("partial", "").strip()
        return None, (partial or None)

    def reset(self) -> None:
        if self._rec is not None:
            self._rec.Reset()


# ── Edge-TTS ───────────────────────────────────────────────────────────────

class EdgeTTSProvider(TTSProvider):
    def __init__(self, voice: str) -> None:
        self._voice = voice

    async def synthesize(self, sentence: str, target: str) -> asyncio.subprocess.Process:
        clean = _MARKDOWN_RE.sub("", sentence).strip()
        if not clean:
            # Return a no-op process: nothing to synthesize
            return await asyncio.create_subprocess_exec(
                "true",
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        cmd = (
            f"{shlex.quote(EDGE_TTS_BIN)} --voice {shlex.quote(self._voice)} "
            f"--stream --text {shlex.quote(clean)} | "
            f"{shlex.quote(PW_PLAY_BIN)} --target={shlex.quote(target)} -"
        )
        return await asyncio.create_subprocess_shell(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
