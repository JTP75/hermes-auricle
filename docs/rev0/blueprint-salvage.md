# Blueprint Salvage

Usable patterns extracted from auricle_adapter_blueprint.md and auricle_config_blueprint.md.
Stale or wrong pieces have been dropped. See design-rev1.md for authoritative decisions.

---

## 1. Streaming egress pattern (adapter_blueprint)

The gateway calls `send()` once with the initial token slice, then calls `edit_message()`
repeatedly with *cumulative* content as the LLM streams. The adapter must diff against
the previous length to extract only newly arrived text each call.

```python
async def send(self, chat_id, content, reply_to=None, metadata=None) -> SendResult:
    self._reset_stream_state()
    self._playback_task = asyncio.create_task(self._playback_worker())
    await self._process_text_delta(content, finalize=False)
    return SendResult(success=True, message_id="auricle_voice_stream")

async def edit_message(self, chat_id, message_id, content, *, finalize=False) -> SendResult:
    await self._process_text_delta(content, finalize=finalize)
    return SendResult(success=True, message_id=message_id)

def _reset_stream_state(self):
    self._processed_len = 0
    self._text_buffer = ""
    self._playback_queue = asyncio.Queue()
    self._playback_task = None

async def _process_text_delta(self, cumulative_text: str, finalize: bool = False):
    new_text = cumulative_text[self._processed_len:]
    self._processed_len = len(cumulative_text)
    self._text_buffer += new_text

    sentences = re.split(r'(?<=[.?!])\s+|\n+', self._text_buffer)

    if not finalize:
        if len(sentences) > 1:
            completed = sentences[:-1]
            self._text_buffer = sentences[-1]
        else:
            completed = []
    else:
        completed = [s for s in sentences if s.strip()]
        self._text_buffer = ""

    for sentence in completed:
        if sentence.strip():
            await self._playback_queue.put(sentence)

    if finalize:
        await self._playback_queue.put(None)   # sentinel
        await self._playback_queue.join()
        if self._playback_task:
            self._playback_task.cancel()
```

---

## 2. Playback worker (adapter_blueprint)

Sequential asyncio queue — sentences play in order, no overlap.
`None` sentinel signals end of turn.

```python
async def _playback_worker(self):
    while True:
        sentence = await self._playback_queue.get()
        if sentence is None:
            self._playback_queue.task_done()
            break
        clean = sentence.strip()
        if clean:
            cmd = (
                f"edge-tts --voice {shlex.quote(self._tts_voice)} "
                f"--text {shlex.quote(clean)} --stream | "
                f"pw-play --target='Jabra SPEAK 510 USB' -"
            )
            proc = await asyncio.create_subprocess_shell(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            await proc.wait()
        self._playback_queue.task_done()
```

Notes:
- `--stream` on edge-tts reduces first-audio latency
- `--target='Jabra SPEAK 510 USB'` is more robust than relying on PipeWire default output
- Store a reference to `proc` on `self` so barge-in can `proc.kill()` mid-sentence

---

## 3. apply_yaml_config_fn (config_blueprint)

Correct pattern; matches what the hermes platform guide prescribes.
`not os.getenv()` guards preserve env > YAML precedence.

```python
def apply_yaml_config_fn(yaml_cfg, platform_cfg):
    auricle_cfg = yaml_cfg.get("gateway", {}).get("auricle", {})
    if not auricle_cfg:
        return None

    updates = {}

    if "hw_device" in auricle_cfg and not os.getenv("AURICLE_HW_DEVICE"):
        os.environ["AURICLE_HW_DEVICE"] = str(auricle_cfg["hw_device"])
        updates["hw_device"] = auricle_cfg["hw_device"]

    if "vosk_model_path" in auricle_cfg and not os.getenv("AURICLE_VOSK_MODEL_PATH"):
        os.environ["AURICLE_VOSK_MODEL_PATH"] = str(auricle_cfg["vosk_model_path"])
        updates["vosk_model_path"] = auricle_cfg["vosk_model_path"]

    if "tts_voice" in auricle_cfg and not os.getenv("AURICLE_TTS_VOICE"):
        os.environ["AURICLE_TTS_VOICE"] = str(auricle_cfg["tts_voice"])
        updates["tts_voice"] = auricle_cfg["tts_voice"]

    if "session_resume" in auricle_cfg and not os.getenv("AURICLE_SESSION_RESUME"):
        os.environ["AURICLE_SESSION_RESUME"] = str(auricle_cfg["session_resume"])
        updates["session_resume"] = auricle_cfg["session_resume"]

    if "mute" in auricle_cfg and not os.getenv("AURICLE_MUTE"):
        os.environ["AURICLE_MUTE"] = str(auricle_cfg["mute"])
        updates["mute"] = auricle_cfg["mute"]

    return updates
```

---

## Dropped from blueprints

- `SUPPORTS_MESSAGE_EDITING = True` — attribute does not exist in BasePlatformAdapter; drop it
- `entry_point: adapter:AuricleAdapter` in plugin.yaml — wrong format; use `register(ctx)` pattern
- `requires_env` dict format in plugin.yaml — wrong; use list format matching IRC plugin.yaml
- `en-US-AndrewNeural` voice default — superseded by decision: `en-GB-LibbyNeural`
- `platform` as `__init__` parameter — handled internally via `Platform` enum in `super().__init__`
