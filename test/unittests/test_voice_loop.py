import unittest
from unittest.mock import Mock, patch


class TestEnums(unittest.TestCase):
    def test_listening_state(self):
        from ovos_dinkum_listener.voice_loop.voice_loop import ListeningState
        for state in (ListeningState.DETECT_WAKEWORD,
                      ListeningState.WAITING_CMD, ListeningState.RECORDING,
                      ListeningState.SLEEPING, ListeningState.CHECK_WAKE_UP,
                      ListeningState.BEFORE_COMMAND, ListeningState.IN_COMMAND,
                      ListeningState.AFTER_COMMAND):
            self.assertIsInstance(state, ListeningState)
            self.assertIsInstance(state, str)
            self.assertIsInstance(state.value, str)

    def test_listening_mode(self):
        from ovos_dinkum_listener.voice_loop.voice_loop import ListeningMode
        for state in (ListeningMode.WAKEWORD, ListeningMode.CONTINUOUS,
                      ListeningMode.HYBRID, ListeningMode.SLEEPING):
            self.assertIsInstance(state, ListeningMode)
            self.assertIsInstance(state, str)
            self.assertIsInstance(state.value, str)


class TestVoiceLoop(unittest.TestCase):
    from ovos_dinkum_listener.voice_loop.voice_loop import VoiceLoop
    mic = Mock()
    hotwords = Mock()
    stt = Mock()
    fallback_stt = Mock()
    vad = Mock()
    transformers = Mock()
    loop = VoiceLoop(mic=mic, hotwords=hotwords, stt=stt,
                     fallback_stt=fallback_stt, vad=vad,
                     transformers=transformers)

    def test_00_loop_init(self):
        self.assertEqual(self.loop.mic, self.mic)
        self.assertEqual(self.loop.hotwords, self.hotwords)
        self.assertEqual(self.loop.stt, self.stt)
        self.assertEqual(self.loop.fallback_stt, self.fallback_stt)
        self.assertEqual(self.loop.vad, self.vad)
        self.assertEqual(self.loop.transformers, self.transformers)

    def test_base_methods(self):
        with self.assertRaises(NotImplementedError):
            self.loop.start()

        with self.assertRaises(NotImplementedError):
            self.loop.run()

        with self.assertRaises(NotImplementedError):
            self.loop.stop()

    def test_debiased_energy(self):
        pass
        # TODO


class TestChunkInfo(unittest.TestCase):
    from ovos_dinkum_listener.voice_loop.voice_loop import ChunkInfo
    # TODO


class TestDinkumVoiceLoop(unittest.TestCase):
    from ovos_dinkum_listener.voice_loop.voice_loop import DinkumVoiceLoop
    mic = Mock()
    hotwords = Mock()
    stt = Mock()
    fallback_stt = Mock()
    vad = Mock()
    transformers = Mock()
    loop = DinkumVoiceLoop(mic=mic,
                           hotwords=hotwords,
                           stt=stt,
                           fallback_stt=fallback_stt,
                           vad=vad,
                           transformers=transformers)

    def test_00_loop_init(self):
        from typing import Deque
        from ovos_dinkum_listener.voice_loop.voice_loop import ListeningState, \
            ListeningMode, ChunkInfo
        self.assertIsInstance(self.loop.speech_seconds, float)
        self.assertIsInstance(self.loop.silence_seconds, float)
        self.assertIsInstance(self.loop.timeout_seconds, float)
        self.assertIsInstance(self.loop.num_stt_rewind_chunks, int)
        self.assertIsInstance(self.loop.num_hotword_keep_chunks, int)
        self.assertIsInstance(self.loop.skip_next_wake, bool)
        self.assertIsInstance(self.loop.hotword_chunks, Deque)
        self.assertIsInstance(self.loop.stt_chunks, Deque)
        self.assertIsInstance(self.loop.stt_audio_bytes, bytes)
        self.assertIsInstance(self.loop.last_ww, float)
        self.assertIsInstance(self.loop.speech_seconds_left, float)
        self.assertIsInstance(self.loop.silence_seconds_left, float)
        self.assertIsInstance(self.loop.timeout_seconds_left, float)
        self.assertIsInstance(self.loop.state, ListeningState)
        self.assertIsInstance(self.loop.listen_mode, ListeningMode)

        self.assertIsNone(self.loop.wake_callback)
        self.assertIsNone(self.loop.text_callback)
        self.assertIsNone(self.loop.listenword_audio_callback)
        self.assertIsNone(self.loop.hotword_audio_callback)
        self.assertIsNone(self.loop.stopword_audio_callback)
        self.assertIsNone(self.loop.wakeupword_audio_callback)
        self.assertIsNone(self.loop.stt_audio_callback)
        self.assertIsNone(self.loop.recording_audio_callback)
        self.assertIsNone(self.loop.chunk_callback)

        self.assertIsInstance(self.loop.recording_filename, str)
        self.assertIsInstance(self.loop.is_muted, bool)
        self.assertIsInstance(self.loop._is_running, bool)
        self.assertIsInstance(self.loop._chunk_info, ChunkInfo)

        self.assertEqual(self.loop.mic, self.mic)
        self.assertEqual(self.loop.hotwords, self.hotwords)
        self.assertEqual(self.loop.stt, self.stt)
        self.assertEqual(self.loop.fallback_stt, self.fallback_stt)
        self.assertEqual(self.loop.vad, self.vad)
        self.assertEqual(self.loop.transformers, self.transformers)

    @patch("ovos_dinkum_listener.voice_loop.voice_loop.Configuration")
    def test_start(self, config):
        from ovos_dinkum_listener.voice_loop import ListeningMode, \
            ListeningState
        mock_config = {"listener": {"continuous_listen": False,
                                    "hybrid_listen": False}}
        config.return_value = mock_config
        self.loop.start()
        self.assertTrue(self.loop.running)
        self.assertEqual(self.loop.listen_mode, ListeningMode.WAKEWORD)
        self.assertEqual(self.loop.state, ListeningState.DETECT_WAKEWORD)
        self.loop._running = False
        self.loop.state = None

        mock_config["listener"]["hybrid_listen"] = True
        self.loop.start()
        self.assertTrue(self.loop.running)
        self.assertEqual(self.loop.listen_mode, ListeningMode.HYBRID)
        self.assertEqual(self.loop.state, ListeningState.DETECT_WAKEWORD)
        self.loop._running = False
        self.loop.state = None

        mock_config["listener"]["continuous_listen"] = True
        self.loop.start()
        self.assertTrue(self.loop.running)
        self.assertEqual(self.loop.listen_mode, ListeningMode.CONTINUOUS)
        self.assertEqual(self.loop.state, ListeningState.DETECT_WAKEWORD)
        self.loop._running = False
        self.loop.state = None

        # Default, no config values
        config.return_value = dict()
        self.loop.start()
        self.assertTrue(self.loop.running)
        self.assertEqual(self.loop.listen_mode, ListeningMode.WAKEWORD)
        self.assertEqual(self.loop.state, ListeningState.DETECT_WAKEWORD)

        # Reset internals
        self.loop._running = False

    def test_run(self):
        from ovos_dinkum_listener.voice_loop import ListeningMode, \
            ListeningState

        def _raise(e, *args):
            raise e

        def _stop_loop(*args):
            self.loop._is_running = False

        real_detect_ww = self.loop._detect_ww
        self.loop._detect_ww = Mock()
        real_debiased_energy = self.loop.debiased_energy
        self.loop.debiased_energy = Mock()

        self.assertEqual(self.loop.listen_mode, ListeningMode.WAKEWORD)
        self.assertEqual(self.loop.state, ListeningState.DETECT_WAKEWORD)
        self.mic.read_chunk.return_value = bytes(4096)

        self.loop.chunk_callback = Mock(side_effect=_stop_loop)
        self.loop._is_running = True

        # Run 1x chunk, WW detected
        self.loop.run()
        self.loop._detect_ww.assert_called_once()
        self.loop.chunk_callback.assert_called_once()

        # Run 1x chunk, trigger hotword reload
        from ovos_dinkum_listener.voice_loop.hotwords import HotWordException
        self.loop._detect_ww.side_effect = lambda x: _raise(HotWordException, x)
        self.loop._is_running = True
        self.hotwords.reload_on_failure = True
        self.loop.run()
        self.hotwords.load_hotword_engines.assert_called_once()

        self.loop._is_running = True
        self.hotwords.reload_on_failure = False
        with self.assertRaises(HotWordException):
            self.loop.run()

        # Run 1x chunk, raise exception
        self.loop._detect_ww.side_effect = lambda x: _raise(ValueError, x)
        self.loop._is_running = True
        with self.assertRaises(ValueError):
            self.loop.run()

        self.loop._detect_ww = real_detect_ww
        self.loop.debiased_energy = real_debiased_energy


if __name__ == '__main__':
    unittest.main()
