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
import base64
import json
import subprocess
import wave
from enum import Enum
from hashlib import md5
from os.path import dirname
from pathlib import Path
from shutil import which
from tempfile import NamedTemporaryFile
from threading import Thread, RLock, Event
from typing import List, Tuple, Optional, Union

import speech_recognition as sr
import time
from ovos_bus_client import MessageBusClient
from ovos_bus_client.message import Message
from ovos_bus_client.session import SessionManager
from ovos_config import Configuration
from ovos_config.locations import get_xdg_data_save_path
from ovos_plugin_manager.microphone import OVOSMicrophoneFactory
from ovos_plugin_manager.stt import get_stt_lang_configs, get_stt_supported_langs, get_stt_module_configs
from ovos_plugin_manager.templates.microphone import Microphone
from ovos_plugin_manager.templates.stt import STT, StreamingSTT
from ovos_plugin_manager.templates.vad import VADEngine
from ovos_plugin_manager.utils.tts_cache import hash_sentence
from ovos_plugin_manager.vad import OVOSVADFactory, get_vad_configs
from ovos_plugin_manager.wakewords import get_ww_lang_configs, get_ww_supported_langs, get_ww_module_configs
from ovos_utils.fakebus import FakeBus
from ovos_utils.log import LOG, log_deprecation
from ovos_utils.process_utils import ProcessStatus, StatusCallbackMap, ProcessState

from ovos_dinkum_listener._util import _TemplateFilenameFormatter
from ovos_dinkum_listener.plugins import load_stt_module, load_fallback_stt, FakeStreamingSTT
from ovos_dinkum_listener.transformers import AudioTransformersService
from ovos_dinkum_listener.voice_loop import DinkumVoiceLoop, ListeningMode, ListeningState
from ovos_dinkum_listener.voice_loop.hotwords import HotwordContainer

try:
    from ovos_backend_client.api import DatasetApi
except ImportError:
    LOG.info("`ovos-backend-client` is not installed. Upload is disabled")
    DatasetApi = None

try:
    from ovos_utils.sound import get_sound_duration
except ImportError:

    def get_sound_duration(*args, **kwargs):
        raise ImportError("please install ovos-utils>=0.1.0a25")

# Seconds between systemd watchdog updates
WATCHDOG_DELAY = 0.5


def bytes2audiodata(data):
    recognizer = sr.Recognizer()
    with NamedTemporaryFile() as fp:
        fp.write(data)
        ffmpeg = which("ffmpeg")
        if ffmpeg:
            p = fp.name + "converted.wav"
            # ensure file format
            cmd = [ffmpeg, "-i", fp.name, "-acodec", "pcm_s16le", "-ar",
                   "16000", "-ac", "1", "-f", "wav", p, "-y"]
            subprocess.call(cmd)
        else:
            LOG.warning("ffmpeg not found, please ensure audio is in a valid format")
            p = fp.name

        with sr.AudioFile(p) as source:
            audio = recognizer.record(source)
    return audio


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
                 on_started=on_started, watchdog=lambda: None,
                 mic: Optional[Microphone] = None,
                 bus: Optional[Union[MessageBusClient, FakeBus]] = None,
                 validate_source: bool = True,
                 stt: Optional[STT] = None,
                 fallback_stt: Optional[STT] = None,
                 vad: Optional[VADEngine] = None,
                 hotwords: Optional[HotwordContainer] = None,
                 disable_fallback: bool = False,
                 *args, **kwargs):
        """
        watchdog: (callable) function to call periodically indicating
          operational status.
        """
        super().__init__(*args, **kwargs)

        LOG.info("Starting Voice Service")
        callbacks = StatusCallbackMap(on_ready=on_ready,
                                      on_error=on_error,
                                      on_stopping=on_stopping,
                                      on_alive=on_alive,
                                      on_started=on_started)
        self.bus = bus
        self.service_id = "voice"
        self.validate_source = validate_source
        self.status = ProcessStatus(self.service_id, self.bus,
                                    callback_map=callbacks)
        self._watchdog = watchdog
        self._shutdown_event = Event()
        self._stopping = False
        self.status.set_alive()
        self.config = Configuration()
        self._applied_config_hash = self._config_hash()
        self._default_vol = 70  # for barge-in

        self._before_start()  # connect to bus

        # Initialize with default (bundled) plugin
        microphone_config = self.config.get("listener",
                                            {}).get("microphone", {})
        microphone_config.setdefault('module', 'ovos-microphone-plugin-alsa')

        self.mic = mic or OVOSMicrophoneFactory.create(microphone_config)

        self.hotwords = hotwords or HotwordContainer(self.bus)
        self.vad = vad or OVOSVADFactory.create()
        if stt and not isinstance(stt, StreamingSTT):
            stt = FakeStreamingSTT(stt)
        self.stt = stt or load_stt_module()
        self.disable_fallback = disable_fallback
        self.disable_reload = stt is not None
        self.disable_hotword_reload = hotwords is not None
        if disable_fallback:
            self.fallback_stt = None
        else:
            self.fallback_stt = fallback_stt or load_fallback_stt()
        self.transformers = AudioTransformersService(self.bus, self.config)

        self._load_lock = RLock()
        self._reload_event = Event()
        self._reload_event.set()
        listener = self.config["listener"]
        self.voice_loop = self._init_voice_loop(listener)

    def _validate_message_context(self, message, native_sources=None):
        """ used to determine if a message should be processed or ignored
        only native sources should trigger on mycroft.mic.listen
        """
        if not message or not self.validate_source:
            return True
        destination = message.context.get("destination")
        if destination:
            native_sources = native_sources or \
                             Configuration()["Audio"].get("native_sources",
                                                          ["debug_cli", "audio"]) or []
            if any(s in destination for s in native_sources):
                # request from device
                return True
            # external request, do not handle
            return False
        # broadcast for everyone
        return True

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
            loop = DinkumVoiceLoop(
                mic=self.mic,
                hotwords=self.hotwords,
                stt=self.stt,
                fallback_stt=self.fallback_stt,
                vad=self.vad,
                transformers=self.transformers,
                instant_listen=listener_config.get("instant_listen", True),
                speech_seconds=listener_config.get("speech_begin", 0.3),
                silence_seconds=listener_config.get("silence_end", 0.7),
                timeout_seconds=listener_config.get("recording_timeout", 10),
                timeout_seconds_with_silence=listener_config.get("recording_timeout_with_silence", 5),
                recording_mode_max_silence_seconds=listener_config.get("recording_mode_max_silence_seconds", 30),
                num_stt_rewind_chunks=listener_config.get("utterance_chunks_to_rewind", 2),
                num_hotword_keep_chunks=listener_config.get("wakeword_chunks_to_save", 15),
                remove_silence=listener_config.get("remove_silence", False),
                wake_callback=self._record_begin,
                text_callback=self._stt_text,
                listenword_audio_callback=self._hotword_audio,
                hotword_audio_callback=self._hotword_audio,
                stopword_audio_callback=self._hotword_audio,
                wakeupword_audio_callback=self._hotword_audio,
                stt_audio_callback=self._stt_audio,
                recording_audio_callback=self._recording_audio,
                wakeup_callback=self._wakeup,
                record_end_callback=self._record_end_signal,
                min_stt_confidence=listener_config.get("min_stt_confidence", 0.6),
                max_transcripts=listener_config.get("max_transcripts", 1)
            )
        return loop

    @property
    def default_save_path(self):
        """ where recorded hotwords/utterances are saved """
        listener = Configuration().get("listener", {})
        return listener.get('save_path', f"{get_xdg_data_save_path()}/listener")

    @property
    def state(self):
        log_deprecation("This property is deprecated, reference `status.state`",
                        "0.1.0")
        if self.status.state in (ProcessState.NOT_STARTED, ProcessState.ALIVE):
            return ServiceState.NOT_STARTED
        if self.status.state == ProcessState.STARTED:
            return ServiceState.STARTED
        if self.status.state == ProcessState.READY:
            return ServiceState.RUNNING
        if self.status.state in (ProcessState.ERROR, ProcessState.STOPPING):
            return ServiceState.STOPPING
        return self.status.state

    def run(self):
        """
        Service entry point
        """
        try:
            self.status.set_alive()
            self._start()
            self.status.set_started()
            self._after_start()
            LOG.debug("Service started")
        except Exception as e:
            LOG.exception("Service failed to start")
            self.status.set_error(str(e))
            return

        try:
            self.status.set_ready()
            LOG.info("Service ready")
            while not self._stopping:
                if not self._reload_event.wait(30):
                    raise TimeoutError("Timed out waiting for reload")
                self.voice_loop.run()
        except KeyboardInterrupt:
            LOG.info("Exit via CTRL+C")
        except Exception as e:
            LOG.exception("voice_loop failed")
            self.status.set_error(str(e))
        finally:
            LOG.info("Service stopping")
            self.stop()
            LOG.debug("shutdown done")
            LOG.debug("stopped")
            if self.status.state != ProcessState.ERROR:
                self.status.state = ProcessState.NOT_STARTED

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
        self.bus.on("mycroft.mic.mute.toggle", self._handle_mute_toggle)

        self.bus.on("mycroft.mic.listen", self._handle_listen)
        self.bus.on('mycroft.mic.get_status', self._handle_mic_get_status)
        self.bus.on('recognizer_loop:audio_output_start', self._handle_audio_start)
        self.bus.on('recognizer_loop:audio_output_end', self._handle_audio_end)
        self.bus.on('mycroft.stop', self._handle_stop)

        self.bus.on('recognizer_loop:sleep', self._handle_sleep)
        self.bus.on('recognizer_loop:wake_up', self._handle_wake_up)
        self.bus.on('recognizer_loop:b64_transcribe', self._handle_b64_transcribe)
        self.bus.on('recognizer_loop:b64_audio', self._handle_b64_audio)
        self.bus.on('recognizer_loop:record_stop', self._handle_stop_recording)
        self.bus.on('recognizer_loop:state.set', self._handle_change_state)
        self.bus.on('recognizer_loop:state.get', self._handle_get_state)
        self.bus.on("intent.service.skills.activated", self._handle_extend_listening)

        self.bus.on("ovos.languages.stt", self._handle_get_languages_stt)
        self.bus.on("opm.stt.query", self._handle_opm_stt_query)
        self.bus.on("opm.ww.query", self._handle_opm_ww_query)
        self.bus.on("opm.vad.query", self._handle_opm_vad_query)

        self.bus.on("mycroft.audio.play_sound.response", self._handle_sound_played)

        # tracking volume for fake barge-in
        self.bus.on("volume.set.percent", self._handle_volume_change)
        self.bus.on("mycroft.volume.increase", self._handle_volume_change)
        self.bus.on("mycroft.volume.decrease", self._handle_volume_change)
        self._query_volume()  # sync initial volume state

        LOG.debug("Messagebus events registered")

    def _after_start(self):
        """Initialization logic called after start()"""
        Thread(target=self._pet_the_dog, daemon=True).start()
        self.status.set_started()

    def stop(self):
        """
        Stop the voice_loop and trigger service shutdown
        """
        self.status.set_stopping()
        self._stopping = True
        if self.voice_loop.running:
            self.voice_loop.stop()
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

            if not self.disable_hotword_reload:
                self.hotwords.shutdown()

            if hasattr(self.vad, "stop"):
                self.vad.stop()

            self.mic.stop()

            self.bus.close()
        except Exception as e:
            LOG.exception(f"Shutdown failed with: {e}")
        self._shutdown_event.set()
        self._load_lock.release()

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

    # Fake Barge In
    def _query_volume(self):
        """get the default volume"""
        response = self.bus.wait_for_response(Message("mycroft.volume.get"))
        if response:
            self._default_vol = int(response.data["percent"] * 100)

    @property
    def fake_barge_in(self) -> bool:
        """lower volume during recording"""
        return Configuration().get("listener", {}).get("fake_barge_in", False)

    @property
    def fake_barge_in_volume(self) -> int:
        """volume to set when recording"""
        return Configuration().get("listener", {}).get("barge_in_volume", 30)

    def _handle_volume_change(self, message: Message):
        """keep track of volume changes so we restore to the correct level"""
        if not self.fake_barge_in or message.context.get("skill_id", "") == "dinkum-listener":
            # ignore our own messages
            return
        if message.msg_type == "mycroft.volume.increase":
            vol = int(message.data.get("percent", .1) * 100)
            self._default_vol += vol
        elif message.msg_type == "mycroft.volume.decrease":
            vol = int(message.data.get("percent", -.1) * 100)
            self._default_vol -= abs(vol)
        else:
            vol = int(message.data["percent"] * 100)
            self._default_vol = vol
        LOG.info(f"tracking user volume for after barge-in: {self._default_vol}")

    # callbacks
    def _wakeup(self):
        """ callback when voice loop exits SLEEP mode"""
        self.bus.emit(Message("mycroft.awoken"))

    def _record_begin(self):
        LOG.debug("Record begin")
        if self.fake_barge_in:
            LOG.info(f"fake barge-in lowering volume to: {self.fake_barge_in_volume}")
            self.bus.emit(
                Message("mycroft.volume.set",
                        {"percent": self.fake_barge_in_volume / 100,  # alsa plugin expects between 0-1
                         "play_sound": False},
                        {"skill_id": "dinkum-listener"})
            )
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

        if DatasetApi is not None:
            Thread(target=upload, daemon=True,
                   args=(wav_data, metadata)).start()
        else:
            LOG.debug("`pip install ovos-backend-client` to enable upload")

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
        stt_lang = ww_context.get("stt_lang")  # per wake word lang override in mycroft.conf
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
                self.bus.emit(Message("recognizer_loop:utterance",
                                      payload,
                                      context))
                return payload

            # If enabled, play a wave file with a short sound to audibly
            # indicate hotword was detected.
            sound = ww_context.get("sound")
            listen = ww_context.get("listen")
            event = ww_context.get("event")

            if sound:
                LOG.debug(f"Handling listen sound: {sound}")
                audio_context = dict(context)
                audio_context["destination"] = ["audio"]
                self.bus.emit(Message("mycroft.audio.play_sound",
                                      {"uri": sound, "force_unmute": True},
                                      audio_context))
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

    def _record_end_signal(self):
        LOG.debug("Record end")
        if self.fake_barge_in:
            LOG.info(f"fake barge-in restoring volume to: {self._default_vol}")
            self.bus.emit(
                Message("mycroft.volume.set",
                        {"percent": self._default_vol / 100,  # alsa plugin expects between 0-1
                         "play_sound": False},
                        {"skill_id": "dinkum-listener"})
            )
        self.bus.emit(Message("recognizer_loop:record_end"))

    def __normtranscripts(self, transcripts: List[Tuple[str, float]]) -> List[str]:
        # unfortunately common enough when using whisper to deserve a setting
        # mainly happens on silent audio, not as a mistranscription
        default_hallucinations = [
            "thanks for watching!",
            'thank you for watching!',
            "so",
            "beep!"
            # "Thank you"  # this one can also be valid!!
        ]
        hallucinations = self.config.get("hallucination_list", default_hallucinations) \
            if self.config.get("filter_hallucinations", True) else []
        utts = [u[0].lstrip(" \"'").strip(" \"'") for u in transcripts if u[0]]
        filtered_hutts = [u for u in utts if u and u.lower() not in hallucinations]
        hutts = [u for u in utts if u not in filtered_hutts]
        if hutts:
            LOG.debug(f"Filtered hallucinations: {hutts}")
        return filtered_hutts

    def _stt_text(self, transcripts: List[Tuple[str, float]], stt_context: dict):
        utts = self.__normtranscripts(transcripts) if transcripts else []
        LOG.debug(f"STT: {utts}")
        if utts:
            lang = stt_context.get("lang") or Configuration().get("lang", "en-us")
            payload = {"utterances": utts, "lang": lang}
            self.bus.emit(Message("recognizer_loop:utterance", payload, stt_context))
        else:
            if self.voice_loop.listen_mode != ListeningMode.CONTINUOUS:
                LOG.error("Empty transcription, either recorded silence or STT failed!")
                self.bus.emit(Message("recognizer_loop:speech.recognition.unknown", context=stt_context))
            else:
                LOG.debug("Ignoring empty transcription in continuous listening mode")

    def _save_stt(self, audio_bytes, stt_meta, save_path=None):
        LOG.info("Saving Utterance Recording")
        if save_path:
            stt_audio_dir = Path(save_path)
        else:
            stt_audio_dir = Path(f"{self.default_save_path}/utterances")
        stt_audio_dir.mkdir(parents=True, exist_ok=True)

        listener = self.config.get("listener", {})

        # Documented in ovos_config/mycroft.conf
        default_template = "{md5}-{uuid4}"
        utterance_filename = listener.get("utterance_filename", default_template)
        formatter = _TemplateFilenameFormatter()

        @formatter.register('md5')
        def transcription_md5():
            # Build a hash of the transcription

            try:
                # transcriptions should be : List[Tuple[str, int]]
                text = stt_meta.get('transcriptions')[0][0]
            except IndexError:
                # handles legacy API
                return stt_meta.get('transcription') or 'null'

            return hash_sentence(text)

        filename = formatter.format(utterance_filename)

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
            DatasetApi().upload_stt(wav_data, metadata, upload_url=upload_url)

        if DatasetApi:
            Thread(target=upload, daemon=True,
                   args=(wav_data, metadata)).start()
        else:
            LOG.debug("`pip install ovos-backend-client` to enable upload")

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
    def _handle_mute(self, message: Message):
        self.voice_loop.is_muted = True

    def _handle_unmute(self, message: Message):
        self.voice_loop.is_muted = False

    def _handle_mute_toggle(self, message: Message):
        self.voice_loop.is_muted = not self.voice_loop.is_muted

    def _handle_listen(self, message: Message):
        if not self._validate_message_context(message) or not self.voice_loop.running:
            # ignore mycroft.mic.listen, it is targeted to an external client
            return
        if self.voice_loop.wake_callback is not None:
            # Emit `recognizer_loop:record_begin`
            self.voice_loop.wake_callback()
        self.voice_loop.reset_speech_timer()
        self.voice_loop.stt_audio_bytes = bytes()
        self.voice_loop.stt.stream_start()
        if self.voice_loop.fallback_stt is not None:
            self.voice_loop.fallback_stt.stream_start()

        if self.config.get('confirm_listening'):
            sound = self.config.get('sounds', {}).get('start_listening')
            if sound:
                self.bus.emit(message.forward("mycroft.audio.play_sound", {"uri": sound}))
                self.voice_loop.state = ListeningState.CONFIRMATION
                try:
                    if sound.startswith("snd/"):
                        dur = get_sound_duration(sound, base_dir=f"{dirname(__file__)}/res")
                    else:
                        dur = get_sound_duration(sound)
                    LOG.debug(f"{sound} duration: {dur} seconds")
                    self.voice_loop.confirmation_seconds_left = dur
                except:
                    self.voice_loop.confirmation_seconds_left = self.voice_loop.confirmation_seconds
        else:
            self.voice_loop.state = ListeningState.BEFORE_COMMAND

    def _handle_mic_get_status(self, message: Message):
        """Query microphone mute status."""
        data = {'muted': self.voice_loop.is_muted}
        self.bus.emit(message.response(data))

    def _handle_audio_start(self, message: Message):
        """audio output started"""
        if self.config.get("listener").get("mute_during_output"):
            self.voice_loop.is_muted = True

    def _handle_audio_end(self, message: Message):
        """audio output ended"""
        if self.config.get("listener").get("mute_during_output"):
            self.voice_loop.is_muted = False  # restore

    def _handle_stop(self, message: Message):
        """Handler for mycroft.stop, i.e. button press."""
        self.voice_loop.is_muted = False  # restore

    # state events
    def _handle_change_state(self, message: Message):
        """Set listening state."""
        # TODO - unify this api, should match ovos-listener exactly
        state = message.data.get("state")
        mode = message.data.get("mode")

        # NOTE: the enums are also strings and will match
        if state:
            if state == ListeningState.SLEEPING:
                self.voice_loop.go_to_sleep()
            elif state == ListeningState.DETECT_WAKEWORD or state == ListeningState.WAITING_CMD:  # "continuous"
                self.voice_loop.reset_state()
            elif state == ListeningState.RECORDING:  # "recording"
                self.voice_loop.start_recording(message.data.get("recording_name"))
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

        self._handle_get_state(message)

    def _handle_get_state(self, message: Message):
        """Query listening state"""
        # TODO - unify this api, should match ovos-listener exactly
        data = {'mode': self.voice_loop.listen_mode,
                "state": self.voice_loop.state}
        self.bus.emit(message.reply("recognizer_loop:state", data))

    def _handle_stop_recording(self, message: Message):
        """Stop current recording session """
        self.voice_loop.stop_recording()
        sound = self.config.get('sounds', {}).get('end_listening')
        if sound:
            self.bus.emit(message.forward("mycroft.audio.play_sound", {"uri": sound}))

    def _handle_extend_listening(self, message: Message):
        """ when a skill is activated (converse) reset the timeout until wakeword is needed again
        only used when in hybrid listening mode """
        if self.voice_loop.listen_mode == ListeningMode.HYBRID:
            self.voice_loop.last_ww = time.time()

    def _handle_sleep(self, message: Message):
        """Put the voice loop to sleep."""
        self.voice_loop.go_to_sleep()

    def _handle_wake_up(self, message: Message):
        """Wake up the voice loop."""
        LOG.debug("SLEEP - wake up triggered from bus event")
        self.voice_loop.wakeup()

    def _handle_sound_played(self, message: Message):
        """Handle response message from audio service."""
        if not self._validate_message_context(message) or not self.voice_loop.running:
            # ignore this sound, it is targeted to an external client
            return
        if self.voice_loop.state == ListeningState.CONFIRMATION:
            self.voice_loop.state = ListeningState.BEFORE_COMMAND

    def _handle_b64_transcribe(self, message: Message):
        """ transcribe base64 encoded audio and return result via message"""
        LOG.debug("Handling Base64 STT request")
        b64audio = message.data["audio"]
        lang = message.data.get("lang", self.voice_loop.stt.lang)

        wav_data = base64.b64decode(b64audio)

        self.voice_loop.stt.stream_start()
        audio = bytes2audiodata(wav_data)
        utterances = self.voice_loop.stt.transcribe(audio, lang)
        self.voice_loop.stt.stream_stop()

        LOG.debug(f"transcripts: {utterances}")
        self.bus.emit(message.response({"transcriptions": utterances, "lang": lang}))

    def _handle_b64_audio(self, message: Message):
        """ transcribe base64 encoded audio and inject result into bus"""
        LOG.debug("Handling Base64 Incoming Audio")
        b64audio = message.data["audio"]
        lang = message.data.get("lang", self.voice_loop.stt.lang)

        wav_data = base64.b64decode(b64audio)

        audio = bytes2audiodata(wav_data)

        utterances = self.voice_loop.stt.transcribe(audio, lang)
        filtered = [u for u in utterances if u[1] >= self.voice_loop.min_stt_confidence]
        if filtered != utterances:
            LOG.info(f"Ignoring low confidence STT transcriptions: {[u for u in utterances if u not in filtered]}")

        if filtered:
            self.bus.emit(message.forward(
                "recognizer_loop:utterance",
                {"utterances": [u[0] for u in filtered],
                 "lang": lang}))
        else:
            self.bus.emit(message.forward(
                "recognizer_loop:speech.recognition.unknown"))

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
            LOG.debug("No relevant configuration changed")
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

            if not self.disable_reload and new_hash['stt'] != self._applied_config_hash['stt']:
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

            if not self.disable_reload and not self.disable_fallback and new_hash['fallback'] != \
                    self._applied_config_hash['fallback']:
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

            if not self.disable_hotword_reload and new_hash['hotwords'] != self._applied_config_hash['hotwords']:
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

            self._applied_config_hash = new_hash
            self.status.set_ready()
            LOG.info("Reload Completed")
        except Exception as e:
            LOG.exception(e)
            self.status.set_error(e)
        finally:
            self._load_lock.release()
