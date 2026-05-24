#!/usr/bin/env python3
"""
Generate audio assets for hermes-auricle using edge-tts.

Run once at setup time, or re-run to regenerate with a different voice:

    python scripts/generate_assets.py
    python scripts/generate_assets.py --voice en-US-AriaNeural
"""
import argparse
import asyncio
import subprocess
import sys
from pathlib import Path

ASSETS_DIR = Path(__file__).parent.parent / "assets"

ASSETS = {
    "ping.wav":    "yes",
    "bong.wav":    "okay",
    "ding.wav":    "hey",
    "cleared.wav": "session cleared",
    "error.wav":   "something went wrong",
}

DEFAULT_VOICE = "en-GB-LibbyNeural"


async def generate(voice: str) -> None:
    ASSETS_DIR.mkdir(exist_ok=True)
    tasks = []
    for filename, phrase in ASSETS.items():
        out_path = ASSETS_DIR / filename
        tasks.append(_render(voice, phrase, out_path))
    results = await asyncio.gather(*tasks, return_exceptions=True)
    ok = True
    for filename, result in zip(ASSETS.keys(), results):
        if isinstance(result, Exception):
            print(f"  FAILED {filename}: {result}", file=sys.stderr)
            ok = False
        else:
            print(f"  OK     {filename}")
    if not ok:
        sys.exit(1)


async def _render(voice: str, text: str, out_path: Path) -> None:
    proc = await asyncio.create_subprocess_exec(
        "edge-tts",
        "--voice", voice,
        "--text", text,
        "--write-media", str(out_path),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(stderr.decode().strip())


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate hermes-auricle audio assets")
    parser.add_argument("--voice", default=DEFAULT_VOICE, help="edge-tts voice name")
    args = parser.parse_args()

    print(f"Generating assets with voice: {args.voice}")
    asyncio.run(generate(args.voice))
    print("Done. Assets written to:", ASSETS_DIR)


if __name__ == "__main__":
    main()
