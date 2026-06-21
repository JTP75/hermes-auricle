# hermes-auricle

Hermes platform plugin that connects [hermes-agent](https://github.com/nousresearch/hermes-agent) to a running [auricle-engine](../auricle-engine/) instance over WebSocket. Provides a voice interface: the engine handles all audio processing; this plugin contains only the hermes adapter logic.

**Requires auricle-engine to be running.** See the engine repo for hardware setup, model downloads, STT/TTS configuration, and audio device wiring.

---

## Requirements

**Python packages** (hermes venv)

```bash
pip install websockets
```

That's it. All audio dependencies (vosk, openwakeword, edge-tts, torch, etc.) belong in the engine's venv.

---

## Installation

1. Clone or install to `~/.hermes/plugins/hermes-auricle/`

2. Install the websockets package in the hermes venv:
   ```bash
   pip install websockets
   ```

3. Start auricle-engine (see its README):
   ```bash
   cd ~/misc/hermes-plugins/auricle-engine
   python __main__.py
   ```

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

6. Verify the connector can reach the engine:
   ```bash
   python doctor.py
   ```

---

## Configuration

| Env var | Default | Description |
|---------|---------|-------------|
| `AURICLE_CONNECTOR_HOST` | `localhost` | Host to bind the WebSocket server on. Set to `0.0.0.0` to accept engine connections from remote machines. |
| `AURICLE_CONNECTOR_PORT` | `57310` | Port to listen on for engine connections. |
| `AURICLE_SESSION_RESUME` | `true` | Resume existing session on hermes restart |
| `AURICLE_SESSION_AUTO_CLEAR` | `true` | Clear session history after a period of inactivity |
| `AURICLE_SESSION_CLEAR_AFTER` | `3600` | Seconds of inactivity before session history is cleared |

These can also be set under an `auricle:` key in `~/.hermes/config.yaml`:

```yaml
auricle:
  connector_host: 0.0.0.0
  connector_port: 57310
  session_resume: true
  session_auto_clear: true
  session_clear_after: 3600
```

All audio, STT, TTS, wakeword, and sleep configuration lives in the engine — see the engine's README for those env vars.

---

## How it works

The adapter connects to auricle-engine at startup and exchanges JSON messages over a persistent WebSocket connection.

**Ingress:** The engine detects the wakeword and transcribes the utterance, then sends `{t:"utterance", text:"..."}`. The adapter applies the session auto-clear check, then calls `handle_message()` to dispatch the text into the hermes gateway as a normal user message.

**Egress:** When hermes calls `send()` with the agent's response, the adapter runs it through `SystemMessageClassifier` (suppresses system noise that shouldn't be spoken aloud), then forwards the text to the engine as `{t:"speak", text:"..."}`. The engine handles segmentation, TTS, and playback.

**Voice commands:** "clear" and "stop" are detected by the engine. The engine plays audio feedback locally and sends `{t:"cmd", name:"new"|"stop"}`. The adapter translates these into `/new` and `/stop` hermes commands.

**Proactive messages (cron):** The `standalone_sender_fn` opens a fresh connection to the engine, sends `{t:"notify", text:"..."}`, and waits for `{t:"notify_done"}`. The engine plays the notify chime followed by the text. Requires the engine to be running.

**Session auto-clear:** Tracked in the connector: if more than `AURICLE_SESSION_CLEAR_AFTER` seconds pass between utterances, the next dispatch silently prepends a `/new` to clear the hermes session history.

---

## Message classification

Not every string hermes sends to `send()` should be read aloud. `SystemMessageClassifier` silently suppresses:

- **Command responses** — the acknowledgement hermes emits after `/new` or `/stop` (tracked via a credit counter)
- **Emoji/glyph-prefixed messages** — system status lines that start with a Unicode symbol (`So`/`Sk` category); the LLM is instructed via `PLATFORM_HINT` never to lead with emoji, so this reliably separates system noise from agent speech
- **Known literals** — a small static set of no-emoji system strings (e.g. "No active task to stop.")

Everything else is forwarded to the engine as a `speak` message.

---

## Project layout

```
hermes-auricle/
  __init__.py      re-export of register()
  plugin.yaml      hermes plugin manifest
  adapter.py       AuricleAdapter + register(ctx) entry point
  consts.py        connector-only constants and env var names
  classifier.py    SystemMessageClassifier — suppresses hermes system messages
  doctor.py        diagnostic script — checks websockets dep and engine reachability
```
