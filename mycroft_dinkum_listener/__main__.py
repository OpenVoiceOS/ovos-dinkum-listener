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
import dataclasses
import time
import wave
from pathlib import Path
from typing import Optional
from uuid import uuid4

import requests
from ovos_bus_client import Message
from ovos_utils.file_utils import resolve_resource_file, get_cache_directory
from ovos_utils.log import LOG
from ovos_utils.sound import play_listening_sound

from mycroft_dinkum_listener.dinkum_service import DinkumService
from mycroft_dinkum_listener.plugins import load_stt_module
from mycroft_dinkum_listener.plugins.ww_tflite import TFLiteHotWordEngine
from mycroft_dinkum_listener.voice_loop import AlsaMicrophone, MycroftVoiceLoop, SileroVoiceActivity


class VoiceService(DinkumService):
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
        super().__init__(service_id="voice")
        self.mycroft_session_id: Optional[str] = None
        self._is_diagnostics_enabled = False
        self._last_hotword_audio_uri: Optional[str] = None
        self._last_stt_audio_uri: Optional[str] = None

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

        try:
            wake_word = listener["wake_word"]
            hotword_config = self.config["hotwords"][wake_word]
            hotword = TFLiteHotWordEngine(hotword_config)
        except:
            raise ValueError("tflite models only check your config, dinkum does not support standard plugins")

        vad_model = listener.get("vad_model") or \
                    "https://github.com/snakers4/silero-vad/raw/74f759c8f87189659ef7b82f78dc1ddb96dee202/files/silero_vad.onnx"
        if vad_model.startswith("http"):
            LOG.info("downloading silero model")
            content = requests.get(vad_model).content
            vad_model = "/tmp/silero_vad.onnx"  # TODO - XDG
            with open(vad_model, "wb") as f:
                f.write(content)
        else:
            vad_model = resolve_resource_file(vad_model)

        if not vad_model:
            raise ValueError("you need to provide the path to vad model, "
                             "dinkum does not support standard plugins")
        vad = SileroVoiceActivity(
            model=vad_model,
            threshold=listener.get("vad_threshold", 0.5),
        )
        vad.start()

        stt = load_stt_module(self.config, self.bus)
        stt.start()

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
        self.bus.on("mycroft.mic.set-diagnostics", self._handle_set_diagnostics)

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

        vad.stop()

        if hasattr(hotword, "shutdown"):
            hotword.shutdown()

        mic.stop()

    def _wake(self):
        LOG.debug("Awake!")
        play_listening_sound()

        if self.mycroft_session_id is None:
            self.mycroft_session_id = str(uuid4())

        self.bus.emit(
            Message(
                "recognizer_loop:awoken",
                data={"mycroft_session_id": self.mycroft_session_id},
            )
        )
        self.bus.emit(
            Message(
                "recognizer_loop:wakeword",
                data={
                    "utterance": self.config["listener"]["wake_word"],
                    "session": self.mycroft_session_id,
                },
            )
        )
        self.bus.emit(
            Message(
                "recognizer_loop:record_begin",
                {
                    "mycroft_session_id": self.mycroft_session_id,
                },
            )
        )

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

        self.bus.emit(
            Message(
                "recognizer_loop:record_end",
                {
                    "mycroft_session_id": self.mycroft_session_id,
                },
            )
        )

        if self._is_diagnostics_enabled:
            # Bypass intent service when diagnostics are enabled
            self.bus.emit(
                Message(
                    "mycroft.mic.diagnostics:utterance",
                    data={"utterance": text},
                )
            )
        else:
            # Report utterance to intent service
            if text:
                self.bus.emit(
                    Message(
                        "recognizer_loop:utterance",
                        {
                            "utterances": [text],
                            "mycroft_session_id": self.mycroft_session_id,
                            "hotword_audio_uri": self._last_hotword_audio_uri,
                            "stt_audio_uri": self._last_stt_audio_uri,
                        },
                    )
                )
            else:
                self.bus.emit(Message("recognizer_loop:speech.recognition.unknown"))

        LOG.debug("STT: %s", text)
        self.mycroft_session_id = None

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

    def _chunk_diagnostics(self, chunk_info):
        if self._is_diagnostics_enabled:
            self.bus.emit(
                Message("mycroft.mic.diagnostics", data=dataclasses.asdict(chunk_info))
            )

    def _handle_mute(self, _message: Message):
        self.voice_loop.is_muted = True

    def _handle_unmute(self, _message: Message):
        self.voice_loop.is_muted = False

    def _handle_listen(self, message: Message):
        self.mycroft_session_id = message.data.get("mycroft_session_id")
        self.voice_loop.skip_next_wake = True

    def _handle_set_diagnostics(self, message: Message):
        self._is_diagnostics_enabled = message.data.get("enabled", True)

        if self._is_diagnostics_enabled:
            self.voice_loop.chunk_callback = self._chunk_diagnostics
            self.log.debug("Diagnostics enabled")
        else:
            self.voice_loop.chunk_callback = None
            self.log.debug("Diagnostics disabled")


def main():
    """Service entry point"""
    VoiceService().main()


if __name__ == "__main__":
    main()
