import asyncio
import logging
import subprocess
import threading
import time
from typing import Callable, Coroutine

import numpy as np

from .consts import (
    AUDIO_CHUNK_BYTES,
    ASSET_BONG,
    ASSET_PING,
    CLEAR_COMMANDS,
    STOP_COMMANDS,
    PW_PLAY_BIN,
    PW_PLAY_TARGET,
    _CMD_CLEAR,
    _CMD_STOP,
)
from .fsm import FSM, State

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
    fsm: FSM,
    loop: asyncio.AbstractEventLoop,
    dispatch_fn: Callable[[str], Coroutine],
    stop_event: threading.Event,
    barge_in_event: asyncio.Event,
    active_listen_duration: float,
    oww_threshold: float,
) -> None:
    """
    Synchronous ingress thread.

    Reads 1280-byte PCM chunks from arecord, runs OWW for wakeword/barge-in
    detection, and feeds vosk for STT. Dispatches final transcripts and
    internal commands to the event loop via asyncio.run_coroutine_threadsafe.
    """
    active_listen_deadline: float | None = None
    stdout = arecord_proc.stdout

    while not stop_event.is_set():
        data = stdout.read(AUDIO_CHUNK_BYTES)
        if not data:
            logger.error("[auricle] arecord closed unexpectedly — ingress exiting")
            break

        state = fsm.get()

        # ── IDLE: only wakeword detection ─────────────────────────────────
        if state == State.IDLE:
            if fsm.muted:
                continue
            audio = np.frombuffer(data, dtype=np.int16)
            prob  = oww.predict(audio).get(wakeword_key, 0.0)
            if prob >= oww_threshold:
                logger.info("[auricle] wakeword detected (p=%.2f) → AWAITING_UTTERANCE", prob)
                oww.reset()
                stt_provider.reset()
                _play_asset_sync(ASSET_PING)
                fsm.transition(State.AWAITING_UTTERANCE)
                active_listen_deadline = None  # armed on first chunk in new state

        # ── SPEAKING: run OWW in parallel for barge-in ────────────────────
        elif state == State.SPEAKING:
            audio = np.frombuffer(data, dtype=np.int16)
            prob  = oww.predict(audio).get(wakeword_key, 0.0)
            if prob >= oww_threshold:
                logger.info("[auricle] barge-in detected (p=%.2f)", prob)
                oww.reset()
                stt_provider.reset()
                egress.kill_active()
                asyncio.run_coroutine_threadsafe(_set_event(barge_in_event), loop).result(timeout=1.0)
                _play_asset_sync(ASSET_PING)
                fsm.transition(State.AWAITING_UTTERANCE)
                active_listen_deadline = None

        # ── AWAITING_UTTERANCE / UTTERANCE: STT ───────────────────────────
        elif state in (State.AWAITING_UTTERANCE, State.UTTERANCE):
            # Arm deadline on first chunk after entering AWAITING_UTTERANCE
            if state == State.AWAITING_UTTERANCE and active_listen_deadline is None:
                active_listen_deadline = time.monotonic() + active_listen_duration

            # Check active-listen expiry
            if state == State.AWAITING_UTTERANCE and time.monotonic() >= active_listen_deadline:
                logger.info("[auricle] active-listen expired → IDLE")
                oww.reset()
                stt_provider.reset()
                _play_asset_sync(ASSET_BONG)
                fsm.transition(State.IDLE)
                active_listen_deadline = None
                continue

            final, partial = stt_provider.feed(data)

            if partial and state == State.AWAITING_UTTERANCE:
                active_listen_deadline = None  # speech started — cancel timer
                fsm.transition(State.UTTERANCE)

            if final:
                logger.info("[auricle] transcript: %r", final)
                active_listen_deadline = None
                oww.reset()
                stt_provider.reset()
                _handle_transcript(final, fsm, loop, dispatch_fn)

        # ── DISPATCHED: agent is running, nothing to do here ──────────────
        elif state in (State.DISPATCHED, State.BOOTING, State.FATAL):
            pass


async def _set_event(event: asyncio.Event) -> None:
    event.set()


def _handle_transcript(
    text: str,
    fsm: FSM,
    loop: asyncio.AbstractEventLoop,
    dispatch_fn: Callable[[str], Coroutine],
) -> None:
    lower = text.lower().strip()

    if lower in CLEAR_COMMANDS:
        logger.info("[auricle] command: clear")
        fsm.transition(State.IDLE)
        asyncio.run_coroutine_threadsafe(dispatch_fn(_CMD_CLEAR), loop)
        return

    if lower in STOP_COMMANDS:
        logger.info("[auricle] command: stop")
        fsm.transition(State.IDLE)
        asyncio.run_coroutine_threadsafe(dispatch_fn(_CMD_STOP), loop)
        return

    fsm.transition(State.DISPATCHED)
    asyncio.run_coroutine_threadsafe(dispatch_fn(text), loop)
