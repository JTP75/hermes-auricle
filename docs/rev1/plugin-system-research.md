# Hermes Platform Plugin System — Research Notes

Sources: `~/misc/hermes-agent/hermes_cli/plugins.py`, `gateway/config.py`,
`gateway/platform_registry.py`, `plugins/platforms/irc/`, `plugins/platforms/teams/`.

---

## 1. Entry point: `__init__.py`, not `adapter.py`

The plugin loader (`_load_directory_module` in `hermes_cli/plugins.py`) explicitly looks
for `plugin_dir / "__init__.py"`. **`adapter.py` is never loaded directly.**

The standard pattern used by IRC and Teams:

```python
# __init__.py
from .adapter import register
__all__ = ["register"]
```

`register(ctx)` lives in `adapter.py` and is re-exported via `__init__.py`.

---

## 2. Sibling file imports work via relative imports

The loader sets `module.__path__ = [str(plugin_dir)]` and
`submodule_search_locations=[str(plugin_dir)]`, making the plugin directory a proper
Python package under the `hermes_plugins` namespace.

Relative imports work in any file within the plugin directory:

```python
# adapter.py
from .consts import CHAT_ID, DEFAULT_TTS_VOICE
from .providers import VoskSTTProvider, EdgeTTSProvider
```

No `sys.path` manipulation, no `importlib` gymnastics — standard relative imports.

Our file layout:

```
~/.hermes/plugins/hermes-auricle/
  __init__.py        # from .adapter import register
  plugin.yaml
  adapter.py         # AuricleAdapter + register(ctx)
  consts.py          # all constants
  providers.py       # STTProvider / TTSProvider ABCs + impls
  assets/
    ping.wav
    bong.wav
    ding.wav
    cleared.wav
    error.wav
```

---

## 3. Platform enum: dynamic pseudo-members, no core edit needed

`Platform` in `gateway/config.py` has a `_missing_()` classmethod. When
`Platform("auricle")` is called and "auricle" is not a built-in member, it:

1. Checks `platform_registry.is_registered("auricle")`
2. If registered, creates a cached pseudo-member with `_value_ = "auricle"` and
   `_name_ = "AURICLE"` — identity-stable across repeated calls

Since `ctx.register_platform(name="auricle", ...)` fires during plugin load (before
`adapter_factory` is ever called), `Platform("auricle")` resolves correctly when
`AuricleAdapter.__init__` calls `super().__init__(config, Platform("auricle"))`.

**No changes to `gateway/config.py` are needed.**

---

## 4. `standalone_sender_fn` signature

From `platform_registry.py` docstring and the IRC reference implementation:

```python
async def _standalone_send(
    pconfig,
    chat_id: str,
    message: str,
    *,
    thread_id: Optional[str] = None,
    media_files: Optional[List[str]] = None,
    force_document: bool = False,
) -> dict:
    ...
    # success:
    return {"success": True, "message_id": "..."}
    # failure:
    return {"error": "description"}
```

`pconfig` is a `PlatformConfig` — use `pconfig.extra` for adapter-specific config, or
read env vars directly (env vars take precedence per the `apply_yaml_config_fn` contract).

For auricle, `standalone_sender_fn` should play `ding.wav` then run the edge-tts pipeline
on `message` — same egress path as the gateway adapter but opened ephemerally.

---

## 5. User plugins require explicit opt-in

**User-installed plugins (`~/.hermes/plugins/`) are NOT auto-loaded.**

Only bundled platform plugins (inside the hermes repo at `plugins/platforms/`) auto-load.
User plugins — including ours — require explicit enablement in `config.yaml`:

```yaml
plugins:
  enabled:
    - hermes-auricle
```

`plugin.yaml` must declare `kind: platform` to be recognized as a gateway adapter.

---

## 6. `plugin.yaml` `kind` field

Valid kinds: `standalone`, `backend`, `exclusive`, `platform`, `model-provider`.

For a gateway adapter: **`kind: platform`**.

Without `kind: platform`, the plugin is treated as `standalone` and will not be
wired into the gateway's platform adapter discovery path (though `register(ctx)` still
runs and `ctx.register_platform()` still works — the kind affects auto-load policy for
bundled plugins only; user plugins are gated by `plugins.enabled` regardless).

---

## 7. `register(ctx)` call — full kwarg surface

`ctx.register_platform()` delegates to `PlatformEntry` in `gateway/platform_registry.py`.
All fields on `PlatformEntry` are available as kwargs. Relevant ones for auricle:

| kwarg | type | purpose |
|-------|------|---------|
| `name` | str | `"auricle"` — used in config, `Platform("auricle")` |
| `label` | str | `"Auricle"` — display name |
| `adapter_factory` | callable | `lambda cfg: AuricleAdapter(cfg)` |
| `check_fn` | callable | `check_requirements()` |
| `validate_config` | callable | check env vars / model paths present |
| `env_enablement_fn` | callable | seeds `PlatformConfig.extra` from env at gateway-status time |
| `apply_yaml_config_fn` | callable | bridges `config.yaml gateway.auricle.*` → env vars |
| `platform_hint` | str | injected into system prompt |
| `emoji` | str | `"🎙️"` |
| `allowed_users_env` | str | `"AURICLE_ALLOWED_USERS"` (MVP: unused, but wire it) |
| `allow_all_env` | str | `"AURICLE_ALLOW_ALL_USERS"` |
| `standalone_sender_fn` | async callable | for cron / out-of-process delivery |
| `cron_deliver_env_var` | str | `"AURICLE_HOME_CHANNEL"` — set to `"local"` via env |
| `install_hint` | str | pip install hint shown when `check_fn` fails |
| `pii_safe` | bool | `True` — no phone numbers or tokens |
| `allow_update_command` | bool | `False` — voice device, `/update` is not meaningful |
