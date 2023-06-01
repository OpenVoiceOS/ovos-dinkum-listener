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
from ovos_bus_client.session import SessionManager
from ovos_dinkum_listener.transformers import AudioTransformersService
from ovos_dinkum_listener.voice_loop.hotwords import HotwordContainer, HotwordState, HotWordException
from ovos_plugin_manager.templates.microphone import Microphone


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
            audioop.add(audio_data,
                        energy_bytes * (len(audio_data) // sample_width),
                        sample_width),
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
    speech_seconds: float = 0.3
    silence_seconds: float = 0.7
    timeout_seconds: float = 10.0
    num_stt_rewind_chunks: int = 2
    num_hotword_keep_chunks: int = 15
    skip_next_wake: bool = False
    hotword_chunks: Deque = field(default_factory=deque)
    stt_chunks: Deque = field(default_factory=deque)
    stt_audio_bytes: bytes = bytes()
    last_ww: float = -1.0
    speech_seconds_left: float = 0.0
    silence_seconds_left: float = 0.0
    timeout_seconds_left: float = 0.0
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

    @property
    def running(self) -> bool:
        """
        Return true while the loop is running
        """
        return self._is_running is True

    def start(self):
        """
        Start the Voice Loop; sets the listening mode based on configuration and
        prepares the loop to be run.
        """
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

        LOG.info(f"Listening mode: {self.listen_mode}")

    def run(self):
        """
        Run the VoiceLoop so long as `self._is_running` is True
        """
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
        n = self.num_stt_rewind_chunks + 1
        if self.listen_mode == ListeningMode.CONTINUOUS:
            self.stt_chunks: Deque[bytes] = deque(maxlen=3 * n)
        else:
            self.stt_chunks: Deque[bytes] = deque(maxlen=n)

        LOG.info(f"Starting loop in mode: {self.listen_mode}")

        while self._is_running:
            # If no audio is provided, raise an exception and stop the loop
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
                try:
                    if self.listen_mode == ListeningMode.CONTINUOUS:
                        LOG.info(f"Continuous listening mode, updating state")
                        self.state = ListeningState.WAITING_CMD
                    elif self._detect_ww(chunk):
                        LOG.info("Wakeword detected")
                    elif self._detect_hot(chunk):
                        LOG.info("Hotword detected")
                    else:
                        self.transformers.feed_audio(chunk)
                except HotWordException as e:
                    if self.hotwords.reload_on_failure:
                        LOG.warning(e)
                        self.hotwords.load_hotword_engines()
                    else:
                        raise e

            if self.state == ListeningState.WAITING_CMD:
                self._wait_cmd(chunk)

            elif self.state == ListeningState.RECORDING:
                self._in_recording(chunk)

            elif self.state == ListeningState.SLEEPING:
                self._before_wakeup(chunk)
            elif self.state == ListeningState.CHECK_WAKE_UP:
                self._detect_wakeup(chunk)

            elif self.state == ListeningState.BEFORE_COMMAND:
                LOG.debug("waiting for speech")
                self._before_cmd(chunk)
            elif self.state == ListeningState.IN_COMMAND:
                LOG.debug("recording speech")
                self._in_cmd(chunk)
            elif self.state == ListeningState.AFTER_COMMAND:
                LOG.info("speech finished")
                self._after_cmd(chunk)

            if self.chunk_callback is not None:
                self._chunk_info.energy = \
                    self.debiased_energy(chunk, self.mic.sample_width)
                self.chunk_callback(self._chunk_info)
        LOG.info(f"Loop stopped running")

    def reset_state(self):
        """
        Reset the internal state to the default
        Continuous Listening -> Waiting for Command
        Wakeword Listening -> Waiting for WW
        Hybrid Listening -> Waiting for WW
        """
        if self.listen_mode == ListeningMode.CONTINUOUS:
            self.state = ListeningState.WAITING_CMD
            self.hotwords.state = HotwordState.HOTWORD
        else:
            self.state = ListeningState.DETECT_WAKEWORD
            self.hotwords.state = HotwordState.LISTEN
        LOG.debug(f"state={self.state}|hotwords.state={self.hotwords.state}")

    def go_to_sleep(self):
        """
        Set the Listening State to "Sleeping" until woken
        """
        self.state = ListeningState.SLEEPING
        LOG.info("sleeping")

    def wakeup(self):
        """
        Reset the Listening State from "Sleeping" to the default for the current
        Listening Mode.
        """
        self.reset_state()
        LOG.info("wakeup")

    def start_recording(self, filename: Optional[str] = None):
        """
        Set the listening state to RECORDING and specify a file to record to
        @param filename: filename to record mic input to
        """
        self.recording_filename = filename or str(time.time())
        LOG.debug(f"Recording to {self.recording_filename}")
        self.state = ListeningState.RECORDING

    def stop_recording(self):
        """
        Stop recording, pass audio and metadata (recording filename) to the
        `recording_audio_callback` method and reset the Listening State
        """
        #  finished recording
        if self.recording_audio_callback is not None:
            metadata = {"recording_name": self.recording_filename}
            self.recording_audio_callback(self.stt_audio_bytes, metadata)
        LOG.debug("Finished recording")
        self.reset_state()

    def _in_recording(self, chunk: bytes):
        """
        Handle a chunk of audio while in the `RECORDING` state.
        Check for stop words in all cases and pass audio to any loaded audio
        transformers.

        If a "stop" hotword is detected, the appropriate method is called.

        If no "stop" hotword is detected, audio is evaluated by VAD and all
        audio frames are passed to audio transformers.

        @param chunk: bytes of audio captured
        """
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
            # Recording audio until user requests stop
            self._chunk_info.is_speech = not self.vad.is_silence(chunk)
            self.stt_audio_bytes += chunk
            self.stt_chunks.append(chunk)

            self.transformers.feed_speech(chunk)

    def _before_wakeup(self, chunk: bytes):
        """
        Handle a chunk of audio as unknown input while sleeping,
        passing to wakeup word detection.
        @param chunk: bytes of audio captured
        """
        self.hotwords.state = HotwordState.LISTEN
        if self._detect_ww(chunk):
            self.state = ListeningState.CHECK_WAKE_UP

    def _detect_wakeup(self, chunk: bytes) -> bool:
        """
        Handle a chunk of audio where a hotword has been detected.
        Determines if the detected hotword should exit sleeping mode.
        If a wake word has been spoken multiple times,
        @param chunk: bytes of audio captured
        @return: True if wake up word was detected
        """
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
            # TODO: What does this do?
        return False

    def _detect_hot(self, chunk: bytes) -> bool:
        """
        Check for a hotword in a chunk of unknown audio. If a hotword is
        detected, call `hotword_audio_callback` and pass audio to transformers.
        @param chunk: bytes of audio captured
        @return: True if a hotword was detected
        """
        self.hotwords.state = HotwordState.HOTWORD
        self.hotwords.update(chunk)
        ww = self.hotwords.found()
        if ww:
            # Callback to handle recorded hotword audio
            if self.hotword_audio_callback is not None:
                hotword_audio_bytes = bytes()
                while self.hotword_chunks:
                    hotword_audio_bytes += self.hotword_chunks.popleft()
                self.hotword_audio_callback(hotword_audio_bytes,
                                            self.hotwords.get_ww(ww))
                self.transformers.feed_hotword(chunk)
                return True
        return False

    def _detect_ww(self, chunk: bytes) -> bool:
        """
        Check for a wake word in a chunk of unknown audio. Audio is passed to
        hotwords in all cases. If a wake word is detected OR
        `self.skip_next_wake` is True, audio is passed to
        `listenword_audio_callback` and `wake_callback`.

        If WW detected and sleeping, check for wakeup word in next audio chunks
        else check for speech input for STT.

        @param chunk:bytes of audio captured
        @return: True if a wakeword was detected
        """
        self.hotwords.state = HotwordState.LISTEN
        self.hotword_chunks.append(chunk)
        self.stt_chunks.append(chunk)
        self.hotwords.update(chunk)

        ww = self.hotwords.found()
        if ww or self.skip_next_wake:
            LOG.debug(f"Wake word detected={ww}")
            # Callback to handle recorded hotword audio
            if (self.listenword_audio_callback is not None) and (
                    not self.skip_next_wake
            ):
                hotword_audio_bytes = bytes()
                while self.hotword_chunks:
                    hotword_audio_bytes += self.hotword_chunks.popleft()

                self.listenword_audio_callback(hotword_audio_bytes,
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

            self.last_ww = time.time()
            self.transformers.feed_hotword(chunk)
            return True

        return False

    def _wait_cmd(self, chunk: bytes):
        """
        Handle audio chunks while in continuous listening mode, before VAD has
        detected any speech.
        @param chunk: bytes of audio captured
        """
        # Recording voice command, but user has not spoken yet
        self._chunk_info.is_speech = not self.vad.is_silence(chunk)
        hot = False
        if self._chunk_info.is_speech:
            self.speech_seconds_left -= self.mic.seconds_per_chunk
            if self.speech_seconds_left <= 0:
                # Voice command has started, so start looking for the end.
                if self.listen_mode == ListeningMode.CONTINUOUS:
                    prev_audio = len(self.stt_chunks) * self.mic.seconds_per_chunk
                    LOG.debug(f"waiting for speech: {prev_audio}")
                    self.stt.stream_start()
                    if self.fallback_stt is not None:
                        self.fallback_stt.stream_start()
                    self.state = ListeningState.IN_COMMAND
                else:
                    self.state = ListeningState.BEFORE_COMMAND
        else:
            # Reset
            self.speech_seconds_left = self.speech_seconds
            # check hotwords
            hot = self._detect_hot(chunk)

        if not hot:
            self.transformers.feed_audio(chunk)
            if self.listen_mode == ListeningMode.CONTINUOUS:
                self.stt_audio_bytes += chunk
                self.stt_chunks.append(chunk)

    def _before_cmd(self, chunk: bytes):
        """
        Handle audio chunks after WW detection or listen triggered, before VAD
        has detected any speech.
        @param chunk: bytes of audio captured
        """
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
            try:
                self._chunk_info.is_speech = not self.vad.is_silence(stt_chunk)
            except Exception as e:
                LOG.exception(f"Error processing chunk of "
                              f"size={len(stt_chunk)}: {e}")

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

    def _in_cmd(self, chunk: bytes):
        """
        Handle audio chunks after VAD has identified speech and before the end
        of user speech is identified
        @param chunk: bytes of audio captured
        """
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

    def _validate_lang(self, lang: str) -> str:
        """
        ensure lang classification from speech is one of the valid langs
        if not then drop classification, as there are no speakers of that
        language around this device
        @param lang: BCP-47 language code to evaluate
        @return: validated language (or default)
        """
        default_lang = Configuration().get("lang", "en-us")
        s = SessionManager.get()
        valid_langs = [l.lower().split("-")[0] for l in s.valid_languages]
        l2 = lang.lower().split("-")[0]
        if l2 in valid_langs:
            if l2 != default_lang.lower().split("-")[0]:
                LOG.info(f"replaced {default_lang} with {lang}")
                return lang
        else:
            LOG.warning(f"ignoring classification: {lang} is not in enabled "
                        f"languages: {valid_langs}")

        return default_lang

    def _get_tx(self, stt_context: dict) -> (str, dict):
        """
        Get a string transcription of audio that was previously streamed to STT.
        @param stt_context: dict context determined by transformers service
        @return: string transcription and dict context
        """
        # handle lang detection from speech
        if "stt_lang" in stt_context:
            lang = self._validate_lang(stt_context["stt_lang"])
            stt_context["stt_lang"] = lang
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

    def _after_cmd(self, chunk: bytes):
        """
        Handle audio chunk after VAD has determined a command is ended.
        Calls `stt_audio_callback` and `text_callback` with finalized utterance
        recording and transcript. The loop is reset to the appropriate state for
        the next command.
        @param chunk: bytes of audio captured
        """
        # Command has ended, call transformers pipeline before STT
        chunk, stt_context = self.transformers.transform(chunk)

        text, stt_context = self._get_tx(stt_context)

        if text:
            LOG.debug(f"transformers metadata: {stt_context}")
            LOG.info(f"transcribed: {text}")
        else:
            LOG.info("nothing transcribed")
        # Voice command has finished recording
        if self.stt_audio_callback is not None:
            self.stt_audio_callback(self.stt_audio_bytes, stt_context)

        self.stt_audio_bytes = bytes()

        # Callback to handle STT text
        if self.text_callback is not None:
            self.text_callback(text, stt_context)

        # Back to detecting wake word
        if self.listen_mode == ListeningMode.CONTINUOUS or \
                self.listen_mode == ListeningMode.HYBRID:
            self.state = ListeningState.WAITING_CMD
        else:
            self.state = ListeningState.DETECT_WAKEWORD

        # Clear any buffered STT chunks
        self.stt_chunks.clear()

        # Reset wakeword detector state, if available
        self.hotwords.reset()

        # Reset the VAD internal state to avoid the model getting
        # into a degenerative state where it always reports silence.
        if hasattr(self.vad, "reset"):
            LOG.debug("reset VAD")
            self.vad.reset()

        self.timeout_seconds_left = self.timeout_seconds

    def stop(self):
        """
        Signal the VoiceLoop to stop processing audio.
        """
        self._is_running = False
