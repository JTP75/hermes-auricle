# hermes-auricle

A local voice platform plugin for [hermes](https://github.com/nousresearch/hermes-agent). Turns a Raspberry Pi + Jabra Speak 510 USB into an Alexa-style smart speaker. STT is fully offline (vosk); TTS uses Edge-TTS and requires an internet connection.

- **Wakeword + STT**: [openWakeWord](https://github.com/dscripka/openWakeWord) (neural detector) + pluggable STT backend: [vosk](https://alphacephei.com/vosk/) (offline, CPU) or [distil-whisper](https://huggingface.co/distil-whisper/distil-large-v3) (GPU-accelerated via HuggingFace transformers)
- **TTS + playback**: [edge-tts](https://github.com/rany2/edge-tts) piped to `aplay` via `ffmpeg`
- **Target hardware**: Raspberry Pi + Jabra Speak 510 USB (mic + speaker in one device, hardware echo cancellation)

---

## Requirements

**Python packages**
```bash
# vosk backend (default, CPU-only)
pip install vosk openwakeword numpy edge-tts

# whisper backend (requires CUDA GPU)
pip install transformers torch accelerate webrtcvad openwakeword numpy edge-tts
```

**System packages** (Debian/Ubuntu)
```
sudo apt install alsa-utils ffmpeg
```

**Models** -- place in `models/` or set env vars / `config.yaml` to point elsewhere:

| File | What it is |
|------|-----------|
| `models/vosk-model/` | Vosk offline STT model directory |
| `models/wakeword.onnx` | openWakeWord custom wakeword model (ONNX) |
| `models/melspectrogram.onnx` | OWW preprocessor (from the OWW repo) |
| `models/embedding_model.onnx` | OWW preprocessor (from the OWW repo) |

---

## Installation

1. Clone or copy this directory to `~/.hermes/plugins/hermes-auricle/`

2. Place models in `models/` (see above).

3. Enable the plugin in `~/.hermes/config.yaml`:
   ```yaml
   plugins:
     enabled:
       - hermes-auricle
   ```

4. Start the hermes gateway:
   ```bash
   hermes gateway start
   ```

---

## Configuration

All settings live under a top-level `auricle:` key in `~/.hermes/config.yaml`. Env vars take precedence.

| config.yaml key | Env var | Default | Description |
|-----------------|---------|---------|-------------|
| `mic_device` | `AURICLE_MIC_DEVICE` | `plughw:3,0` | ALSA device for `arecord` |
| `tts_voice` | `AURICLE_TTS_VOICE` | `en-GB-LibbyNeural` | edge-tts voice name |
| `active_listen_duration` | `AURICLE_ACTIVE_LISTEN_DURATION` | `5` | Seconds of no-wakeword listen after TTS ends |
| `session_resume` | `AURICLE_SESSION_RESUME` | `true` | Resume session history on hermes restart |
| `mute` | `AURICLE_MUTE` | `false` | Disable wakeword detection on startup |
| `stt_backend` | `AURICLE_STT_BACKEND` | `vosk` | STT backend: `vosk` or `whisper` |
| `vosk_model_path` | `AURICLE_VOSK_MODEL_PATH` | `models/vosk-model` | Path to vosk model directory (vosk backend only) |
| `whisper_model_id` | `AURICLE_WHISPER_MODEL_ID` | `distil-whisper/distil-large-v3` | HuggingFace model ID (whisper backend only) |
| `oww_wakeword_model_path` | `AURICLE_OWW_WAKEWORD_MODEL_PATH` | `models/wakeword.onnx` | Path to OWW wakeword `.onnx` model |
| `oww_melspec_model_path` | `AURICLE_OWW_MELSPEC_MODEL_PATH` | `models/melspectrogram.onnx` | Path to OWW melspec preprocessor |
| `oww_embedding_model_path` | `AURICLE_OWW_EMBEDDING_MODEL_PATH` | `models/embedding_model.onnx` | Path to OWW embedding preprocessor |
| `sleep_timeout` | `AURICLE_SLEEP_TIMEOUT` | `60` | Seconds of IDLE silence before auto-sleep |
| `sleep_wake_sensitivity` | `AURICLE_SLEEP_WAKE_SENSITIVITY` | `3.0` | Flux multiplier over baseline to wake; lower = more sensitive |
| `sleep_flux_threshold` | `AURICLE_SLEEP_FLUX_THRESHOLD` | `0.02` | Normalized flux EMA cutoff for "quiet" classification |
| `session_auto_clear` | `AURICLE_SESSION_AUTO_CLEAR` | `true` | Clear session history after a period of inactivity |
| `session_clear_after` | `AURICLE_SESSION_CLEAR_AFTER` | `3600` | Seconds of inactivity before session history is cleared |

Example `config.yaml` block:
```yaml
auricle:
  mic_device: plughw:3,0
  tts_voice: en-GB-LibbyNeural
  vosk_model_path: ~/.hermes/plugins/hermes-auricle/models/vosk-model
  oww_wakeword_model_path: ~/.hermes/plugins/hermes-auricle/models/wakeword.onnx
  oww_melspec_model_path: ~/.hermes/plugins/hermes-auricle/models/melspectrogram.onnx
  oww_embedding_model_path: ~/.hermes/plugins/hermes-auricle/models/embedding_model.onnx
```

---

## Voice commands

These are matched against the full vosk transcript (exact, case-insensitive). **All voice commands require the wakeword to be said first to activate**:

| Say | Effect |
|-----|--------|
| "clear", "reset" | Clear session history, play confirmation |
| "stop" | Cancel in-flight agent run, stop TTS, return to wakeword mode |

---

## How it works

**Ingress:** A single `arecord` subprocess feeds raw 16kHz PCM through a state-gated pipeline. In IDLE, OWW watches for the wakeword. In SPEAKING and DISPATCHED, OWW also runs for barge-in detection. In AWAITING_UTTERANCE and UTTERANCE, vosk captures the utterance. OWW and vosk never run on the same chunk simultaneously — which state the FSM is in determines which model processes each chunk. After the utterance ends (vosk final result), the transcript is dispatched to the hermes agent.

**Egress:** The full agent response arrives in one `send()` call and is segmented internally by newlines into units. Each unit is synthesized via the `edge_tts` Python library and written to a `pw-play` stdin pipe. While the current unit plays, the next one is pre-fetched concurrently (lookahead). Barge-in (wakeword during TTS) kills playback immediately and opens a new listen window.

**Active-listen window:** After TTS ends, the mic stays open for 5 seconds (configurable) without requiring the wakeword. This allows natural follow-up questions. The tosleep chime plays on expiry.

**Auto-clear:** When a new utterance is dispatched after a period of inactivity (default 1 hour), the session history is silently cleared before the message is sent. The threshold is measured from the last dispatched message — only real utterances and voice commands count, not passive listening. Disable with `session_auto_clear: false`.

**Auto-sleep:** After 60 seconds (configurable) of acoustic inactivity in IDLE mode, the wakeword model is gated off to save compute. The OWW model stays loaded; sleep is a software flag, not a model reload. Wake detection uses normalized spectral flux — the spectrum is compared frame-to-frame, so stable background noise (fans, HVAC) doesn't prevent sleep while any novel acoustic event (speech, door, clap) instantly re-enables the wakeword. Wake-up is silent and invisible to the user.

**Fault recovery:** If the Jabra is unplugged at boot or a model fails to load, the adapter enters a fatal state visible in `hermes gateway status` but does not crash the hermes process. It retries automatically every 30 seconds.

---

## Misinput filtering

After wakeword detection, single-word or grammatically incomplete transcripts (articles, bare pronouns, dangling prepositions, etc.) are treated as misinputs rather than dispatched to the agent. On the first misinput the confused chime plays and the adapter stays in AWAITING_UTTERANCE. On the second consecutive misinput it gives up, plays the tosleep chime, and returns to IDLE. The full list of misinput phrases is in `consts.py: MISINPUT_PHRASES`.

---

## Message classification

Not every string hermes sends to `send()` should be read aloud. `SystemMessageClassifier` silently suppresses:

- **Command responses** — the acknowledgement hermes emits after `/new` or `/stop` (tracked via a credit counter)
- **Emoji/glyph-prefixed messages** — system status lines that start with a Unicode symbol (`So`/`Sk` category); the LLM is instructed via `PLATFORM_HINT` never to lead with emoji, so this reliably separates system noise from agent speech
- **Known literals** — a small static set of no-emoji system strings (e.g. "No active task to stop.")

Everything else passes through to TTS.

---

## Project layout

```
hermes-auricle/
  __init__.py          re-export of register()
  plugin.yaml          hermes plugin manifest
  adapter.py           AuricleAdapter + register(ctx) entry point
  consts.py            all constants, env var names, defaults
  fsm.py               thread-safe FSM (7 states)
  providers.py         STTProvider / TTSProvider ABCs + implementations
  ingress.py           arecord + OWW + vosk thread loop
  egress.py            streaming TTS playback queue
  classifier.py        SystemMessageClassifier — suppresses hermes system messages
  audio_buffer.py      ring buffer with TTS-active tracking for echo suppression
  sleep.py             SleepDetector — spectral flux EMA for auto-sleep
  assets/              auricle-wakeup / auricle-tosleep / auricle-notify / auricle-confused WAVs
  models/              model files (not committed)
```
