# Hotwords

**Module:** `ovos_dinkum_listener.voice_loop.hotwords`
**Source:** `ovos_dinkum_listener/voice_loop/hotwords.py`

`HotwordContainer` manages all loaded hotword/wake-word engines and routes audio to the appropriate subset based on the current `HotwordState`. `CyclicAudioBuffer` provides a fixed-size sliding audio window used by some hotword engine plugins.

---

## `HotWordException` — `hotwords.py:18`

Raised by `HotwordContainer.found()` when the current `HotwordState` is `LISTEN` but no listen words are loaded. Signals a configuration error — the listener has no way to exit the waiting state.

---

## `CyclicAudioBuffer` — `hotwords.py:22`

A fixed-size sliding window of audio bytes. New data is appended; oldest data is dropped when capacity is exceeded.

```python
from ovos_dinkum_listener.voice_loop.hotwords import CyclicAudioBuffer

buf = CyclicAudioBuffer(duration=0.98, sample_rate=16000, sample_width=2)
buf.append(chunk)
audio = buf.get()
```

### Constructor — `hotwords.py:30`

| Parameter | Default | Description |
|---|---|---|
| `duration` | `0.98` | Window size in seconds |
| `initial_data` | `None` | Seed data; defaults to silence |
| `sample_rate` | `16000` | Sample rate for byte-size calculation |
| `sample_width` | `2` | Sample width in bytes |

`self.size = duration_to_bytes(duration, sample_rate, sample_width)` — `hotwords.py:32`

Initial buffer: the last `size` bytes of `initial_data`, or silence. — `hotwords.py:35`

### Methods

| Method | Signature | Description |
|---|---|---|
| `append(data)` | `bytes → None` | Concatenate `_buffer + data`, then keep only the last `size` bytes — `hotwords.py:64` |
| `get()` | `→ bytes` | Return current buffer contents — `hotwords.py:75` |
| `clear()` | `→ None` | Reset buffer to `size` null bytes — `hotwords.py:37` |
| `duration_to_bytes(duration, sr, sw)` | `static → int` | `int(duration * sr) * sw` — `hotwords.py:44` |
| `get_silence(num_bytes)` | `static → bytes` | Return `b'\0' * num_bytes` — `hotwords.py:56` |

---

## `HotwordState` — `hotwords.py:82`

Controls which engine subset receives audio in `update()` and is checked in `found()`.

| State | String value | Active engines | Use case |
|---|---|---|---|
| `LISTEN` | `"wakeword"` | `listen_words` | Default WW detection |
| `HOTWORD` | `"hotword"` | `hot_words` | Continuous/hybrid mode (non-listen hotwords) |
| `RECORDING` | `"recording"` | `stop_words` | During free recording |
| `WAKEUP` | `"wakeup"` | `wakeup_words` | While sleeping; looking for wakeup word |

---

## `_safe_get_plugins` decorator — `hotwords.py:90`

Wraps `HotwordContainer` property accessors. Blocks on `HotwordContainer._loaded.wait(30)` — raises `TimeoutError` if engines are not loaded within 30 seconds. Converts `KeyError` to `HotWordException`.

---

## `HotwordContainer` — `hotwords.py:102`

Class-level shared plugin registry. All instances share `_plugins` (dict) and `_loaded` (`threading.Event`).

```python
from ovos_dinkum_listener.voice_loop.hotwords import HotwordContainer

container = HotwordContainer(bus)
container.load_hotword_engines()
```

### Constructor — `hotwords.py:106`

| Parameter | Default | Description |
|---|---|---|
| `bus` | `FakeBus()` | Message bus for binding hotword plugins |
| `expected_duration` | `3` | Reserved; not currently used in FSM logic |
| `sample_rate` | `16000` | Audio sample rate |
| `sample_width` | `2` | Audio sample width in bytes |
| `reload_allowed` | `True` | If `False`, `load_hotword_engines()` is a no-op after first load |
| `autoload` | `False` | If `True`, call `load_hotword_engines()` in `__init__` |

Initial state: `HotwordState.HOTWORD`, `reload_on_failure = False`.

**Important:** `_plugins` and `_loaded` are **class attributes** (not instance attributes). Resetting them affects all instances:

```python
# In tests — reset class state between test cases:
HotwordContainer._plugins = {}
HotwordContainer._loaded = Event()
```

### `load_hotword_engines()` — `hotwords.py:116`

Reads `mycroft.conf["hotwords"]` and loads the configured engines.

Key behaviours:
- Skips reload if `reload_allowed=False` and `_loaded` is already set — `hotwords.py:120`
- Normalises hotword names: spaces → underscores — `hotwords.py:145`
- Auto-enables main WW and stand-up word when `active` is `None` — `hotwords.py:158`
- Retrieves sound duration from audio file if `sound` is set — `hotwords.py:195`
- Sets `_loaded` event when done — `hotwords.py:208`
- Sets `reload_on_failure = True` if at least one listen word was loaded — `hotwords.py:213`

Per-plugin record stored in `_plugins[word]`:

```python
{
    "engine": HotWordEngine,  # plugin instance
    "sound": "snd/start_listening.wav",  # or None
    "bus_event": "custom.event",          # or None
    "utterance": "run script",            # or None; hard-coded STT output
    "stt_lang": "en-us",
    "listen": True,
    "wakeup": False,
    "stopword": False,
    "sound_duration": 0.8,               # seconds; only if sound is set
}
```

### Hotword Types

| Type | Config key | Effect when detected |
|---|---|---|
| **Listen word** | `listen: true` or matches `listener.wake_word` | Starts VAD/STT recording pipeline |
| **Wakeup word** | `wakeup: true` or matches `listener.stand_up_word` | Exits sleep mode |
| **Stop word** | `stopword: true` | Ends free `RECORDING` mode |
| **Hotword** | none of the above (active=true) | Plays sound and/or emits bus event |

Auto-enable rules (when `active` is `null`/`None`) — `hotwords.py:158`:
- Main wake word (`listener.wake_word`) → enabled
- Stand-up word (`listener.stand_up_word`) → enabled
- All other hotwords → disabled

### `update(chunk)` — `hotwords.py:312`

Feeds `chunk` to all engines in the currently active subset (determined by `self.state`). Exceptions per-engine are caught and logged.

| `self.state` | Engines updated |
|---|---|
| `LISTEN` | `listen_words.values()` |
| `WAKEUP` | `wakeup_words.values()` |
| `RECORDING` | `stop_words.values()` |
| `HOTWORD` | `hot_words.values()` |

### `found()` → `Optional[str]` — `hotwords.py:259`

Checks whether any engine in the active subset has fired. Returns the first matching hotword name, or `None`.

Raises `HotWordException` if `state == LISTEN` and `listen_words` is empty — `hotwords.py:269`.

### `get_ww(ww)` → `dict` — `hotwords.py:292`

Returns a copy of `_plugins[ww]` enriched with:
- `"key_phrase"`: the hotword name
- `"module"`: `engine.config["module"]`
- `"engine"`: `engine.__class__.__name__`

The `"engine"` field in the returned dict is the **class name string** (not the engine object).

### `verify(ww_audio)` → `bool` — `hotwords.py:308`

Stub — always returns `True`. Intended for future verifier plugins.

### `reset()` — `hotwords.py:336`

Calls `engine.reset()` on all loaded engines (if the method exists). Called after each utterance to prevent stale model state.

### `shutdown()` — `hotwords.py:348`

Calls `engine.shutdown()` on all engines, then removes all entries from `_plugins`.

### Properties

`ww_names` reads `_plugins` directly and is **not** guarded by `@_safe_get_plugins` — it does not wait on `_loaded` and will not raise `HotWordException`. All other properties below are decorated with `@_safe_get_plugins` and block until engines are loaded.

| Property | Returns | Guarded | Description |
|---|---|---|---|
| `ww_names` | `[str, ...]` | No | All loaded hotword names — `hotwords.py:220` |
| `listen_words` | `{name: engine}` | Yes | Engines with `listen: true` — `hotwords.py:238` |
| `wakeup_words` | `{name: engine}` | Yes | Engines with `wakeup: true` — `hotwords.py:231` |
| `stop_words` | `{name: engine}` | Yes | Engines with `stopword: true` — `hotwords.py:244` |
| `hot_words` | `{name: engine}` | Yes | Engines that are not listen, wakeup, or stop — `hotwords.py:251` |
| `plugins` | `[engine, ...]` | Yes | All loaded engine instances — `hotwords.py:226` |

---

## Configuration Reference

Each entry under `hotwords` in `mycroft.conf`:

```json
{
  "hotwords": {
    "hey_mycroft": {
      "module": "ovos-ww-plugin-precise-lite",
      "listen": true,
      "sound": "snd/start_listening.wav",
      "active": null
    },
    "wake_up": {
      "module": "ovos-ww-plugin-vosk",
      "wakeup": true,
      "active": null
    },
    "stop_recording": {
      "module": "ovos-ww-plugin-vosk",
      "stopword": true,
      "active": true
    },
    "hey_computer": {
      "module": "ovos-ww-plugin-precise-lite",
      "bus_event": "my.custom.event",
      "sound": "snd/ding.wav",
      "active": true
    },
    "hola_mycroft": {
      "module": "ovos-ww-plugin-precise-lite",
      "listen": true,
      "stt_lang": "es-es",
      "active": true
    }
  }
}
```

| Key | Type | Default | Description |
|---|---|---|---|
| `module` | `str` | — | OPM entry point name for the hotword plugin |
| `active` | `bool\|null` | `null` | `true` to load; auto-enabled for main WW and stand-up word |
| `listen` | `bool` | `false` | Triggers the VAD/STT recording pipeline |
| `wakeup` | `bool` | `false` | Exits sleep mode |
| `stopword` | `bool` | `false` | Ends free recording mode |
| `sound` | `str\|list` | — | Sound file played on detection |
| `bus_event` | `str` | — | Bus message type emitted on detection |
| `utterance` | `str` | — | Hard-coded utterance bypassing STT |
| `stt_lang` | `str` | global lang | Override STT language for the following command |

### Global Listening Sound

If `confirm_listening: true` is set in config and a listen word has no `sound`, the sound is taken from `sounds.start_listening` — `hotwords.py:165`.

---

## Sound Duration Detection — `hotwords.py:193`

When a hotword has a `sound` path, `get_sound_duration()` is called to determine the `CONFIRMATION` state duration. For paths starting with `"snd/"`, the path is resolved relative to the package `res/` directory.

---

## Testing Notes

Because `_plugins` and `_loaded` are class-level, test isolation requires resetting them:

```python
from threading import Event
from ovos_dinkum_listener.voice_loop.hotwords import HotwordContainer

def setUp(self):
    HotwordContainer._plugins = {}
    HotwordContainer._loaded = Event()
    self.container = HotwordContainer()
    # Manually set _loaded if you skip load_hotword_engines():
    HotwordContainer._loaded.set()
```

See `test/unittests/test_hotwords.py` for full test suite covering `CyclicAudioBuffer`, `HotwordContainer.found()`, `update()`, `reset()`, `shutdown()`, and `load_hotword_engines()`.
