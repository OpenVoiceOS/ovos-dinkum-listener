# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
import json
import time
import wave
from enum import Enum
from hashlib import md5
from pathlib import Path
from threading import Thread, RLock, Event

from ovos_backend_client.api import DatasetApi
from ovos_bus_client import Message, MessageBusClient
from ovos_bus_client.session import SessionManager
from ovos_config import Configuration
from ovos_config.locations import get_xdg_data_save_path
from ovos_plugin_manager.microphone import OVOSMicrophoneFactory
from ovos_plugin_manager.stt import get_stt_lang_configs, get_stt_supported_langs, get_stt_module_configs
from ovos_plugin_manager.utils.tts_cache import hash_sentence
from ovos_plugin_manager.vad import OVOSVADFactory
from ovos_plugin_manager.vad import get_vad_configs
from ovos_plugin_manager.wakewords import get_ww_lang_configs, get_ww_supported_langs, get_ww_module_configs
from ovos_utils.file_utils import resolve_resource_file
from ovos_utils.log import LOG
from ovos_utils.process_utils import ProcessStatus, StatusCallbackMap
from ovos_utils.sound import play_audio

from ovos_dinkum_listener.plugins import load_stt_module, load_fallback_stt
from ovos_dinkum_listener.transformers import AudioTransformersService
from ovos_dinkum_listener.voice_loop import DinkumVoiceLoop, ListeningMode, ListeningState
from ovos_dinkum_listener.voice_loop.hotwords import HotwordContainer

# Seconds between systemd watchdog updates
WATCHDOG_DELAY = 0.5


class ServiceState(str, Enum):
    NOT_STARTED = "not_started"
    STARTED = "started"
    RUNNING = "running"
    STOPPING = "stopping"


def on_ready():
    LOG.info('DinkumVoiceService is ready.')


def on_alive():
    LOG.info('DinkumVoiceService is alive.')


def on_started():
    LOG.info('DinkumVoiceService started.')


def on_error(e='Unknown'):
    LOG.error(f'DinkumVoiceService failed to launch ({e}).')


def on_stopping():
    LOG.info('DinkumVoiceService is shutting down...')


class OVOSDinkumVoiceService(Thread):
    """
    Service for handling user voice input.

    Performs the following tasks:
    * Recording audio from microphone
    * Hotword detection
    * Voice activity detection (silence at end of voice command)
    * Speech to text

    Input messages:
    * mycroft.mic.mute
      * Produces empty audio stream
    * mycroft.mic.unmute
      * Uses real audio stream
    * mycroft.mic.listen
      * Wakes up mycroft and starts recording voice command

    Output messages:
    * recognizer_loop:awoken
      * Reports that mycroft is now awake
    * recognizer_loop:wake
      * Reports wake word used to wake up mycroft
    * recognizer_loop:record_begin
      * Reports that voice command recording has begun
    * recognizer_loop:record_end
      * Reports that voice command recording has ended
    * recognizer_loop:utterance
      * Result from speech to text of voice command
    * recognizer_loop:speech.recognition.unknown
      * Sent when empty result from speech to text is returned

    Service messages:
    * voice.service.connected
    * voice.service.connected.response
    * voice.initialize.started
    * voice.initialize.ended

    """

    def __init__(self, on_ready=on_ready, on_error=on_error,
                 on_stopping=on_stopping, on_alive=on_alive,
                 on_started=on_started, watchdog=lambda: None, mic=None,
                 bus=None):
        """
        watchdog: (callable) function to call periodically indicating
          operational status.
        """
        super().__init__()

        LOG.info("Starting Voice Service")
        callbacks = StatusCallbackMap(on_ready=on_ready,
                                      on_error=on_error,
                                      on_stopping=on_stopping,
                                      on_alive=on_alive,
                                      on_started=on_started)
        self.bus = bus
        self.service_id = "voice"
        self.status = ProcessStatus(self.service_id, self.bus,
                                    callback_map=callbacks)
        self._watchdog = watchdog
        self._shutdown_event = Event()
        self._stopping = False
        self.status.set_alive()
        self._state: ServiceState = ServiceState.NOT_STARTED
        self.config = Configuration()

        self._before_start()  # connect to bus

        # Initialize with default (bundled) plugin
        microphone_config = self.config.get("listener",
                                            {}).get("microphone", {})
        microphone_config.setdefault('module', 'ovos-microphone-plugin-alsa')

        self.mic = mic or OVOSMicrophoneFactory.create(microphone_config)

        self.hotwords = HotwordContainer(self.bus)
        self.vad = OVOSVADFactory.create()
        self.stt = load_stt_module()
        self.fallback_stt = load_fallback_stt()
        self.transformers = AudioTransformersService(self.bus, self.config)

        self._load_lock = RLock()
        self._reload_event = Event()
        self._reload_event.set()
        self._applied_config_hash = None
        listener = self.config["listener"]
        self.voice_loop = self._init_voice_loop(listener)

    def _config_hash(self):
        lang = self.config.get("lang")
        stt_module = self.config.get("stt", {}).get("module")
        fallback_module = self.config.get("stt", {}).get("fallback_module")
        stt_config = {
            "lang": lang,
            "module": stt_module,
            "config": self.config.get("stt", {}).get(stt_module)
        }
        stt_fallback = {
            "lang": lang,
            "module": fallback_module,
            "config": self.config.get("stt", {}).get(fallback_module)
        }
        loop_config = {
            "listener": self.config.get('listener'),
            "vad": self.config.get("VAD"),
            "mic": self.config.get("microphone")
        }
        hotword_config = {
            "confirm": self.config.get('confirm_listening'),
            "sounds": self.config.get("sounds"),
            "listener": self.config.get('listener'),  # Defaults, save_hotwords
            "hotwords": self.config.get('hotwords')
        }
        config_hashes = {
            "loop": hash(json.dumps(loop_config)),
            "hotwords": hash(json.dumps(hotword_config)),
            "stt": hash(json.dumps(stt_config)),
            "fallback": hash(json.dumps(stt_fallback)),
        }
        return config_hashes

    def _init_voice_loop(self, listener_config: dict):
        """
        Initialize a DinkumVoiceLoop object with the specified config
        @param listener_config:
        @return: Initialized VoiceLoop object
        """
        with self._load_lock:
            self._applied_config_hash = self._config_hash()
            loop = DinkumVoiceLoop(
                mic=self.mic,
                hotwords=self.hotwords,
                stt=self.stt,
                fallback_stt=self.fallback_stt,
                vad=self.vad,
                transformers=self.transformers,
                #
                speech_seconds=listener_config.get("speech_begin", 0.3),
                silence_seconds=listener_config.get("silence_end", 0.7),
                timeout_seconds=listener_config.get("recording_timeout", 10),
                num_stt_rewind_chunks=listener_config.get(
                    "utterance_chunks_to_rewind", 2),
                num_hotword_keep_chunks=listener_config.get(
                    "wakeword_chunks_to_save", 15),
                #
                wake_callback=self._record_begin,
                text_callback=self._stt_text,
                listenword_audio_callback=self._hotword_audio,
                hotword_audio_callback=self._hotword_audio,
                stopword_audio_callback=self._hotword_audio,
                wakeupword_audio_callback=self._hotword_audio,
                stt_audio_callback=self._stt_audio,
                recording_audio_callback=self._recording_audio,
            )
        return loop

    @property
    def default_save_path(self):
        """ where recorded hotwords/utterances are saved """
        listener = Configuration().get("listener", {})
        return listener.get('save_path', f"{get_xdg_data_save_path()}/listener")

    @property
    def state(self):
        return self._state

    def run(self):
        """
        Service entry point
        """
        try:
            self._state = ServiceState.NOT_STARTED
            self._before_start()  # Ensure configuration and bus are initialized
            self._start()
            self._state = ServiceState.STARTED
            self._after_start()
            LOG.debug("Service started")

            try:
                self.status.set_ready()
                LOG.info("Service ready")
                while not self._stopping:
                    if not self._reload_event.wait(30):
                        raise TimeoutError("Timed out waiting for reload")
                    self._state = ServiceState.RUNNING
                    self.voice_loop.run()
            except KeyboardInterrupt:
                pass
            except Exception as e:
                LOG.exception("voice_loop failed")
                self.status.set_error(str(e))
            finally:
                LOG.info("Service stopping")
                self._state = ServiceState.STOPPING
                self._shutdown()
                self._after_stop()
                self._state = ServiceState.NOT_STARTED
        except Exception as e:
            LOG.exception("Service failed to start")
            self.status.set_error(str(e))

    def _before_start(self):
        """
        Initialization logic called on module init
        """
        self.config = self.config or Configuration()
        LOG.info("Starting service...")
        self._connect_to_bus()

    def _start(self):
        """
        Start microphone and listener loop
        @return:
        """
        self.mic.start()
        self.hotwords.load_hotword_engines()
        self.voice_loop.start()
        self.register_event_handlers()

    def register_event_handlers(self):
        # Register events
        self.bus.on("mycroft.mic.mute", self._handle_mute)
        self.bus.on("mycroft.mic.unmute", self._handle_unmute)
        self.bus.on("mycroft.mic.listen", self._handle_listen)
        self.bus.on('mycroft.mic.get_status', self._handle_mic_get_status)
        self.bus.on('recognizer_loop:audio_output_start', self._handle_audio_start)
        self.bus.on('recognizer_loop:audio_output_end', self._handle_audio_end)
        self.bus.on('mycroft.stop', self._handle_stop)

        self.bus.on('recognizer_loop:sleep', self._handle_sleep)
        self.bus.on('recognizer_loop:wake_up', self._handle_wake_up)
        self.bus.on('recognizer_loop:record_stop', self._handle_stop_recording)
        self.bus.on('recognizer_loop:state.set', self._handle_change_state)
        self.bus.on('recognizer_loop:state.get', self._handle_get_state)
        self.bus.on("intent.service.skills.activated", self._handle_extend_listening)

        self.bus.on("ovos.languages.stt", self._handle_get_languages_stt)
        self.bus.on("opm.stt.query", self._handle_opm_stt_query)
        self.bus.on("opm.ww.query", self._handle_opm_ww_query)
        self.bus.on("opm.vad.query", self._handle_opm_vad_query)

        LOG.debug("Messagebus events registered")

    def _after_start(self):
        """Initialization logic called after start()"""
        Thread(target=self._pet_the_dog, daemon=True).start()
        self.status.set_started()

    def stop(self):
        """
        Stop the voice_loop and trigger service shutdown
        """
        self._stopping = True
        if not self.voice_loop.running:
            LOG.debug("voice_loop not running, just shutdown the service")
            self._shutdown()
            return
        self._shutdown_event.clear()
        self.voice_loop.stop()
        if not self._shutdown_event.wait(30):
            LOG.error(f"voice_loop didn't call _shutdown")
            self._shutdown()

    def _shutdown(self):
        """
        Internal method to shut down any running services. Called after
        `self.voice_loop` is stopped
        """
        if not self._load_lock.acquire(timeout=30):
            LOG.warning("Lock not acquired after 30 seconds; "
                        "shutting down anyways")
        try:
            if hasattr(self.stt, "shutdown"):
                self.stt.shutdown()

            if hasattr(self.fallback_stt, "shutdown"):
                self.fallback_stt.shutdown()

            self.hotwords.shutdown()

            if hasattr(self.vad, "stop"):
                self.vad.stop()

            self.mic.stop()
        except Exception as e:
            LOG.exception(f"Shutdown failed with: {e}")
        self._shutdown_event.set()
        self._load_lock.release()

    def _after_stop(self):
        """Shut down code called after stop()"""
        self.status.set_stopping()
        self.bus.close()

    def _connect_to_bus(self):
        """Connects to the websocket message bus"""
        self.bus = self.bus or MessageBusClient()
        if not self.bus.started_running:
            LOG.debug("Starting bus connection")
            self.bus.run_in_thread()
            self.bus.connected_event.wait()
        if not self.status.bus:
            self.status.bind(self.bus)
        self.config.set_config_update_handlers(self.bus)
        self.config.set_config_watcher(self.reload_configuration)
        LOG.info("Connected to Mycroft Core message bus")

    def _report_service_state(self, message):
        """Response to service state requests"""
        self.bus.emit(message.response(data={"state": self.state.value})),

    def _pet_the_dog(self):
        """Notify systemd that the service is still running"""
        if self._watchdog is not None:
            try:
                while True:
                    # Prevent systemd from restarting service
                    self._watchdog()
                    time.sleep(WATCHDOG_DELAY)
            except Exception:
                LOG.exception("Unexpected error in watchdog thread")

    # callbacks
    def _record_begin(self):
        LOG.debug("Record begin")
        self.bus.emit(Message("recognizer_loop:record_begin"))

    def _save_ww(self, audio_bytes, ww_meta, save_path=None):
        if save_path:
            hotword_audio_dir = Path(save_path)
        else:
            hotword_audio_dir = Path(f"{self.default_save_path}/wake_words")
            hotword_audio_dir.mkdir(parents=True, exist_ok=True)

        metafile = self._compile_ww_context(ww_meta["key_phrase"], ww_meta["module"])
        # TODO - do we need to keep this convention? i don't think so...
        #   move to the standard ww_id + timestamp from OPM
        filename = '_'.join(str(metafile[k]) for k in sorted(metafile))

        mic = self.voice_loop.mic
        wav_path = hotword_audio_dir / f"{filename}.wav"
        meta_path = hotword_audio_dir / f"{filename}.json"
        with open(wav_path, "wb") as wav_io, \
                wave.open(wav_io, "wb") as wav_file:
            wav_file.setframerate(mic.sample_rate)
            wav_file.setsampwidth(mic.sample_width)
            wav_file.setnchannels(mic.sample_channels)
            wav_file.writeframes(audio_bytes)
        with open(meta_path, "w") as f:
            json.dump(metafile, f)

        LOG.debug(f"Wrote {wav_path}")
        return f"file://{wav_path.absolute()}"

    def _upload_hotword(self, wav_data, metadata):
        """Upload the wakeword in a background thread."""

        upload_url = Configuration().get("listener", {}).get('wake_word_upload', {}).get('url')

        def upload(wav_data, metadata):
            DatasetApi().upload_wake_word(wav_data,
                                          metadata,
                                          upload_url=upload_url)

        Thread(target=upload, daemon=True, args=(wav_data, metadata)).start()

    @staticmethod
    def _compile_ww_context(key_phrase, ww_module):
        """ creates metadata in the format expected by selene
        while this format is mostly deprecated we want to
        ensure backwards compat and no missing keys"""
        model_hash = '0'
        return {
            'name': key_phrase,
            'engine': md5(ww_module.encode('utf-8')).hexdigest(),
            'time': str(int(1000 * time.time())),
            'sessionId': SessionManager.get().session_id,
            'accountId': "Anon",
            'model': str(model_hash)
        }

    def _hotword_audio(self, audio_bytes: bytes, ww_context: dict):
        """
        Callback method for when a hotword is detected
        @param audio_bytes: Audio that triggered detection
        @param ww_context: Context attached to hotword detection
        """
        payload = ww_context
        context = {'client_name': 'ovos_dinkum_listener',
                   'source': 'audio',  # default native audio source
                   'destination': ["skills"]}
        stt_lang = ww_context.get("lang")
        if stt_lang:
            context["lang"] = stt_lang

        try:
            listener = self.config["listener"]
            if listener["record_wake_words"]:
                payload["filename"] = self._save_ww(audio_bytes, ww_context)

            upload_disabled = listener.get('wake_word_upload',
                                           {}).get('disable')
            if self.config['opt_in'] and not upload_disabled:
                self._upload_hotword(audio_bytes, ww_context)

            utterance = ww_context.get("utterance")
            if utterance:
                LOG.debug("Hotword utterance: " + utterance)
                # send the transcribed word on for processing
                payload = {
                    'utterances': [utterance],
                    "lang": stt_lang or Configuration().get("lang", "en-us")
                }
                self.bus.emit(Message("recognizer_loop:utterance", payload,
                                      context))
                return payload

            # If enabled, play a wave file with a short sound to audibly
            # indicate hotword was detected.
            sound = ww_context.get("sound")
            listen = ww_context.get("listen")
            event = ww_context.get("event")

            if sound:
                LOG.debug(f"Handling listen sound: {sound}")
                try:
                    sound = resolve_resource_file(sound, config=self.config)
                    if sound:
                        play_audio(sound)
                except Exception as e:
                    LOG.warning(e)

            if listen:
                msg_type = "recognizer_loop:wakeword"
                payload["utterance"] = \
                    ww_context["key_phrase"].replace("_", " ").replace("-", " ")
            elif event:
                msg_type = event
            else:
                if ww_context.get("wakeup"):
                    wordtype = "wakeupword"
                elif ww_context.get("stop"):
                    wordtype = "stopword"
                else:
                    wordtype = "hotword"
                msg_type = f"recognizer_loop:{wordtype}"

            LOG.debug(f"Emitting hotword event: {msg_type}")
            # emit ww event
            self.bus.emit(Message(msg_type, payload, context))

        except Exception:
            LOG.exception("Error while saving STT audio")
        return payload

    def _stt_text(self, text: str, stt_context: dict):
        if isinstance(text, list):
            text = text[0]

        LOG.debug("Record end")
        self.bus.emit(Message("recognizer_loop:record_end",
                              context=stt_context))

        # Report utterance to intent service
        if text:
            LOG.debug(f"STT: {text}")
            payload = stt_context
            payload["utterances"] = [text]
            self.bus.emit(Message("recognizer_loop:utterance", payload, stt_context))
        elif self.voice_loop.listen_mode == ListeningMode.CONTINUOUS:
            LOG.debug("ignoring transcription failure")
        else:
            self.bus.emit(Message("recognizer_loop:speech.recognition.unknown", context=stt_context))

    def _save_stt(self, audio_bytes, stt_meta, save_path=None):
        LOG.info("Saving Utterance Recording")
        if save_path:
            stt_audio_dir = Path(save_path)
        else:
            stt_audio_dir = Path(f"{self.default_save_path}/utterances")
        stt_audio_dir.mkdir(parents=True, exist_ok=True)

        filename = hash_sentence(stt_meta["transcription"])
        mic = self.voice_loop.mic
        wav_path = stt_audio_dir / f"{filename}.wav"
        meta_path = stt_audio_dir / f"{filename}.json"
        with open(wav_path, "wb") as wav_io, \
                wave.open(wav_io, "wb") as wav_file:
            wav_file.setframerate(mic.sample_rate)
            wav_file.setsampwidth(mic.sample_width)
            wav_file.setnchannels(mic.sample_channels)
            wav_file.writeframes(audio_bytes)
        with open(meta_path, "w") as f:
            json.dump(stt_meta, f)

        LOG.debug(f"Wrote {wav_path}")
        return f"file://{wav_path.absolute()}"

    def _upload_stt(self, wav_data, metadata):
        """Upload the STT in a background thread."""

        upload_url = Configuration().get("listener", {}).get('stt_upload', {}).get('url')

        def upload(wav_data, metadata):
            # TODO - not yet merged in backend-client
            try:
                DatasetApi().upload_stt(wav_data, metadata, upload_url=upload_url)
            except:
                pass

        Thread(target=upload, daemon=True, args=(wav_data, metadata)).start()

    def _stt_audio(self, audio_bytes: bytes, stt_context: dict):
        try:
            listener = self.config["listener"]
            if listener["save_utterances"]:
                stt_context["filename"] = self._save_stt(audio_bytes, stt_context)
                upload_disabled = listener.get('stt_upload', {}).get('disable')
                if self.config['opt_in'] and not upload_disabled:
                    self._upload_stt(audio_bytes, stt_context)
        except Exception:
            LOG.exception("Error while saving STT audio")
        return stt_context

    def _save_recording(self, audio_bytes, stt_meta, save_path=None):
        LOG.info("Saving Recording")
        if save_path:
            rec_audio_dir = Path(save_path)
        else:
            rec_audio_dir = Path(self.default_save_path) / "recordings"
        rec_audio_dir.mkdir(parents=True, exist_ok=True)

        filename = stt_meta.get("recording_name", time.time())
        mic = self.voice_loop.mic
        wav_path = rec_audio_dir / f"{filename}.wav"
        meta_path = rec_audio_dir / f"{filename}.json"
        with open(wav_path, "wb") as wav_io, \
                wave.open(wav_io, "wb") as wav_file:
            wav_file.setframerate(mic.sample_rate)
            wav_file.setsampwidth(mic.sample_width)
            wav_file.setnchannels(mic.sample_channels)
            wav_file.writeframes(audio_bytes)
        with open(meta_path, "w") as f:
            json.dump(stt_meta, f)

        LOG.debug(f"Wrote {wav_path}")
        return f"file://{wav_path.absolute()}"

    def _recording_audio(self, audio_bytes: bytes, stt_context: dict):
        try:
            stt_context["filename"] = self._save_recording(audio_bytes, stt_context)
        except Exception:
            LOG.exception("Error while saving recording audio")
        return stt_context

    # mic bus api
    def _handle_mute(self, _message: Message):
        self.voice_loop.is_muted = True

    def _handle_unmute(self, _message: Message):
        self.voice_loop.is_muted = False

    def _handle_listen(self, message: Message):
        instant_listen = self.config.get('listener', {}).get('instant_listen')
        if self.config.get('confirm_listening'):
            sound = self.config.get('sounds', {}).get('start_listening')
            sound = resolve_resource_file(sound, config=self.config)
            if sound:
                play = play_audio(sound)
                if not instant_listen:
                    play.wait(10)
            else:
                LOG.error(f"Requested sound not available: {sound}")
        self.voice_loop.skip_next_wake = True

    def _handle_mic_get_status(self, event):
        """Query microphone mute status."""
        data = {'muted': self.voice_loop.is_muted}
        self.bus.emit(event.response(data))

    def _handle_audio_start(self, event):
        """Mute voice loop."""
        if self.config.get("listener").get("mute_during_output"):
            self.voice_loop.is_muted = True

    def _handle_audio_end(self, event):
        """Request unmute, if more sources have requested the mic to be muted
        it will remain muted.
        """
        if self.config.get("listener").get("mute_during_output"):
            self.voice_loop.is_muted = False  # restore

    def _handle_stop(self, event):
        """Handler for mycroft.stop, i.e. button press."""
        self.voice_loop.is_muted = False  # restore

    # state events
    def _handle_change_state(self, event):
        """Set listening state."""
        # TODO - unify this api, should match ovos-listener exactly
        state = event.data.get("state")
        mode = event.data.get("mode")

        # NOTE: the enums are also strings and will match
        if state:
            if state == ListeningState.SLEEPING:
                self.voice_loop.go_to_sleep()
            elif state == ListeningState.DETECT_WAKEWORD or state == ListeningState.WAITING_CMD:  # "continuous"
                self.voice_loop.reset_state()
            elif state == ListeningState.RECORDING:  # "recording"
                self.voice_loop.start_recording(event.data.get("recording_name"))
            else:
                LOG.error(f"Invalid listening state: {state}")

        if mode:
            if mode == ListeningMode.WAKEWORD:
                self.voice_loop.listen_mode = ListeningMode.WAKEWORD
            elif mode == ListeningMode.CONTINUOUS:
                self.voice_loop.listen_mode = ListeningMode.CONTINUOUS
            elif mode == ListeningMode.HYBRID:
                self.voice_loop.listen_mode = ListeningMode.HYBRID
            elif mode == ListeningMode.SLEEPING:
                self.voice_loop.listen_mode = ListeningMode.SLEEPING
            else:
                LOG.error(f"Invalid listen mode: {mode}")

        self._handle_get_state(event)

    def _handle_get_state(self, event):
        """Query listening state"""
        # TODO - unify this api, should match ovos-listener exactly
        data = {'mode': self.voice_loop.listen_mode,
                "state": self.voice_loop.state}
        self.bus.emit(event.reply("recognizer_loop:state", data))

    def _handle_stop_recording(self, event):
        """Stop current recording session """
        self.voice_loop.stop_recording()

    def _handle_extend_listening(self, event):
        """ when a skill is activated (converse) reset the timeout until wakeword is needed again
        only used when in hybrid listening mode """
        if self.voice_loop.listen_mode == ListeningMode.HYBRID:
            self.voice_loop.last_ww = time.time()

    def _handle_sleep(self, event):
        """Put the voice loop to sleep."""
        self.voice_loop.go_to_sleep()

    def _handle_wake_up(self, event):
        """Wake up the voice loop."""
        self.voice_loop.wakeup()
        self.bus.emit(Message("mycroft.awoken"))

    # OPM bus api
    def _handle_get_languages_stt(self, message):
        """
        Handle a request for supported STT languages
        :param message: ovos.languages.stt request
        """
        stt_langs = self.voice_loop.stt.available_languages or \
                    [self.config.get('lang') or 'en-us']
        LOG.debug(f"Got stt_langs: {stt_langs}")
        self.bus.emit(message.response({'langs': list(stt_langs)}))

    @staticmethod
    def get_stt_lang_options(lang, blacklist=None):
        blacklist = blacklist or []
        opts = []
        cfgs = get_stt_lang_configs(lang=lang, include_dialects=True)
        for engine, configs in cfgs.items():
            if engine in blacklist:
                continue
            # For Display purposes, we want to show the engine name without the underscore or dash and capitalized all
            plugin_display_name = engine.replace("_", " ").replace("-", " ").title()
            for config in configs:
                config["plugin_name"] = plugin_display_name
                config["engine"] = engine
                config["lang"] = config.get("lang") or lang
                opts.append(config)
        return opts

    @staticmethod
    def get_ww_lang_options(lang, blacklist=None):
        blacklist = blacklist or []
        opts = []
        cfgs = get_ww_lang_configs(lang=lang, include_dialects=True)
        for engine, configs in cfgs.items():
            if engine in blacklist:
                continue
            # For Display purposes, we want to show the engine name without the underscore or dash and capitalized all
            plugin_display_name = engine.replace("_", " ").replace("-", " ").title()
            for config in configs:
                config["plugin_name"] = plugin_display_name
                config["engine"] = engine
                config["lang"] = config.get("lang") or lang
                opts.append(config)
        return opts

    @staticmethod
    def get_vad_options(blacklist=None):
        blacklist = blacklist or []
        tts_opts = []
        cfgs = get_vad_configs()
        for engine, configs in cfgs.items():
            if engine in blacklist:
                continue
            # For Display purposes, we want to show the engine name without the underscore or dash and capitalized all
            plugin_display_name = engine.replace("_", " ").replace("-", " ").title()
            for voice in configs:
                voice["plugin_name"] = plugin_display_name
                voice["engine"] = engine
                tts_opts.append(voice)
        return tts_opts

    def _handle_opm_stt_query(self, message):
        plugs = get_stt_supported_langs()
        configs = {}
        opts = {}
        for lang, m in plugs.items():
            for p in m:
                configs[p] = get_stt_module_configs(p)
            opts[lang] = self.get_stt_lang_options(lang)

        data = {
            "plugins": plugs,
            "langs": list(plugs.keys()),
            "configs": configs,
            "options": opts
        }
        self.bus.emit(message.response(data))

    def _handle_opm_ww_query(self, message):
        plugs = get_ww_supported_langs()
        configs = {}
        opts = {}
        for lang, m in plugs.items():
            for p in m:
                configs[p] = get_ww_module_configs(p)
            opts[lang] = self.get_ww_lang_options(lang)

        data = {
            "plugins": plugs,
            "langs": list(plugs.keys()),
            "configs": configs,
            "options": opts
        }
        self.bus.emit(message.response(data))

    def _handle_opm_vad_query(self, message):
        cfgs = get_vad_configs()
        data = {
            "plugins": list(cfgs.keys()),
            "configs": cfgs,
            "options": self.get_vad_options()
        }
        self.bus.emit(message.response(data))

    def reload_configuration(self):
        """
        Reload configuration and restart loop. Automatically called when
        Configuration object reports a change
        """
        if self._config_hash() == self._applied_config_hash:
            LOG.info(f"No relevant configuration changed")
            return
        LOG.info("Reloading changed configuration")
        if not self._load_lock.acquire(timeout=30):
            raise TimeoutError("Lock not acquired after 30 seconds")
        if self._shutdown_event.is_set():
            LOG.info("Shutting down, skipping config reload")
            self._load_lock.release()
            return
        try:
            LOG.debug("Lock Acquired")
            new_hash = self._config_hash()

            # Configuration changed, update status and reload
            self.status.set_alive()

            if new_hash['stt'] != self._applied_config_hash['stt']:
                LOG.info(f"Reloading STT")
                if self.stt:
                    LOG.debug(f"old={self.stt.__class__}: {self.stt.config}")
                if hasattr(self.stt, "shutdown"):
                    self.stt.shutdown()
                del self.stt
                self.stt = load_stt_module(self.config['stt'])
                self.voice_loop.stt = self.stt
                if self.stt:
                    LOG.debug(f"new={self.stt.__class__}: {self.stt.config}")

            if new_hash['fallback'] != self._applied_config_hash['fallback']:
                LOG.info(f"Reloading Fallback STT")
                if self.fallback_stt:
                    LOG.debug(f"old={self.fallback_stt.__class__}: "
                              f"{self.fallback_stt.config}")
                if hasattr(self.fallback_stt, "shutdown"):
                    self.fallback_stt.shutdown()
                del self.fallback_stt
                self.fallback_stt = load_fallback_stt(self.config['stt'])
                self.voice_loop.fallback_stt = self.fallback_stt
                if self.fallback_stt:
                    LOG.debug(f"new={self.fallback_stt.__class__}: "
                              f"{self.fallback_stt.config}")

            if new_hash['hotwords'] != self._applied_config_hash['hotwords']:
                LOG.info(f"Reloading Hotwords")
                LOG.debug(f"old={self.hotwords.applied_hotwords_config}")
                self._reload_event.clear()
                self.voice_loop.stop()
                self.hotwords.shutdown()
                self.hotwords.load_hotword_engines()
                LOG.debug(f"new={self.hotwords.applied_hotwords_config}")

            if new_hash['loop'] != self._applied_config_hash['loop']:
                LOG.info(f"Reloading Listener")
                self._reload_event.clear()
                self.voice_loop.stop()

                if hasattr(self.vad, "stop"):
                    self.vad.stop()
                del self.vad
                self.vad = OVOSVADFactory.create(self.config)

                self.mic.stop()
                del self.mic
                microphone_config = self.config.get("microphone", {})
                microphone_config.setdefault('module',
                                             'ovos-microphone-plugin-alsa')
                self.mic = OVOSMicrophoneFactory.create(microphone_config)
                self.mic.start()

                # Update voice_loop with new parameters
                self.voice_loop.mic = self.mic
                self.voice_loop.vad = self.vad
                listener_config = self.config['listener']
                self.voice_loop.speech_seconds = \
                    listener_config.get("speech_begin", 0.3)
                self.voice_loop.silence_seconds = \
                    listener_config.get("silence_end", 0.7)
                self.voice_loop.timeout_seconds = listener_config.get(
                    "recording_timeout", 10)
                self.voice_loop.num_stt_rewind_chunks = listener_config.get(
                    "utterance_chunks_to_rewind", 2)
                self.voice_loop.num_hotword_keep_chunks = listener_config.get(
                    "wakeword_chunks_to_save", 15)
            if not self.voice_loop.running:
                self.voice_loop.start()
                self._reload_event.set()

            self._applied_config_hash = self._config_hash()
            self.status.set_ready()
            LOG.info("Reload Completed")
        except Exception as e:
            LOG.exception(e)
            self.status.set_error(e)
        finally:
            self._load_lock.release()
