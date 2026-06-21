#!/usr/bin/env python3
"""hermes-auricle connector doctor — checks that the connector can reach auricle-engine.

Run from anywhere:
    python /path/to/hermes-auricle/doctor.py

For audio pipeline diagnostics (STT, TTS, mic, speaker, models), run the
doctor in the auricle-engine repo instead.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

_PLUGIN_DIR = Path(__file__).parent
sys.path.insert(0, str(_PLUGIN_DIR))

from consts import DEFAULT_ENGINE_WS_URL, ENV_ENGINE_WS_URL

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

# ── B: Engine reachability ─────────────────────────────────────────────────

_hdr("B. Engine reachability")

engine_url = os.getenv(ENV_ENGINE_WS_URL, DEFAULT_ENGINE_WS_URL)
print(f"  Engine URL: {engine_url}")


async def _probe_engine(url: str) -> tuple[bool, str]:
    try:
        import websockets as ws_mod
        async with ws_mod.connect(url, open_timeout=5) as ws:
            raw = await asyncio.wait_for(ws.recv(), timeout=5)
            msg = json.loads(raw)
            if msg.get("t") == "ready":
                return True, f"client_id={msg.get('client_id')}"
            return False, f"unexpected message: {msg}"
    except Exception as exc:
        return False, str(exc)


try:
    ok, detail = asyncio.run(_probe_engine(engine_url))
    _check("auricle-engine reachable", ok, detail)
except Exception as exc:
    _check("auricle-engine reachable", False, str(exc))

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
