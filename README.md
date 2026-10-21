# OpenVoiceOS Dinkum Listener

`ovos-dinkum-listener` is the voice input daemon for [OpenVoiceOS](https://openvoiceos.com). It reads audio from the microphone, runs it through a wakeword, VAD, and STT pipeline, and emits the results on the message bus for the rest of OVOS to use.

Full documentation lives in [the technical manual](https://openvoiceos.github.io/ovos-technical-manual/speech_service/) and in [docs/](docs/index.md).

## Install

`pip install ovos-dinkum-listener[extras]` to install this package and the default plugins.

Without `extras` you will also need to manually install, and possibly configure STT, WW, and VAD modules as described below.


## Configuration

you can set the Wakeword, VAD, STT and Microphone plugins

eg, to run under MacOS you should use https://github.com/OpenVoiceOS/ovos-microphone-plugin-sounddevice

non exhaustive list of config options
```
{
  "stt": {
    "module": "ovos-stt-plugin-server",
    "fallback_module": "",
    "ovos-stt-plugin-server": {"url": "https://stt.openvoiceos.com/stt"}
  },
  "listener": {
    // NOTE, multiple hotwords are supported, these fields define the main wake_word,
    // this is equivalent to setting "active": true in the "hotwords" section
    // see "hotwords" section at https://github.com/OpenVoiceOS/ovos-config/blob/dev/ovos_config/mycroft.conf
    "wake_word": "hey_mycroft",
    "stand_up_word": "wake_up",
    "microphone": {
      "module": "ovos-microphone-plugin-alsa"
    },
    // If enabled will only check for wakeword if VAD also detected speech
    // this should reduce false activations
    "vad_pre_wake_enabled": true,
    // Voice Activity Detection is used to determine when users are speaking
    "VAD": {
     // recommended plugin: "ovos-vad-plugin-silero"
     "module": "ovos-vad-plugin-silero",
     "ovos-vad-plugin-silero": {"threshold": 0.2},
     "ovos-vad-plugin-webrtcvad": {"vad_mode": 3}
    },
    // Seconds of speech before voice command has begun
    "speech_begin": 0.1,
    // Seconds of silence before a voice command has finished
    "silence_end": 0.5,
    // Settings used by microphone to set recording timeout with and without speech detected
    "recording_timeout": 10.0,
    // Settings used by microphone to set recording timeout without speech detected.
    "recording_timeout_with_silence": 3.0,
    // max time allowed without user speaking before exiting RECORDING mode
    "recording_mode_max_silence_seconds": 30.0,
    // Setting to remove all silence/noise from start and end of recorded speech (only non-streaming)
    "remove_silence": true,
    // continuous listen is an experimental setting, it removes the need for
    // wake words and uses VAD only, a streaming STT is strongly recommended
    // NOTE: depending on hardware this may cause mycroft to hear its own TTS responses as questions
    "continuous_listen": false,

    // hybrid listen is an experimental setting,
    // it will not require a wake word for X seconds after a user interaction
    // this means you dont need to say "hey mycroft" for follow up questions
    "hybrid_listen": false,
    // number of seconds to wait for an interaction before requiring wake word again
    "listen_timeout": 45
  }
}
```

## Open data uploads

`ovos-dinkum-listener` can optionally upload wake word and STT audio samples to an
[ovos-opendata-server](https://github.com/OpenVoiceOS/ovos-opendata-server) instance,
so users and developers can donate samples to help improve wake word and STT plugins.

**This is exclusively opt-in and off by default.** Nothing is ever uploaded unless
you explicitly configure at least one `ww_urls` or `stt_urls` endpoint below - there
is no default server. This pairs with the intent-match metrics upload already
supported by `ovos-core` (`open_data.intent_urls`).

```json
{
  "open_data": {
    "ww_urls": ["https://your-opendata-server.example/api/wake_word"],
    "stt_urls": ["https://your-opendata-server.example/api/stt"],
    "user_agent": "ovos-metrics",
    "api_key": null
  }
}
```

- `ww_urls` - list of server URLs to POST wake word samples to (`name`, `audio`, `model`,
  `lang`, `plugin`, `plugin_config`). Leave unset/empty to disable wake word uploads.
- `stt_urls` - list of server URLs to POST STT samples to (`transcript`, `lang`, `audio`,
  `model`, `plugin`, `plugin_config`). Leave unset/empty to disable STT uploads.
- `user_agent` - `User-Agent` header sent with uploads, defaults to `"ovos-metrics"`.
- `api_key` - optional, sent as the `X-API-Key` header if set.

Uploads happen in a background thread and never block the listener; failures are
logged and otherwise ignored.

## Tips and tricks

### Saving Transcriptions

You can enable saving of recordings to file, this should be your first step to diagnose problems, is the audio inteligible? is it being cropped? too noisy? low volume?

> set `"save_utterances": true` in your [listener config](https://github.com/OpenVoiceOS/ovos-config/blob/V0.0.13a19/ovos_config/mycroft.conf#L436), recordings will be saved to `~/.local/share/mycroft/listener/utterances`

If the recorded audio looks good to you, maybe you need to use a different STT plugin, maybe the one you are using does not like your microphone, or just isn't very good for your language

### Wrong Transcriptions

If you consistently get specific words or utterances transcribed wrong, you can remedy around this to some extent by using the [ovos-utterance-corrections-plugin](https://github.com/OpenVoiceOS/ovos-utterance-corrections-plugin)

> You can define replacements at word level `~/.local/share/mycroft/word_corrections.json`

for example whisper STT often gets artist names wrong, this allows you to correct them
```json
{
    "Jimmy Hendricks": "Jimi Hendrix",
    "Eric Klapptern": "Eric Clapton",
    "Eric Klappton": "Eric Clapton"
}
```

### Silence Removal

By default OVOS applies VAD (Voice Activity Detection) to crop silence from the audio sent to STT, this helps in performance and in accuracy (reduces hallucinations in plugins like FasterWhisper)

Depending on your microphone/VAD plugin, this might be removing too much audio

> set `"remove_silence": false` in your [listener config](https://github.com/OpenVoiceOS/ovos-config/blob/V0.0.13a19/ovos_config/mycroft.conf#L452), this will send the full audio recording to STT

### Listen Sound

does your listen sound contain speech? some users replace the "ding" sound with words such as "yes?"

In this case the listen sound will be sent to STT and might negatively affect the transcription

> set `"instant_listen": false` in your [listener config](https://github.com/OpenVoiceOS/ovos-config/blob/V0.0.13a19/ovos_config/mycroft.conf#L519), this will drop the listen sound audio from the STT audio buffer. You will need to wait for the listen sound to finish before speaking your command in this case


### Wake Word Verifiers

After the wake-word engine fires, optional *verifier plugins* can inspect the
raw audio and suppress false detections before any callback is triggered.
Plugins that implement `HotWordVerifier` (from `ovos-plugin-manager`) are
discovered automatically.

Example — enable the Silero-VAD verifier with a custom threshold:

```json
{
  "listener": {
    "ww_verifiers": {
      "ovos-ww-verifier-silero": {"threshold": 0.1}
    }
  }
}
```

**Fail-open behaviour:** if a verifier plugin raises an unexpected exception,
the exception is logged and the detection is **not** suppressed.  Only an
explicit `False` return from `HotWordVerifier.verify()` discards the wake.

Disable a specific verifier without removing it from config:

```json
{
  "listener": {
    "ww_verifiers": {
      "ovos-ww-verifier-silero": {"enabled": false}
    }
  }
}
```

> **Note:** enabling `ovos-ww-verifier-silero` and `"vad_pre_wake_enabled": true`
> at the same time applies Silero VAD twice.  Use one or the other.

### Measuring false activations

`examples/measure_false_activations.py` runs the real voice loop on your
recordings and reports recall and false activations per hour for four setups:
the engine only, the pre-wake VAD, the verifiers, and both. It needs
`ovoscope`, `scipy` and `soundfile` next to the listener:

```bash
python examples/measure_false_activations.py \
    --module ovos-ww-plugin-wakeforge --ww-config '{"model": "wakehubert_hey_floyd"}' \
    --vad ovos-vad-plugin-silero --verifier ovos-ww-verifier-silero \
    --positives hey_floyd/ --negatives noise/ --out results.json
```

The script feeds the loop in microphone chunks of `--chunk-bytes`, 4096 by
default (128 ms of 16 kHz 16-bit mono). That is the chunk size of the default
microphone plugins. If your `microphone` configuration sets a different
`chunk_size`, give that value, because the chunk size changes what the
verifiers see. Without the pre-wake VAD, they get the last
`num_hotword_keep_chunks` chunks before the detection: 15 × 128 ms = 1.92 s.
With the pre-wake VAD on, which is the default, the window starts where the
VAD heard speech, so it is shorter.

Before each positive, the script plays `--lead-silence` seconds (3 by default)
of white noise at `--noise-floor-dbfs` (-60 by default), because a real
microphone always hears a noise floor. It plays each positive at
`--lead-phases` offsets of that lead (8 by default), spread evenly over one
chunk. The chunk boundaries fall at a different place in the recording for each
offset, and the pre-wake VAD and the engines react to that cut. A single fixed
lead shows only one of the cuts. Recall counts every play.

These rates were measured at 4096-byte chunks and 8 lead offsets, so recall
counts 928 plays for `hey_chatterbox` (116 clips) and 768 for `hey_floyd` (96
clips). The positives are real recordings from
[ovos-community-wakewords-dataset](https://huggingface.co/datasets/OpenVoiceOS/ovos-community-wakewords-dataset):
116 of "hey chatterbox" and 96 of "hey floyd". The noise negatives are 1.015 h
of [wake_word_noise](https://huggingface.co/datasets/OpenVoiceOS/wake_word_noise):
false activations captured on real devices, spoken near-misses, microphone
taps and background noise. The speech negatives are 5.4 h of LibriSpeech
test-clean.

| Engine | Negatives | Setup | Recall | False activations per hour |
|---|---|---|---|---|
| `wakehubert_hey_chatterbox` | noise, 1.015 h | engine only | 82.2% | 4.93 |
| `wakehubert_hey_chatterbox` | noise, 1.015 h | pre-wake VAD | 82.2% | 4.93 |
| `wakehubert_hey_chatterbox` | noise, 1.015 h | Silero verifier | 71.2% | 4.93 |
| `wakehubert_hey_chatterbox` | noise, 1.015 h | both | 79.5% | 4.93 |
| `wakehubert_hey_floyd` | noise, 1.015 h | engine only | 92.4% | 0.99 |
| `wakehubert_hey_floyd` | noise, 1.015 h | pre-wake VAD | 92.4% | 0.99 |
| `wakehubert_hey_floyd` | noise, 1.015 h | Silero verifier | 81.4% | 0.99 |
| `wakehubert_hey_floyd` | noise, 1.015 h | both | 82.2% | 0 |
| `wakehubert_hey_floyd` | speech, 5.4 h | engine only | 92.4% | 0.56 |
| `wakehubert_hey_floyd` | speech, 5.4 h | pre-wake VAD | 92.4% | 0.74 |
| `wakehubert_hey_floyd` | speech, 5.4 h | Silero verifier | 81.4% | 0.56 |
| `wakehubert_hey_floyd` | speech, 5.4 h | both | 82.2% | 0.74 |

The recall column is the same for both negative sets because the positives are
the same. The false activation counts are 1 (`hey_floyd`) and 5
(`hey_chatterbox`) in the noise set, and 3 or 4 in the speech set. One
activation moves a rate by 0.99 per hour in the noise set and by 0.19 in the
speech set, so differences of one activation are not a measured effect.

The pre-wake VAD costs no recall. It fires after the wake word has started, so
the loop feeds the audio recorded just before the trigger (the last five
chunks) to the wake word engines. The recall above includes that audio.

The pre-wake VAD removed no false activation in these runs, and it added one to the `hey_floyd` speech set (4 against 3).

The Silero verifier removed no false activation on its own. It cost 11.1 points
of recall for `hey_floyd` and 11.0 for `hey_chatterbox`. Added to the pre-wake VAD, it cost 2.7 points for
`hey_chatterbox` and 10.3 points for `hey_floyd`. In the `hey_floyd` noise set
that combination had no false activation where each setup alone had one. That is
a single activation. Leave the verifier off unless your own measurements show a
gain. The verifier rows were measured with ovos-vad-plugin-silero 0.1.3a2.

Three of the five `hey_chatterbox` false activations come from the spoken
near-misses "a cheddar block" and "a chatter block" in the noise set. All five are spoken words, so no VAD
or verifier can reject them.

## How to test

Install the package with test dependencies, then run the suite:

```bash
pip install -e ".[extras]"
pip install pytest pytest-timeout
pytest test/ --timeout=30 -q
```

Expected output (clean environment):

```
...
258 passed in ~30s
```

To exercise the verifier chain in isolation:

```bash
pytest test/unittests/test_hotwords.py::TestHotwordVerifierChain -v
```

Expected output:

```
PASSED test_verify_no_verifiers_returns_true
PASSED test_verify_single_verifier_accepts
PASSED test_verify_single_verifier_rejects
PASSED test_verify_multiple_all_accept
PASSED test_verify_first_verifier_rejects_short_circuits
PASSED test_verify_second_verifier_rejects
PASSED test_verify_raising_verifier_is_fail_open
PASSED test_verify_raising_verifier_followed_by_rejecting_verifier
PASSED test_verify_constructor_verifiers_param

9 passed
```

To test end-to-end with the Silero verifier plugin installed:

```bash
pip install ovos-vad-plugin-silero
```

The `ovos-ww-verifier-silero` verifier is an entry point of the `ovos-vad-plugin-silero` package.

Add to your OVOS config:

```json
{
  "listener": {
    "ww_verifiers": {
      "ovos-ww-verifier-silero": {"threshold": 0.1}
    },
    "vad_pre_wake_enabled": false
  }
}
```

Then start the listener and observe logs — a detected wake word followed by
`"wake word verifier plugins discarded detection"` means the verifier rejected
the audio (expected on a non-speech trigger).

## Related projects

- [OpenVoiceOS/ovos-core](https://github.com/OpenVoiceOS/ovos-core) - the assistant core that runs this listener
- [OpenVoiceOS/ovos-plugin-manager](https://github.com/OpenVoiceOS/ovos-plugin-manager) - discovers and loads the STT, VAD, wakeword, and audio transformer plugins used here
- [OpenVoiceOS/ovos-utterance-corrections-plugin](https://github.com/OpenVoiceOS/ovos-utterance-corrections-plugin) - fixes recurring STT transcription errors
- [OpenVoiceOS/ovos-microphone-plugin-sounddevice](https://github.com/OpenVoiceOS/ovos-microphone-plugin-sounddevice) - microphone plugin for MacOS

## Credits

Voice Loop state machine implementation by [@Synesthesiam](https://github.com/synesthesiam) for [mycroft-dinkum](https://github.com/MycroftAI/mycroft-dinkum)

## License

Apache License 2.0. See [LICENSE](LICENSE).
