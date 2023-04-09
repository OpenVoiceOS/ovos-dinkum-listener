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
import audioop
from collections import deque
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Callable, Deque, Optional

from ovos_plugin_manager.stt import StreamingSTT
from ovos_plugin_manager.vad import VADEngine
from ovos_plugin_manager.wakewords import HotWordEngine

from mycroft_dinkum_listener.voice_loop.microphone import Microphone


def debiased_energy(audio_data: bytes, sample_width: int) -> float:
    """Compute RMS of debiased audio."""
    # Thanks to the speech_recognition library!
    # https://github.com/Uberi/speech_recognition/blob/master/speech_recognition/__init__.py
    energy = -audioop.rms(audio_data, sample_width)
    energy_bytes = bytes([energy & 0xFF, (energy >> 8) & 0xFF])
    debiased_energy = audioop.rms(
        audioop.add(
            audio_data, energy_bytes * (len(audio_data) // sample_width), sample_width
        ),
        sample_width,
    )

    return debiased_energy


@dataclass
class VoiceLoop:
    mic: Microphone
    hotword: HotWordEngine
    stt: StreamingSTT
    vad: VADEngine

    def start(self):
        raise NotImplementedError()

    def run(self):
        raise NotImplementedError()

    def stop(self):
        raise NotImplementedError()


# -----------------------------------------------------------------------------


@dataclass
class ChunkInfo:
    vad_probability: float = 0.0
    is_speech: bool = False
    energy: float = 0.0
    hotword_probability: Optional[float] = None


WakeCallback = Callable[[], None]
TextCallback = Callable[[str], None]
AudioCallback = Callable[[bytes], None]
ChunkCallback = Callable[[ChunkInfo], None]


class State(Enum):
    DETECT_WAKEWORD = auto()
    BEFORE_COMMAND = auto()
    IN_COMMAND = auto()
    AFTER_COMMAND = auto()


@dataclass
class MycroftVoiceLoop(VoiceLoop):
    speech_seconds: float
    silence_seconds: float
    timeout_seconds: float
    num_stt_rewind_chunks: int
    num_hotword_keep_chunks: int
    skip_next_wake: bool = False
    wake_callback: Optional[WakeCallback] = None
    text_callback: Optional[TextCallback] = None
    hotword_audio_callback: Optional[AudioCallback] = None
    stt_audio_callback: Optional[AudioCallback] = None
    chunk_callback: Optional[ChunkCallback] = None
    is_muted: bool = False
    _is_running: bool = False
    _chunk_info: ChunkInfo = field(default_factory=ChunkInfo)

    def start(self):
        self._is_running = True

    def run(self):
        # Voice command state
        self.speech_seconds_left = self.speech_seconds
        self.silence_seconds_left = self.silence_seconds
        self.timeout_seconds_left = self.timeout_seconds
        self.state = State.DETECT_WAKEWORD

        # Keep hotword/STT audio so they can (optionally) be saved to disk
        self.hotword_chunks = deque(maxlen=self.num_hotword_keep_chunks)
        self.stt_audio_bytes = bytes()

        # Audio from just before the wake word is detected is kept for STT.
        # This allows you to speak a command immediately after the wake word.
        self.stt_chunks: Deque[bytes] = deque(maxlen=self.num_stt_rewind_chunks + 1)

        while self._is_running:
            chunk = self.mic.read_chunk()
            assert chunk is not None, "No audio from microphone"

            if self.is_muted:
                # Soft mute
                chunk = bytes(self.mic.chunk_size)

            self._chunk_info.is_speech = False
            self._chunk_info.energy = 0.0

            # State machine:
            #
            # DETECT_HOTWORD -> BEFORE_COMMAND
            # BEFORE_COMMAND -> {IN_COMMAND, AFTER_COMMAND}
            # IN_COMMAND -> AFTER_COMMAND
            # AFTER_COMMAND -> DETECT_HOTWORD
            #
            if self.state == State.DETECT_WAKEWORD:
                self._detect_ww(chunk)
            elif self.state == State.BEFORE_COMMAND:
                self._before_cmd(chunk)
            elif self.state == State.IN_COMMAND:
                self._in_cmd(chunk)
            elif self.state == State.AFTER_COMMAND:
                self._after_cmd(chunk)

            if self.chunk_callback is not None:
                self._chunk_info.energy = debiased_energy(chunk, self.mic.sample_width)
                self.chunk_callback(self._chunk_info)

    def _detect_ww(self, chunk):
        self.hotword_chunks.append(chunk)
        self.stt_chunks.append(chunk)
        self.hotword.update(chunk)

        if self.hotword.found_wake_word(None) or self.skip_next_wake:

            # Callback to handle recorded hotword audio
            if (self.hotword_audio_callback is not None) and (
                    not self.skip_next_wake
            ):
                hotword_audio_bytes = bytes()
                while self.hotword_chunks:
                    hotword_audio_bytes += self.hotword_chunks.popleft()

                self.hotword_audio_callback(hotword_audio_bytes)

            self.skip_next_wake = False
            self.hotword_chunks.clear()

            # Callback to handle wake up
            if self.wake_callback is not None:
                self.wake_callback()

            # Wake word detected, begin recording voice command
            self.state = State.BEFORE_COMMAND
            self.speech_seconds_left = self.speech_seconds
            self.timeout_seconds_left = self.timeout_seconds
            self.stt_audio_bytes = bytes()
            self.stt.stream_start()

            # Reset the VAD internal state to avoid the model getting
            # into a degenerative state where it always reports silence.
            if hasattr(self.vad, "reset"):
                self.vad.reset()

    def _before_cmd(self, chunk):
        # Recording voice command, but user has not spoken yet
        self.stt_audio_bytes += chunk
        self.stt_chunks.append(chunk)
        while self.stt_chunks:
            stt_chunk = self.stt_chunks.popleft()
            self.stt.stream_data(stt_chunk)

            self.timeout_seconds_left -= self.mic.seconds_per_chunk
            if self.timeout_seconds_left <= 0:
                # Recording has timed out
                self.state = State.AFTER_COMMAND
                break

            # Wait for enough speech before looking for the end of the
            # command (silence).

            self._chunk_info.is_speech = not self.vad.is_silence(stt_chunk)

            if self._chunk_info.is_speech:
                self.speech_seconds_left -= self.mic.seconds_per_chunk
                if self.speech_seconds_left <= 0:
                    # Voice command has started, so start looking for the
                    # end.
                    self.state = State.IN_COMMAND
                    self.silence_seconds_left = self.silence_seconds
                    break
            else:
                # Reset
                self.speech_seconds_left = self.speech_seconds

    def _in_cmd(self, chunk):
        # Recording voice command until user stops speaking
        self.stt_audio_bytes += chunk
        self.stt_chunks.append(chunk)
        while self.stt_chunks:
            stt_chunk = self.stt_chunks.popleft()
            self.stt.stream_data(stt_chunk)

            self.timeout_seconds_left -= self.mic.seconds_per_chunk
            if self.timeout_seconds_left <= 0:
                # Recording has timed out
                self.state = State.AFTER_COMMAND
                break

            # Wait for enough silence before considering the command to be
            # ended.
            self._chunk_info.is_speech = not self.vad.is_silence(stt_chunk)

            if not self._chunk_info.is_speech:
                self.silence_seconds_left -= self.mic.seconds_per_chunk
                if self.silence_seconds_left <= 0:
                    # End of voice command detected
                    self.state = State.AFTER_COMMAND
                    break
            else:
                # Reset
                self.silence_seconds_left = self.silence_seconds

    def _after_cmd(self, chunk):
        # Voice command has finished recording
        if self.stt_audio_callback is not None:
            self.stt_audio_callback(self.stt_audio_bytes)

        self.stt_audio_bytes = bytes()

        # Command has ended, get text and trigger callback
        text = self.stt.stream_stop() or ""

        # Callback to handle STT text
        if self.text_callback is not None:
            self.text_callback(text)

        # Back to detecting wake word
        self.state = State.DETECT_WAKEWORD

        # Clear any buffered STT chunks
        self.stt_chunks.clear()

        # Reset wakeword detector state, if available
        if hasattr(self.hotword, "reset"):
            self.hotword.reset()

    def stop(self):
        self._is_running = False
