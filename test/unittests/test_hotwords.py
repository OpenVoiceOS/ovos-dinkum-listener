import unittest


class TestCyclicAudioBuffer(unittest.TestCase):
    from ovos_dinkum_listener.voice_loop.hotwords import CyclicAudioBuffer
    # TODO


class TestHotwordState(unittest.TestCase):
    def test_hotword_state(self):
        from ovos_dinkum_listener.voice_loop.hotwords import HotwordState
        for state in (HotwordState.LISTEN, HotwordState.HOTWORD,
                      HotwordState.RECORDING, HotwordState.WAKEUP):
            self.assertIsInstance(state, HotwordState)
            self.assertIsInstance(state, str)
            self.assertIsInstance(state.value, str)


class TestHotwordContainer(unittest.TestCase):
    from ovos_dinkum_listener.voice_loop.hotwords import HotwordContainer
    # TODO


if __name__ == '__main__':
    unittest.main()
