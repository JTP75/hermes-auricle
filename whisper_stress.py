#!/usr/bin/env python3
"""
whisper_stress.py — Live and synthetic stress-test for the whisper_worker shim.

Feed audio of increasing duration to the worker, using the same env-configured
Python, model, and mic device as the plugin. Detects crashes, hangs, and reports
transcription results per round.

Usage:
  # Live mic rounds (default: 5s, 15s, 30s, 60s)
  python whisper_stress.py

  # Custom durations
  python whisper_stress.py --durations 10 30 60 120

  # Synthetic (no mic) — generates sustained "voiced" audio to stress the VAD buffer
  python whisper_stress.py --synth --durations 30 60 120

  # Skip the worker reset between rounds
  python whisper_stress.py --no-reset

  # Hang timeout per chunk (seconds, default 10)
  python whisper_stress.py --timeout 20

Environment variables read (same as the plugin):
  AURICLE_WHISPER_PYTHON    path to the Python interpreter with torch/transformers
  AURICLE_WHISPER_MODEL_ID  HuggingFace model ID
  AURICLE_SD_INPUT_DEVICE   sounddevice input device index or name
"""

import argparse
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

# ── Mirrored constants (no package import needed) ────────────────────────────

AUDIO_CHUNK_BYTES    = 1280
SAMPLE_RATE          = 16000
DEFAULT_MODEL_ID     = "distil-whisper/distil-large-v3"
DEFAULT_DURATIONS    = [5, 15, 30, 60]
DEFAULT_TIMEOUT_S    = 10.0

WORKER_PATH = Path(__file__).parent / "whisper_worker.py"


# ── Worker I/O wrapper ────────────────────────────────────────────────────────

class WorkerIO:
    """
    Wraps a whisper_worker subprocess with timeout-safe stdout reads.

    readline(timeout) returns:
      bytes  — a response line (newline included)
      None   — timeout elapsed (worker alive but not responding)
      b""    — pipe closed (worker exited)
    """

    def __init__(self, proc: subprocess.Popen) -> None:
        self._proc = proc
        self._q: queue.Queue = queue.Queue()
        t = threading.Thread(target=self._drain_stdout, daemon=True)
        t.start()
        threading.Thread(target=self._drain_stderr, daemon=True).start()

    def _drain_stdout(self) -> None:
        try:
            for line in self._proc.stdout:
                self._q.put(line)
        finally:
            self._q.put(b"")  # sentinel: pipe closed

    def _drain_stderr(self) -> None:
        try:
            for raw in self._proc.stderr:
                msg = raw.decode("utf-8", errors="replace").rstrip()
                if msg:
                    print(f"  [worker stderr] {msg}", flush=True)
        except Exception:
            pass

    def readline(self, timeout: float = DEFAULT_TIMEOUT_S):
        try:
            return self._q.get(timeout=timeout)
        except queue.Empty:
            return None

    def send(self, data: bytes) -> None:
        self._proc.stdin.write(data)
        self._proc.stdin.flush()

    def alive(self) -> bool:
        return self._proc.poll() is None

    def exit_code(self) -> int | None:
        return self._proc.poll()

    def shutdown(self) -> None:
        if self.alive():
            try:
                self._proc.stdin.write(b"\x03")
                self._proc.stdin.flush()
            except OSError:
                pass
            try:
                self._proc.terminate()
                self._proc.wait(timeout=3)
            except Exception:
                pass


# ── Worker lifecycle ──────────────────────────────────────────────────────────

def spawn_worker(python: str, model_id: str) -> WorkerIO:
    creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    proc = subprocess.Popen(
        [python, str(WORKER_PATH), "--model-id", model_id],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=creationflags,
    )
    io = WorkerIO(proc)

    print("Loading model (this may take a while on first run)...", flush=True)
    # Model load can take several minutes on CPU; use a generous timeout.
    line = io.readline(timeout=300)
    if line is None:
        proc.kill()
        raise RuntimeError("Worker timed out during model load (300s).")
    if line == b"":
        code = io.exit_code()
        raise RuntimeError(f"Worker exited before READY (exit code {code}).")
    if line.strip() != b"READY":
        proc.kill()
        raise RuntimeError(f"Worker sent unexpected startup line: {line!r}")
    return io


# ── Per-chunk send/receive ────────────────────────────────────────────────────

class ChunkResult:
    __slots__ = ("resp", "latency_ms", "hang", "crash")

    def __init__(self, resp: str, latency_ms: float, hang: bool = False, crash: bool = False):
        self.resp       = resp
        self.latency_ms = latency_ms
        self.hang       = hang
        self.crash      = crash


def send_chunk(io: WorkerIO, pcm: bytes, timeout: float) -> ChunkResult:
    if not io.alive():
        return ChunkResult("", 0.0, crash=True)
    t0 = time.monotonic()
    io.send(b"\x01" + pcm)
    line = io.readline(timeout=timeout)
    elapsed = (time.monotonic() - t0) * 1000

    if line is None:
        return ChunkResult("", elapsed, hang=True)
    if line == b"":
        return ChunkResult("", elapsed, crash=True)
    return ChunkResult(line.decode("utf-8", errors="replace").strip(), elapsed)


def reset_worker(io: WorkerIO, timeout: float) -> bool:
    """Send reset command; return False if worker is unresponsive."""
    if not io.alive():
        return False
    io.send(b"\x02")
    line = io.readline(timeout=timeout)
    return line is not None and line != b""


# ── Round types ───────────────────────────────────────────────────────────────

def run_live_round(io: WorkerIO, duration_s: int, device, chunk_timeout: float) -> dict:
    import sounddevice as sd

    print(f"\n{'─'*60}", flush=True)
    print(f"  LIVE  {duration_s}s  |  device={device!r}", flush=True)
    print(f"{'─'*60}", flush=True)

    block_frames = AUDIO_CHUNK_BYTES // 2
    chunks_fed   = 0
    n_ok = n_onset = n_transcript = 0
    transcripts: list[str] = []
    failure = None

    try:
        with sd.RawInputStream(
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype="int16",
            blocksize=block_frames,
            device=device or None,
        ) as stream:
            deadline = time.monotonic() + duration_s
            while time.monotonic() < deadline:
                data, _ = stream.read(block_frames)
                cr = send_chunk(io, bytes(data), chunk_timeout)
                chunks_fed += 1

                if cr.crash:
                    failure = f"crash at chunk {chunks_fed} (exit code {io.exit_code()})"
                    print(f"  !! CRASH  chunk={chunks_fed}  exit={io.exit_code()}", flush=True)
                    break
                if cr.hang:
                    failure = f"hang at chunk {chunks_fed} (>{chunk_timeout}s no response)"
                    print(f"  !! HANG   chunk={chunks_fed}  timeout={chunk_timeout}s", flush=True)
                    break

                if cr.resp == "P":
                    n_onset += 1
                    print(f"  chunk {chunks_fed:5d}  P         {cr.latency_ms:6.1f}ms", flush=True)
                elif cr.resp.startswith("F:"):
                    n_transcript += 1
                    text = cr.resp[2:]
                    transcripts.append(text)
                    print(f"  chunk {chunks_fed:5d}  F: {text!r}  {cr.latency_ms:6.1f}ms", flush=True)
                else:
                    n_ok += 1

    except Exception as exc:
        failure = f"exception: {exc}"
        print(f"  !! EXCEPTION: {exc}", flush=True)

    return _round_summary("live", duration_s, chunks_fed, n_ok, n_onset, n_transcript,
                          transcripts, failure)


def run_synth_round(io: WorkerIO, duration_s: int, chunk_timeout: float) -> dict:
    """
    Feed the worker `duration_s` seconds of synthetic voiced audio without a mic.

    Generates bandlimited noise at moderate amplitude — webrtcvad (mode 2) classifies
    this as speech reliably, so voiced_frames accumulates without ever hitting the
    silence-frame threshold. Useful for isolating VAD buffer growth as an OOM cause.
    """
    import numpy as np

    print(f"\n{'─'*60}", flush=True)
    print(f"  SYNTH {duration_s}s  |  (no mic — sustained voiced audio)", flush=True)
    print(f"{'─'*60}", flush=True)

    rng = np.random.default_rng(42)
    n_chunks = int(duration_s * SAMPLE_RATE * 2 / AUDIO_CHUNK_BYTES)
    chunks_fed = 0
    n_ok = n_onset = n_transcript = 0
    transcripts: list[str] = []
    failure = None

    # Pre-generate one chunk of voiced-sounding noise.
    noise_int16 = rng.integers(-6000, 6000, size=AUDIO_CHUNK_BYTES // 2, dtype=np.int16)
    chunk_pcm   = noise_int16.tobytes()

    for i in range(n_chunks):
        cr = send_chunk(io, chunk_pcm, chunk_timeout)
        chunks_fed += 1

        if cr.crash:
            failure = f"crash at chunk {chunks_fed} (exit code {io.exit_code()})"
            print(f"  !! CRASH  chunk={chunks_fed}  exit={io.exit_code()}", flush=True)
            break
        if cr.hang:
            failure = f"hang at chunk {chunks_fed} (>{chunk_timeout}s no response)"
            print(f"  !! HANG   chunk={chunks_fed}  timeout={chunk_timeout}s", flush=True)
            break

        if cr.resp == "P":
            n_onset += 1
            print(f"  chunk {chunks_fed:5d}  P         {cr.latency_ms:6.1f}ms", flush=True)
        elif cr.resp.startswith("F:"):
            n_transcript += 1
            text = cr.resp[2:]
            transcripts.append(text)
            print(f"  chunk {chunks_fed:5d}  F: {text!r}  {cr.latency_ms:6.1f}ms", flush=True)
        else:
            n_ok += 1

    return _round_summary("synth", duration_s, chunks_fed, n_ok, n_onset, n_transcript,
                          transcripts, failure)


def _round_summary(mode, duration_s, chunks_fed, n_ok, n_onset, n_transcript,
                   transcripts, failure) -> dict:
    label = f"{mode}/{duration_s}s"
    est_voiced_bytes = chunks_fed * AUDIO_CHUNK_BYTES  # worst case if all voiced
    print(f"\n  Summary ({label}):", flush=True)
    print(f"    chunks fed:    {chunks_fed}", flush=True)
    print(f"    OK/onset/F:    {n_ok}/{n_onset}/{n_transcript}", flush=True)
    if transcripts:
        for t in transcripts:
            print(f"    transcript:    {t!r}", flush=True)
    if failure:
        print(f"    FAILED:        {failure}", flush=True)
    else:
        print(f"    result:        OK", flush=True)
    return {
        "label":      label,
        "chunks_fed": chunks_fed,
        "n_ok":       n_ok,
        "n_onset":    n_onset,
        "n_transcript": n_transcript,
        "transcripts": transcripts,
        "failure":    failure,
        "ok":         failure is None,
    }


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--durations", nargs="+", type=int, default=DEFAULT_DURATIONS,
                        metavar="S", help="Round durations in seconds")
    parser.add_argument("--synth", action="store_true",
                        help="Use synthetic audio instead of live mic")
    parser.add_argument("--no-reset", action="store_true",
                        help="Do not reset worker state between rounds")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_S,
                        metavar="S", help="Per-chunk response timeout in seconds")
    parser.add_argument("--list-devices", action="store_true",
                        help="Print available sounddevice input devices and exit")
    args = parser.parse_args()

    if args.list_devices:
        import sounddevice as sd
        print(sd.query_devices())
        return

    python   = os.environ.get("AURICLE_WHISPER_PYTHON", sys.executable)
    model_id = os.environ.get("AURICLE_WHISPER_MODEL_ID", DEFAULT_MODEL_ID)
    sd_device_raw = os.environ.get("AURICLE_SD_INPUT_DEVICE", "")
    # Convert to int if numeric (sounddevice device index), else pass as name or None.
    if sd_device_raw.strip().lstrip("-").isdigit():
        sd_device = int(sd_device_raw)
    else:
        sd_device = sd_device_raw or None

    print(f"python:    {python}")
    print(f"model:     {model_id}")
    print(f"device:    {sd_device!r}  ({'synth — no mic' if args.synth else 'live mic'})")
    print(f"durations: {args.durations}")
    print(f"timeout:   {args.timeout}s per chunk")
    print()

    io = spawn_worker(python, model_id)

    results = []
    try:
        for duration_s in args.durations:
            if args.synth:
                r = run_synth_round(io, duration_s, args.timeout)
            else:
                r = run_live_round(io, duration_s, sd_device, args.timeout)
            results.append(r)

            if r["failure"] and not io.alive():
                print("\nWorker is dead — stopping early.", flush=True)
                break

            if not args.no_reset and io.alive():
                ok = reset_worker(io, args.timeout)
                if not ok:
                    print("\nReset failed — worker unresponsive.", flush=True)
                    break
    finally:
        io.shutdown()

    # ── Final report ──────────────────────────────────────────────────────────
    print(f"\n{'═'*60}")
    print("  FINAL REPORT")
    print(f"{'═'*60}")
    any_failure = False
    for r in results:
        status = "FAIL" if r["failure"] else "OK  "
        detail = f"  [{r['failure']}]" if r["failure"] else ""
        print(f"  {status}  {r['label']:12s}  chunks={r['chunks_fed']:5d}  "
              f"F={r['n_transcript']}{detail}")
        if r["failure"]:
            any_failure = True
    print()

    sys.exit(1 if any_failure else 0)


if __name__ == "__main__":
    main()
