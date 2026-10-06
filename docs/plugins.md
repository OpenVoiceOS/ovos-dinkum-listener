# STT Plugins

**Module:** `ovos_dinkum_listener.plugins`
**Source:** `ovos_dinkum_listener/plugins.py`

Helpers for loading STT plugins and adapting non-streaming plugins to the `StreamingSTT` interface required by `DinkumVoiceLoop`.

---

## `load_stt_module(config=None)` → `StreamingSTT` - `plugins.py:81`

Load the primary STT plugin from configuration.

```python
from ovos_dinkum_listener.plugins import load_stt_module

stt = load_stt_module()                          # reads mycroft.conf["stt"]
stt = load_stt_module({"module": "ovos-stt-plugin-whisper", ...})
```

Behaviour - `plugins.py:88`:
1. Uses `config or Configuration()["stt"]`
2. Sets `stt_config["lang"]` to global `lang` if not already set
3. Calls `OVOSSTTFactory.create(stt_config)`
4. If the result is **not** a `StreamingSTT` → wraps in `FakeStreamingSTT` - `plugins.py:96`
5. Returns the `StreamingSTT` (native or wrapped)

---

## `load_fallback_stt(cfg=None)` → `Optional[StreamingSTT]` - `plugins.py:102`

Load the fallback STT plugin.

```python
from ovos_dinkum_listener.plugins import load_fallback_stt

fallback = load_fallback_stt()
```

Returns `None` if:
- `stt.fallback_module` is not configured or is empty - `plugins.py:111`
- Plugin instantiation raises any exception - `plugins.py:124`

Config resolution:
1. Uses `cfg or Configuration()["stt"]`
2. Reads `cfg["fallback_module"]` as the module name
3. Reads `cfg[fallback_module]` as the per-plugin config dict
4. Sets `config["lang"]` to global lang if not present
5. Calls `OVOSSTTFactory.create({"module": fbm, fbm: config})`
6. Wraps non-`StreamingSTT` result in `FakeStreamingSTT`

---

## `FakeStreamingSTT` - `plugins.py:47`

Adapter that wraps a regular (non-streaming) `STT` plugin inside the `StreamingSTT` interface. All audio chunks are buffered internally, the underlying plugin is invoked once at transcription time.

```python
from ovos_dinkum_listener.plugins import FakeStreamingSTT

streaming = FakeStreamingSTT(engine=non_streaming_stt_instance, config={})
```

This is used transparently by `load_stt_module()` and `load_fallback_stt()`.

### Constructor - `plugins.py:48`

| Parameter | Description |
|---|---|
| `engine` | A non-streaming `STT` plugin instance |
| `config` | Optional config dict passed to `StreamingSTT.__init__` |

### `create_streaming_thread()` - `plugins.py:52`

Creates and returns a `FakeStreamThread`. Reads `listener.sample_rate` (default `16000`) and `listener.sample_width` (default `2`) from `Configuration`.

Requires `self.queue` to be set before calling (done by `stream_start()` in the parent class).

### `transcribe(audio=None, lang=None)` → `List[Tuple[str, float]]` - `plugins.py:59`

| `audio` value | Behaviour |
|---|---|
| `None` | Reads from `self.stream.buffer.read()`, clears buffer - `plugins.py:64` |
| `bytes` | Wraps in `AudioData` using stream sample rate/width - `plugins.py:69` |
| `AudioData` | Passes directly to `engine.transcribe()` - `plugins.py:73` |
| any other type | Raises `ValueError` - `plugins.py:76` |

---

## `FakeStreamThread` - `plugins.py:11`

Internal streaming thread used by `FakeStreamingSTT`. Buffers audio in a `ReadWriteStream` and invokes the real STT engine at the end.

```python
from queue import Queue
from ovos_dinkum_listener.plugins import FakeStreamThread

thread = FakeStreamThread(Queue(), "en-us", engine, sample_rate=16000, sample_width=2)
thread.update(audio_bytes)     # buffer audio
result = thread.finalize()     # transcribe and return string
```

### Constructor - `plugins.py:13`

| Parameter | Type | Description |
|---|---|---|
| `queue` | `Queue` | Parent `StreamThread` queue |
| `language` | `str` | BCP-47 language code |
| `engine` | `STT` | Non-streaming STT plugin instance |
| `sample_rate` | `int` | Audio sample rate |
| `sample_width` | `int` | Audio sample width in bytes |

Creates `self.buffer = ReadWriteStream()`.

### `update(chunk)` - `plugins.py:43`

Writes `chunk` to `self.buffer`. Called by `stream_data()` via the parent class.

### `handle_audio_stream(audio, language)` - `plugins.py:39`

Iterates over `audio` (an iterable of byte chunks) and calls `update()` for each. Used when feeding a complete audio stream at once.

### `finalize()` → `Optional[str]` - `plugins.py:20`

Transcription finalisation:

1. Returns `""` immediately if `self.buffer` is empty (falsy) - `plugins.py:23`
2. Reads all buffered audio via `self.buffer.read()`
3. Wraps in `AudioData(raw, sample_rate, sample_width)`
4. Calls `self.engine.execute(audio_data, self.language)`
5. Clears the buffer
6. Returns the transcript string
7. On any exception: logs and returns `None` - `plugins.py:35`

Note: `finalize()` returns:
- `""` - buffer was empty (no audio received)
- `str` - successful transcription
- `None` - engine raised an exception

---

## STT Configuration Reference

```json
{
  "stt": {
    "module": "ovos-stt-plugin-whisper",
    "ovos-stt-plugin-whisper": {
      "model": "base",
      "lang": "en-us"
    },
    "fallback_module": "ovos-stt-plugin-server",
    "ovos-stt-plugin-server": {
      "url": "https://stt.openvoiceos.com/stt"
    }
  }
}
```

| Key | Description |
|---|---|
| `stt.module` | Entry point name of the primary STT plugin |
| `stt.<module>` | Per-plugin configuration dict |
| `stt.fallback_module` | Entry point name for the fallback STT plugin |
| `stt.<fallback_module>` | Per-plugin configuration dict for fallback |

---

## Plugin Introspection

`OVOSDinkumVoiceService` responds to `opm.stt.query` with metadata about all installed STT plugins. Response format:

```json
{
  "plugins": {"en-us": ["ovos-stt-plugin-whisper", ...]},
  "langs":   ["en-us", "de-de", ...],
  "configs": {"ovos-stt-plugin-whisper": {"en-us": [...]}},
  "options": {"en-us": [{"engine": "ovos-stt-plugin-whisper", "offline": true}]}
}
```

---

## How `FakeStreamingSTT` Integrates with `DinkumVoiceLoop`

The voice loop interfaces with STT through three method calls:

| Call site | Loop state | Method |
|---|---|---|
| `_detect_ww()` - `voice_loop.py:567` | WW detected | `stt.stream_start()` |
| `_before_cmd()` / `_in_cmd()` | BEFORE_COMMAND / IN_COMMAND | `stt.stream_data(chunk)` |
| `_after_cmd()` via `_get_tx()` | AFTER_COMMAND | `stt.transcribe(lang=lang)` |

When `remove_silence: true` and the STT is a `FakeStreamingSTT`, the loop also:
- Reads and trims `stt.stream.buffer` directly via `_vad_remove_silence()` - `voice_loop.py:810`

---

## Testing Notes

```python
from unittest.mock import Mock
from queue import Queue
from ovos_dinkum_listener.plugins import FakeStreamThread, FakeStreamingSTT

# FakeStreamThread
engine = Mock()
engine.execute.return_value = "hello"
thread = FakeStreamThread(Queue(), "en-us", engine, 16000, 2)
thread.update(b'\x00' * 100)
assert thread.finalize() == "hello"

# FakeStreamingSTT - set queue before create_streaming_thread()
with patch("ovos_dinkum_listener.plugins.Configuration") as mock_cfg:
    mock_cfg.return_value = {"listener": {"sample_rate": 16000, "sample_width": 2}}
    stt = FakeStreamingSTT(engine=Mock(), config={})
    stt.queue = Queue()
    thread = stt.create_streaming_thread()
    assert isinstance(thread, FakeStreamThread)
```

See `test/unittests/test_plugins.py` for full test coverage.

---
[← Transformers](transformers.md) · [Home](index.md)
