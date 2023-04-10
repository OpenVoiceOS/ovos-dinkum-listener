# OpenVoiceOS Dinkum Listener 

Dinkum Listener made standalone, the voice loop is now a much more responsive state machine

the usual configuration files are loaded, some new params are exposed under the `"listener"` section but not yet documented (check the code...)

## Install

Non streaming STT plugins are wrapped into a `FlacStreamingPlugin`(adapted from default dinkum STT), this uses `flac` via subprocess, if using a StreamingSTT directly the `flac` dependency is not needed

using [ovos-vad-plugin-silero](https://github.com/OpenVoiceOS/ovos-vad-plugin-silero) is strongly recommended instead of the default webrtcvad plugin

## mycroft-dinkum vs ovos-dinkum-listener

- release 0.0.0 is the extracted dinkum listener, plugins are hardcoded options
- release 0.0.1 adds OPM support
- release 0.1.0 adds full feature parity with ovos-listener and has very little dinkum left

ovos exclusive features:

- sleep mode
- continuous listening  (no wakeword, VAD only)
- hybrid listening  (no wakeword for follow up commands)
- multiple wakewords
   - assign a STT lang per wakeword (multilingual support)
- hotword types (perform actions other than listen)
- recording mode (save speech to file instead of STT)
- OPM bus api (query available plugins)
- wake word upload (backend)
- XDG path standards for recorded audio data

## Usage

```
/home/miro/.venvs/ovos-core/bin/python /home/miro/PycharmProjects/mycroft-dinkum-listener/ovos_dinkum_listener/__main__.py 
2023-04-10 14:23:26.044 - OVOS - ovos_utils.process_utils:PIDLock:301 - INFO - Create PIDLock in: None
2023-04-10 14:23:26.123 - OVOS - ovos_config.models:load_local:96 - DEBUG - Configuration /home/miro/PycharmProjects/ovos-core/mycroft/configuration/mycroft.conf loaded
2023-04-10 14:23:26.144 - OVOS - ovos_config.models:load_local:102 - DEBUG - Configuration '/etc/mycroft/mycroft.conf' not defined, skipping
2023-04-10 14:23:26.163 - OVOS - ovos_config.models:load_local:96 - DEBUG - Configuration /home/miro/.config/mycroft/web_cache.json loaded
2023-04-10 14:23:26.184 - OVOS - ovos_config.models:load_local:102 - DEBUG - Configuration '/home/miro/.config/mycroft/mycroft.conf' not defined, skipping
2023-04-10 14:23:26.203 - OVOS - ovos_config.models:load_local:102 - DEBUG - Configuration '/etc/xdg/mycroft/mycroft.conf' not defined, skipping
2023-04-10 14:23:26.239 - OVOS - ovos_config.models:load_local:102 - DEBUG - Configuration '/home/miro/.config/kdedefaults/mycroft/mycroft.conf' not defined, skipping
2023-04-10 14:23:26.266 - OVOS - ovos_config.models:load_local:102 - DEBUG - Configuration '/home/miro/.mycroft/mycroft.conf' not defined, skipping
2023-04-10 14:23:26.446 - OVOS - ovos_utils.messagebus:<module>:281 - WARNING - ovos-bus-client not installed
2023-04-10 14:23:26.548 - OVOS - ovos_utils.intents.layers:<module>:5 - ERROR - This module is deprecated, import from `ovos_workshop.skills.layers
2023-04-10 14:23:26.597 - OVOS - ovos_utils.configuration:<module>:52 - WARNING - configuration moved to the `ovos_config` package. This submodule will be removed in ovos_utils 0.1.0
2023-04-10 14:23:26.620 - OVOS - __main__:before_start:133 - INFO - Starting service...
2023-04-10 14:23:26.620 - OVOS - ovos_bus_client.conf:load_message_bus_config:19 - INFO - Loading message bus configs
2023-04-10 14:23:26.624 - OVOS - ovos_bus_client.client.client:on_open:88 - INFO - Connected
2023-04-10 14:23:26.625 - OVOS - ovos_bus_client.session:reset_default_session:171 - INFO - New Default Session Start: 32073cb1-e92c-4f2d-a77c-4c7102ebd36a
2023-04-10 14:23:26.626 - OVOS - __main__:_connect_to_bus:249 - INFO - Connected to Mycroft Core message bus
2023-04-10 14:23:26.632 - OVOS - ovos_dinkum_listener.voice_loop.microphone:_run:91 - DEBUG - Opening microphone (device=default, rate=16000, width=2, channels=1)
2023-04-10 14:23:26.643 - OVOS - ovos_dinkum_listener.voice_loop.hotwords:load_hotword_engines:63 - INFO - creating hotword engines
2023-04-10 14:23:26.644 - OVOS - ovos_plugin_manager.wakewords:load_module:110 - INFO - Loading "hey_mycroft" wake word via ovos-ww-plugin-precise-lite
2023-04-10 14:23:26.808419: I tensorflow/core/util/port.cc:110] oneDNN custom operations are on. You may see slightly different numerical results due to floating-point round-off errors from different computation orders. To turn them off, set the environment variable `TF_ENABLE_ONEDNN_OPTS=0`.
2023-04-10 14:23:26.810269: I tensorflow/tsl/cuda/cudart_stub.cc:28] Could not find cuda drivers on your machine, GPU will not be used.
2023-04-10 14:23:26.852304: I tensorflow/tsl/cuda/cudart_stub.cc:28] Could not find cuda drivers on your machine, GPU will not be used.
2023-04-10 14:23:26.852766: I tensorflow/core/platform/cpu_feature_guard.cc:182] This TensorFlow binary is optimized to use available CPU instructions in performance-critical operations.
To enable the following instructions: AVX2 AVX512F AVX512_VNNI FMA, in other operations, rebuild TensorFlow with the appropriate compiler flags.
2023-04-10 14:23:27.361300: W tensorflow/compiler/tf2tensorrt/utils/py_utils.cc:38] TF-TRT Warning: Could not find TensorRT
2023-04-10 14:23:27.931 - OVOS - ovos_plugin_manager.wakewords:load_module:117 - INFO - Loaded the Wake Word plugin ovos-ww-plugin-precise-lite
2023-04-10 14:23:27.933 - OVOS - ovos_plugin_manager.wakewords:load_module:110 - INFO - Loading "wake_up" wake word via ovos-ww-plugin-pocketsphinx
INFO: Created TensorFlow Lite XNNPACK delegate for CPU.
2023-04-10 14:23:27.940 - OVOS - ovos_plugin_manager.wakewords:load_module:117 - INFO - Loaded the Wake Word plugin ovos-ww-plugin-pocketsphinx
2023-04-10 14:23:28.047 - OVOS - ovos_dinkum_listener.plugins:load_stt_module:116 - WARNING - dinkum only supports streaming STTs
2023-04-10 14:23:28.048 - OVOS - ovos_dinkum_listener.plugins:load_stt_module:117 - INFO - Using FlacStreamingSTT wrapper -> ovos-backend-client.api.STTApi(backend_type=BackendType.OFFLINE)
2023-04-10 14:23:34.234 - OVOS - __main__:_record_being:271 - DEBUG - Record begin
2023-04-10 14:23:38.672 - OVOS - __main__:_stt_text:351 - DEBUG - Record end
2023-04-10 14:23:38.689 - OVOS - __main__:_stt_text:366 - DEBUG - STT: hello
```

## Credits

Voice Loop state machine implementation by [@Synesthesiam](https://github.com/synesthesiam) for [mycroft-dinkum](https://github.com/MycroftAI/mycroft-dinkum)
