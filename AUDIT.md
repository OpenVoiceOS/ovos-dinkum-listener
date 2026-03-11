
# ovos-dinkum-listener — Audit Report

## Fixed Bugs (2026-03-11)

### `ww_context["event"]` key mismatch — `service.py:591` ✅ FIXED
`hotwords.py:187` stores the custom bus event topic as `"bus_event"`.
`service.py:591` was reading `ww_context.get("event")` — always None in
production, so custom bus events were silently dropped.
Fixed: `ww_context.get("bus_event")`.

### `ww_context["stop"]` key mismatch — `service.py:613` ✅ FIXED
`hotwords.py:192` stores the stopword flag as `"stopword"`.
`service.py:613` was reading `ww_context.get("stop")` — always False,
so stopword detections always emitted `recognizer_loop:hotword` instead of
`recognizer_loop:stopword`.
Fixed: `ww_context.get("stopword")`.

---

## Config Keys Read by Dinkum Listener but Missing from Canonical mycroft.conf

These are undocumented extensions that need to be added to `ovos-config/ovos_config/mycroft.conf`:

| Key | Default | Source | Notes |
|---|---|---|---|
| `listener.sample_width` | `2` | `plugins.py:55` | Bytes per sample; not configurable via conf |
| `listener.recording_mode_max_silence_seconds` | `30` | `service.py:258` | Hard timeout in RECORDING mode |
| `listener.min_stt_confidence` | `0.6` | `service.py:272` | Min confidence for STT transcript |
| `listener.max_transcripts` | `1` | `service.py:273` | Max alternative transcripts to STT callback |
| `listener.barge_in_volume` | `30` | `service.py:478` | Volume % during fake barge-in |
| `listener.audio_transformers` | `{}` | `transformers.py:43` | Audio transformer plugin configs |
| `hallucination_list` | `[...]` | `service.py:649` | Top-level key; list of strings to filter |
| `filter_hallucinations` | `True` | `service.py:650` | Top-level key; enables hallucination filter |

## Hotword Config Keys Added by Dinkum Listener

The canonical `hotwords` examples in mycroft.conf do not document these dinkum-specific
per-hotword keys:

| Key | Description | Source |
|---|---|---|
| `stopword` | If true, detecting this WW emits `recognizer_loop:stopword` | `hotwords.py:151` |
| `bus_event` | Custom bus event type to emit instead of default | `hotwords.py:154` |
| `stt_lang` | Override STT language for this wake word | `hotwords.py:152` |
| `utterance` | Override utterance text emitted with wakeword event | `hotwords.py:148` |

## Config Keys in Canonical mycroft.conf Not Read by Dinkum Listener

| Key | Value | Notes |
|---|---|---|
| `listener.listen_timeout` | `45` | Used for hybrid mode timeout — not implemented in voice_loop |
| `listener.ww_verifiers` | `{...}` | Belongs to unmerged PR #191 "Hotword verifier"; `verify()` method not in source |
| `listener.retry_mic_init` | `true` | Not consumed by service.py |
| `listener.phoneme_duration` | `120` | Legacy Mycroft field; not used |
| `listener.multiplier` | `1.0` | Legacy Mycroft field; not used |
| `listener.energy_ratio` | `1.5` | Legacy Mycroft field; not used |

## Duplicate Key in Canonical mycroft.conf

`recording_timeout` appears **twice** in the `listener` section of
`ovos-config/ovos_config/mycroft.conf` (line 387: `10`, line 520: `10.0`).
JSON parsers accept this but the second value silently wins. The first instance
(inside what reads as the `speech_begin`/`silence_end` block) should be removed.
**Fix needed in `ovos-config` repo.**

---

## Remaining Technical Debt

- `listener.listen_timeout` (45s) is in canonical conf for hybrid listen mode
  but `voice_loop.py` does not use it — hybrid mode has no timeout implementation.
  `voice_loop.py:203` only sets `ListeningMode.HYBRID` without scheduling a reset.
- `setup.py` still present alongside `pyproject.toml` — dual packaging; should
  remove `setup.py` once CI confirms `pyproject.toml` is sufficient.
- `ovos_dinkum_listener/res/snd/` directory recognized as an importable package
  by setuptools (warns in build log) — add `__init__.py` or exclude from
  packages in `pyproject.toml`.
