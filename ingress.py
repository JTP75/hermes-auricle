import asyncio
import logging
import subprocess
import threading
import time
from typing import Callable, Coroutine

import numpy as np

from .audio_buffer import AudioBuffer
from .consts import (
    AUDIO_CHUNK_BYTES,
    ASSET_CONFUSED,
    ASSET_TOSLEEP,
    ASSET_WAKEUP,
    CLEAR_COMMANDS,
    MISINPUT_MAX_CONSECUTIVE,
    MISINPUT_PHRASES,
    STOP_COMMANDS,
    PW_PLAY_BIN,
    PW_PLAY_TARGET,
    _CMD_CLEAR,
    _CMD_STOP,
)
from .fsm import FSM, State
from .sleep import SleepDetector, SleepSignal

logger = logging.getLogger(__name__)


def _play_asset_sync(path) -> None:
    """Blocking WAV playback for short feedback assets."""
    subprocess.run(
        [PW_PLAY_BIN, f"--target={PW_PLAY_TARGET}", str(path)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def run_ingress_loop(
    *,
    arecord_proc: subprocess.Popen,
    oww,
    wakeword_key: str,
    stt_provider,
    egress,
    audio_buffer: AudioBuffer,
    fsm: FSM,
    loop: asyncio.AbstractEventLoop,
    dispatch_fn: Callable[[str], Coroutine],
    stop_event: threading.Event,
    active_listen_duration: float,
    oww_threshold: float,
    sleep_detector: SleepDetector,
) -> None:
    """
    Synchronous ingress thread.

    Reads 1280-byte PCM chunks from arecord, runs OWW for wakeword/barge-in
    detection, and feeds vosk for STT. Dispatches final transcripts and
    internal commands to the event loop via asyncio.run_coroutine_threadsafe.
    """
    active_listen_deadline: float | None = None
    consecutive_misinputs: int = 0
    was_idle: bool = False
    stdout = arecord_proc.stdout

    while not stop_event.is_set():
        data = stdout.read(AUDIO_CHUNK_BYTES)
        if not data:
            logger.error("[auricle] arecord closed unexpectedly — ingress exiting")
            break

        audio_buffer.append(data)
        state = fsm.get()

        # ── IDLE: wakeword detection + auto-sleep ──────────────────────────
        if state == State.IDLE:
            if not was_idle:
                sleep_detector.reset()
            was_idle = True

            if fsm.muted:
                continue

            sig = sleep_detector.feed(data)
            if sig is SleepSignal.SLEEP and not fsm.sleeping:
                fsm.sleeping = True
            elif sig is SleepSignal.WAKE and fsm.sleeping:
                fsm.sleeping = False

            if fsm.sleeping:
                continue

            audio = np.frombuffer(data, dtype=np.int16)
            prob  = oww.predict(audio).get(wakeword_key, 0.0)
            if prob >= oww_threshold:
                logger.info("[auricle] wakeword detected (p=%.2f) → AWAITING_UTTERANCE", prob)
                oww.reset()
                stt_provider.reset()
                _play_asset_sync(ASSET_WAKEUP)
                fsm.transition(State.AWAITING_UTTERANCE)
                active_listen_deadline = None  # armed on first chunk in new state

        # ── SPEAKING: run OWW in parallel for barge-in ────────────────────
        elif state == State.SPEAKING:
            was_idle = False
            audio = np.frombuffer(data, dtype=np.int16)
            prob  = oww.predict(audio).get(wakeword_key, 0.0)
            if prob >= oww_threshold:
                logger.info("[auricle] barge-in detected (p=%.2f)", prob)
                oww.reset()
                stt_provider.reset()
                loop.call_soon_threadsafe(egress.abort)
                # Play ping async so ingress starts listening immediately
                loop.call_soon_threadsafe(lambda: asyncio.ensure_future(egress.play_file(ASSET_WAKEUP)))
                fsm.transition(State.AWAITING_UTTERANCE)
                active_listen_deadline = None

        # ── AWAITING_UTTERANCE / UTTERANCE: STT ───────────────────────────
        elif state in (State.AWAITING_UTTERANCE, State.UTTERANCE):
            was_idle = False
            # Arm deadline on first chunk after entering AWAITING_UTTERANCE
            if state == State.AWAITING_UTTERANCE and active_listen_deadline is None:
                active_listen_deadline = time.monotonic() + active_listen_duration
                logger.info("[auricle] active-listen window armed (%.1fs)", active_listen_duration)

            # Check active-listen expiry
            if state == State.AWAITING_UTTERANCE and time.monotonic() >= active_listen_deadline:
                logger.info("[auricle] active-listen expired → IDLE")
                oww.reset()
                stt_provider.reset()
                _play_asset_sync(ASSET_TOSLEEP)
                fsm.transition(State.IDLE)
                active_listen_deadline = None
                continue

            if audio_buffer.tts_active:
                final, partial = None, None
            else:
                final, partial = stt_provider.feed(data)

            if partial and state == State.AWAITING_UTTERANCE:
                logger.info("[auricle] speech detected → UTTERANCE")
                active_listen_deadline = None  # speech started — cancel timer
                fsm.transition(State.UTTERANCE)

            if final:
                logger.info("[auricle] transcript: %r", final)
                active_listen_deadline = None
                oww.reset()
                stt_provider.reset()
                if _handle_transcript(final, fsm, loop, dispatch_fn):
                    consecutive_misinputs += 1
                    if consecutive_misinputs >= MISINPUT_MAX_CONSECUTIVE:
                        logger.info("[auricle] misinput limit reached → IDLE")
                        _play_asset_sync(ASSET_TOSLEEP)
                        fsm.transition(State.IDLE)
                        consecutive_misinputs = 0
                    else:
                        logger.info("[auricle] misinput %d/%d → AWAITING_UTTERANCE",
                                    consecutive_misinputs, MISINPUT_MAX_CONSECUTIVE)
                        _play_asset_sync(ASSET_CONFUSED)
                        fsm.transition(State.AWAITING_UTTERANCE)
                else:
                    consecutive_misinputs = 0

        # ── DISPATCHED: agent is running, nothing to do here ──────────────
        elif state in (State.DISPATCHED, State.BOOTING, State.FATAL):
            was_idle = False


def _handle_transcript(
    text: str,
    fsm: FSM,
    loop: asyncio.AbstractEventLoop,
    dispatch_fn: Callable[[str], Coroutine],
) -> bool:
    """Handle a finalized transcript. Returns True if it was a misinput (caller handles FSM/sound)."""
    lower = text.lower().strip()

    if lower in MISINPUT_PHRASES:
        logger.info("[auricle] misinput detected: %r", text)
        return True

    if lower in CLEAR_COMMANDS:
        logger.info("[auricle] command: clear")
        fsm.transition(State.IDLE)
        asyncio.run_coroutine_threadsafe(dispatch_fn(_CMD_CLEAR), loop)
        return False

    if lower in STOP_COMMANDS:
        logger.info("[auricle] command: stop")
        fsm.transition(State.IDLE)
        asyncio.run_coroutine_threadsafe(dispatch_fn(_CMD_STOP), loop)
        return False

    fsm.transition(State.DISPATCHED)
    asyncio.run_coroutine_threadsafe(dispatch_fn(text), loop)
    return False
