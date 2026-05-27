# Hermes Auricle (hermes-auricle) Plugin Agent Reference

Hermes Auricle is a **platform plugin** for NousResearch Hermes Agent. It 
gives a hermes agent a pure audio interface, including efficient wakeword 
detection, speech-to-text (STT), and text-to-speech (TTS) capabilities. 

## Documentation Index

| Document | What it covers |
|----------|---------------|
| [`README.md`](README.md) | Installation, configuration table (all env vars + defaults), voice commands, how-it-works prose, misinput filtering, message classification, project layout |
| [`docs/uml/fsm.md`](docs/uml/fsm.md) | Mermaid stateDiagram-v2 of the 7-state FSM with orthogonal `sleeping` / `muted` regions and all transitions labelled |
| [`docs/uml/class.md`](docs/uml/class.md) | Mermaid classDiagram of the full plugin architecture — namespaces (Core, Ingress, Egress, Support), relationships, and critical method signatures |
| [`docs/rev1/design-rev1.md`](docs/rev1/design-rev1.md) | Authoritative design document for the current revision; compiled from the feature-set and decision Q&A sessions |
| [`docs/rev1/feature-set-rev1.md`](docs/rev1/feature-set-rev1.md) | Enumerated feature set that drove the rev1 implementation |
| [`docs/rev1/plugin-system-research.md`](docs/rev1/plugin-system-research.md) | Notes on how the hermes platform plugin system works (entry points, registration, config wiring) — written during initial integration |
| [`docs/rev1/setup-log-rev1.md`](docs/rev1/setup-log-rev1.md) | Installation and integration log for rev1 on the Pi (dependency alignment, venv setup) |
| [`docs/rev0/blueprint-salvage.md`](docs/rev0/blueprint-salvage.md) | Usable patterns extracted from the original rev0 blueprints; stale pieces dropped; superseded by `design-rev1.md` |

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

## Known Gotchas

Integration-level surprises that aren't obvious from the code alone.

### Gotcha 1 — New env vars must be declared in `plugin.yaml`

Every env var introduced in `consts.py` must have a corresponding entry in `plugin.yaml` under `optional_env` (or `requires_env` if mandatory).

**Why:** Hermes reads `optional_env` from `plugin.yaml` to populate its setup UI and the known-keys set used for `.env` file sanitization. An undeclared var is invisible to `hermes setup` and won't be interactively configurable. It also won't appear in `hermes plugins info` output.

Note: hermes loads **all** vars from `~/.hermes/.env` unconditionally — missing `optional_env` does not prevent the var from reaching `os.getenv()`. The failure mode is silent: setup and status tooling silently omits the var, which makes misconfiguration hard to diagnose.

**How to apply:** For every new `ENV_*` constant added to `consts.py`, add a matching block to `plugin.yaml`:
```yaml
optional_env:
  - name: AURICLE_MY_NEW_VAR
    description: "What it controls and its default"
    prompt: "Short label for hermes setup"
    password: false
```
