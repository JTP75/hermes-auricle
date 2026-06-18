#!/usr/bin/env python3
"""
f5_tts_test.py — end-to-end integration test for the F5-TTS worker.

Starts f5_worker.py exactly as the runtime does, synthesizes a short phrase,
and plays it back through the configured audio output.

Usage:
    ~/.hermes/.venv/bin/python f5_tts_test.py ["phrase to speak"]
    ~/.hermes/.venv/bin/python f5_tts_test.py --text "hello world"

Reads AURICLE_* env vars from the environment or ~/.hermes/.env.
Gateway does not need to be running.
"""

import argparse
import os
import struct
import subprocess
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from consts import (
    APLAY_BIN,
    DEFAULT_AUDIO_OUTPUT,
    DEFAULT_F5_MODEL,
    DEFAULT_F5_SPEED,
    DEFAULT_F5_STEPS,
    DEFAULT_SD_OUTPUT_DEVICE,
    DEFAULT_SPEAKER_DEVICE,
    ENV_AUDIO_OUTPUT,
    ENV_F5_MODEL,
    ENV_F5_PYTHON,
    ENV_F5_REF_TXT,
    ENV_F5_REF_WAV,
    ENV_F5_SPEED,
    ENV_F5_STEPS,
    ENV_SD_OUTPUT_DEVICE,
    ENV_SPEAKER_DEVICE,
    FFMPEG_BIN,
)

_DEFAULT_TEXT = "The F5 TTS worker is online and synthesizing audio correctly."
_DOT_ENV      = os.path.expanduser("~/.hermes/.env")
_WORKER_PATH  = os.path.join(os.path.dirname(os.path.abspath(__file__)), "f5_worker.py")


def _load_dotenv(path: str) -> None:
    try:
        with open(path) as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                key = key.strip()
                val = val.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = val
    except FileNotFoundError:
        pass


def _read_exact(fp, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = fp.read(n - len(buf))
        if not chunk:
            break
        buf += chunk
    return buf


def _forward_stderr(proc: subprocess.Popen) -> None:
    def _drain():
        for line in proc.stderr:
            sys.stderr.write("[f5_worker] " + line.decode("utf-8", errors="replace").rstrip() + "\n")
            sys.stderr.flush()
    threading.Thread(target=_drain, daemon=True).start()


def _play_aplay(wav_bytes: bytes, device: str) -> None:
    r_fd, w_fd = os.pipe()
    ffmpeg = subprocess.Popen(
        [FFMPEG_BIN, "-hide_banner", "-loglevel", "quiet",
         "-i", "pipe:0", "-f", "s16le", "-ar", "48000", "-ac", "2", "pipe:1"],
        stdin=subprocess.PIPE,
        stdout=w_fd,
    )
    os.close(w_fd)
    aplay = subprocess.Popen(
        [APLAY_BIN, "-D", device, "-r", "48000", "-c", "2", "-f", "S16_LE"],
        stdin=r_fd,
    )
    os.close(r_fd)
    ffmpeg.communicate(wav_bytes)  # concurrent stdin/stdout — no pipe deadlock
    aplay.wait()


def _play_sounddevice(wav_bytes: bytes, device_str: str) -> None:
    import numpy as np
    import sounddevice as sd

    result = subprocess.run(
        [FFMPEG_BIN, "-hide_banner", "-loglevel", "quiet",
         "-i", "pipe:0", "-f", "s16le", "-ar", "48000", "-ac", "1", "pipe:1"],
        input=wav_bytes,
        capture_output=True,
    )
    pcm = np.frombuffer(result.stdout, dtype=np.int16)
    device = int(device_str) if device_str.isdigit() else (device_str or None)
    sd.play(pcm.reshape(-1, 1), samplerate=48000, device=device)
    sd.wait()


def main() -> None:
    _load_dotenv(_DOT_ENV)

    parser = argparse.ArgumentParser(description="F5-TTS worker integration test")
    parser.add_argument("text", nargs="?", default=None,
                        help="Text to synthesize (positional)")
    parser.add_argument("--text", dest="text_flag", default=None,
                        help="Text to synthesize (flag form)")
    parser.add_argument("--worker", default=_WORKER_PATH,
                        help="Path to f5_worker.py")
    args = parser.parse_args()

    text = args.text_flag or args.text or _DEFAULT_TEXT

    # ── Config from env (same vars as the runtime) ────────────────────────────
    f5_python = os.getenv(ENV_F5_PYTHON, "")
    f5_model  = os.getenv(ENV_F5_MODEL,  DEFAULT_F5_MODEL)
    f5_steps  = int(os.getenv(ENV_F5_STEPS, str(DEFAULT_F5_STEPS)))
    f5_speed  = float(os.getenv(ENV_F5_SPEED, str(DEFAULT_F5_SPEED)))
    ref_wav   = os.path.expanduser(os.getenv(ENV_F5_REF_WAV, ""))
    ref_txt   = os.path.expanduser(os.getenv(ENV_F5_REF_TXT, ""))

    audio_backend = os.getenv(ENV_AUDIO_OUTPUT,    DEFAULT_AUDIO_OUTPUT).lower()
    speaker_dev   = os.getenv(ENV_SPEAKER_DEVICE,  DEFAULT_SPEAKER_DEVICE)
    sd_dev        = os.getenv(ENV_SD_OUTPUT_DEVICE, DEFAULT_SD_OUTPUT_DEVICE)

    if not f5_python:
        sys.exit(f"error: {ENV_F5_PYTHON} is not set — add it to ~/.hermes/.env or export it.")

    output_label = sd_dev if audio_backend == "sounddevice" else speaker_dev
    print(f"[f5_tts_test] worker  : {args.worker}")
    print(f"[f5_tts_test] model   : {f5_model}  steps={f5_steps}  speed={f5_speed}")
    print(f"[f5_tts_test] ref     : wav={ref_wav or '(bundled)'}  txt={ref_txt or '(bundled)'}")
    print(f"[f5_tts_test] output  : {audio_backend}  device={output_label or '(default)'}")
    print(f"[f5_tts_test] text    : {text!r}")
    print()

    # ── Start worker ─────────────────────────────────────────────────────────
    t0 = time.monotonic()
    proc = subprocess.Popen(
        [f5_python, args.worker,
         "--model",   f5_model,
         "--steps",   str(f5_steps),
         "--speed",   str(f5_speed),
         "--ref-wav", ref_wav,
         "--ref-txt", ref_txt],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    _forward_stderr(proc)
    print("[f5_tts_test] worker started — waiting for READY...")

    # ── Wait for READY handshake ──────────────────────────────────────────────
    ready_line = proc.stdout.readline()
    if not ready_line or ready_line.strip() != b"READY":
        proc.terminate()
        sys.exit(f"error: expected READY, got {ready_line!r}")

    load_time = time.monotonic() - t0
    print(f"[f5_tts_test] READY ({load_time:.1f}s)")

    # ── Send synth request ────────────────────────────────────────────────────
    encoded = text.encode("utf-8")
    proc.stdin.write(b"\x01" + struct.pack(">I", len(encoded)) + encoded)
    proc.stdin.flush()

    t1 = time.monotonic()
    print(f"[f5_tts_test] synthesizing ({len(encoded)} chars)...")

    # ── Read response ─────────────────────────────────────────────────────────
    status_byte = proc.stdout.read(1)
    if not status_byte:
        proc.terminate()
        sys.exit("error: worker closed stdout without responding")

    n_bytes = _read_exact(proc.stdout, 4)
    if len(n_bytes) < 4:
        proc.terminate()
        sys.exit("error: truncated length field in response")
    n = struct.unpack(">I", n_bytes)[0]
    payload = _read_exact(proc.stdout, n) if n > 0 else b""

    synth_time = time.monotonic() - t1

    if status_byte[0] == 0x02:
        proc.terminate()
        sys.exit(f"error: synthesis failed: {payload.decode('utf-8', errors='replace')}")
    if status_byte[0] == 0x00:
        print("[f5_tts_test] worker returned empty (blank text?)")
        proc.terminate()
        return
    if status_byte[0] != 0x01:
        proc.terminate()
        sys.exit(f"error: unexpected status byte {status_byte[0]:#04x}")

    print(f"[f5_tts_test] synthesis done ({synth_time:.1f}s,  {len(payload):,} bytes WAV)")

    # Shut down worker cleanly
    try:
        proc.stdin.write(b"\x03")
        proc.stdin.flush()
        proc.stdin.close()
    except Exception:
        pass
    proc.terminate()

    # ── Playback ──────────────────────────────────────────────────────────────
    print(f"[f5_tts_test] playing ({audio_backend})...")
    t2 = time.monotonic()

    if audio_backend == "sounddevice":
        _play_sounddevice(payload, sd_dev)
    else:
        _play_aplay(payload, speaker_dev)

    play_time = time.monotonic() - t2
    print(f"[f5_tts_test] done — load={load_time:.1f}s  synth={synth_time:.1f}s  play={play_time:.1f}s")


if __name__ == "__main__":
    main()
