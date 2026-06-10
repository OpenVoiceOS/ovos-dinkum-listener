# OVOSDinkumVoiceService

**Module:** `ovos_dinkum_listener.service`
**Class:** `OVOSDinkumVoiceService` — `service.py:84`

Top-level daemon for `ovos-dinkum-listener`. A `Thread` subclass that owns all sub-components, manages the voice loop lifecycle, handles all bus events, and persists audio to disk.

---

## Constructor — `service.py:84`

```python
OVOSDinkumVoiceService(
    on_ready=None, on_error=None, on_stopping=None, on_alive=None, on_started=None,
    watchdog=lambda: None,
    mic=None,
    bus=None,
    validate_source=True,
    stt=None,
    fallback_stt=None,
    vad=None,
    hotwords=None,
    disable_fallback=False
)
```

| Parameter | Description |
|---|---|
| `on_ready` / `on_error` / `on_stopping` / `on_alive` / `on_started` | `ProcessStatus` lifecycle callbacks |
| `watchdog` | Called every `WATCHDOG_DELAY` (0.5s) for systemd watchdog keepalive |
| `mic` | Pre-created `Microphone`; loaded via `OVOSMicrophoneFactory` if `None` |
| `bus` | `MessageBusClient`; created automatically if `None` |
| `validate_source` | If `True`, only handle `mycroft.mic.listen` from native audio destinations |
| `stt` | Pre-created `StreamingSTT`; disables auto-reload on config change if provided |
| `fallback_stt` | Pre-created fallback `StreamingSTT` |
| `vad` | Pre-created `VADEngine`; loaded via `OVOSVADFactory` if `None` |
| `hotwords` | Pre-created `HotwordContainer`; disables auto-reload on config change if provided |
| `disable_fallback` | If `True`, never load the fallback STT plugin |

Default microphone plugin: `ovos-microphone-plugin-alsa`.

---

## Startup Sequence

```
Thread.__init__()
  └── _before_start()
        ├── connect to MessageBus
        └── register config change watcher

Thread.run()  [OVOSDinkumVoiceService.run()]
  ├── _start()
  │     ├── mic.start()
  │     ├── hotwords.load_hotword_engines()
  │     └── register_event_handlers()   ← all bus events wired here
  ├── status.set_ready()                ← emits mycroft.ready
  └── voice_loop.run()                  ← blocks until stop()
```

After `voice_loop.run()` returns:
- `mic.stop()`
- `hotwords.shutdown()`
- `stt.shutdown()` / `fallback_stt.shutdown()`
- `transformers.shutdown()`

---

## Voice Loop Initialisation — `_init_voice_loop()`

Creates `DinkumVoiceLoop` with all callbacks wired to service methods. Key bindings:

| Loop callback | Service method |
|---|---|
| `wake_callback` | `_on_wake` → emits `recognizer_loop:record_begin` |
| `text_callback` | `_stt_text` → filters hallucinations, emits `recognizer_loop:utterance` |
| `stt_audio_callback` | `_stt_audio` → optionally calls `_save_stt` |
| `recording_audio_callback` | `_recording_audio` → optionally calls `_save_recording` |
| `listenword_audio_callback` | `_hotword_audio` → emits WW bus events, optionally saves WW audio |
| `hotword_audio_callback` | `_hotword_audio` |
| `stopword_audio_callback` | `_hotword_audio` |
| `wakeupword_audio_callback` | `_hotword_audio` |
| `record_end_callback` | `_on_record_end` → emits `recognizer_loop:record_end` |
| `chunk_callback` | `_on_chunk` → watchdog keepalive |

---

## Configuration Reload

`reload_configuration()` is called on `configuration.updated` bus events. It computes MD5 hashes of four config sections and only reloads the components whose hash changed:

| Config section | Hash key | Effect |
|---|---|---|
| listener loop config | `loop` | Rebuild `DinkumVoiceLoop` |
| hotwords config | `hotwords` | Reload hotword engines |
| STT config | `stt` | Reload primary STT plugin |
| Fallback STT config | `fallback` | Reload fallback STT plugin |

If `stt` or `hotwords` were passed as constructor arguments, they are not reloaded on config change.

---

## Source Validation — `_validate_message_context(message)`

Guards `mycroft.mic.listen` so that only messages targeted at native audio sources are processed. Native sources are configured via `Audio.native_sources` (default: `["debug_cli", "audio"]`).

Messages with no `destination` in context are treated as broadcasts and always accepted.

Set `validate_source=False` in the constructor to disable this check.

---

## Hallucination Filtering — `_stt_text()`

After STT, transcripts are filtered using a block list. Enabled by `filter_hallucinations: true` (default: `true`). Additional strings can be added via `hallucination_list` in config.

Default filtered strings include: `"thanks for watching!"`, `"so"`, `"beep!"`, and others.

Filtering behaviour by mode:
- `WAKEWORD` mode: empty result emits `recognizer_loop:speech.recognition.unknown`
- `CONTINUOUS` mode: empty result is silently ignored (no bus event)

---

## Fake Barge-In

When `listener.fake_barge_in: true`, the service:
1. Lowers speaker volume to `listener.barge_in_volume` (default: `30`) via `mycroft.volume.set` at `record_begin`
2. Restores previous volume at `record_end`

This simulates hardware echo cancellation for systems without it.

Config: `listener.mute_during_output: true` mutes the mic entirely during TTS playback (`recognizer_loop:audio_output_start`).

---

## Audio Saving

### Save Path — `default_save_path` (property)

Base path is `listener.save_path` or `{XDG_DATA_DIR}/listener/`. Three subdirectories:
- `utterances/` — STT audio
- `wake_words/` — hotword audio
- `recordings/` — free recording audio

`default_save_path` is a **read-only property** — it cannot be assigned directly.

### `_save_stt(stt_meta, save_path=None)`

Saves utterance audio as WAV and a JSON sidecar when `listener.save_utterances: true`. Filename uses the template from `listener.utterance_filename` (default: `"{md5}-{uuid4}"`).

`stt_meta["transcriptions"][0][0]` is used as the transcript for the MD5 hash. Raises `IndexError` (caught internally) if the list is empty.

### `_save_ww(ww_meta, save_path=None)`

Saves hotword/listen-word audio when `listener.record_wake_words: true`. Written to the `wake_words/` subdirectory.

### `_save_recording(audio_bytes, ctx, save_path=None)`

Saves free recording audio to the `recordings/` subdirectory.

---

## `_compile_ww_context(key_phrase, ww_module)` — static method

Returns a wakeword context dict for the bus event:

```python
{
    "name": "hey_mycroft",
    "engine": "<md5 hash of ww_module string>",
    "time": "<epoch string>",
    "sessionId": "<session id>",
    "accountId": "0",
    "model": str(hash_sentence(key_phrase)),
}
```

The `"engine"` field is an MD5 hex digest of the module name string, not the class name.

---

## `_hotword_audio(audio, ww_data)` — callback

Called for all hotword types (listen, stop, wakeup, hotword). The `ww_data["event"]` key (if set) is used as the custom bus event type. Note: the key is `"event"`, not `"bus_event"` — the internal plugin record uses `"bus_event"` but `get_ww()` maps it to `"event"` in the returned dict.

---

## Bus Events Handled

| Event | Handler | Effect |
|---|---|---|
| `mycroft.mic.mute` | `_handle_mute` | Sets `voice_loop.is_muted = True` |
| `mycroft.mic.unmute` | `_handle_unmute` | Sets `voice_loop.is_muted = False` |
| `mycroft.mic.mute.toggle` | `_handle_mute_toggle` | Toggles `is_muted` |
| `mycroft.mic.listen` | `_handle_listen` | Programmatic listen trigger (validates source) |
| `mycroft.mic.get_status` | `_handle_mic_get_status` | Replies with `{"muted": bool}` |
| `mycroft.stop` | `_handle_stop` | Calls `voice_loop.stop_recording()` |
| `recognizer_loop:sleep` | `_handle_sleep` | Calls `voice_loop.go_to_sleep()` |
| `recognizer_loop:wake_up` | `_handle_wake_up` | Calls `voice_loop.wakeup()` |
| `recognizer_loop:audio_output_start` | `_handle_audio_start` | Mutes mic if `mute_during_output` |
| `recognizer_loop:audio_output_end` | `_handle_audio_end` | Unmutes mic after TTS |
| `recognizer_loop:b64_transcribe` | `_handle_b64_transcribe` | Decodes audio, runs STT, emits result |
| `recognizer_loop:b64_audio` | `_handle_b64_audio` | Injects decoded audio as mic input |
| `recognizer_loop:record_stop` | `_handle_stop_recording` | Calls `voice_loop.stop_recording()` |
| `recognizer_loop:state.set` | `_handle_change_state` | Sets `listen_mode` and/or `state` |
| `recognizer_loop:state.get` | `_handle_get_state` | Replies with current mode and state |
| `intent.service.skills.activated` | `_handle_extend_listening` | Extends listen timeout |
| `ovos.languages.stt` | `_handle_get_languages_stt` | Replies with supported STT languages |
| `opm.stt.query` | `_handle_opm_stt_query` | Replies with STT plugin metadata |
| `opm.ww.query` | `_handle_opm_ww_query` | Replies with wake word plugin metadata |
| `opm.vad.query` | `_handle_opm_vad_query` | Replies with VAD plugin metadata |
| `mycroft.audio.play_sound.response` | `_handle_sound_played` | Notified when confirmation sound finishes |
| `volume.set.percent` / `mycroft.volume.*` | `_handle_volume_change` | Tracks volume for fake barge-in restore |

## Bus Events Emitted

| Event | When | Payload |
|---|---|---|
| `recognizer_loop:record_begin` | Recording starts | `{}` |
| `recognizer_loop:record_end` | Recording ends | `{}` |
| `recognizer_loop:wakeword` | Listen-type WW detected | `{"utterance": ww_name, ...}` |
| `recognizer_loop:hotword` | Non-listen hotword detected | `{"utterance": ww_name, ...}` |
| `recognizer_loop:wakeupword` | Wakeup word detected | `{"utterance": ww_name, ...}` |
| `recognizer_loop:stopword` | Stop word detected | `{"utterance": ww_name, ...}` |
| `recognizer_loop:utterance` | STT complete | `{"utterances": [...], "lang": "..."}` |
| `recognizer_loop:speech.recognition.unknown` | STT returned empty (WAKEWORD mode) | `{}` |
| `mycroft.awoken` | Voice loop exited sleep mode | `{}` |
| `mycroft.audio.play_sound` | Play the listen confirmation sound | `{"uri": "..."}` |
| `mycroft.volume.set` | Lower/restore volume for fake barge-in | `{"percent": 30}` |

---

## OPM Introspection Handlers

Three handlers respond to plugin metadata queries on the bus:

| Event | Handler | OPM functions called |
|---|---|---|
| `opm.stt.query` | `_handle_opm_stt_query` | `get_stt_supported_langs`, `get_stt_lang_configs`, `get_stt_module_configs` |
| `opm.ww.query` | `_handle_opm_ww_query` | `get_ww_supported_langs`, `get_ww_lang_configs`, `get_ww_module_configs` |
| `opm.vad.query` | `_handle_opm_vad_query` | `get_vad_configs` |

Response format for STT/WW queries:

```json
{
    "plugins": {"en-us": ["plugin-name", ...]},
    "langs":   ["en-us", "de-de", ...],
    "configs": {"plugin-name": {"en-us": [...]}},
    "options": {"en-us": [{"engine": "plugin-name", "offline": true, ...}]}
}
```

---

## `_handle_change_state(message)` — `recognizer_loop:state.set`

Accepts a message with `data` keys:
- `"mode"` → sets `voice_loop.listen_mode` (e.g. `"wakeword"`, `"continuous"`, `"hybrid"`)
- `"state"` → sets `voice_loop.state` directly

If only `"mode"` is given, the FSM state is also reset to the appropriate default via `reset_state()`.

---

## `_handle_b64_transcribe(message)` — `recognizer_loop:b64_transcribe`

1. Decodes base64 audio from `message.data["audio"]`
2. Runs through `transformers.transform()`
3. Transcribes with `stt.transcribe()` (and fallback if needed)
4. Emits result as `recognizer_loop:b64_transcribe.response`

---

## Testing Notes

`OVOSDinkumVoiceService` can be unit-tested without a real bus or audio device. The recommended pattern:

```python
from unittest.mock import Mock, patch, MagicMock

with patch("ovos_dinkum_listener.service.OVOSMicrophoneFactory"), \
     patch("ovos_dinkum_listener.service.OVOSVADFactory"), \
     patch("ovos_dinkum_listener.service.load_stt_module"), \
     patch("ovos_dinkum_listener.service.load_fallback_stt"):
    service = OVOSDinkumVoiceService(bus=FakeBus())
    service.voice_loop = Mock()  # replace FSM with mock
```

See `test/unittests/test_service.py` and `test/unittests/test_service_extended.py` for lifecycle and handler tests.
