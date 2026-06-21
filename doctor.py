#!/usr/bin/env python3
"""hermes-auricle connector doctor — checks connector configuration.

Run from anywhere:
    python /path/to/hermes-auricle/doctor.py

For audio pipeline diagnostics (STT, TTS, mic, speaker, models), run the
doctor in the auricle-engine repo instead.
"""
from __future__ import annotations

import os
import socket
import sys
from pathlib import Path

_PLUGIN_DIR = Path(__file__).parent
sys.path.insert(0, str(_PLUGIN_DIR))

from consts import DEFAULT_CONNECTOR_HOST, DEFAULT_CONNECTOR_PORT, ENV_CONNECTOR_HOST, ENV_CONNECTOR_PORT

# ── ANSI output ────────────────────────────────────────────────────────────

_G, _Y, _R, _X = "\033[32m", "\033[33m", "\033[31m", "\033[0m"

def _ok(msg: str)   -> None: print(f"  {_G}✓{_X}  {msg}")
def _warn(msg: str) -> None: print(f"  {_Y}⚠{_X}  {msg}")
def _fail(msg: str) -> None: print(f"  {_R}✗{_X}  {msg}")
def _hdr(msg: str)  -> None: print(f"\n{msg}")

_failures: list[str] = []

def _check(label: str, ok: bool, detail: str = "") -> None:
    if ok:
        _ok(label + (f" — {detail}" if detail else ""))
    else:
        _fail(label + (f" — {detail}" if detail else ""))
        _failures.append(label)


# ── A: Python dependencies ─────────────────────────────────────────────────

_hdr("A. Python dependencies")

try:
    import websockets
    _ok(f"websockets ({websockets.__version__})")
except ImportError:
    _fail("websockets not found — pip install websockets")
    _failures.append("websockets")

# ── B: Port availability ───────────────────────────────────────────────────

_hdr("B. Connector server port")

host = os.getenv(ENV_CONNECTOR_HOST, DEFAULT_CONNECTOR_HOST)
port = int(os.getenv(ENV_CONNECTOR_PORT, str(DEFAULT_CONNECTOR_PORT)))
print(f"  Listening address: ws://{host}:{port}")

with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind((host if host != "0.0.0.0" else "127.0.0.1", port))
        _ok(f"Port {port} is available")
    except OSError as exc:
        _fail(f"Port {port} is already in use — {exc}")
        _failures.append(f"port {port}")
        _warn("If hermes gateway is already running this is expected")

# ── C: Classifier sanity ───────────────────────────────────────────────────

_hdr("C. SystemMessageClassifier")

try:
    from classifier import SystemMessageClassifier, Classification
    clf = SystemMessageClassifier()
    result = clf.classify("Hello, how are you?")
    _check(
        "SystemMessageClassifier instantiates and classifies",
        result == Classification.AGENT_RESPONSE,
        f"verdict={result.name}",
    )
except Exception as exc:
    _check("SystemMessageClassifier", False, str(exc))

# ── Summary ────────────────────────────────────────────────────────────────

print()
if _failures:
    print(f"{_R}doctor: {len(_failures)} problem(s) found{_X}")
    for f in _failures:
        print(f"  {_R}✗{_X}  {f}")
    sys.exit(1)
else:
    print(f"{_G}doctor: all checks passed{_X}")
