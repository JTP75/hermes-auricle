# hermes-auricle Design Document (Revision 1)

Compiled from feature-set-rev1 and the Q&A decision sessions.
Authoritative reference for implementation.

---

## Overview

hermes-auricle is a hermes platform plugin providing a fully local, alexa-like
smart speaker interface on a Raspberry Pi connected to a Jabra Speak 510 USB
device. All audio is local — no cloud STT, no cloud wakeword. The user speaks,
hermes responds aloud.

---

## Plugin Architecture

- **Location:** `~/.hermes/plugins/hermes-auricle/`
- **Structure:** `plugin.yaml` + `adapter.py` with a `register(ctx)` entry point
- **Base class:** `BasePlatformAdapter` from `gateway/platforms/base.py`
- **Registration:** `ctx.register_platform(name="auricle", ...)` in `register(ctx)`
- **Session identity:** Single hardcoded `chat_id = "local"`, `chat_type = "dm"` — one persistent
  session key for the lifetime of the adapter, representing the physical device
- **Provider shape:** Thin `STTProvider` and `TTSProvider` abstract classes (ABCs). Vosk and
  edge-tts are the first implementations. The abstraction exists to allow future provider swaps
  without touching the adapter core. Wiring is via factory/DI at adapter init time.

---

## Configuration

Two-layer config:

### plugin.yaml
Declares plugin metadata and any env vars surfaced in `hermes config` UI.

### config.yaml (under `gateway.auricle.*`)
User-editable settings. The `apply_yaml_config_fn` hook bridges these keys into env vars
so the adapter reads env vars uniformly. Keys include:

| Key | Description | Default |
|-----|-------------|---------|
| `mic_device` | ALSA device string for arecord | `plughw:3,0` |
| `tts_voice` | edge-tts voice name | `en-GB-LibbyNeural` |
| `active_listen_duration` | seconds of no-wakeword listen after TTS ends | `5` |
| `session_resume` | whether to resume session history on hermes restart | `true` |
| `mute` | disable mic wakeword path entirely | `false` |

Wakeword model path and string are **not** exposed in config (deferred — see Wakeword section).

---

## Hardware Assumptions

- **Mic/speaker device:** Jabra Speak 510 USB — card 3, device 0 (`plughw:3,0`)
- **Audio subsystem:** PipeWire — egress via `pw-play`, ingress via `arecord`
- **Echo cancellation:** Handled by the Jabra hardware and PipeWire — no software AEC needed

---

## Ingress Pipeline (mic → hermes)

### Audio capture
One `arecord` subprocess shared across both the wakeword engine and the STT engine:

```
arecord -D plughw:3,0 -f S16_LE -c 1 -r 16000 -t raw -q
```

Output is tee'd in Python — the same byte stream feeds both openWakeWord and vosk in parallel.
Buffer size: **1280 bytes (40ms chunks at 16kHz)** — openWakeWord's hard requirement. Vosk handles
this chunk size without issue.

### Wakeword detection
- **Engine:** openWakeWord (neural detector, fully offline, PipeWire-compatible via raw PCM)
- **Pattern:** Pure openWakeWord — no vosk transcript confirmation stage. The neural detector
  alone is the trigger. No two-stage DTW+vosk pattern.
- **Configurability:** Deferred. The wakeword model is fixed for now. Do not expose the wakeword
  string or model path in any config surface, documentation, or code comments.
- **On detection:** Play `ping.wav` immediately, then enter utterance capture mode.
- **False positives:** Accepted as-is for MVP. The ping sound provides implicit feedback;
  user says "stop" if needed. No cooldown, no intent confirmation.

### Utterance capture and end detection
- vosk `KaldiRecognizer` at 16kHz processes the same PCM stream
- End detection: `AcceptWaveform()` returning `True` (vosk final result) = utterance complete
- No trailing silence window, no energy VAD — vosk's internal segmentation is sufficient

### Command recognition
Commands are matched against the vosk final transcript before dispatch to the agent.
Matching is **exact word/phrase match only** — no fuzzy matching, no edit distance, no
confidence gating. Vosk is accurate enough that misrecognitions simply pass through to the
agent as text.

Recognized commands:

| Spoken phrase | Action |
|--------------|--------|
| "clear" / "reset" / synonyms (said in isolation) | Clear session, play `cleared.wav` verbal confirmation |
| wakeword + "stop" | Cancel in-flight agent run (if any), stop TTS (if playing), return to wakeword-only mode — bypasses 5s active-listen window |

### Active-listen window
After TTS playback ends, the mic enters an active-listen state for `active_listen_duration`
seconds (default 5s) — no wakeword required during this window.

- Timer resets on each new exchange (starts fresh when each TTS response ends)
- Also applies to the "wakeword fired, user hasn't spoken yet" case
- Timer cancels immediately when vosk detects any word
- On expiry: play `bong.wav`, return to wakeword-only mode

---

## Egress Pipeline (hermes → speaker)

### TTS synthesis and playback
```
edge-tts --voice en-GB-LibbyNeural -t "{text}" | pw-play -
```

Voice is configurable in `config.yaml` (default `en-GB-LibbyNeural`).

### Streaming TTS (sentence-by-sentence)
The LLM response stream is segmented into sentences before TTS synthesis. Segmentation
uses regex sentence boundary detection — split on `.`, `!`, or `?` followed by whitespace
or end-of-string. Each sentence is synthesized and played as soon as it arrives from the
stream; the next sentence queues behind it.

Tool call activity is never sent to TTS — only final prose reaches the speaker.

### Barge-in
If the wakeword fires during TTS playback:
1. Kill the current `pw-play` subprocess immediately
2. Drop any queued TTS sentences
3. Play `ping.wav`
4. Cancel the agent run if still in progress
5. Enter utterance capture mode

### BasePlatformAdapter integration
- `send(chat_id, content)` — runs the TTS pipeline, returns `SendResult(success=True)` on completion
- `prepare_tts_text(text)` — strips markdown (inherited default is sufficient, may extend to strip URLs)
- `play_tts(chat_id, audio_path)` — overridden for invisible playback (no file attachment shown to user)

---

## System Prompt

Hardcoded in the adapter. Short, voice-specific behavioral guidance injected into every session.
Directs the agent to: use no markdown, no code fences, no URLs, keep responses concise and
conversational, and never emit tool call narration. Exact text to be written at implementation time.

---

## Audio Assets

Pre-generated `.wav` files committed to the repo under `assets/`:

| File | Trigger |
|------|---------|
| `ping.wav` | Wakeword detected — "I'm listening" signal |
| `bong.wav` | Active-listen window expired — "going back to sleep" signal |
| `ding.wav` | Proactive / unsolicited message arriving |
| `cleared.wav` | Session cleared verbal confirmation |
| `error.wav` | Pre-recorded verbal error message (edge-tts down, arecord crash, agent hang) |

Generated with edge-tts at project setup time. Format: WAV (pw-play compatible).
Assets are small and static — committed to the repo, not generated at runtime.

---

## Proactive / Unsolicited Messages

Messages arriving from cron jobs, notifications, or async completions:

1. Play `ding.wav`
2. 1s pause
3. Stream message through TTS pipeline (same sentence-by-sentence path as normal responses)

---

## Failure Modes

Pre-recorded `error.wav` plays on:
- edge-tts subprocess failure (e.g., network down if voice requires it, binary missing)
- `arecord` / openWakeWord subprocess crash
- Hermes agent hang or unhandled error response

### Boot / connect() failure isolation
- `connect()` hard-fails (returns `False`) if the Jabra is absent, vosk model is corrupt,
  or openWakeWord fails to initialize
- Failure calls `_set_fatal_error(...)` — adapter enters fatal state, visible in
  `hermes gateway status`
- **Critically:** this does not crash the hermes agent process — other adapters and the
  gateway continue running
- A background retry task fires every **30 seconds** and re-attempts `connect()`, so the
  adapter recovers automatically if the Jabra is plugged in after hermes starts

---

## Privacy / Mute

- Mic is always-on by default
- Mute is toggled via slash command in the TUI, `config.yaml`, or env var — not via voice
- When muted, the wakeword path is gated off entirely (arecord subprocess still runs but
  openWakeWord inference is skipped — or subprocess is paused, TBD at implementation)

---

## Approvals

MVP: hardcoded allowlist in plugin config. No voice approval flow.
Post-MVP: verbal yes / always / no flow (deferred).

---

## Session History

Configurable in `config.yaml` under `session_resume` (default: `true`).
- `true` — on adapter connect, resume the existing session for `chat_id = "local"` if one exists
- `false` — always start fresh on connect (equivalent to `/new` at boot)

---

## Logging

- Output via standard Python `logging` module → hermes gateway log
- No separate auricle log file
- Verbosity: minimal. Log the following and nothing more:
  - Wakeword detection events
  - Final vosk transcript dispatched to agent
  - Final TTS text sent to speaker
  - State transitions (idle → listening → speaking → idle)
  - All faults and errors (subprocess crashes, connect failures, TTS failures)

---

## Deferred / Out of Scope (Rev 1)

- Wakeword configurability (model path, wakeword string, custom model training)
- Voice approval flow (post-MVP)
- Vosk fuzzy/autocorrect matching
- Provider config surface beyond voice name (STT model, etc.)
- Mute implementation detail (pause subprocess vs. skip inference)
- Exact system prompt text
- Utterance end detection tuning (trailing silence, VAD) — revisit if vosk-only proves insufficient
