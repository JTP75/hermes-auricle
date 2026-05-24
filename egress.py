import asyncio
import logging
import os
import re
import signal
from pathlib import Path
from typing import Optional

from .audio_buffer import AudioBuffer
from .consts import PW_PLAY_BIN, PW_PLAY_TARGET

logger = logging.getLogger(__name__)

_SENTENCE_BOUNDARY = re.compile(r'(?<=[.?!])\s+|\n+')


def _segment(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_BOUNDARY.split(text) if s.strip()]


class EgressController:
    """
    Manages streaming TTS playback for one agent response turn.

    Hermes calls send() once then edit_message() repeatedly with cumulative
    text. This controller diffs each call, segments sentences, and plays them
    in order via a sequential asyncio queue.
    """

    def __init__(
        self,
        tts_provider,
        barge_in_event: asyncio.Event,
        audio_buffer: AudioBuffer,
    ) -> None:
        self._tts          = tts_provider
        self._barge_in     = barge_in_event
        self._audio_buffer = audio_buffer

        self._processed_len: int                              = 0
        self._text_buffer:   str                              = ""
        self._queue:         asyncio.Queue                    = asyncio.Queue()
        self._active_proc:   Optional[asyncio.subprocess.Process] = None
        self._worker_task:   Optional[asyncio.Task]           = None

    def reset(self) -> None:
        self._processed_len = 0
        self._text_buffer   = ""
        self._queue         = asyncio.Queue()
        self._active_proc   = None
        self._worker_task   = None
        self._barge_in.clear()
        self._audio_buffer.set_tts_active(False)

    def abort(self) -> None:
        """Forcefully abort the active egress playback task and clear the queue."""
        logger.info("[auricle] aborting active egress playback")
        self._barge_in.set()
        self._audio_buffer.set_tts_active(False)
        self.kill_active()
        
        # Drain queue and mark tasks done to unlock any pending queue joins
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
                self._queue.task_done()
            except (asyncio.QueueEmpty, ValueError):
                break
        
        # Cancel worker thread
        if self._worker_task and not self._worker_task.done():
            self._worker_task.cancel()
            self._worker_task = None

    def start_worker(self) -> None:
        self._worker_task = asyncio.create_task(self._worker())

    async def process_delta(self, cumulative_text: str, *, finalize: bool) -> None:
        if self._barge_in.is_set():
            logger.debug("[auricle] process_delta ignored: barge-in event is set")
            return

        new_text = cumulative_text[self._processed_len:]
        self._processed_len = len(cumulative_text)
        self._text_buffer  += new_text

        sentences = _segment(self._text_buffer)

        if finalize:
            completed         = sentences
            self._text_buffer = ""
        elif len(sentences) > 1:
            completed         = sentences[:-1]
            self._text_buffer = sentences[-1]
        else:
            completed = []

        for sentence in completed:
            await self._queue.put(sentence)

        if finalize:
            await self._queue.put(None)  # sentinel — signals end of turn
            await self._queue.join()
            if self._worker_task:
                self._worker_task.cancel()

    def kill_active(self) -> None:
        """Kill the current pw-play process group. Safe to call from any thread."""
        proc = self._active_proc
        if proc is not None:
            try:
                # Forcefully terminate the entire process group (shell, edge-tts and pw-play pipeline)
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except (ProcessLookupError, OSError):
                # Fallback to direct kill if process group lookup fails
                try:
                    proc.kill()
                except Exception:
                    pass

    async def play_file(self, path: Path) -> None:
        """Play a WAV asset file directly (for ding/ping/bong/etc.)."""
        proc = await asyncio.create_subprocess_exec(
            PW_PLAY_BIN,
            f"--target={PW_PLAY_TARGET}",
            str(path),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.wait()

    async def _worker(self) -> None:
        while True:
            if self._barge_in.is_set():
                self._drain()
                break

            sentence = await self._queue.get()

            if self._barge_in.is_set():
                self._queue.task_done()
                self._drain()
                break

            if sentence is None:
                self._queue.task_done()
                logger.info("[auricle] TTS turn complete")
                break

            try:
                proc = await self._tts.synthesize(sentence, PW_PLAY_TARGET)
                self._active_proc = proc
                self._audio_buffer.set_tts_active(True)
                await proc.wait()
            except Exception as exc:
                logger.error("[auricle] TTS playback error: %s", exc)
            finally:
                self._audio_buffer.set_tts_active(False)
                self._active_proc = None
                self._queue.task_done()

    def _drain(self) -> None:
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
                self._queue.task_done()
            except asyncio.QueueEmpty:
                break
