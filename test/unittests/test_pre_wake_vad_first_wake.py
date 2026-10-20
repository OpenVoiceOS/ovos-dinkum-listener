"""The wake word is heard on the first activation when pre-wake VAD is on.

A real ``DinkumVoiceLoop`` runs over a scripted microphone with a real
``HotwordContainer`` in the state a freshly created container has. The VAD
fires one chunk after the wake word starts, so the onset of the wake word is
only in the audio the loop rewinds into the hotword engines.
"""

import itertools
import time
import unittest
from unittest.mock import Mock, patch

from ovos_plugin_manager.templates.hotwords import HotWordEngine
from ovos_utils.fakebus import FakeBus

from ovos_dinkum_listener.voice_loop.hotwords import HotwordContainer, HotwordState
from ovos_dinkum_listener.voice_loop.voice_loop import DinkumVoiceLoop, ListeningState

CHUNK_SIZE = 2048
SILENCE = bytes(CHUNK_SIZE)
ONSET = b"\x01" * CHUNK_SIZE  # quiet start of the wake word, below the VAD
CODA = b"\x02" * CHUNK_SIZE  # the VAD fires on this chunk


class OnsetEngine(HotWordEngine):
    """Hears the wake word only if it was fed the onset and then the coda."""

    def __init__(self, name: str):
        super().__init__(name, {"module": "test"})
        self.fed = []

    def update(self, chunk: bytes):
        self.fed.append(chunk)

    def found_wake_word(self) -> bool:
        return ONSET in self.fed and CODA in self.fed[self.fed.index(ONSET) :]


class ScriptedMic:
    chunk_size = CHUNK_SIZE
    sample_width = 2
    seconds_per_chunk = 0.0

    def __init__(self, chunks, on_exhausted):
        self.chunks = list(chunks)
        self.on_exhausted = on_exhausted

    def read_chunk(self):
        if not self.chunks:
            self.on_exhausted()
            return None
        return self.chunks.pop(0)


def entry(engine, listen):
    return {
        "engine": engine,
        "sound": None,
        "bus_event": None,
        "utterance": None,
        "stt_lang": "en-us",
        "listen": listen,
        "wakeup": False,
        "stopword": False,
    }


class TestPreWakeVadFirstWake(unittest.TestCase):
    def setUp(self):
        loaded = HotwordContainer._loaded.is_set()
        HotwordContainer._loaded.set()
        self.addCleanup(lambda: loaded or HotwordContainer._loaded.clear())

        self.listen = OnsetEngine("hey_mycroft")
        self.hot = OnsetEngine("some_hotword")
        self.hotwords = HotwordContainer(bus=FakeBus())
        self.hotwords._plugins = {
            "hey_mycroft": entry(self.listen, listen=True),
            "some_hotword": entry(self.hot, listen=False),
        }

        vad = Mock()
        vad.is_silence.side_effect = lambda chunk: chunk != CODA
        stt = Mock()
        stt.lang = "en-us"
        self.loop = DinkumVoiceLoop(
            mic=Mock(chunk_size=CHUNK_SIZE, sample_width=2, seconds_per_chunk=0.0),
            hotwords=self.hotwords,
            stt=stt,
            fallback_stt=None,
            vad=vad,
            transformers=Mock(),
            speech_seconds=0.1,
            silence_seconds=0.2,
            timeout_seconds=1.0,
            timeout_seconds_with_silence=0.5,
            confirmation_seconds=0.3,
            min_stt_confidence=0.6,
            max_transcripts=1,
            vad_pre_wake_enabled=True,
        )
        self.loop.wake_callback = Mock()

    def test_first_wake_word_after_start_is_detected(self):
        """The container starts in HOTWORD, as in the service."""
        self.assertEqual(self.hotwords.state, HotwordState.HOTWORD)
        chunks = [SILENCE] * 8 + [ONSET, CODA] + [SILENCE] * 2
        self.loop.mic = ScriptedMic(chunks, self.loop.stop)
        self.loop.start()
        self.loop.run()

        self.loop.wake_callback.assert_called_once_with()
        self.assertEqual(self.loop.state, ListeningState.BEFORE_COMMAND)

    def test_wake_word_after_a_false_vad_trigger_is_detected(self):
        """Noise opens the VAD window, no wake word follows, the window ends and
        the loop goes back to PRE_WAKE_VAD."""
        chunks = [CODA] + [SILENCE] * 8 + [ONSET, CODA] + [SILENCE] * 2
        self.loop.mic = ScriptedMic(chunks, self.loop.stop)
        ticks = itertools.count(1_000_000)
        self.loop.start()
        with patch.object(time, "time", side_effect=lambda: float(next(ticks))):
            self.loop.run()

        self.loop.wake_callback.assert_called_once_with()

    def test_rewound_audio_still_reaches_the_other_hotwords(self):
        chunks = [SILENCE] * 8 + [ONSET, CODA] + [SILENCE] * 2
        self.loop.mic = ScriptedMic(chunks, self.loop.stop)
        self.loop.start()
        self.loop.run()

        self.assertIn(ONSET, self.hot.fed)
        self.assertIn(CODA, self.hot.fed)

    def test_closed_vad_window_does_not_replay_fed_audio(self):
        """Continuous speech keeps re-triggering the VAD after each 5 s window
        closes. The engines hear every chunk once, in order."""
        self.listen.found_wake_word = lambda: False
        self.hot.found_wake_word = lambda: False
        self.loop.vad.is_silence.side_effect = lambda chunk: False
        chunks = [i.to_bytes(4, "big") * (CHUNK_SIZE // 4) for i in range(1, 40)]
        self.loop.mic = ScriptedMic(chunks, self.loop.stop)
        ticks = itertools.count(1_000_000)
        self.loop.start()
        with patch.object(time, "time", side_effect=lambda: float(next(ticks))):
            self.loop.run()

        self.assertEqual(self.listen.fed, chunks)
        self.assertEqual(self.hot.fed, chunks)
