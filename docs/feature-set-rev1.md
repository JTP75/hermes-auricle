# Feature Set (Revision 1)

## Features

### Plugin architecture

- Hermes platform plugin at ~/.hermes/plugins/hermes-auricle/
- Subclass BasePlatformAdapter; register(ctx) entry point
- Single hardcoded chat_id = "local", chat_type = "dm"
- Persistent session across all utterances (one session key)
- Designed for future provider swappability (vosk + edge-tts are the first impls)

### Configuration

- plugin.yaml — declares metadata and required env vars (the hermes-idiomatic surface)
- Central config.yaml — user-editable settings under gateway.auricle.* (mic device, wakeword, TTS voice, active-window duration, approval allowlist, mute-state default, etc.)
- apply_yaml_config_fn hook bridges config.yaml keys → env vars, so users edit one file and the adapter reads env vars uniformly
- TTS voice exposed in config from day one

### Ingress (mic → hermes)

- Continuous vosk STT via arecord -D plughw:3,0 subprocess
- Wakeword-gated capture
- "Ping" sound plays the moment wakeword is detected

### Egress (hermes → speaker)

- Edge-tts piped to pw-play
- Streaming TTS: sentence-by-sentence as the LLM produces text
- Mic listens concurrently with TTS playback (PipeWire echo cancellation)

### Active-listen window (the "5s window")

- After TTS ends, mic enters a 5s active-listen state — no wakeword required
- Timer resets on each new exchange (starts fresh when each TTS response ends)
- Same 5s window also covers the "wakeword fired but user hasn't started speaking yet" case
- Timer cancels as soon as vosk detects any word
- On 5s expiry: play a "bong" sound and return to wakeword-only mode

### Barge-in

- Wakeword during TTS cuts off playback and starts listening
- Same wakeword path applies whether idle or speaking

### Commands

- clear / reset (and synonyms, said in isolation) — clears the session, plays verbal "session cleared"
- {wakeword}, stop — two contexts, same end state:
- Pre-TTS (agent still running): cancels the agent run, plays ping
- During TTS: stops TTS immediately and cancels the run if still in progress
- In both cases: ends the conversation — next interaction requires the wakeword (bypasses the 5s window). Any queued TTS sentences are dropped.

### Agent behavior shaping

- TTS never reads tool call activity — only final prose reaches the speaker
- Plugin injects a voice-specific system prompt (no markdown, no code fences, no URLs, concise)

### Proactive / unsolicited messages (cron, notifications, async completions)

- "Ding" sound → 1s pause → stream the message
- Same TTS path as normal responses

### Failure modes

- Pre-recorded verbal error message played on:
- edge-tts failure (e.g., offline)
- Vosk / arecord subprocess crash
- Hermes agent hang or error

### Privacy / mute

- Mic is always-on by default
- Mute toggle exists, but not via voice — via slash command in the TUI, config value, or env var
- When muted: the wakeword path is gated off entirely

### Approvals

- MVP: hardcoded allowlist in plugin config (no voice prompts)
- Post-MVP: verbal yes / always / no flow

### Logging

- Structured logging throughout: vosk transcript, final TTS text, wakeword detections, command matches, state transitions, errors

---

## Deferred Questions (from Revision 1)

1. Provider extensibility shape — thin STTProvider / TTSProvider classes vs module-level functions
2. Utterance end detection mechanism (likely vosk final result + trailing silence)
3. Wakeword implementation — in-transcript keyword match vs dedicated engine (openWakeWord / Porcupine)
4. Vosk autocorrect / fuzzy matching (Trie, confidence levels) to reduce misrecognition
5. Pre-recorded audio assets — ping, bong, "session cleared", verbal error message — generation, storage location, format
6. Actual wakeword string (leaning "hermes")
7. System prompt contents — exact text of the voice behavioral guidance
8. Wakeword false-positive handling (user says "hermes" conversationally to themselves)
9. Boot / lifecycle edges — vosk model load timing in connect(), behavior when Jabra is unplugged at boot, cleanup when hermes restarts mid-conversation