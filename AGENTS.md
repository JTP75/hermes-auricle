# Hermes Auricle (hermes-auricle) Plugin Agent Reference

Hermes Auricle is a **platform plugin** for NousResearch Hermes Agent. It
connects hermes-agent to a running `auricle-engine` instance over WebSocket,
providing a voice interface. All audio pipeline logic (wakeword, STT, TTS,
FSM) lives in the engine; this plugin contains only the thin connector.

## Documentation Index

| Document | What it covers |
|----------|---------------|
| [`README.md`](README.md) | Installation, configuration, project layout |
| [`doctor.py`](doctor.py) | Connector diagnostic — checks websockets dep and engine reachability |
| `auricle-engine/docs/protocol.md` | WebSocket protocol spec (engine repo) |

## Required behavior for agents 

These rules apply to every task in this project unless explicitly overridden.
Bias: caution over speed on non-trivial work. Use judgment on trivial tasks.

### Rule 1 — Think Before Coding

State assumptions explicitly. If uncertain, ask rather than guess.
Present multiple interpretations when ambiguity exists.
Push back when a simpler approach exists.
Stop when confused. Name what's unclear.

### Rule 2 — Simplicity First

Minimum code that solves the problem. Nothing speculative.
No features beyond what was asked. No abstractions for single-use code.
Test: would a senior engineer say this is overcomplicated? If yes, simplify.

### Rule 3 — Surgical Changes

Touch only what you must. Clean up only your own mess.
Don't "improve" adjacent code, comments, or formatting.
Don't refactor what isn't broken. Match existing style.

### Rule 4 — Goal-Driven Execution

Define success criteria. Loop until verified.
Don't follow steps. Define success and iterate.
Strong success criteria let you loop independently.

### Rule 5 — Use the model only for judgment calls

Use me for: classification, drafting, summarization, extraction.
Do NOT use me for: routing, retries, deterministic transforms.
If code can answer, code answers.

### Rule 6 — IF YOU ARE CO-PILOT, IGNORE THIS RULE Token budgets are not advisory

Per-task: 4,000 tokens. Per-session: 30,000 tokens.
If approaching budget, summarize and start fresh.
Surface the breach. Do not silently overrun.

### Rule 7 — Surface conflicts, don't average them

If two patterns contradict, pick one (more recent / more tested).
Explain why. Flag the other for cleanup.
Don't blend conflicting patterns.

### Rule 8 — Read before you write

Before adding code, read exports, immediate callers, shared utilities.
"Looks orthogonal" is dangerous. If unsure why code is structured a way, ask.

### Rule 9 — Tests verify intent, not just behavior

Tests must encode WHY behavior matters, not just WHAT it does.
A test that can't fail when business logic changes is wrong.

### Rule 10 — Checkpoint after every significant step

Summarize what was done, what's verified, what's left.
Don't continue from a state you can't describe back.
If you lose track, stop and restate.

### Rule 11 — Match the codebase's conventions, even if you disagree

Conformance > taste inside the codebase.
If you genuinely think a convention is harmful, surface it. Don't fork silently.

### Rule 12 — Fail loud

"Completed" is wrong if anything was skipped silently.
"Tests pass" is wrong if any were skipped.
Default to surfacing uncertainty, not hiding it.

## Codebase-specific rules

### Rule 1 — Constants live in `consts.py`

Never hardcode numeric, string, or tuning values directly in module files.
All constants belong in `consts.py`. Import from there.
This applies to thresholds, timeouts, paths, defaults, and magic strings — anything that could need tuning without touching logic.

### Rule 2 — Do not auto-approve commits

Do not stage, commit, or push without being explicitly asked to do so.
When asked to commit, propose the message and wait for confirmation before running `git commit`.

### Rule 3 — Commit messages ≤ 100 characters

Subject line must be 100 characters or fewer.
Use the conventional format: `type: short description` (e.g. `fix: guard against empty transcript`).
No period at the end. No body unless the user asks for one.

### Rule 4 — Update docs when you update code

New features additions must be documented with at least one line in `README.md`.
Patches and bug fixes should only be documented if they contradict what is in `README.md`.
The amount of documentation for an addition should reflect the scale code change.

### Rule 5 — Keep `doctor.py` in sync with the connector

When adding a new connector-side Python dependency or env var, add the
corresponding check to `doctor.py`. Audio/model/binary checks belong in the
engine's `doctor.py`, not here.

## Known Gotchas

### Gotcha 1 — New connector env vars must be declared in `plugin.yaml`

Every `AURICLE_*` env var added to `consts.py` needs a matching entry in
`plugin.yaml` under `optional_env`. Without it, hermes setup UI and
`hermes plugins info` silently omit the var.

Engine env vars (`AURICLE_MIC_DEVICE`, `AURICLE_STT_BACKEND`, etc.) are
**not** declared here — they are engine config and the engine reads them
independently.

### Gotcha 2 — Audio logic belongs in auricle-engine, not here

This connector has no audio pipeline. Do not add imports of `vosk`,
`openwakeword`, `edge_tts`, `sounddevice`, or similar audio deps. If you
find yourself writing audio logic, it goes in the engine repo.
