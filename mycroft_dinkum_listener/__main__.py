# Copyright 2022 Mycroft AI Inc.
#
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
import time
import wave
from enum import Enum
from pathlib import Path
from threading import Thread
from typing import List, Optional
from ovos_plugin_manager.vad import OVOSVADFactory, VADEngine

import requests
import sdnotify
from ovos_bus_client import Message, MessageBusClient
from ovos_config import Configuration
from ovos_utils.file_utils import resolve_resource_file, get_cache_directory
from ovos_utils.log import LOG
from ovos_utils.sound import play_listening_sound

from mycroft_dinkum_listener.plugins import load_stt_module
from ovos_plugin_manager.wakewords import OVOSWakeWordFactory
from mycroft_dinkum_listener.voice_loop import AlsaMicrophone, MycroftVoiceLoop

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
        self._last_hotword_audio_uri: Optional[str] = None
        self._last_stt_audio_uri: Optional[str] = None

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

        hotword = OVOSWakeWordFactory.create_hotword(listener["wake_word"])

        vad = OVOSVADFactory.create()
        stt = load_stt_module(self.config, self.bus)

        self.voice_loop = MycroftVoiceLoop(
            mic=mic,
            hotword=hotword,
            stt=stt,
            vad=vad,
            #
            speech_seconds=listener.get("speech_begin", 0.3),
            silence_seconds=listener.get("silence_end", 0.7),
            timeout_seconds=listener.get("recording_timeout", 10),
            num_stt_rewind_chunks=listener.get("utterance_chunks_to_rewind", 2),
            num_hotword_keep_chunks=listener.get("wakeword_chunks_to_save", 15),
            #
            wake_callback=self._wake,
            text_callback=self._stt_text,
            hotword_audio_callback=self._hotword_audio,
            stt_audio_callback=self._stt_audio,
        )
        self.voice_loop.start()

        # Register events
        self.bus.on("mycroft.mic.mute", self._handle_mute)
        self.bus.on("mycroft.mic.unmute", self._handle_unmute)
        self.bus.on("mycroft.mic.listen", self._handle_listen)

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

        mic, hotword, vad, stt = (
            self.voice_loop.mic,
            self.voice_loop.hotword,
            self.voice_loop.vad,
            self.voice_loop.stt,
        )

        if hasattr(stt, "shutdown"):
            stt.shutdown()

        if hasattr(hotword, "shutdown"):
            hotword.shutdown()

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
        self.bus.on("configuration.updated", self._reload_config)

        self.bus.emit(Message(f"{self.service_id}.initialize.started"))
        LOG.info("Connected to Mycroft Core message bus")

    def _report_service_state(self, message):
        """Response to service state requests"""
        self.bus.emit(message.response(data={"state": self.state.value})),

    def _reload_config(self, _message):
        """Force reloading of config"""
        Configuration.reload()
        LOG.debug("Reloaded configuration")

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

    # audio handlers
    def _wake(self):
        LOG.debug("Awake!")
        play_listening_sound()

        self.bus.emit(Message("recognizer_loop:awoken"))
        self.bus.emit(Message("recognizer_loop:wakeword", {"utterance": self.config["listener"]["wake_word"]}))
        self.bus.emit(Message("recognizer_loop:record_begin"))

    def _hotword_audio(self, audio_bytes: bytes):
        try:
            listener = self.config["listener"]
            if listener["record_wake_words"]:
                save_path = listener.get("save_path")
                if save_path:
                    hotword_audio_dir = Path(save_path) / "mycroft_wake_words"
                else:
                    hotword_audio_dir = Path(get_cache_directory("mycroft_wake_words"))

                hotword_audio_dir.mkdir(parents=True, exist_ok=True)

                mic = self.voice_loop.mic
                wav_path = hotword_audio_dir / f"{time.monotonic_ns()}.wav"
                with open(wav_path, "wb") as wav_io, wave.open(
                        wav_io, "wb"
                ) as wav_file:
                    wav_file.setframerate(mic.sample_rate)
                    wav_file.setsampwidth(mic.sample_width)
                    wav_file.setnchannels(mic.sample_channels)
                    wav_file.writeframes(audio_bytes)

                LOG.debug("Wrote %s", wav_path)
                self._last_hotword_audio_uri = f"file://{wav_path.absolute()}"
        except Exception:
            LOG.exception("Error while saving STT audio")

    def _stt_text(self, text: str):
        if isinstance(text, list):
            text = text[0]

        self.bus.emit(Message("recognizer_loop:record_end"))

        # Report utterance to intent service
        if text:
            self.bus.emit(
                Message(
                    "recognizer_loop:utterance",
                    {"utterances": [text]},
                    {"hotword_audio_uri": self._last_hotword_audio_uri,
                     "stt_audio_uri": self._last_stt_audio_uri, }
                )
            )
        else:
            self.bus.emit(Message("recognizer_loop:speech.recognition.unknown"))

        LOG.debug("STT: %s", text)

    def _stt_audio(self, audio_bytes: bytes):
        try:
            listener = self.config["listener"]
            if listener["save_utterances"]:
                save_path = listener.get("save_path")
                if save_path:
                    stt_audio_dir = Path(save_path) / "mycroft_utterances"
                else:
                    stt_audio_dir = Path(get_cache_directory("mycroft_utterances"))

                stt_audio_dir.mkdir(parents=True, exist_ok=True)

                mic = self.voice_loop.mic
                wav_path = stt_audio_dir / f"{time.monotonic_ns()}.wav"
                with open(wav_path, "wb") as wav_io, wave.open(
                        wav_io, "wb"
                ) as wav_file:
                    wav_file.setframerate(mic.sample_rate)
                    wav_file.setsampwidth(mic.sample_width)
                    wav_file.setnchannels(mic.sample_channels)
                    wav_file.writeframes(audio_bytes)

                LOG.debug("Wrote %s", wav_path)
                self._last_stt_audio_uri = f"file://{wav_path.absolute()}"
        except Exception:
            LOG.exception("Error while saving STT audio")

    def _handle_mute(self, _message: Message):
        self.voice_loop.is_muted = True

    def _handle_unmute(self, _message: Message):
        self.voice_loop.is_muted = False

    def _handle_listen(self, message: Message):
        self.voice_loop.skip_next_wake = True


def main():
    """Service entry point"""
    DinkumVoiceService().main()


if __name__ == "__main__":
    main()
