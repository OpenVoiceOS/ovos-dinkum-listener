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

from mycroft_dinkum_listener.voice_loop.microphone import Microphone
from mycroft_dinkum_listener.voice_loop.voice_activity import DinkumVoiceActivity
from ovos_plugin_manager.wakewords import HotWordEngine
from ovos_plugin_manager.stt import StreamingSTT


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
    vad: DinkumVoiceActivity

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
        speech_seconds_left = self.speech_seconds
        silence_seconds_left = self.silence_seconds
        timeout_seconds_left = self.timeout_seconds
        state = State.DETECT_WAKEWORD

        # Keep hotword/STT audio so they can (optionally) be saved to disk
        hotword_chunks = deque(maxlen=self.num_hotword_keep_chunks)
        stt_audio_bytes = bytes()

        # Audio from just before the wake word is detected is kept for STT.
        # This allows you to speak a command immediately after the wake word.
        stt_chunks: Deque[bytes] = deque(maxlen=self.num_stt_rewind_chunks + 1)

        has_probability = hasattr(self.hotword, "probability")

        while self._is_running:
            chunk = self.mic.read_chunk()
            assert chunk is not None, "No audio from microphone"

            if self.is_muted:
                # Soft mute
                chunk = bytes(self.mic.chunk_size)

            # State machine:
            #
            # DETECT_HOTWORD -> BEFORE_COMMAND
            # BEFORE_COMMAND -> {IN_COMMAND, AFTER_COMMAND}
            # IN_COMMAND -> AFTER_COMMAND
            # AFTER_COMMAND -> DETECT_HOTWORD
            #
            if state == State.DETECT_WAKEWORD:
                hotword_chunks.append(chunk)
                stt_chunks.append(chunk)
                self.hotword.update(chunk)

                if has_probability:
                    # For diagnostics
                    self._chunk_info.hotword_probability = self.hotword.probability

                if self.chunk_callback is not None:
                    self._chunk_info.is_speech = not self.vad.is_silence(stt_chunk)

                if self.hotword.found_wake_word(None) or self.skip_next_wake:

                    # Callback to handle recorded hotword audio
                    if (self.hotword_audio_callback is not None) and (
                            not self.skip_next_wake
                    ):
                        hotword_audio_bytes = bytes()
                        while hotword_chunks:
                            hotword_audio_bytes += hotword_chunks.popleft()

                        self.hotword_audio_callback(hotword_audio_bytes)

                    self.skip_next_wake = False
                    hotword_chunks.clear()

                    # Callback to handle wake up
                    if self.wake_callback is not None:
                        self.wake_callback()

                    # Wake word detected, begin recording voice command
                    state = State.BEFORE_COMMAND
                    speech_seconds_left = self.speech_seconds
                    timeout_seconds_left = self.timeout_seconds
                    stt_audio_bytes = bytes()
                    self.stt.stream_start()

                    # Reset the VAD internal state to avoid the model getting
                    # into a degenerative state where it always reports silence.
                    self.vad.reset()

            elif state == State.BEFORE_COMMAND:
                # Recording voice command, but user has not spoken yet
                stt_audio_bytes += chunk
                stt_chunks.append(chunk)
                while stt_chunks:
                    stt_chunk = stt_chunks.popleft()
                    self.stt.stream_data(stt_chunk)

                    timeout_seconds_left -= self.mic.seconds_per_chunk
                    if timeout_seconds_left <= 0:
                        # Recording has timed out
                        state = State.AFTER_COMMAND
                        break

                    # Wait for enough speech before looking for the end of the
                    # command (silence).

                    self._chunk_info.is_speech = not self.vad.is_silence(stt_chunk)

                    if self._chunk_info.is_speech:
                        speech_seconds_left -= self.mic.seconds_per_chunk
                        if speech_seconds_left <= 0:
                            # Voice command has started, so start looking for the
                            # end.
                            state = State.IN_COMMAND
                            silence_seconds_left = self.silence_seconds
                            break
                    else:
                        # Reset
                        speech_seconds_left = self.speech_seconds
            elif state == State.IN_COMMAND:
                # Recording voice command until user stops speaking
                stt_audio_bytes += chunk
                stt_chunks.append(chunk)
                while stt_chunks:
                    stt_chunk = stt_chunks.popleft()
                    self.stt.stream_data(stt_chunk)

                    timeout_seconds_left -= self.mic.seconds_per_chunk
                    if timeout_seconds_left <= 0:
                        # Recording has timed out
                        state = State.AFTER_COMMAND
                        break

                    # Wait for enough silence before considering the command to be
                    # ended.
                    self._chunk_info.is_speech = not self.vad.is_silence(stt_chunk)

                    if not self._chunk_info.is_speech:
                        silence_seconds_left -= self.mic.seconds_per_chunk
                        if silence_seconds_left <= 0:
                            # End of voice command detected
                            state = State.AFTER_COMMAND
                            break
                    else:
                        # Reset
                        silence_seconds_left = self.silence_seconds
            elif state == State.AFTER_COMMAND:
                # Voice command has finished recording
                if self.stt_audio_callback is not None:
                    self.stt_audio_callback(stt_audio_bytes)

                stt_audio_bytes = bytes()

                # Command has ended, get text and trigger callback
                text = self.stt.stream_stop() or ""

                # Callback to handle STT text
                if self.text_callback is not None:
                    self.text_callback(text)

                # Back to detecting wake word
                state = State.DETECT_WAKEWORD

                # Clear any buffered STT chunks
                stt_chunks.clear()

                # Reset wakeword detector state, if available
                if hasattr(self.hotword, "reset"):
                    self.hotword.reset()

    def stop(self):
        self._is_running = False

