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
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Deque, Optional

from ovos_config import Configuration
from ovos_plugin_manager.stt import StreamingSTT
from ovos_plugin_manager.vad import VADEngine
from ovos_utils.log import LOG

from ovos_dinkum_listener.transformers import AudioTransformersService
from ovos_dinkum_listener.voice_loop.hotwords import HotwordContainer, HotwordState
from ovos_dinkum_listener.voice_loop.microphone import Microphone


class ListeningState(str, Enum):
    DETECT_WAKEWORD = "wakeword"
    WAITING_CMD = "continuous"

    RECORDING = "recording"

    SLEEPING = "sleeping"
    CHECK_WAKE_UP = "wake_up"

    BEFORE_COMMAND = "before_cmd"
    IN_COMMAND = "in_cmd"
    AFTER_COMMAND = "after_cmd"


class ListeningMode(str, Enum):
    """ global listening mode """
    WAKEWORD = "wakeword"
    CONTINUOUS = "continuous"
    HYBRID = "hybrid"
    SLEEPING = "sleeping"


@dataclass
class VoiceLoop:
    mic: Microphone
    hotwords: HotwordContainer
    stt: StreamingSTT
    fallback_stt: StreamingSTT
    vad: VADEngine
    transformers: AudioTransformersService

    def start(self):
        raise NotImplementedError()

    def run(self):
        raise NotImplementedError()

    def stop(self):
        raise NotImplementedError()

    @staticmethod
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
class ChunkInfo:
    is_speech: bool = False
    energy: float = 0.0


WakeCallback = Callable[[], None]
TextCallback = Callable[[str, dict], None]
AudioCallback = Callable[[bytes, dict], None]
ChunkCallback = Callable[[ChunkInfo], None]


@dataclass
class DinkumVoiceLoop(VoiceLoop):
    speech_seconds: float
    silence_seconds: float
    timeout_seconds: float
    num_stt_rewind_chunks: int
    num_hotword_keep_chunks: int
    skip_next_wake: bool = False
    hotword_chunks: Deque = field(default_factory=deque)
    stt_chunks: Deque = field(default_factory=deque)
    stt_audio_bytes: bytes = bytes()
    last_ww: float = -1
    speech_seconds_left: float = 0
    silence_seconds_left: float = 0
    timeout_seconds_left: float = 0
    state: ListeningState = ListeningState.DETECT_WAKEWORD
    listen_mode: ListeningMode = ListeningMode.WAKEWORD
    wake_callback: Optional[WakeCallback] = None
    text_callback: Optional[TextCallback] = None
    listenword_audio_callback: Optional[AudioCallback] = None
    hotword_audio_callback: Optional[AudioCallback] = None
    stopword_audio_callback: Optional[AudioCallback] = None
    wakeupword_audio_callback: Optional[AudioCallback] = None
    stt_audio_callback: Optional[AudioCallback] = None
    recording_audio_callback: Optional[AudioCallback] = None
    chunk_callback: Optional[ChunkCallback] = None
    recording_filename: str = "rec"
    is_muted: bool = False
    _is_running: bool = False
    _chunk_info: ChunkInfo = field(default_factory=ChunkInfo)

    def start(self):
        self._is_running = True
        self.state = ListeningState.DETECT_WAKEWORD
        self.last_ww = -1
        listener_config = Configuration().get("listener", {})
        if listener_config.get("continuous_listen", False):
            self.listen_mode = ListeningMode.CONTINUOUS
        elif listener_config.get("hybrid_listen", False):
            self.listen_mode = ListeningMode.HYBRID
        else:
            self.listen_mode = ListeningMode.WAKEWORD

    def run(self):
        # Voice command state
        self.speech_seconds_left = self.speech_seconds
        self.silence_seconds_left = self.silence_seconds
        self.timeout_seconds_left = self.timeout_seconds
        self.state = ListeningState.DETECT_WAKEWORD

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
            if self.state == ListeningState.DETECT_WAKEWORD:
                if not self._detect_ww(chunk):  # check hotwords
                    if not self._detect_hot(chunk):
                        self.transformers.feed_audio(chunk)
            elif self.state == ListeningState.WAITING_CMD:
                self._wait_cmd(chunk)

            elif self.state == ListeningState.RECORDING:
                self._in_recording(chunk)

            elif self.state == ListeningState.SLEEPING:
                self._before_wakeup(chunk)
            elif self.state == ListeningState.CHECK_WAKE_UP:
                self._detect_wakeup(chunk)

            elif self.state == ListeningState.BEFORE_COMMAND:
                self._before_cmd(chunk)
            elif self.state == ListeningState.IN_COMMAND:
                self._in_cmd(chunk)
            elif self.state == ListeningState.AFTER_COMMAND:
                self._after_cmd(chunk)

            if self.chunk_callback is not None:
                self._chunk_info.energy = self.debiased_energy(chunk, self.mic.sample_width)
                self.chunk_callback(self._chunk_info)

    def reset_state(self):
        if self.listen_mode == ListeningMode.CONTINUOUS:
            self.state = ListeningState.WAITING_CMD
            self.hotwords.state = HotwordState.HOTWORD
        else:
            self.state = ListeningState.DETECT_WAKEWORD
            self.hotwords.state = HotwordState.LISTEN

    def go_to_sleep(self):
        self.state = ListeningState.SLEEPING

    def wakeup(self):
        self.reset_state()

    def start_recording(self, filename=None):
        self.recording_filename = filename or str(time.time())
        self.state = ListeningState.RECORDING

    def stop_recording(self):
        #  finished recording
        if self.recording_audio_callback is not None:
            metadata = {"recording_name": self.recording_filename}
            metadata = self.recording_audio_callback(self.stt_audio_bytes, metadata) or \
                       metadata
        self.reset_state()

    def _in_recording(self, chunk):
        self.hotwords.state = HotwordState.RECORDING
        self.hotwords.update(chunk)
        ww = self.hotwords.found()
        if ww:
            # stop recording
            self.stop_recording()

            self.transformers.feed_hotword(chunk)

            # Callback to handle recorded hotword audio
            if self.stopword_audio_callback is not None:
                hotword_audio_bytes = bytes()
                while self.hotword_chunks:
                    hotword_audio_bytes += self.hotword_chunks.popleft()
                self.stopword_audio_callback(hotword_audio_bytes,
                                             self.hotwords.get_ww(ww))
        else:
            # Recording voice command until user requests stop
            self._chunk_info.is_speech = not self.vad.is_silence(chunk)
            self.stt_audio_bytes += chunk
            self.stt_chunks.append(chunk)

            self.transformers.feed_speech(chunk)

    def _before_wakeup(self, chunk):
        self.hotwords.state = HotwordState.LISTEN
        if self._detect_ww(chunk):
            self.state = ListeningState.CHECK_WAKE_UP

    def _detect_wakeup(self, chunk):
        self.hotwords.state = HotwordState.WAKEUP
        self.hotwords.update(chunk)
        ww = self.hotwords.found()
        if ww:
            # get out of sleep mode
            self.state = self.state.DETECT_WAKEWORD
            self.hotwords.state = HotwordState.LISTEN

            # Callback to handle recorded hotword audio
            if self.wakeupword_audio_callback is not None:
                hotword_audio_bytes = bytes()
                while self.hotword_chunks:
                    hotword_audio_bytes += self.hotword_chunks.popleft()
                self.wakeupword_audio_callback(hotword_audio_bytes,
                                               self.hotwords.get_ww(ww))

            self.transformers.feed_hotword(chunk)
            return True
        elif time.time() - self.last_ww > 10:
            # require wake word again
            self.hotwords.state = HotwordState.LISTEN
            self.state = ListeningState.SLEEPING
        return False

    def _detect_hot(self, chunk):
        self.hotwords.state = HotwordState.HOTWORD
        self.hotwords.update(chunk)
        ww = self.hotwords.found()
        if ww:
            # Callback to handle recorded hotword audio
            if self.hotword_audio_callback is not None:
                hotword_audio_bytes = bytes()
                while self.hotword_chunks:
                    hotword_audio_bytes += self.hotword_chunks.popleft()
                metadata = self.hotword_audio_callback(hotword_audio_bytes,
                                                       self.hotwords.get_ww(ww))
                self.transformers.feed_hotword(chunk)
                return True
        return False

    def _detect_ww(self, chunk):
        self.hotwords.state = HotwordState.LISTEN
        self.hotword_chunks.append(chunk)
        self.stt_chunks.append(chunk)
        self.hotwords.update(chunk)

        ww = self.hotwords.found()
        if ww or self.skip_next_wake:

            # Callback to handle recorded hotword audio
            if (self.listenword_audio_callback is not None) and (
                    not self.skip_next_wake
            ):
                hotword_audio_bytes = bytes()
                while self.hotword_chunks:
                    hotword_audio_bytes += self.hotword_chunks.popleft()

                metadata = self.listenword_audio_callback(hotword_audio_bytes,
                                                          self.hotwords.get_ww(ww))

            self.skip_next_wake = False
            self.hotword_chunks.clear()

            # Callback to handle wake up
            if self.wake_callback is not None:
                self.wake_callback()

            if self.listen_mode == ListeningMode.SLEEPING:
                # Wake word detected, begin detecting "wake up" word
                self.state = ListeningState.CHECK_WAKE_UP
            else:
                # Wake word detected, begin recording voice command
                self.state = ListeningState.BEFORE_COMMAND
                self.speech_seconds_left = self.speech_seconds
                self.timeout_seconds_left = self.timeout_seconds
                self.stt_audio_bytes = bytes()
                self.stt.stream_start()
                if self.fallback_stt is not None:
                    self.fallback_stt.stream_start()

            # Reset the VAD internal state to avoid the model getting
            # into a degenerative state where it always reports silence.
            if hasattr(self.vad, "reset"):
                self.vad.reset()

            self.last_ww = time.time()
            self.transformers.feed_hotword(chunk)
            return True

        return False

    def _wait_cmd(self, chunk):
        # Recording voice command, but user has not spoken yet
        self._chunk_info.is_speech = not self.vad.is_silence(chunk)
        hot = False
        if self._chunk_info.is_speech:
            self.speech_seconds_left -= self.mic.seconds_per_chunk
            if self.speech_seconds_left <= 0:
                # Voice command has started, so start looking for the end.
                self.state = ListeningState.BEFORE_COMMAND
        else:
            # Reset
            self.speech_seconds_left = self.speech_seconds
            # check hotwords
            hot = self._detect_hot(chunk)

        if not hot:
            self.transformers.feed_audio(chunk)

    def _before_cmd(self, chunk):
        # Recording voice command, but user has not spoken yet
        self.transformers.feed_audio(chunk)

        self.stt_audio_bytes += chunk
        self.stt_chunks.append(chunk)
        while self.stt_chunks:
            stt_chunk = self.stt_chunks.popleft()
            self.stt.stream_data(stt_chunk)
            if self.fallback_stt is not None:
                self.fallback_stt.stream_data(stt_chunk)

            self.timeout_seconds_left -= self.mic.seconds_per_chunk
            if self.timeout_seconds_left <= 0:
                # Recording has timed out
                self.state = ListeningState.AFTER_COMMAND
                break

            # Wait for enough speech before looking for the end of the
            # command (silence).

            self._chunk_info.is_speech = not self.vad.is_silence(stt_chunk)

            if self._chunk_info.is_speech:
                self.speech_seconds_left -= self.mic.seconds_per_chunk
                if self.speech_seconds_left <= 0:
                    # Voice command has started, so start looking for the
                    # end.
                    self.state = ListeningState.IN_COMMAND
                    self.silence_seconds_left = self.silence_seconds
                    break
            else:
                # Reset
                self.speech_seconds_left = self.speech_seconds

    def _in_cmd(self, chunk):
        self.transformers.feed_speech(chunk)

        # Recording voice command until user stops speaking
        self.stt_audio_bytes += chunk
        self.stt_chunks.append(chunk)
        while self.stt_chunks:
            stt_chunk = self.stt_chunks.popleft()

            self.stt.stream_data(stt_chunk)
            if self.fallback_stt is not None:
                self.fallback_stt.stream_data(stt_chunk)

            self.timeout_seconds_left -= self.mic.seconds_per_chunk
            if self.timeout_seconds_left <= 0:
                # Recording has timed out
                self.state = ListeningState.AFTER_COMMAND
                break

            # Wait for enough silence before considering the command to be
            # ended.
            self._chunk_info.is_speech = not self.vad.is_silence(stt_chunk)

            if not self._chunk_info.is_speech:
                self.silence_seconds_left -= self.mic.seconds_per_chunk
                if self.silence_seconds_left <= 0:
                    # End of voice command detected
                    self.state = ListeningState.AFTER_COMMAND
                    break
            else:
                # Reset
                self.silence_seconds_left = self.silence_seconds

    def _validate_lang(self, lang):
        """ ensure lang classification from speech is one of the valid langs
        if not then drop classification, as there are no speakers of that language around this device
        """
        cfg = Configuration()
        default_lang = cfg.get("lang", "en-us")
        valid_langs = set([default_lang] + cfg.get("secondary_langs'", []))

        if lang in valid_langs:
            if lang != default_lang:
                LOG.info(f"replaced {default_lang} with {lang}")
            return v
        else:
            LOG.warning(f"ignoring classification: {lang} is not in enabled languages: {valid_langs}")

        return default_lang

    def _get_tx(self, stt_context):
        # handle lang detection from speech
        if "stt_lang" in stt_context:
            lang = self._validate_lang(stt_context["stt_lang"])
            # note: self.stt.stream is recreated every listen start
            # this is safe to do, and makes lang be passed to self.execute
            self.stt.stream.language = lang
            if self.fallback_stt:
                self.fallback_stt.stream.language = lang

        # get text and trigger callback
        try:
            text = self.stt.stream_stop() or ""
        except:
            LOG.exception("STT failed")
            text = ""

        if not text and self.fallback_stt is not None:
            LOG.info("Attempting fallback STT plugin")
            text = self.fallback_stt.stream_stop() or ""

        # TODO - some plugins return list of transcripts some just text
        # standardize support for this
        if isinstance(text, list):
            text = text[0]
        stt_context["transcription"] = text
        return text, stt_context

    def _after_cmd(self, chunk):
        # Command has ended, call transformers pipeline before STT
        chunk, stt_context = self.transformers.transform(chunk)
        LOG.debug(f"transformers metadata: {stt_context}")

        text, stt_context = self._get_tx(stt_context)


        # Voice command has finished recording
        if self.stt_audio_callback is not None:
            metadata = self.stt_audio_callback(self.stt_audio_bytes, stt_context)

        self.stt_audio_bytes = bytes()

        # Callback to handle STT text
        if self.text_callback is not None:
            self.text_callback(text, stt_context)

        # Back to detecting wake word
        if self.listen_mode == ListeningMode.CONTINUOUS or \
                self.listen_mode == ListeningMode.HYBRID:

            # Reset the VAD internal state to avoid the model getting
            # into a degenerative state where it always reports silence.
            if hasattr(self.vad, "reset"):
                self.vad.reset()

            self.state = ListeningState.WAITING_CMD
        else:
            self.state = ListeningState.DETECT_WAKEWORD

        # Clear any buffered STT chunks
        self.stt_chunks.clear()

        # Reset wakeword detector state, if available
        self.hotwords.reset()

    def stop(self):
        self._is_running = False
