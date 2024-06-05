# OpenVoiceOS Dinkum Listener 

Documentation can be found in [the technical manual](https://openvoiceos.github.io/ovos-technical-manual/speech_service/)

## Install

`pip install ovos-dinkum-listener[extras]` to install this package and the default
plugins. Note that by default, either `tensorflow` or `tflite_runtime` will need
to be installed separately for wakeword detection.

> If unable to install tflite_runtime in your platform, you can find wheels
> here https://whl.smartgic.io/. eg, for pyhon 3.11 in x86
> `pip install https://whl.smartgic.io/tflite_runtime-2.13.0-cp311-cp311-linux_x86_64.whl`

Without `extras`, wakeword and STT audio upload will be disabled unless you install 
[`ovos-backend-client`](https://github.com/OpenVoiceOS/ovos-backend-client) separately. You will also need to manually install,
and possibly configure STT, WW, and VAD modules as described below.

Using [ovos-vad-plugin-silero](https://github.com/OpenVoiceOS/ovos-vad-plugin-silero) 
is strongly recommended

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

## Credits

Voice Loop state machine implementation by [@Synesthesiam](https://github.com/synesthesiam) for [mycroft-dinkum](https://github.com/MycroftAI/mycroft-dinkum)
