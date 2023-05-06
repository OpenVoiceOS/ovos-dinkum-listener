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
import argparse
import json
import time
import wave
from enum import Enum
from hashlib import md5
from pathlib import Path
from threading import Thread
from typing import List, Optional

import sdnotify
from ovos_dinkum_listener.transformers import AudioTransformersService
from ovos_backend_client.api import DatasetApi
from ovos_bus_client import Message, MessageBusClient
from ovos_bus_client.session import SessionManager
from ovos_config import Configuration
from ovos_config.locations import get_xdg_data_save_path
from ovos_plugin_manager.stt import get_stt_lang_configs, get_stt_supported_langs, get_stt_module_configs
from ovos_plugin_manager.utils.tts_cache import hash_sentence
from ovos_plugin_manager.vad import OVOSVADFactory
from ovos_plugin_manager.vad import get_vad_configs
from ovos_plugin_manager.wakewords import get_ww_lang_configs, get_ww_supported_langs, get_ww_module_configs
from ovos_utils.file_utils import resolve_resource_file
from ovos_utils.log import LOG
from ovos_utils.sound import play_audio

from ovos_dinkum_listener.plugins import load_stt_module, load_fallback_stt
from ovos_dinkum_listener.voice_loop import AlsaMicrophone, DinkumVoiceLoop, ListeningMode, ListeningState
from ovos_dinkum_listener.voice_loop.hotwords import HotwordContainer

# Seconds between systemd watchdog updates
WATCHDOG_DELAY = 0.5


class ServiceState(str, Enum):
    NOT_STARTED = "not_started"
    STARTED = "started"
    RUNNING = "running"
    STOPPING = "stopping"


class DinkumVoiceService:
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

    def __init__(self):
        self.service_id = "voice"
        self._notifier = sdnotify.SystemdNotifier()
        self._state: ServiceState = ServiceState.NOT_STARTED

    @property
    def default_save_path(self):
        """ where recorded hotwords/utterances are saved """
        listener = Configuration().get("listener", {})
        return listener.get('save_path', f"{get_xdg_data_save_path()}/listener")

    @property
    def state(self):
        return self._state

    def main(self, argv: Optional[List[str]] = None):
        """Service entry point"""
        parser = argparse.ArgumentParser()
        parser.add_argument("--service-id", help="Override service id")
        args = parser.parse_args(argv)

        if args.service_id is not None:
            self.service_id = args.service_id

        try:
            self._state = ServiceState.NOT_STARTED
            self.before_start()
            self.start()
            self._state = ServiceState.STARTED
            self.after_start()

            try:
                self._state = ServiceState.RUNNING
                self.run()
            except KeyboardInterrupt:
                pass
            finally:
                self._state = ServiceState.STOPPING
                self.stop()
                self.after_stop()
                self._state = ServiceState.NOT_STARTED
        except Exception:
            LOG.exception("Service failed to start")

    def before_start(self):
        """Initialization logic called before start()"""
        self.config = Configuration()
        LOG.info("Starting service...")

        self._connect_to_bus()

    def start(self):
        listener = self.config["listener"]

        mic = AlsaMicrophone(
            device=listener.get("device_name") or "default",
            sample_rate=listener.get("sample_rate", 1600),
            sample_width=listener.get("sample_width", 2),
            sample_channels=listener.get("sample_channels", 1),
            chunk_size=listener.get("chunk_size", 4096),
            period_size=listener.get("period_size", 1024),
            multiplier=listener.get("multiplier", 1),
            timeout=listener.get("audio_timeout", 5),
            audio_retries=listener.get("audio_retries", 3),
            audio_retry_delay=listener.get("audio_retry_delay", 1),
        )
        mic.start()

        hotwords = HotwordContainer(self.bus)
        hotwords.load_hotword_engines()

        vad = OVOSVADFactory.create()
        stt = load_stt_module()
        fallback_stt = load_fallback_stt()

        transformers = AudioTransformersService(self.bus, self.config)

        self.voice_loop = DinkumVoiceLoop(
            mic=mic,
            hotwords=hotwords,
            stt=stt,
            fallback_stt=fallback_stt,
            vad=vad,
            transformers=transformers,
            #
            speech_seconds=listener.get("speech_begin", 0.3),
            silence_seconds=listener.get("silence_end", 0.7),
            timeout_seconds=listener.get("recording_timeout", 10),
            num_stt_rewind_chunks=listener.get("utterance_chunks_to_rewind", 2),
            num_hotword_keep_chunks=listener.get("wakeword_chunks_to_save", 15),
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
        self.voice_loop.start()

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

    def after_start(self):
        """Initialization logic called after start()"""
        self._start_watchdog()

        # Inform systemd that we successfully started
        self._notifier.notify("READY=1")
        self.bus.emit(Message(f"{self.service_id}.initialize.ended"))

    def run(self):
        self.voice_loop.run()

    def stop(self):
        self.voice_loop.stop()

        mic, hotwords, vad, stt = (
            self.voice_loop.mic,
            self.voice_loop.hotwords,
            self.voice_loop.vad,
            self.voice_loop.stt,
        )

        if hasattr(stt, "shutdown"):
            stt.shutdown()

        if hasattr(hotwords, "shutdown"):
            hotwords.shutdown()

        if hasattr(vad, "stop"):
            vad.stop()

        mic.stop()

    def after_stop(self):
        """Shut down code called after stop()"""
        self.bus.close()

    def _connect_to_bus(self):
        """Connects to the websocket message bus"""
        self.bus = MessageBusClient()
        self.bus.run_in_thread()
        self.bus.connected_event.wait()

        # Add event handlers
        self.bus.on(f"{self.service_id}.service.state", self._report_service_state)

        self.bus.emit(Message(f"{self.service_id}.initialize.started"))
        LOG.info("Connected to Mycroft Core message bus")

    def _report_service_state(self, message):
        """Response to service state requests"""
        self.bus.emit(message.response(data={"state": self.state.value})),

    def _start_watchdog(self):
        """Run systemd watchdog in separate thread"""
        Thread(target=self._watchdog, daemon=True).start()

    def _watchdog(self):
        """Notify systemd that the service is still running"""
        try:
            while True:
                # Prevent systemd from restarting service
                self._notifier.notify("WATCHDOG=1")
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

            upload_disabled = listener.get('wake_word_upload', {}).get('disable')
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
                self.bus.emit(Message("recognizer_loop:utterance", payload, context))
                return payload

            # If enabled, play a wave file with a short sound to audibly
            # indicate hotword was detected.
            sound = ww_context.get("sound")
            listen = ww_context.get("listen")
            event = ww_context.get("event")

            if sound:
                try:
                    sound = resolve_resource_file(sound)
                    if sound:
                        play_audio(sound)
                except Exception as e:
                    LOG.warning(e)

            if listen:
                msg_type = "recognizer_loop:wakeword"
                payload["utterance"] = ww_context["key_phrase"].replace("_", " ").replace("-", " ")
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
            payload = stt_context
            payload["utterances"] = [text]
            self.bus.emit(Message("recognizer_loop:utterance", payload, stt_context))
        else:
            self.bus.emit(Message("recognizer_loop:speech.recognition.unknown", context=stt_context))

        LOG.debug(f"STT: {text}")

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


def main():
    """Service entry point"""
    DinkumVoiceService().main()


if __name__ == "__main__":
    main()
