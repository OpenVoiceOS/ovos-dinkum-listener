"""End-to-end tests of free recording mode.

A real ``DinkumVoiceLoop`` runs over a scripted microphone with a real
``HotwordContainer`` whose stop-word and listen-word engines are ovoscope's
``MockHotWordEngine``. The recording is written by the service's own
``_save_recording``. The tests assert the audio saved to disk, the end of the
RECORDING state and the bus events, for a spoken stop word and for the
max-silence timeout.
"""

import json
import tempfile
import unittest
import wave
from pathlib import Path

from ovos_bus_client.message import Message
from ovos_plugin_manager.templates.hotwords import HotWordEngine
from ovoscope.voice_loop import MiniVoiceLoop, MockFileMicrophone, MockHotWordEngine

from ovos_dinkum_listener.service import OVOSDinkumVoiceService
from ovos_dinkum_listener.voice_loop.hotwords import HotwordContainer
from ovos_dinkum_listener.voice_loop.voice_loop import ListeningState

CHUNK_SIZE = 2048
CHUNK_SECONDS = CHUNK_SIZE / 2 / 16000
NEVER = 10**9


def speech_chunk(index: int) -> bytes:
    """A non-silent chunk that differs from every other index."""
    return bytes([index + 1]) * CHUNK_SIZE


SILENCE = bytes(CHUNK_SIZE)


class EngineAdapter(HotWordEngine):
    """The container only feeds ``HotWordEngine`` instances."""

    def __init__(self, mock: MockHotWordEngine):
        super().__init__(mock.key_phrase, {"module": "mock"})
        self.mock = mock

    def update(self, chunk: bytes):
        self.mock.update(chunk)

    def found_wake_word(self) -> bool:
        return self.mock.found_wake_word()

    def reset(self):
        self.mock.reset()


class RecordingMic(MockFileMicrophone):
    """Starts recording mode on the first read, as the listener does when it
    receives the recording request on the bus, and logs the loop's state before
    each read."""

    def __init__(self, loop, chunks, recording_name):
        super().__init__(b"", chunk_size=CHUNK_SIZE, silence_chunks=0)
        self._chunks = list(chunks)
        self.loop = loop
        self.recording_name = recording_name
        self.saved_path = None
        self.observed = []

    def read_chunk(self):
        if self._idx == 0:
            self.loop.start_recording(self.recording_name)
        self.observed.append(
            (
                self.loop.state,
                len(self.loop.stt_audio_bytes),
                self.saved_path is not None and self.saved_path.exists(),
            )
        )
        return super().read_chunk()


class TestRecordingModeE2E(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.save_dir = Path(self._tmp.name)
        self._loaded_before = HotwordContainer._loaded.is_set()
        HotwordContainer._loaded.set()
        self.addCleanup(self._restore_loaded)

    def _restore_loaded(self):
        if not self._loaded_before:
            HotwordContainer._loaded.clear()

    def _run(self, chunks, stop_after, max_silence=30.0):
        """Run the voice loop over ``chunks`` with recording mode requested on
        the first read. The stop-word engine fires on its ``stop_after``-th
        update."""
        listen = EngineAdapter(MockHotWordEngine("hey_mycroft", trigger_after=NEVER))
        stop = EngineAdapter(MockHotWordEngine("stop_recording", trigger_after=stop_after))
        harness = MiniVoiceLoop(ww_instances={})
        loop = harness.voice_loop
        loop.hotwords = HotwordContainer(bus=harness.bus)
        loop.hotwords._plugins = {
            "hey_mycroft": self._entry(listen, listen=True),
            "stop_recording": self._entry(stop, stopword=True),
        }
        loop.recording_mode_max_silence_seconds = max_silence

        service = OVOSDinkumVoiceService.__new__(OVOSDinkumVoiceService)
        service.voice_loop = loop

        mic = RecordingMic(loop, chunks, "session")
        mic.saved_path = self.save_dir / "session.wav"
        loop.recording_audio_callback = lambda audio, meta: service._save_recording(
            audio, meta, save_path=self.save_dir
        )
        mic.on_exhausted = loop.stop
        loop.mic = mic
        harness._messages.clear()
        loop.start()
        loop.run()
        return harness, loop, mic, stop

    @staticmethod
    def _entry(engine, listen=False, stopword=False):
        return {
            "engine": engine,
            "sound": None,
            "bus_event": None,
            "utterance": None,
            "stt_lang": "en-us",
            "listen": listen,
            "wakeup": False,
            "stopword": stopword,
        }

    def _saved_audio(self) -> bytes:
        with wave.open(str(self.save_dir / "session.wav")) as wav:
            self.assertEqual(wav.getframerate(), 16000)
            self.assertEqual(wav.getsampwidth(), 2)
            self.assertEqual(wav.getnchannels(), 1)
            return wav.readframes(wav.getnframes())

    @staticmethod
    def _types(harness):
        return [m.msg_type for m in harness._messages]

    def test_stop_word_ends_recording_and_closes_file(self):
        before = [speech_chunk(i) for i in range(6)]
        stop_chunk = speech_chunk(6)
        after = [speech_chunk(7)] + [SILENCE] * 3
        harness, loop, mic, stop = self._run(
            before + [stop_chunk] + after, stop_after=len(before) + 1
        )

        states = [state for state, _, _ in mic.observed]
        grown = [size for state, size, _ in mic.observed if state == ListeningState.RECORDING]
        self.assertEqual(grown, [i * CHUNK_SIZE for i in range(len(before) + 1)])
        self.assertFalse(
            any(on_disk for _, _, on_disk in mic.observed[: len(before) + 1]),
            "the recording is held in memory and written when it ends",
        )

        self.assertNotEqual(loop.state, ListeningState.RECORDING)
        self.assertEqual(loop.state, ListeningState.DETECT_WAKEWORD)
        self.assertEqual(states.count(ListeningState.RECORDING), len(before) + 1)
        self.assertEqual(stop.mock.update_count, len(before) + 1)

        self.assertEqual(self._saved_audio(), b"".join(before))
        meta = json.loads((self.save_dir / "session.json").read_text())
        self.assertEqual(meta["recording_name"], "session")

        types = self._types(harness)
        self.assertEqual(types.count("recognizer_loop:record_begin"), 1)
        self.assertEqual(types.count("recognizer_loop:record_end"), 1)
        self.assertLess(
            types.index("recognizer_loop:record_begin"),
            types.index("recognizer_loop:record_end"),
        )

    def test_max_silence_ends_recording_without_stop_word(self):
        speech = [speech_chunk(i) for i in range(3)]
        silent_chunks_kept = 5
        max_silence = 3.5 * CHUNK_SECONDS
        chunks = speech + [SILENCE] * 12
        harness, loop, mic, stop = self._run(
            chunks, stop_after=NEVER, max_silence=max_silence
        )

        self.assertEqual(stop.mock.update_count, len(speech) + 5)
        self.assertEqual(loop.state, ListeningState.DETECT_WAKEWORD)
        self.assertEqual(
            self._saved_audio(), b"".join(speech) + SILENCE * silent_chunks_kept
        )
        types = self._types(harness)
        self.assertEqual(types.count("recognizer_loop:record_end"), 1)

    def test_speech_resets_the_silence_timer(self):
        max_silence = 3.5 * CHUNK_SECONDS
        chunks = (
            [speech_chunk(0)]
            + [SILENCE] * 3
            + [speech_chunk(1)]
            + [SILENCE] * 3
            + [speech_chunk(2)]
            + [SILENCE] * 12
        )
        harness, loop, mic, stop = self._run(
            chunks, stop_after=NEVER, max_silence=max_silence
        )
        expected = b"".join(chunks[:8]) + speech_chunk(2) + SILENCE * 5
        self.assertEqual(self._saved_audio(), expected)
        self.assertEqual(loop.state, ListeningState.DETECT_WAKEWORD)


if __name__ == "__main__":
    unittest.main()
