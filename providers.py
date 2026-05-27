import collections
import json
import re
from abc import ABC, abstractmethod
from typing import AsyncIterator, Optional, Tuple

import edge_tts
import numpy as np

from .consts import (
    SAMPLE_RATE,
    WHISPER_FRAME_BYTES,
    WHISPER_MIN_SPEECH_FRAMES,
    WHISPER_PADDING_MS,
    WHISPER_SILENCE_FRAMES,
    WHISPER_VAD_AGGRESSIVENESS,
    WHISPER_VAD_BLOCK_MS,
)

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
    VAD-gated Whisper STT provider using webrtcvad + HuggingFace transformers.

    Each feed() call receives a 1280-byte OWW chunk (40 ms). Internally, chunks
    are re-sliced into 960-byte VAD frames (30 ms) via a remainder buffer, since
    1280 is not a multiple of 960. Speech onset returns a partial sentinel so the
    FSM can transition AWAITING_UTTERANCE → UTTERANCE. Inference fires once per
    utterance (after silence), so latency scales with utterance length, not chunk
    size. CUDA is used automatically when available.
    """

    def __init__(self, model_id: str) -> None:
        self._model_id = model_id
        self._pipe     = None
        self._vad      = None
        self._padding_frames = WHISPER_PADDING_MS // WHISPER_VAD_BLOCK_MS
        self._ring_buffer: collections.deque | None = None
        self._voiced_frames: list[bytes] = []
        self._remainder: bytes = b""
        self._triggered    = False
        self._silence_count = 0

    def load(self) -> None:
        import torch
        import webrtcvad
        from transformers import AutoModelForSpeechSeq2Seq, AutoProcessor, pipeline as hf_pipeline

        device = "cuda" if torch.cuda.is_available() else "cpu"
        dtype  = torch.float16 if torch.cuda.is_available() else torch.float32

        model = AutoModelForSpeechSeq2Seq.from_pretrained(
            self._model_id,
            torch_dtype=dtype,
            low_cpu_mem_usage=True,
            use_safetensors=True,
        ).to(device)
        processor = AutoProcessor.from_pretrained(self._model_id)
        self._pipe = hf_pipeline(
            "automatic-speech-recognition",
            model=model,
            tokenizer=processor.tokenizer,
            feature_extractor=processor.feature_extractor,
            max_new_tokens=128,
            torch_dtype=dtype,
            device=device,
        )
        self._vad         = webrtcvad.Vad(WHISPER_VAD_AGGRESSIVENESS)
        self._ring_buffer = collections.deque(maxlen=self._padding_frames)
        self._reset_state()

    def feed(self, pcm_bytes: bytes) -> Tuple[Optional[str], Optional[str]]:
        assert self._pipe is not None and self._vad is not None, "WhisperSTTProvider not loaded"

        # Re-slice incoming chunk into exact VAD frame sizes.
        data = self._remainder + pcm_bytes
        offset = 0
        frames: list[bytes] = []
        while offset + WHISPER_FRAME_BYTES <= len(data):
            frames.append(data[offset : offset + WHISPER_FRAME_BYTES])
            offset += WHISPER_FRAME_BYTES
        self._remainder = data[offset:]

        has_onset = False

        for pcm in frames:
            is_speech = self._vad.is_speech(pcm, SAMPLE_RATE)

            if not self._triggered:
                self._ring_buffer.append((pcm, is_speech))
                num_voiced = sum(1 for _, s in self._ring_buffer if s)
                if num_voiced > 0.8 * self._ring_buffer.maxlen:
                    # Speech onset — pull buffered context so we don't clip the start of a word
                    self._triggered     = True
                    self._voiced_frames = [p for p, _ in self._ring_buffer]
                    self._ring_buffer.clear()
                    self._silence_count = 0
                    has_onset = True
            else:
                self._voiced_frames.append(pcm)
                if is_speech:
                    self._silence_count = 0
                else:
                    self._silence_count += 1

                if self._silence_count >= WHISPER_SILENCE_FRAMES:
                    # End of utterance — run inference
                    self._triggered     = False
                    self._silence_count = 0

                    if len(self._voiced_frames) >= WHISPER_MIN_SPEECH_FRAMES:
                        audio_np  = np.frombuffer(b"".join(self._voiced_frames), dtype=np.int16)
                        audio_f32 = audio_np.astype(np.float32) / 32768.0
                        result    = self._pipe(
                            {"array": audio_f32, "sampling_rate": SAMPLE_RATE},
                            generate_kwargs={"language": "english"},
                        )
                        text = result["text"].strip()
                        if text:
                            self._voiced_frames = []
                            self._ring_buffer.clear()
                            return text, None

                    self._voiced_frames = []
                    self._ring_buffer.clear()

        if has_onset:
            return None, "..."   # partial sentinel — trips AWAITING_UTTERANCE → UTTERANCE
        return None, None

    def reset(self) -> None:
        self._reset_state()

    def _reset_state(self) -> None:
        self._remainder     = b""
        self._voiced_frames = []
        self._triggered     = False
        self._silence_count = 0
        if self._ring_buffer is not None:
            self._ring_buffer.clear()


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
