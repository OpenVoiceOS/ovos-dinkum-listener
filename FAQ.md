
# FAQ — `ovos-dinkum-listener`

## What is `ovos-dinkum-listener`?
`ovos-dinkum-listener` is ovos-core listener daemon client.

## What is the test coverage target?
85% minimum. As of 2026-03-11, coverage is **88%** across 252 tests.

## What test files exist?
- `test/unittests/test_hotwords.py` — `CyclicAudioBuffer`, `HotwordContainer`
- `test/unittests/test_plugins.py` — `FakeStreamThread`, `FakeStreamingSTT`, STT loaders
- `test/unittests/test_service.py` — `OVOSDinkumVoiceService` lifecycle
- `test/unittests/test_service_extended.py` — service handlers, save/upload methods
- `test/unittests/test_transformers.py` — `AudioTransformersService`
- `test/unittests/test_util.py` — `_TemplateFilenameFormatter`
- `test/unittests/test_voice_loop.py` — `VoiceLoop`, `DinkumVoiceLoop` basic
- `test/unittests/test_voice_loop_methods.py` — all `DinkumVoiceLoop` methods

## How do I install it?
```bash
pip install ovos-dinkum-listener
```
Or for development:
```bash
uv pip install -e ovos-dinkum-listener/
```

## Where do I report bugs?
Open an issue on the GitHub repository. Ensure you are targeting the `dev` branch for fixes.

## How do I run tests?
```bash
uv run pytest ovos-dinkum-listener/test/ --cov=ovos_dinkum_listener
```

## How do I contribute?
1. Fork the repository and create a feature branch from `dev`.
2. Write tests for your changes.
3. Open a PR targeting the `dev` branch.
4. Ensure CI passes before requesting review.

## What Python versions are supported?
See `QUICK_FACTS.md` — currently `>=3.9`.
