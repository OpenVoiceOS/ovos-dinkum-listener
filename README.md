# Mycroft Dinkum Listener 

Dinkum Listener made standalone, at this point in time this repo is just a copy pasta with updated imports

A proof of concept alternate implementation, this does NOT support OPM and standard plugins

Only precise-lite models are supported for wake word

Only silero is supported for VAD

If you are using offline backend then STT plugins are supported via ovos-backend-client (default STT) otherwise the backend STT proxy is used by default

Valid hardcoded dinkum plugins are vosk and coqui, but usage is not recommended (needs extra deps)

You need to manually install either tflite runtime or full tensorflow for wake word detection


## Usage

```
/home/miro/.venvs/ovos-core/bin/python /home/miro/PycharmProjects/mycroft-dinkum-listener/mycroft_dinkum_listener/__main__.py 
2023-04-09 00:51:01.448 - OVOS - ovos_utils.process_utils:PIDLock:301 - INFO - Create PIDLock in: None
2023-04-09 00:51:01.576 - OVOS - ovos_config.models:load_local:96 - DEBUG - Configuration /home/miro/PycharmProjects/ovos-core/mycroft/configuration/mycroft.conf loaded
2023-04-09 00:51:01.594 - OVOS - ovos_config.models:load_local:102 - DEBUG - Configuration '/etc/mycroft/mycroft.conf' not defined, skipping
2023-04-09 00:51:01.610 - OVOS - ovos_config.models:load_local:96 - DEBUG - Configuration /home/miro/.config/mycroft/web_cache.json loaded
2023-04-09 00:51:01.626 - OVOS - ovos_config.models:load_local:102 - DEBUG - Configuration '/home/miro/.config/mycroft/mycroft.conf' not defined, skipping
2023-04-09 00:51:01.642 - OVOS - ovos_config.models:load_local:102 - DEBUG - Configuration '/etc/xdg/mycroft/mycroft.conf' not defined, skipping
2023-04-09 00:51:01.658 - OVOS - ovos_config.models:load_local:102 - DEBUG - Configuration '/home/miro/.config/kdedefaults/mycroft/mycroft.conf' not defined, skipping
2023-04-09 00:51:01.674 - OVOS - ovos_config.models:load_local:102 - DEBUG - Configuration '/home/miro/.mycroft/mycroft.conf' not defined, skipping
2023-04-09 00:51:01.696 - OVOS - ovos_utils.intents.layers:<module>:5 - ERROR - This module is deprecated, import from `ovos_workshop.skills.layers
2023-04-09 00:51:01.739 - OVOS - ovos_utils.configuration:<module>:52 - WARNING - configuration moved to the `ovos_config` package. This submodule will be removed in ovos_utils 0.1.0
2023-04-09 00:51:01.987829: I tensorflow/core/util/port.cc:110] oneDNN custom operations are on. You may see slightly different numerical results due to floating-point round-off errors from different computation orders. To turn them off, set the environment variable `TF_ENABLE_ONEDNN_OPTS=0`.
2023-04-09 00:51:01.990321: I tensorflow/tsl/cuda/cudart_stub.cc:28] Could not find cuda drivers on your machine, GPU will not be used.
2023-04-09 00:51:02.021600: I tensorflow/tsl/cuda/cudart_stub.cc:28] Could not find cuda drivers on your machine, GPU will not be used.
2023-04-09 00:51:02.021892: I tensorflow/core/platform/cpu_feature_guard.cc:182] This TensorFlow binary is optimized to use available CPU instructions in performance-critical operations.
To enable the following instructions: AVX2 AVX512F AVX512_VNNI FMA, in other operations, rebuild TensorFlow with the appropriate compiler flags.
2023-04-09 00:51:02.434434: W tensorflow/compiler/tf2tensorrt/utils/py_utils.cc:38] TF-TRT Warning: Could not find TensorRT
2023-04-09 00:51:02.830 - OVOS - ovos_bus_client.conf:load_message_bus_config:19 - INFO - Loading message bus configs
2023-04-09 00:51:02.832 - OVOS - ovos_bus_client.client.client:on_open:77 - INFO - Connected
2023-04-09 00:51:02.833 - OVOS - ovos_bus_client.session:reset_default_session:171 - INFO - New Default Session Start: d4834df5-2a86-4993-b7a6-342debed1146
INFO:voice:Starting service...
INFO:websocket:Websocket connected
INFO:voice:Connected to Mycroft Core message bus
DEBUG:urllib3.connectionpool:Starting new HTTPS connection (1): github.com:443
2023-04-09 00:51:02.917 - OVOS - mycroft_dinkum_listener.voice_loop.microphone:_run:94 - DEBUG - Opening microphone (device=default, rate=16000, width=2, channels=1)
DEBUG:urllib3.connectionpool:https://github.com:443 "GET /OpenVoiceOS/precise-lite-models/raw/master/wakewords/en/hey_mycroft.tflite HTTP/1.1" 302 0
DEBUG:urllib3.connectionpool:Starting new HTTPS connection (1): raw.githubusercontent.com:443
DEBUG:urllib3.connectionpool:https://raw.githubusercontent.com:443 "GET /OpenVoiceOS/precise-lite-models/master/wakewords/en/hey_mycroft.tflite HTTP/1.1" 200 21888
INFO: Created TensorFlow Lite XNNPACK delegate for CPU.
DEBUG:urllib3.connectionpool:Starting new HTTPS connection (1): github.com:443
2023-04-09 00:51:03.150 - OVOS - mycroft_dinkum_listener.plugins.ww_tflite:_load_model:230 - DEBUG - Loading model from /tmp/dinkum_ww.tflite
2023-04-09 00:51:03.151 - OVOS - __main__:start:108 - INFO - downloading silero model
DEBUG:urllib3.connectionpool:https://github.com:443 "GET /snakers4/silero-vad/raw/74f759c8f87189659ef7b82f78dc1ddb96dee202/files/silero_vad.onnx HTTP/1.1" 302 0
DEBUG:urllib3.connectionpool:Starting new HTTPS connection (1): raw.githubusercontent.com:443
DEBUG:urllib3.connectionpool:https://raw.githubusercontent.com:443 "GET /snakers4/silero-vad/74f759c8f87189659ef7b82f78dc1ddb96dee202/files/silero_vad.onnx HTTP/1.1" 200 797513
2023-04-09 00:51:03.596 - OVOS - mycroft_dinkum_listener.voice_loop.voice_activity:start:51 - DEBUG - Loading VAD model: /tmp/silero_vad.onnx
2023-04-09 00:51:03.634 - OVOS - mycroft_dinkum_listener.plugins:load_stt_module:158 - WARNING - dinkum does not follow plugin standards, choose one of 'mycroft'/'coqui'/'vosk'
2023-04-09 00:51:03.634 - OVOS - mycroft_dinkum_listener.plugins:load_stt_module:160 - DEBUG - Using Dinkum Remote STT (ovos-backend-client)
2023-04-09 00:51:06.893 - OVOS - mycroft_dinkum_listener.plugins.ww_tflite:update:319 - DEBUG - Triggered
2023-04-09 00:51:06.894 - OVOS - __main__:_wake:177 - DEBUG - Awake!
DEBUG:urllib3.connectionpool:Starting new HTTPS connection (1): stt.openvoiceos.com:443
DEBUG:urllib3.connectionpool:https://stt.openvoiceos.com:443 "POST /stt?session_id=en-US HTTP/1.1" 200 8
2023-04-09 00:51:10.266 - OVOS - __main__:_stt_text:273 - DEBUG - STT: Hello
```

## Credits

implementation by [@Synesthesiam](https://github.com/synesthesiam) for [mycroft-dinkum](https://github.com/MycroftAI/mycroft-dinkum)
