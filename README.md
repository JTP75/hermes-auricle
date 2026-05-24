# hermes-auricle

A local voice platform plugin for [hermes](https://github.com/nousresearch/hermes-agent). Turns a Raspberry Pi + Jabra Speak 510 USB into an Alexa-style smart speaker. All audio is processed locally: no cloud STT, no cloud TTS, no network dependency for the audio pipeline.

- **Wakeword + STT**: [openWakeWord](https://github.com/dscripka/openWakeWord) (neural detector) + [vosk](https://alphacephei.com/vosk/) (offline STT)
- **TTS + playback**: [edge-tts](https://github.com/rany2/edge-tts) piped to [PipeWire](https://pipewire.org/) (`pw-play`)
- **Target hardware**: Raspberry Pi + Jabra Speak 510 USB (mic + speaker in one device, hardware echo cancellation)

---

## Requirements

**Python packages**
```
pip install vosk openwakeword numpy edge-tts
```

**System packages** (Raspberry Pi / Debian)
```
sudo apt install alsa-utils pipewire
```

**Models** -- place in `models/` or set env vars / `config.yaml` to point elsewhere:

| File | What it is |
|------|-----------|
| `models/vosk-model/` | Vosk offline STT model directory |
| `models/wakeword.tflite` | openWakeWord custom wakeword model |
| `models/melspectrogram.tflite` | OWW preprocessor (from the OWW repo) |
| `models/embedding_model.tflite` | OWW preprocessor (from the OWW repo) |

---

## Installation

1. Clone or copy this directory to `~/.hermes/plugins/hermes-auricle/`

2. Generate audio assets (one-time):
   ```bash
   python scripts/generate_assets.py
   # or with a different voice:
   python scripts/generate_assets.py --voice en-US-AriaNeural
   ```

3. Place models in `models/` (see above).

4. Enable the plugin in `~/.hermes/config.yaml`:
   ```yaml
   plugins:
     enabled:
       - hermes-auricle
   ```

5. Start the hermes gateway:
   ```bash
   hermes gateway start
   ```

---

## Configuration

All settings live under `gateway.auricle` in `~/.hermes/config.yaml`. Env vars take precedence.

| config.yaml key | Env var | Default | Description |
|-----------------|---------|---------|-------------|
| `mic_device` | `AURICLE_MIC_DEVICE` | `plughw:3,0` | ALSA device for `arecord` |
| `tts_voice` | `AURICLE_TTS_VOICE` | `en-GB-LibbyNeural` | edge-tts voice name |
| `active_listen_duration` | `AURICLE_ACTIVE_LISTEN_DURATION` | `5` | Seconds of no-wakeword listen after TTS ends |
| `session_resume` | `AURICLE_SESSION_RESUME` | `true` | Resume session history on hermes restart |
| `mute` | `AURICLE_MUTE` | `false` | Disable wakeword detection on startup |
| `vosk_model_path` | `AURICLE_VOSK_MODEL_PATH` | `models/vosk-model` | Path to vosk model directory |
| `oww_wakeword_model_path` | `AURICLE_OWW_WAKEWORD_MODEL_PATH` | `models/wakeword.tflite` | Path to OWW wakeword `.tflite` |
| `oww_melspec_model_path` | `AURICLE_OWW_MELSPEC_MODEL_PATH` | `models/melspectrogram.tflite` | Path to OWW melspec preprocessor |
| `oww_embedding_model_path` | `AURICLE_OWW_EMBEDDING_MODEL_PATH` | `models/embedding_model.tflite` | Path to OWW embedding preprocessor |

Example `config.yaml` block:
```yaml
gateway:
  auricle:
    mic_device: plughw:3,0
    tts_voice: en-GB-LibbyNeural
    vosk_model_path: ~/.hermes/plugins/hermes-auricle/models/vosk-model
    oww_wakeword_model_path: ~/.hermes/plugins/hermes-auricle/models/wakeword.tflite
    oww_melspec_model_path: ~/.hermes/plugins/hermes-auricle/models/melspectrogram.tflite
    oww_embedding_model_path: ~/.hermes/plugins/hermes-auricle/models/embedding_model.tflite
```

---

## Voice commands

These are matched against the full vosk transcript (exact, case-insensitive):

| Say | Effect |
|-----|--------|
| "clear" or "reset" | Clear session history, play confirmation |
| "stop" (after wakeword) | Cancel in-flight agent run, stop TTS, return to wakeword mode |

---

## How it works

**Ingress:** A single `arecord` subprocess feeds raw 16kHz PCM to both openWakeWord and vosk simultaneously. In idle mode, OWW watches for the wakeword. On detection, a ping plays and the adapter enters active-listen mode where vosk captures the full utterance. After the utterance ends (vosk final result), the transcript is dispatched to the hermes agent.

**Egress:** Hermes streams the agent response sentence by sentence. Each sentence is synthesized with `edge-tts --stream` and piped directly to `pw-play`. The next sentence queues behind the current one. Barge-in (wakeword during TTS) kills playback immediately and opens a new listen window.

**Active-listen window:** After TTS ends, the mic stays open for 5 seconds (configurable) without requiring the wakeword. This allows natural follow-up questions. A bong plays on expiry.

**Fault recovery:** If the Jabra is unplugged at boot or a model fails to load, the adapter enters a fatal state visible in `hermes gateway status` but does not crash the hermes process. It retries automatically every 30 seconds.

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
  assets/              ping / bong / ding / cleared / error WAVs
  models/              model files (not committed)
  scripts/
    generate_assets.py one-time audio asset generation
```
