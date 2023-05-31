import unittest

from unittest.mock import patch, Mock
from copy import copy
from ovos_plugin_manager.templates import StreamingSTT

_MOCK_CONFIG = {
    "lang": "global",
    "stt": {
        "module": "test_module",
        "fallback_module": "test_fallback",
        "test_module": {
            "config": True,
            "lang": "module"
        }
    }
}


class MockStreamingSTT(StreamingSTT):
    def create_streaming_thread(self):
        pass


class TestPlugins(unittest.TestCase):
    def test_fake_stream_thread(self):
        from ovos_dinkum_listener.plugins import FakeStreamThread
        # TODO

    def test_fake_streaming_stt(self):
        from ovos_dinkum_listener.plugins import FakeStreamingSTT
        # TODO

    @patch("ovos_dinkum_listener.plugins.Configuration")
    @patch("ovos_plugin_manager.stt.OVOSSTTFactory.create")
    def test_load_stt_module(self, create, config):
        config.return_value = _MOCK_CONFIG
        create.return_value = MockStreamingSTT()
        from ovos_dinkum_listener.plugins import load_stt_module

        # Test passed config
        stt = load_stt_module(_MOCK_CONFIG['stt'])
        create.assert_called_once_with(
            {"lang": "global", **_MOCK_CONFIG['stt']})
        self.assertIsInstance(stt, StreamingSTT)

        # Test default config
        stt = load_stt_module()
        create.assert_called_with(
            {"lang": "global", **_MOCK_CONFIG['stt']})
        self.assertIsInstance(stt, StreamingSTT)

        # Test module init raises exception
        # TODO

    @patch("ovos_dinkum_listener.plugins.Configuration")
    @patch("ovos_plugin_manager.stt.OVOSSTTFactory.create")
    def test_load_fallback_stt(self, create, config):
        config.return_value = _MOCK_CONFIG
        create.return_value = MockStreamingSTT()
        from ovos_dinkum_listener.plugins import load_fallback_stt

        # Test passed config global lang
        fallback = _MOCK_CONFIG['stt']['fallback_module']
        stt = load_fallback_stt(_MOCK_CONFIG['stt'])
        create.assert_called_once_with(
            {"module": fallback, fallback: {'lang': _MOCK_CONFIG['lang']}})
        self.assertIsInstance(stt, StreamingSTT)

        # Test passed config module lang
        test_config = copy(_MOCK_CONFIG.get('stt'))
        module = "test_module"
        test_config['fallback_module'] = module
        stt = load_fallback_stt(test_config)
        create.assert_called_with(
            {"module": module, module: _MOCK_CONFIG['stt'][module]})
        self.assertIsInstance(stt, StreamingSTT)

        # Test default config
        fallback = _MOCK_CONFIG['stt']['fallback_module']
        stt = load_fallback_stt()
        create.assert_called_with(
            {"module": fallback, fallback: {'lang': _MOCK_CONFIG['lang']}})
        self.assertIsInstance(stt, StreamingSTT)

        # Test no module configured
        create.reset_mock()
        test_config = _MOCK_CONFIG.get('stt')
        module = ""
        test_config['fallback_module'] = module
        stt = load_fallback_stt(test_config)
        self.assertIsNone(stt)
        create.assert_not_called()

        # Test module init raises exception
        # TODO


if __name__ == '__main__':
    unittest.main()
