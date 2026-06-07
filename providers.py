import json
import re
import subprocess
import sys
from abc import ABC, abstractmethod
from typing import AsyncIterator, Optional, Tuple

import edge_tts

from .consts import SAMPLE_RATE

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
    def stream_audio(self, sentence: str) -> AsyncIterator[bytes]:
        """Yield raw MP3 bytes for the sentence as they arrive from the TTS service."""


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


# ── Distil-Whisper STT ─────────────────────────────────────────────────────

class WhisperSTTProvider(STTProvider):
    """
    Subprocess-backed Whisper STT provider.

    Spawns whisper_worker.py in a separate Python 3.10 process that owns model
    loading, webrtcvad framing, and HuggingFace pipeline inference. The parent
    communicates via a binary/line protocol over stdin/stdout pipes. The model
    loads once on load(); feed() and reset() are thin IPC wrappers with no
    inference overhead in the hermes process.

    Wire protocol:
      IN  0x01 + 1280 bytes  → feed chunk  OUT: OK | P | F:<text>
      IN  0x02               → reset       OUT: OK
      IN  0x03               → shutdown    (no response)
    """

    def __init__(self, model_id: str, python_path: str, worker_path: str) -> None:
        self._model_id    = model_id
        self._python_path = python_path
        self._worker_path = worker_path
        self._proc: Optional[subprocess.Popen] = None
        self._loading: bool = False

    def load(self) -> None:
        # If a worker is already mid-load (gateway timed out and retried), wait
        # for it rather than killing it — killing aborts the HF model download
        # and corrupts the cache.
        if self._loading and self._proc and self._proc.poll() is None:
            line = self._proc.stdout.readline()
            if line and line.strip() == b"READY":
                self._loading = False
                return
            # Worker died during loading; fall through to spawn a fresh one.
            self._proc = None
            self._loading = False

        # Kill any previously loaded (non-loading) worker before replacing it.
        if self._proc and self._proc.poll() is None:
            try:
                self._proc.stdin.write(b"\x03")
                self._proc.stdin.flush()
            except OSError:
                pass
            self._proc.terminate()
            self._proc = None

        self._loading = True
        self._proc = subprocess.Popen(
            [self._python_path, self._worker_path, "--model-id", self._model_id],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL if sys.platform == "win32" else None,
        )
        line = self._proc.stdout.readline()
        self._loading = False
        if not line or line.strip() != b"READY":
            if self._proc is not None:
                self._proc.kill()
            self._proc = None
            raise RuntimeError(
                f"whisper_worker failed to start (got {line!r}). "
                "Check AURICLE_WHISPER_PYTHON and its installed dependencies."
            )

    def feed(self, pcm_bytes: bytes) -> Tuple[Optional[str], Optional[str]]:
        if self._proc is None or self._proc.poll() is not None:
            raise RuntimeError("WhisperSTTProvider worker is not running")
        self._proc.stdin.write(b"\x01" + pcm_bytes)
        self._proc.stdin.flush()
        line = self._proc.stdout.readline().decode().strip()
        if line == "P":
            return None, "..."   # partial sentinel — trips AWAITING_UTTERANCE → UTTERANCE
        if line.startswith("F:"):
            return line[2:], None
        return None, None        # OK or unexpected

    def reset(self) -> None:
        if self._proc is None or self._proc.poll() is not None:
            return
        self._proc.stdin.write(b"\x02")
        self._proc.stdin.flush()
        self._proc.stdout.readline()  # consume OK

    def terminate(self) -> None:
        """Shut down the worker process. Called by the adapter on disconnect."""
        if self._proc and self._proc.poll() is None:
            try:
                self._proc.stdin.write(b"\x03")
                self._proc.stdin.flush()
            except OSError:
                pass
            self._proc.terminate()
        self._proc = None


# ── Edge-TTS ───────────────────────────────────────────────────────────────

class EdgeTTSProvider(TTSProvider):
    def __init__(self, voice: str) -> None:
        self._voice = voice

    async def stream_audio(self, sentence: str) -> AsyncIterator[bytes]:
        clean = _MARKDOWN_RE.sub("", sentence).strip()
        if not clean:
            return
        communicate = edge_tts.Communicate(clean, self._voice)
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                yield chunk["data"]
