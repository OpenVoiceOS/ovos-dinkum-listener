# ovos-dinkum-listener

`ovos-dinkum-listener` is the voice input daemon for [OpenVoiceOS](https://openvoiceos.com). It
continuously reads audio from the microphone, runs it through a deterministic finite-state machine
(wakeword → VAD → STT → utterance), and emits bus events that drive the intent pipeline.

---

## Responsibilities

| Responsibility | How |
|---|---|
| Microphone input | Reads raw audio chunks from a `Microphone` plugin via `OVOSMicrophoneFactory` |
| Wakeword / hotword detection | Routes chunks through `HotwordContainer` to detect listen words, hotwords, stop words, and wakeup words |
| Voice Activity Detection | Determines speech start/end boundaries using a `VADEngine` plugin |
| STT transcription | Streams accumulated audio to a `StreamingSTT` plugin (with optional fallback) |
| Audio transformation | Runs audio through `AudioTransformersService` before and after STT |
| Bus integration | Reports recording lifecycle, transcription results, handles remote listen/mute/sleep/state controls |
| Audio saving | Optionally saves utterance, hotword, and recording audio to disk with JSON metadata |

---

## Architecture

```
OVOSDinkumVoiceService (Thread)             service.py:84
    │
    ├── Microphone plugin               OVOSMicrophoneFactory.create()
    ├── HotwordContainer                voice_loop/hotwords.py:102
    │     ├── listen_words   (WW)
    │     ├── wakeup_words
    │     ├── stop_words
    │     └── hot_words
    ├── VADEngine plugin                OVOSVADFactory.create()
    ├── StreamingSTT + fallback STT     plugins.py
    ├── AudioTransformersService        transformers.py
    │
    └── DinkumVoiceLoop (FSM)           voice_loop/voice_loop.py:106
            │
            ├── PRE_WAKE_VAD        ← gate wakeword on speech presence (optional)
            ├── DETECT_WAKEWORD     ← feed audio to hotword engines
            ├── WAITING_CMD         ← continuous mode: accumulate audio until speech
            ├── CONFIRMATION        ← playing listen sound, no STT buffering yet
            ├── BEFORE_COMMAND      ← waiting for VAD to confirm speech started
            ├── IN_COMMAND          ← VAD confirmed, streaming audio to STT
            ├── AFTER_COMMAND       ← silence end: finalise STT, fire callbacks
            ├── RECORDING           ← free recording mode (stop-word or max-silence exits)
            ├── SLEEPING            ← suppressed, wakeup-word only
            └── CHECK_WAKE_UP       ← heard WW while sleeping, waiting for wakeup word
```

---

## Navigation

| Document | Contents |
|---|---|
| [voice-loop.md](voice-loop.md) | `DinkumVoiceLoop` FSM - states, modes, callbacks, timing, all config fields |
| [hotwords.md](hotwords.md) | `HotwordContainer`, hotword types, `HotwordState`, `CyclicAudioBuffer` |
| [service.md](service.md) | `OVOSDinkumVoiceService` - startup, bus events, config reload, audio saving |
| [transformers.md](transformers.md) | `AudioTransformersService` - feed methods, transform pipeline, plugin API |
| [plugins.md](plugins.md) | STT loading, `FakeStreamingSTT`, fallback STT, plugin introspection bus API |

---

## Quick Start

The service is started by `ovos-core` or standalone:

```bash
ovos-dinkum-listener
```

Or in Python:

```python
from ovos_dinkum_listener.service import OVOSDinkumVoiceService

service = OVOSDinkumVoiceService()
service.start()
service.join()
```

---

## Package Layout

```
ovos_dinkum_listener/
├── __main__.py             # Entry point: ovos-dinkum-listener
├── service.py              # OVOSDinkumVoiceService - main daemon thread
├── voice_loop/
│   ├── __init__.py         # Re-exports DinkumVoiceLoop, ListeningState, ListeningMode
│   ├── voice_loop.py       # DinkumVoiceLoop FSM, VoiceLoop base, ChunkInfo
│   └── hotwords.py         # HotwordContainer, HotwordState, CyclicAudioBuffer
├── transformers.py         # AudioTransformersService
├── plugins.py              # load_stt_module, FakeStreamingSTT, load_fallback_stt
└── _util.py                # _TemplateFilenameFormatter (filename templating for saved audio)
```

---

## Key Configuration (`mycroft.conf`)

### Listening Mode

| Key | Type | Default | Description |
|---|---|---|---|
| `listener.wake_word` | `str` | `"hey_mycroft"` | Primary wake word name (must match a key in `hotwords`) |
| `listener.stand_up_word` | `str` | `"wake_up"` | Word to exit sleep mode |
| `listener.continuous_listen` | `bool` | `false` | Enable continuous listening mode (no wakeword needed) |
| `listener.hybrid_listen` | `bool` | `false` | Listen continuously but also recognise hotwords |
| `listener.vad_pre_wake_enabled` | `bool` | `false` | Only activate wakeword engines when VAD detects speech |

### Timing

| Key | Type | Default | Description |
|---|---|---|---|
| `listener.speech_begin` | `float` | `0.3` | Seconds of VAD-confirmed speech before recording is active |
| `listener.silence_end` | `float` | `0.7` | Seconds of VAD-confirmed silence to end recording |
| `listener.recording_timeout` | `float` | `10.0` | Max total recording seconds |
| `listener.recording_timeout_with_silence` | `float` | `5.0` | Max pre-speech silence seconds after wakeword |
| `listener.recording_mode_max_silence` | `float` | `30.0` | Max silence seconds in free recording mode |

### STT Quality

| Key | Type | Default | Description |
|---|---|---|---|
| `listener.min_stt_confidence` | `float` | `0.6` | Minimum confidence to accept a transcript |
| `listener.max_transcripts` | `int` | `1` | Maximum alternative transcripts to emit |
| `listener.remove_silence` | `bool` | `false` | Use VAD to strip silence before STT finalisation |
| `listener.instant_listen` | `bool` | `true` | Skip confirmation sound delay before recording |

### Audio Saving

| Key | Type | Default | Description |
|---|---|---|---|
| `listener.save_utterances` | `bool` | `false` | Save STT audio + JSON metadata to disk |
| `listener.record_wake_words` | `bool` | `false` | Save hotword audio + JSON metadata to disk |
| `listener.save_path` | `str` | XDG data dir | Base directory for saved audio |
| `listener.utterance_filename` | `str` | `"{md5}-{uuid4}"` | Filename template for saved utterances |

### STT Plugins

| Key | Type | Default | Description |
|---|---|---|---|
| `stt.module` | `str` | - | OPM entry point name for the primary STT plugin |
| `stt.fallback_module` | `str` | - | OPM entry point name for fallback STT |

### Confirmation Sound

| Key | Type | Default | Description |
|---|---|---|---|
| `confirm_listening` | `bool` | `false` | Play a sound when wake word is detected |
| `sounds.start_listening` | `str` | - | Path or name of the listen-start sound |
| `sounds.end_listening` | `str` | - | Sound played when recording mode ends |

### Miscellaneous

| Key | Type | Default | Description |
|---|---|---|---|
| `listener.fake_barge_in` | `bool` | `false` | Lower speaker volume during recording |
| `listener.barge_in_volume` | `int` | `30` | Volume (0-100) during fake barge-in |
| `listener.mute_during_output` | `bool` | `false` | Mute mic while audio is playing |
| `filter_hallucinations` | `bool` | `true` | Remove known STT hallucinations from transcripts |
| `hallucination_list` | `list[str]` | `[...]` | Additional strings to filter from STT output |
| `secondary_langs` | `list[str]` | `[]` | Additional language codes accepted from language-detection transformers |

---

## Hotword Configuration Reference

Each entry under `hotwords` in `mycroft.conf`:

| Key | Type | Default | Description |
|---|---|---|---|
| `module` | `str` | - | OPM wake word plugin entry point |
| `active` | `bool\|null` | `null` | `true` to load, auto-enabled for main WW and stand-up word |
| `listen` | `bool` | `false` | Starts the STT recording flow |
| `wakeup` | `bool` | `false` | Exits sleep mode |
| `stopword` | `bool` | `false` | Ends free recording mode |
| `sound` | `str\|list` | - | Sound file (or list of choices) played on detection |
| `bus_event` | `str` | - | Bus message type emitted on detection |
| `utterance` | `str` | - | Hard-coded utterance to emit instead of running STT |
| `stt_lang` | `str` | - | Override STT language for commands following this word |

---

## Data Flow

```
mic.read_chunk()
       │
       ▼
DinkumVoiceLoop.run()                       voice_loop.py:205
       │
       ├─[PRE_WAKE_VAD]─── vad.is_silence() ──► transformers.feed_audio()
       │                         │ speech
       ├─[DETECT_WAKEWORD]─ hotwords.update() + found()
       │                       │ WW detected
       │                  listenword_audio_callback, wake_callback
       │                       │
       ├─[CONFIRMATION]─── wait for sound duration, then → BEFORE_COMMAND
       │
       ├─[BEFORE_COMMAND]─ vad.is_silence() ──► stt.stream_data()
       │                         │ speech_seconds elapsed
       ├─[IN_COMMAND]───── vad.is_silence() ──► stt.stream_data()
       │                       │ silence_seconds elapsed
       ├─[AFTER_COMMAND]── transformers.transform()
       │                   stt.transcribe()   [+ fallback]
       │                   stt_audio_callback, text_callback
       │                   ▼
       └──────────────── DETECT_WAKEWORD  (or WAITING_CMD in continuous/hybrid)
```

---

## Bus Event Quick Reference

### Emitted by `OVOSDinkumVoiceService`

| Event | When |
|---|---|
| `recognizer_loop:record_begin` | Recording starts (wakeword or programmatic listen) |
| `recognizer_loop:record_end` | Recording ends |
| `recognizer_loop:wakeword` | Listen word detected (`listen: true`) |
| `recognizer_loop:hotword` | Non-listen hotword detected |
| `recognizer_loop:wakeupword` | Wakeup word detected while sleeping |
| `recognizer_loop:stopword` | Stop word detected during recording |
| `recognizer_loop:utterance` | STT complete - `{"utterances": [...], "lang": "..."}` |
| `recognizer_loop:speech.recognition.unknown` | STT returned empty or hallucination-only result |
| `mycroft.awoken` | Voice loop exited sleep mode |
| `mycroft.audio.play_sound` | Play the listen-start confirmation sound |
| `mycroft.volume.set` | Lower/restore volume for fake barge-in |

### Handled by `OVOSDinkumVoiceService`

| Event | Effect |
|---|---|
| `mycroft.mic.listen` | Programmatic listen (bypasses wakeword) |
| `mycroft.mic.mute` / `unmute` / `mute.toggle` | Soft mute control |
| `recognizer_loop:sleep` | Enter sleep mode |
| `recognizer_loop:wake_up` | Exit sleep mode |
| `recognizer_loop:state.set` | Set listening state and/or mode |
| `recognizer_loop:state.get` | Reply with current state and mode |
| `recognizer_loop:record_stop` | Stop free recording mode |
| `recognizer_loop:b64_audio` | Inject base64-encoded audio as if from mic |
| `recognizer_loop:b64_transcribe` | Transcribe audio and return result on bus |
| `opm.stt.query` | Reply with installed STT plugin metadata |
| `opm.ww.query` | Reply with installed wake word plugin metadata |
| `opm.vad.query` | Reply with installed VAD plugin metadata |
| `ovos.languages.stt` | Reply with supported STT languages |

---

## Testing

Run the full test suite from the workspace root:

```bash
uv run pytest ovos-dinkum-listener/test/ \
    --cov=ovos_dinkum_listener \
    --cov-report=term-missing
```

Current coverage: **88%** (252 tests).

Test files:

| File | Covers |
|---|---|
| `test/unittests/test_util.py` | `_TemplateFilenameFormatter` |
| `test/unittests/test_hotwords.py` | `CyclicAudioBuffer`, `HotwordContainer` |
| `test/unittests/test_plugins.py` | `FakeStreamThread`, `FakeStreamingSTT`, STT loaders |
| `test/unittests/test_service.py` | `OVOSDinkumVoiceService` lifecycle |
| `test/unittests/test_service_extended.py` | Service handlers, callbacks, save methods |
| `test/unittests/test_transformers.py` | `AudioTransformersService` |
| `test/unittests/test_voice_loop.py` | `VoiceLoop`, `DinkumVoiceLoop` init/start/run |
| `test/unittests/test_voice_loop_methods.py` | All `DinkumVoiceLoop` FSM state methods |
