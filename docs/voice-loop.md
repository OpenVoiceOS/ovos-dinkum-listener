# Voice Loop

**Module:** `ovos_dinkum_listener.voice_loop.voice_loop`
**Source:** `ovos_dinkum_listener/voice_loop/voice_loop.py`

The `DinkumVoiceLoop` is the core finite-state machine (FSM) of `ovos-dinkum-listener`. It reads raw audio chunks from the microphone and orchestrates wakeword detection, VAD-based speech segmentation, and STT transcription.

---

## Enums

### `ListeningMode` — `voice_loop.py:49`

Global operating mode. Set once at startup from config. Controls which FSM states are reachable.

| Value | Config trigger | Description |
|---|---|---|
| `WAKEWORD` | default | Only listen after a wake word is detected |
| `CONTINUOUS` | `listener.continuous_listen: true` | Always listen; no wake word required |
| `HYBRID` | `listener.hybrid_listen: true` | Always listen but also detect hotwords |
| `SLEEPING` | programmatic | Suppressed; only wakeup words are checked |

### `ListeningState` — `voice_loop.py:32`

Internal FSM state. Transitions on every audio chunk in `DinkumVoiceLoop.run()`.

| State | String value | Description |
|---|---|---|
| `PRE_WAKE_VAD` | `"pre_wake_vad"` | Gate wakeword detection on VAD speech presence |
| `DETECT_WAKEWORD` | `"wakeword"` | Feed audio to hotword engines waiting for a listen word |
| `WAITING_CMD` | `"continuous"` | Continuous mode: accumulate audio until speech begins |
| `CONFIRMATION` | `"confirmation"` | Playing listen sound; no STT buffering yet |
| `BEFORE_COMMAND` | `"before_cmd"` | Waiting for VAD to confirm speech started |
| `IN_COMMAND` | `"in_cmd"` | VAD confirmed speech; streaming audio to STT |
| `AFTER_COMMAND` | `"after_cmd"` | Silence end detected; finalise STT and fire callbacks |
| `RECORDING` | `"recording"` | Free recording mode; exits on stop word or max silence |
| `SLEEPING` | `"sleeping"` | Suppressed; only wakeup words are active |
| `CHECK_WAKE_UP` | `"wake_up"` | Wake word heard while sleeping; waiting for wakeup word |

---

## `VoiceLoop` — `voice_loop.py:58`

Abstract base `@dataclass` holding the five required plugin references. Subclasses must implement `start()`, `run()`, and `stop()`.

| Field | Type | Description |
|---|---|---|
| `mic` | `Microphone` | Microphone plugin — source of raw audio chunks |
| `hotwords` | `HotwordContainer` | Wake word / hotword engine manager |
| `stt` | `StreamingSTT` | Primary STT plugin |
| `fallback_stt` | `StreamingSTT` | Fallback STT plugin (may be `None`) |
| `vad` | `VADEngine` | Voice activity detection plugin |
| `transformers` | `AudioTransformersService` | Audio transformer pipeline |

### `VoiceLoop.debiased_energy(audio_data, sample_width)` — `voice_loop.py:76`

Static method. Computes the RMS energy of an audio chunk after subtracting the mean (DC bias removal). Uses `audioop.rms` twice: once to get the bias value, once to compute RMS of the bias-corrected signal.

A DC-biased signal (constant value) returns `0` energy even if the raw bytes are non-zero.

---

## `DinkumVoiceLoop` — `voice_loop.py:106`

Concrete FSM implementation. A `@dataclass` that extends `VoiceLoop`.

```python
from ovos_dinkum_listener.voice_loop import DinkumVoiceLoop, ListeningMode, ListeningState
```

### Fields

| Field | Type | Default | Description |
|---|---|---|---|
| `speech_seconds` | `float` | `0.3` | Seconds of VAD-confirmed speech required before `IN_COMMAND` |
| `silence_seconds` | `float` | `0.7` | Seconds of VAD-confirmed silence required to end recording |
| `timeout_seconds` | `float` | `10.0` | Max total recording seconds (from `BEFORE_COMMAND`) |
| `timeout_seconds_with_silence` | `float` | `5.0` | Max pre-speech silence seconds after wakeword |
| `confirmation_seconds` | `float` | `0.5` | Duration to stay in `CONFIRMATION` state (overridden by sound file length) |
| `num_stt_rewind_chunks` | `int` | `2` | Chunks before the WW to rewind into the STT stream |
| `num_hotword_keep_chunks` | `int` | `15` | Chunks retained for `listenword_audio_callback` |
| `remove_silence` | `bool` | `False` | Strip silence from STT buffer using VAD before transcription |
| `instant_listen` | `bool` | `False` | Skip `CONFIRMATION` delay and begin recording immediately |
| `min_stt_confidence` | `float` | `0.6` | Minimum confidence to accept a transcript |
| `max_transcripts` | `int` | `1` | Max alternative transcripts passed to `text_callback` |
| `recording_mode_max_silence_seconds` | `float` | `30.0` | Max silence in `RECORDING` mode before auto-stop |
| `state` | `ListeningState` | `DETECT_WAKEWORD` | Current FSM state |
| `listen_mode` | `ListeningMode` | `WAKEWORD` | Global listening mode |
| `is_muted` | `bool` | `False` | If `True`, zero-fill chunks (soft mute) |
| `vad_pre_wake_enabled` | `bool` | from config | Enable the `PRE_WAKE_VAD` state |

Internal timer fields (countdown per chunk): `speech_seconds_left`, `silence_seconds_left`, `confirmation_seconds_left`, `timeout_seconds_left`, `timeout_seconds_with_silence_left`, `recording_seconds_with_silence_left`.

Internal audio buffers: `hotword_chunks` (deque, maxlen=`num_hotword_keep_chunks`), `stt_chunks` (deque, maxlen varies by mode), `stt_audio_bytes` (accumulated raw bytes).

### Callbacks — `voice_loop.py:132`

All callbacks are wired by `OVOSDinkumVoiceService._init_voice_loop()`.

| Callback | Signature | Fires when |
|---|---|---|
| `wake_callback` | `() → None` | Wake word detected or `start_recording()` called — triggers `recognizer_loop:record_begin` |
| `wakeup_callback` | `() → None` | Wakeup word confirmed — triggers `mycroft.awoken` |
| `text_callback` | `(utts: List[Tuple[str, float]], ctx: dict) → None` | STT complete; `utts` is a ranked list of `(transcript, confidence)` |
| `stt_audio_callback` | `(audio: bytes, ctx: dict) → None` | After `AFTER_COMMAND`, with raw utterance audio |
| `recording_audio_callback` | `(audio: bytes, metadata: dict) → None` | After `stop_recording()`, with full recording audio |
| `listenword_audio_callback` | `(audio: bytes, ww_data: dict) → None` | Listen word detected (includes hotword audio window) |
| `hotword_audio_callback` | `(audio: bytes, ww_data: dict) → None` | Non-listen hotword detected |
| `stopword_audio_callback` | `(audio: bytes, ww_data: dict) → None` | Stop word detected during `RECORDING` |
| `wakeupword_audio_callback` | `(audio: bytes, ww_data: dict) → None` | Wakeup word detected while sleeping |
| `record_end_callback` | `() → None` | Recording ended — triggers `recognizer_loop:record_end` |
| `chunk_callback` | `(ChunkInfo) → None` | Called on every audio chunk regardless of state |

---

## State Machine — Normal Wakeword Flow

```
PRE_WAKE_VAD  (if vad_pre_wake_enabled)
  │  VAD detects speech → keep up to 5 prior chunks, transition
  ▼
DETECT_WAKEWORD
  │  listen word found
  ├─ ww_data has sound  →  CONFIRMATION ─► BEFORE_COMMAND
  └─ no sound           →  BEFORE_COMMAND
                              │  speech_seconds of VAD speech
                              ▼
                          IN_COMMAND
                              │  silence_seconds of VAD silence
                              │  (or timeout_seconds elapsed)
                              ▼
                          AFTER_COMMAND
                              │  transformers.transform()
                              │  _get_tx() / fallback STT
                              │  text_callback + stt_audio_callback
                              ▼
                          DETECT_WAKEWORD  (WAITING_CMD if CONTINUOUS/HYBRID)
```

---

## State Machine — Continuous/Hybrid Flow

```
WAITING_CMD
  │  speech_seconds of VAD speech detected
  │  (hotwords checked on silence chunks)
  ▼
IN_COMMAND → AFTER_COMMAND → WAITING_CMD
```

---

## State Machine — Sleep Flow

```
SLEEPING
  │  listen word detected  →  CHECK_WAKE_UP
  │
CHECK_WAKE_UP
  ├─  wakeup word detected  →  DETECT_WAKEWORD  (+ wakeup_callback)
  └─  10s elapsed with no wakeup word  →  SLEEPING
```

---

## State Machine — Recording Mode

```
RECORDING  (entered via start_recording())
  │  stop word detected    →  stop_recording()  →  reset_state()
  │  max silence elapsed   →  stop_recording()  →  reset_state()
```

---

## Method Reference

### `DinkumVoiceLoop.start()` — `voice_loop.py:164`

Reads `listener.continuous_listen` and `listener.hybrid_listen` from config to set `listen_mode`. Sets initial `state` (`PRE_WAKE_VAD` if enabled, else `DETECT_WAKEWORD`). Resets `last_ww` timer.

### `DinkumVoiceLoop.run()` — `voice_loop.py:205`

Main loop. Reads from `mic.read_chunk()` until `stop()` is called. On each chunk:
1. Zero-fills the chunk if `is_muted` — `voice_loop.py:243`
2. Resets `_chunk_info.is_speech` and `energy` — `voice_loop.py:247`
3. Dispatches to the appropriate FSM handler
4. Calls `chunk_callback(ChunkInfo)` with RMS energy — `voice_loop.py:312`

State transitions during `DETECT_WAKEWORD`:
- `CONTINUOUS` mode → immediately transition to `WAITING_CMD`
- else check `_detect_ww()` then `_detect_hot()` then feed transformers
- if `vad_pre_wake_enabled` and no WW within 5s → return to `PRE_WAKE_VAD` — `voice_loop.py:276`

### `DinkumVoiceLoop._pre_wake_vad(chunk)` — `voice_loop.py:190`

In `PRE_WAKE_VAD` state. Calls `vad.is_silence()`. On speech detected: transitions to `DETECT_WAKEWORD`, records `_vad_window_start`, appends chunk to `hotword_chunks`. On silence: feeds `transformers.feed_audio()`.

### `DinkumVoiceLoop._detect_ww(chunk)` — `voice_loop.py:512`

1. Appends chunk to both `hotword_chunks` and `stt_chunks`
2. Calls `hotwords.update(chunk)` with state `LISTEN`
3. Calls `hotwords.found()` → returns WW name or `None`
4. On detection: calls `hotwords.verify()` with the accumulated wake-word audio; if any verifier plugin rejects it, the detection is discarded and the method returns `False`
5. Fires `listenword_audio_callback` with accumulated `hotword_chunks`
6. Fires `wake_callback`
7. If sleeping → `CHECK_WAKE_UP`; else if sound → `CONFIRMATION`; else → `BEFORE_COMMAND`
8. Starts STT stream: `stt.stream_start()` (and `fallback_stt.stream_start()`)
9. Returns `True` if detected

### `DinkumVoiceLoop._confirmation_sound(chunk)` — `voice_loop.py:613`

In `CONFIRMATION` state. If `instant_listen=True`, immediately transitions to `BEFORE_COMMAND` and calls `_before_cmd()`. Otherwise, decrements `confirmation_seconds_left` and transitions to `BEFORE_COMMAND` when it reaches zero.

### `DinkumVoiceLoop._before_cmd(chunk)` — `voice_loop.py:631`

In `BEFORE_COMMAND` state. Streams every chunk from `stt_chunks` into `stt.stream_data()`. Decrements `timeout_seconds_with_silence_left`; if expired → `AFTER_COMMAND`. Checks VAD: on `speech_seconds` of speech → `IN_COMMAND`.

### `DinkumVoiceLoop._in_cmd(chunk)` — `voice_loop.py:678`

In `IN_COMMAND` state. Feeds `transformers.feed_speech()`. Streams audio to `stt.stream_data()`. Checks VAD: on `silence_seconds` of silence → `AFTER_COMMAND`. If `timeout_seconds_left` reaches zero → `AFTER_COMMAND`.

### `DinkumVoiceLoop._after_cmd(chunk)` — `voice_loop.py:818`

In `AFTER_COMMAND` state. Sequence:
1. `transformers.transform(chunk)` → `(chunk, stt_context)` — `voice_loop.py:827`
2. If `isinstance(stt, FakeStreamingSTT) and remove_silence` → `_vad_remove_silence()` — `voice_loop.py:828`
3. `_get_tx(stt_context)` → `(utts, stt_context)` — `voice_loop.py:831`
4. `stt_audio_callback(stt_audio_bytes, stt_context)` — `voice_loop.py:838`
5. `record_end_callback()` — `voice_loop.py:843`
6. `text_callback(utts, stt_context)` — `voice_loop.py:848`
7. Transition to `DETECT_WAKEWORD` or `WAITING_CMD` — `voice_loop.py:851`
8. `hotwords.reset()` — `voice_loop.py:862`
9. Optional `vad.reset()` — `voice_loop.py:866`

### `DinkumVoiceLoop._get_tx(stt_context)` — `voice_loop.py:739`

Attempts transcription:
1. If `stt_context["stt_lang"]` present → `_validate_lang()` — updates `stt.stream.language`
2. `stt.transcribe(lang=lang)` → list of `(text, confidence)` tuples
3. If empty and `fallback_stt` present → `fallback_stt.transcribe(lang=lang)`
4. Filter below `min_stt_confidence` (always keep at least 1)
5. Truncate to `max_transcripts`
6. Returns `(filtered, stt_context)` with `stt_context["transcriptions"]` populated

### `DinkumVoiceLoop._validate_lang(lang)` — `voice_loop.py:717`

Validates a language code against `config["lang"]` + `config["secondary_langs"]`. Comparison uses only the primary subtag (e.g. `"en"` from `"en-us"`). Returns validated lang or the default if unknown.

### `DinkumVoiceLoop._vad_remove_silence()` — `voice_loop.py:792`

Only executes when `isinstance(self.stt, FakeStreamingSTT) and self.remove_silence`. Calls `vad.extract_speech(stt_audio_bytes)`. If the result is ≥1 second, replaces `stt.stream.buffer` with the trimmed audio. Skips if the original recording is under 1 second.

### `DinkumVoiceLoop._wait_cmd(chunk)` — `voice_loop.py:578`

In `WAITING_CMD` state (continuous mode). Checks VAD. On speech: decrements `speech_seconds_left`; when exhausted → starts STT stream and transitions to `IN_COMMAND`. On silence: resets `speech_seconds_left`, checks for hotwords via `_detect_hot()`, feeds `transformers.feed_audio()`.

### `DinkumVoiceLoop._in_recording(chunk)` — `voice_loop.py:382`

In `RECORDING` state. Checks for stop words via `hotwords.found()`. On stop word: calls `stop_recording()`, fires `stopword_audio_callback`. Otherwise: checks VAD and accumulates audio; decrements `recording_seconds_with_silence_left` on silence; calls `stop_recording()` on timeout.

### `DinkumVoiceLoop._detect_wakeup(chunk)` — `voice_loop.py:442`

In `CHECK_WAKE_UP` state. Updates hotword engines with state `WAKEUP`. On wakeup word: transitions to `DETECT_WAKEWORD`, fires `wakeup_callback` and `wakeupword_audio_callback`. If `last_ww` is more than 10 seconds ago → returns to `SLEEPING`.

### `DinkumVoiceLoop.reset_state()` — `voice_loop.py:318`

Resets to default state for current `listen_mode`:
- `CONTINUOUS` → `WAITING_CMD`, hotwords state → `HOTWORD`
- `WAKEWORD` / `HYBRID` → `DETECT_WAKEWORD`, hotwords state → `LISTEN`

### `DinkumVoiceLoop.go_to_sleep()` — `voice_loop.py:335`

Sets `state = SLEEPING`.

### `DinkumVoiceLoop.start_recording(filename)` — `voice_loop.py:353`

Sets `state = RECORDING`, resets `recording_seconds_with_silence_left`, fires `wake_callback` (to emit `record_begin`).

### `DinkumVoiceLoop.stop_recording()` — `voice_loop.py:367`

Fires `recording_audio_callback(stt_audio_bytes, {"recording_name": filename})`, then `record_end_callback()`, then `reset_state()`.

### `DinkumVoiceLoop.stop()` — `voice_loop.py:873`

Sets `_is_running = False`. The `run()` loop exits on the next iteration.

---

## `ChunkInfo` — `voice_loop.py:93`

Dataclass passed to `chunk_callback` on every audio chunk.

| Field | Type | Description |
|---|---|---|
| `is_speech` | `bool` | VAD reported speech in this chunk |
| `is_listen_sound` | `bool` | `True` while in `CONFIRMATION` state |
| `energy` | `float` | Debiased RMS energy of the chunk |

---

## VAD Pre-Wake Feature

When `listener.vad_pre_wake_enabled: true` in config:

1. Loop starts in `PRE_WAKE_VAD` — `voice_loop.py:215`
2. Each chunk is sent to `transformers.feed_audio()` unless VAD detects speech
3. On speech: `_vad_window_start` is set, chunk appended to `hotword_chunks`, state → `DETECT_WAKEWORD` — `voice_loop.py:199`
4. If no wake word detected within 5 seconds of VAD activation → back to `PRE_WAKE_VAD` — `voice_loop.py:276`

This reduces CPU usage from hotword engines when the environment is silent.

---

## STT Transcription Flow

Full sequence after `AFTER_COMMAND` — `voice_loop.py:818`:

```
_after_cmd()
 ├─ transformers.transform()        → stt_context dict with injected metadata
 ├─ _vad_remove_silence()           → (if FakeStreamingSTT + remove_silence)
 ├─ _get_tx(stt_context)
 │    ├─ _validate_lang(stt_context["stt_lang"])   → updates stream.language
 │    ├─ stt.transcribe(lang)        → [(text, conf), ...]
 │    ├─ fallback_stt.transcribe()   → (if primary returned nothing)
 │    ├─ filter by min_stt_confidence (keep at least 1)
 │    └─ truncate to max_transcripts
 ├─ stt_audio_callback(bytes, ctx)
 ├─ record_end_callback()
 └─ text_callback(utts, ctx)
```

---

## Testing Notes

`DinkumVoiceLoop` can be unit-tested without a real mic or STT. Create an instance with mocked plugins:

```python
from unittest.mock import Mock, MagicMock
from collections import deque
from ovos_dinkum_listener.voice_loop.voice_loop import DinkumVoiceLoop, ListeningMode

loop = DinkumVoiceLoop(
    mic=Mock(chunk_size=4096, seconds_per_chunk=0.256, sample_width=2),
    hotwords=Mock(),
    stt=Mock(),
    fallback_stt=None,
    vad=Mock(),
    transformers=Mock(),
)
loop.hotword_chunks = deque(maxlen=15)
loop.stt_chunks = deque(maxlen=3)
loop.stt_audio_bytes = bytes()
```

Using `seconds_per_chunk=0.256` ensures timers (e.g. `speech_seconds_left=0.3`) expire within 1–2 chunks in tests.

See `test/unittests/test_voice_loop_methods.py` for comprehensive state-method tests.
