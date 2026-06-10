# AudioTransformersService

**Module:** `ovos_dinkum_listener.transformers`
**Class:** `AudioTransformersService` — `transformers.py:34`

Manages a prioritised pipeline of audio transformer plugins. Plugins can inspect and modify raw audio before and during STT processing, and can inject metadata into the utterance context (e.g. detected language) that is merged into `recognizer_loop:utterance`.

---

## Overview

```python
from ovos_dinkum_listener.transformers import AudioTransformersService

svc = AudioTransformersService(bus, config=full_config_dict)
```

Config is read from `config["listener"]["audio_transformers"]`. If no config is provided, `AudioTransformersService` reads from `{}`.

---

## Constructor — `transformers.py:36`

| Parameter | Description |
|---|---|
| `bus` | Message bus client; bound to each plugin via `plugin.bind(bus)` |
| `config` | Full config dict (e.g. `Configuration()`). `None` defaults to `{}` |

On construction, `load_plugins()` is called immediately — `transformers.py:44`.

---

## Plugin Discovery and Loading — `load_plugins()` — `transformers.py:46`

Discovers all installed audio transformer plugins via `find_audio_transformer_plugins()` (OPM entry point group: `opm.audio_transformer`).

A plugin is loaded only if:
1. Its entry point name appears as a key in `config["listener"]["audio_transformers"]`
2. Its config entry does not have `"active": false`

```python
# Only this plugin is loaded:
{
  "listener": {
    "audio_transformers": {
      "ovos-audio-transformer-example": {
        "active": true,
        "priority": 50
      }
    }
  }
}
```

Each loaded plugin is instantiated with `plug()` and bound to the bus with `plugin.bind(bus)`. Load errors are logged and skipped.

Sets `self.has_loaded = True` when done — `transformers.py:59`.

---

## `plugins` property — `transformers.py:62`

Returns all loaded plugins sorted by **descending** priority — `transformers.py:71`:

```python
sorted(self.loaded_plugins.values(), key=lambda k: k.priority, reverse=True)
```

Higher `priority` number → called first. A plugin with `priority=1` runs last and has final say over both audio and context.

---

## Feed Methods

The voice loop calls these on each audio chunk to inform plugins of the audio type. Plugins implement the corresponding `feed_*_chunk()` method.

### `feed_audio(chunk)` — `transformers.py:84`

Called for **ambient audio** — chunks where no hotword is active and no command is being recorded. Invoked in states: `DETECT_WAKEWORD`, `WAITING_CMD`, `PRE_WAKE_VAD`, `BEFORE_COMMAND`, `CONFIRMATION`.

Calls `module.feed_audio_chunk(chunk)` for each plugin in priority order.

### `feed_hotword(chunk)` — `transformers.py:92`

Called for the chunk in which a hotword/wake-word was detected. Allows plugins to process or log the moment of wakeword detection.

Calls `module.feed_hotword_chunk(chunk)` for each plugin.

### `feed_speech(chunk)` — `transformers.py:100`

Called for each chunk while the user is speaking an active command (`IN_COMMAND`, `RECORDING`).

Calls `module.feed_speech_chunk(chunk)` for each plugin. Exceptions are caught and logged — `transformers.py:108`.

---

## `transform(chunk)` → `(bytes, dict)` — `transformers.py:111`

Called once per utterance at the end of a command (`AFTER_COMMAND`), after all speech audio has been recorded.

```python
audio_bytes, context = svc.transform(chunk)
# context is merged into message.context for recognizer_loop:utterance
```

For each plugin in priority order — `transformers.py:120`:
1. `module.feed_speech_utterance(chunk)` → plugin receives the complete utterance audio
2. `module.transform(chunk)` → returns `(transformed_chunk, metadata_dict)`
3. `context = merge_dict(context, metadata_dict)` — later plugins' keys override earlier ones

Default context initialised at the start of `transform()` — `transformers.py:117`:

```python
{
    "client_name": "ovos_dinkum_listener",
    "source": "audio",
    "destination": ["skills"]
}
```

Common metadata keys that plugins may inject:

| Key | Description |
|---|---|
| `stt_lang` | Override STT language for this utterance (validated by `_validate_lang`) |
| `detected_lang` | Language identified by a classifier plugin |
| `request_lang` | Language volunteered by the source device |

The merged context becomes `message.context` in `recognizer_loop:utterance`.

---

## `shutdown()` — `transformers.py:74`

Calls `module.shutdown()` on each loaded plugin in priority order. Exceptions are caught and logged as warnings.

```python
svc.shutdown()
```

---

## Plugin API (for plugin authors)

Audio transformer plugins must implement (from the OPM base class):

| Method | Called by | Audio type |
|---|---|---|
| `feed_audio_chunk(chunk)` | `feed_audio()` | Ambient (no speech/WW) |
| `feed_hotword_chunk(chunk)` | `feed_hotword()` | Hotword detection moment |
| `feed_speech_chunk(chunk)` | `feed_speech()` | Active speech |
| `feed_speech_utterance(chunk)` | `transform()` | Complete utterance |
| `transform(chunk)` → `(bytes, dict)` | `transform()` | Final transform + metadata |

Attribute `priority: int` controls ordering (higher = runs first).

Entry point group: `opm.audio_transformer`

---

## Integration in Voice Loop

`AudioTransformersService` is called at these points in `DinkumVoiceLoop.run()`:

| Voice loop call | State | Method |
|---|---|---|
| `voice_loop.py:203` | `PRE_WAKE_VAD` (silence) | `feed_audio(chunk)` |
| `voice_loop.py:274` | `DETECT_WAKEWORD` (no WW) | `feed_audio(chunk)` |
| `voice_loop.py:508` | `DETECT_WAKEWORD` (WW detected) | `feed_hotword(chunk)` |
| `voice_loop.py:638` | `BEFORE_COMMAND` | `feed_audio(chunk)` |
| `voice_loop.py:684` | `IN_COMMAND` | `feed_speech(chunk)` |
| `voice_loop.py:827` | `AFTER_COMMAND` | `transform(chunk)` |
| `voice_loop.py:403` | `RECORDING` (hotword found) | `feed_hotword(chunk)` |
| `voice_loop.py:418` | `RECORDING` (speaking) | `feed_speech(chunk)` |
| `voice_loop.py:475` | `CHECK_WAKE_UP` (wakeup found) | `feed_hotword(chunk)` |
| `voice_loop.py:608` | `WAITING_CMD` (no speech) | `feed_audio(chunk)` |

---

## Testing Notes

`AudioTransformersService` can be tested with no real plugins installed. Pass an empty or minimal config:

```python
from unittest.mock import patch, Mock
from ovos_dinkum_listener.transformers import AudioTransformersService

with patch("ovos_dinkum_listener.transformers.find_audio_transformer_plugins", return_value={}):
    svc = AudioTransformersService(bus=Mock(), config={"listener": {"audio_transformers": {}}})

assert svc.loaded_plugins == {}
assert svc.has_loaded is True

chunk, ctx = svc.transform(b'\x00' * 100)
assert ctx["source"] == "audio"
assert ctx["destination"] == ["skills"]
```

See `test/unittests/test_transformers.py` for complete test coverage.
