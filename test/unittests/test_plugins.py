import unittest


class TestPlugins(unittest.TestCase):
    def test_fake_stream_thread(self):
        from ovos_dinkum_listener.plugins import FakeStreamThread
        # TODO

    def test_fake_streaming_stt(self):
        from ovos_dinkum_listener.plugins import FakeStreamingSTT
        # TODO

    def test_load_stt_module(self):
        from ovos_dinkum_listener.plugins import load_stt_module
        # TODO

    def test_load_fallback_stt(self):
        from ovos_dinkum_listener.plugins import load_fallback_stt
        # TODO


if __name__ == '__main__':
    unittest.main()
