import asyncio
import logging
import os
import signal
import subprocess
from abc import ABC, abstractmethod
from pathlib import Path

from .consts import APLAY_BIN, AUDIO_CHUNK_BYTES, FFMPEG_BIN, SAMPLE_RATE

logger = logging.getLogger(__name__)


class PlaybackHandle(ABC):
    @abstractmethod
    async def wait(self) -> None:
        """Wait until playback completes."""

    @abstractmethod
    def kill(self) -> None:
        """Immediately terminate playback. Safe to call from any thread."""


class AudioInput(ABC):
    @abstractmethod
    def open(self) -> None:
        """Open the audio input stream."""

    @abstractmethod
    def read_chunk(self) -> bytes:
        """Read one audio chunk. Blocks. Returns empty bytes on EOF."""

    @abstractmethod
    def close(self) -> None:
        """Close the audio input stream."""


class AudioOutput(ABC):
    @abstractmethod
    async def play_bytes(self, audio_bytes: bytes) -> PlaybackHandle:
        """Spawn playback of raw audio bytes. Returns a handle to track/kill."""

    @abstractmethod
    async def play_file(self, path: Path) -> None:
        """Play a WAV file and wait for completion."""

    @abstractmethod
    def play_file_sync(self, path: Path) -> None:
        """Blocking WAV playback."""


# ── Concrete: aplay + ffmpeg ───────────────────────────────────────────────

class AplayPlaybackHandle(PlaybackHandle):
    def __init__(self, ffmpeg, aplay) -> None:
        self._ffmpeg = ffmpeg
        self._aplay  = aplay

    async def wait(self) -> None:
        await asyncio.gather(self._ffmpeg.wait(), self._aplay.wait())

    def kill(self) -> None:
        for proc in (self._ffmpeg, self._aplay):
            if proc is None:
                continue
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except (ProcessLookupError, OSError):
                try:
                    proc.kill()
                except Exception:
                    pass


class ArecordInput(AudioInput):
    def __init__(self, device: str) -> None:
        self._device = device
        self._proc: subprocess.Popen | None = None

    def open(self) -> None:
        self._proc = subprocess.Popen(
            [
                "arecord", "-D", self._device,
                "-f", "S16_LE", "-c", "1", "-r", str(SAMPLE_RATE), "-t", "raw", "-q",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )

    def read_chunk(self) -> bytes:
        assert self._proc is not None and self._proc.stdout is not None
        return self._proc.stdout.read(AUDIO_CHUNK_BYTES)

    def close(self) -> None:
        if self._proc is not None:
            try:
                self._proc.kill()
                self._proc.wait(timeout=2)
            except Exception:
                pass
            self._proc = None


class AplayOutput(AudioOutput):
    def __init__(self, device: str) -> None:
        self._device = device

    async def play_bytes(self, audio_bytes: bytes) -> PlaybackHandle:
        r_fd, w_fd = os.pipe()
        try:
            ffmpeg = await asyncio.create_subprocess_exec(
                FFMPEG_BIN, "-hide_banner", "-loglevel", "quiet",
                "-i", "pipe:0",
                "-f", "s16le", "-ar", "48000", "-ac", "2", "pipe:1",
                stdin=asyncio.subprocess.PIPE,
                stdout=w_fd,
                stderr=asyncio.subprocess.DEVNULL,
                preexec_fn=os.setsid,
            )
        finally:
            os.close(w_fd)
        try:
            aplay = await asyncio.create_subprocess_exec(
                APLAY_BIN, "-D", self._device,
                "-r", "48000", "-c", "2", "-f", "S16_LE",
                stdin=r_fd,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                preexec_fn=os.setsid,
            )
        finally:
            os.close(r_fd)
        ffmpeg.stdin.write(audio_bytes)
        await ffmpeg.stdin.drain()
        ffmpeg.stdin.close()
        return AplayPlaybackHandle(ffmpeg, aplay)

    async def play_file(self, path: Path) -> None:
        proc = await asyncio.create_subprocess_exec(
            APLAY_BIN, "-D", self._device, str(path),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.wait()

    def play_file_sync(self, path: Path) -> None:
        subprocess.run(
            [APLAY_BIN, "-D", self._device, str(path)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


# ── Concrete: sounddevice (Windows / cross-platform) ──────────────────────

class SounddevicePlaybackHandle(PlaybackHandle):
    async def wait(self) -> None:
        import sounddevice as sd
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, sd.wait)

    def kill(self) -> None:
        import sounddevice as sd
        sd.stop()


class SounddeviceInput(AudioInput):
    def __init__(self, device=None) -> None:
        self._device = device
        self._stream = None

    def open(self) -> None:
        import sounddevice as sd
        self._stream = sd.RawInputStream(
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype="int16",
            blocksize=AUDIO_CHUNK_BYTES // 2,
            device=self._device,
        )
        self._stream.start()

    def read_chunk(self) -> bytes:
        assert self._stream is not None
        data, _ = self._stream.read(AUDIO_CHUNK_BYTES // 2)
        return bytes(data)

    def close(self) -> None:
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None


class SounddeviceOutput(AudioOutput):
    def __init__(self, device=None) -> None:
        self._device = device

    async def play_bytes(self, audio_bytes: bytes) -> PlaybackHandle:
        import numpy as np
        import sounddevice as sd
        proc = await asyncio.create_subprocess_exec(
            FFMPEG_BIN, "-hide_banner", "-loglevel", "quiet",
            "-i", "pipe:0",
            "-f", "s16le", "-ar", "48000", "-ac", "1",
            "pipe:1",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        pcm_bytes, _ = await proc.communicate(audio_bytes)
        arr = np.frombuffer(pcm_bytes, dtype=np.int16)
        sd.play(arr, samplerate=48000, device=self._device)
        return SounddevicePlaybackHandle()

    async def play_file(self, path: Path) -> None:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._play_wav_sync, path)

    def play_file_sync(self, path: Path) -> None:
        self._play_wav_sync(path)

    def _play_wav_sync(self, path: Path) -> None:
        import wave
        import numpy as np
        import sounddevice as sd
        with wave.open(str(path), "rb") as wf:
            frames = wf.readframes(wf.getnframes())
            arr = np.frombuffer(frames, dtype=np.int16)
            n_ch = wf.getnchannels()
            if n_ch > 1:
                arr = arr.reshape(-1, n_ch)
            sd.play(arr, samplerate=wf.getframerate(), device=self._device)
            sd.wait()
