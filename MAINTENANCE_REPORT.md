
# Maintenance Report — `ovos-dinkum-listener`

## [2026-03-11] — CI failure fixes (lint, build, tests)

- **AI Model**: claude-sonnet-4-6
- **Actions Taken**:
  - Fixed sdist build failure: `MANIFEST.in` missing `recursive-include requirements *.txt`
  - Added `ruff` config to `pyproject.toml` (select E,F; exclude `_gh_automations`)
  - Fixed 9 ruff violations across `service.py`, `hotwords.py`, `voice_loop.py`, `plugins.py`, `voice_loop/__init__.py`
  - Fixed 3 pre-existing test failures: removed `test_verify_returns_true` (method from unmerged PR #191), corrected `test_speech_detected_switches_state` (hotword_chunks drained on speech detection), replaced `test_verify_failure_returns_false` with correct assertion
  - All 251 unit tests pass
- **Oversight**: Agent-driven; human review required before push

## [2026-03-11] — Extensive docs rewrite

- **AI Model**: claude-sonnet-4-6
- **Actions Taken**:
  - Rewrote `docs/voice-loop.md` — full reference for `ListeningMode`, `ListeningState`, `VoiceLoop`, `DinkumVoiceLoop` fields/callbacks/all state methods, `ChunkInfo`, VAD pre-wake, STT transcription flow, testing notes; all methods cited with `voice_loop.py:LINE`
  - Rewrote `docs/hotwords.md` — full reference for `CyclicAudioBuffer`, `HotwordState`, `HotwordContainer` (all methods, properties, class-level state, configuration), testing notes; all methods cited with `hotwords.py:LINE`
  - Rewrote `docs/service.md` — full reference for `OVOSDinkumVoiceService` (constructor, startup sequence, voice loop init, config reload, source validation, hallucination filtering, fake barge-in, audio saving, all bus events); all sections cited with `service.py:LINE`
  - Rewrote `docs/plugins.md` — full reference for `load_stt_module`, `load_fallback_stt`, `FakeStreamingSTT`, `FakeStreamThread` with all method behaviours; cited with `plugins.py:LINE`
  - Rewrote `docs/transformers.md` — full reference for `AudioTransformersService` (discovery, priority ordering, all feed methods, transform pipeline, plugin API, voice loop integration points); cited with `transformers.py:LINE`
- **Oversight**: Automated — human review recommended before merging

## [2026-03-11] — Test coverage raised from 48% to 88%

- **AI Model**: claude-sonnet-4-6
- **Actions Taken**:
  - Created `test/unittests/test_util.py` — full coverage of `_TemplateFilenameFormatter` (100%)
  - Rewrote `test/unittests/test_hotwords.py` — `CyclicAudioBuffer` full suite + `HotwordContainer.found()`, `update()`, `reset()`, `shutdown()`, `load_hotword_engines()` (86%)
  - Created `test/unittests/test_voice_loop_methods.py` — all `DinkumVoiceLoop` state-machine methods: `_pre_wake_vad`, `_confirmation_sound`, `_before_cmd`, `_in_cmd`, `_after_cmd`, `_validate_lang`, `_get_tx`, `_vad_remove_silence`, `_wait_cmd`, `_in_recording`, `_detect_wakeup`, `_detect_ww`, `reset_state`, `go_to_sleep`, `start_recording`, `stop_recording`, `stop` (89%)
  - Expanded `test/unittests/test_plugins.py` — `FakeStreamThread` (finalize, update, handle_audio_stream), `FakeStreamingSTT` (transcribe all input types, create_streaming_thread, FakeStreamingSTT wrapper path) (93%)
  - Created `test/unittests/test_service_extended.py` — callback functions, static methods (`_compile_ww_context`, `get_stt_lang_options`, `get_ww_lang_options`, `get_vad_options`), all message handlers (`_handle_mute/unmute/toggle`, `_handle_sleep/wake_up/stop`, `_handle_change_state`, `_handle_b64_audio`, `_handle_b64_transcribe`, OPM handlers), `_stt_text`, `_hotword_audio`, `_save_stt`, `_save_ww`, `_save_recording` (88%)
  - 252 tests, all passing
- **Oversight**: Automated — human review recommended before merging

## [2026-03-08] — Initial compliance scaffold

### Changes
- Created `QUICK_FACTS.md` with machine-readable package metadata.
- Created `FAQ.md` with common Q&A.
- Created `MAINTENANCE_REPORT.md` (this file) as the change log.
- Created `SUGGESTIONS.md` with initial improvement proposals.
- Created `docs/index.md` as the documentation entry point (if missing).

### Rationale
Establishing the required file set mandated by `AGENTS.md` for all active workspace repositories.

### Verification
- All required files exist at repo root and `docs/` folder.
- No existing content was overwritten.

### AI Transparency Report
- **AI Model**: Claude Sonnet 4.6
- **Actions Taken**: Generated boilerplate compliance scaffold (QUICK_FACTS, FAQ, MAINTENANCE_REPORT, SUGGESTIONS, docs/index).
- **Oversight**: Files are stubs — human review and enrichment required before treating as authoritative.
