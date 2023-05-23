import unittest


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
    # TODO


class TestChunkInfo(unittest.TestCase):
    from ovos_dinkum_listener.voice_loop.voice_loop import ChunkInfo
    # TODO


class TestDinkumVoiceLoop(unittest.TestCase):
    from ovos_dinkum_listener.voice_loop.voice_loop import DinkumVoiceLoop
    # TODO


if __name__ == '__main__':
    unittest.main()
