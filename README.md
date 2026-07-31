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
    VAD": {
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
pip install ovos-ww-verifier-silero
```

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
