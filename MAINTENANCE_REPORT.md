
# Maintenance Report — `ovos-dinkum-listener`

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
