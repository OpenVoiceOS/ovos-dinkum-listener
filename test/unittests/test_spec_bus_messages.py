"""Namespace bus-message tests.

The utterance entry topic is emitted in exactly one namespace, chosen by the
``legacy_namespace`` config (default True): the legacy
``recognizer_loop:utterance`` or the OVOS-AUDIO-IN-1 §5 ``ovos.utterance.handle``.
Both modes are covered here.
"""
import shutil
import unittest
from os import environ, makedirs
from os.path import join, dirname
from unittest.mock import Mock, MagicMock, patch

from ovos_config.config import Configuration
from ovos_utils.messagebus import FakeBus


class TestUtteranceEntryNamespace(unittest.TestCase):

    config_dir = join(dirname(__file__), "config_spec")

    @classmethod
    def setUpClass(cls):
        environ["XDG_CONFIG_HOME"] = cls.config_dir
        makedirs(cls.config_dir, exist_ok=True)

    @classmethod
    def tearDownClass(cls):
        environ.pop("XDG_CONFIG_HOME", None)
        shutil.rmtree(cls.config_dir, ignore_errors=True)

    @patch("ovos_dinkum_listener.service.OVOSMicrophoneFactory.create")
    @patch("ovos_dinkum_listener.service.OVOSVADFactory.create")
    @patch("ovos_dinkum_listener.voice_loop.DinkumVoiceLoop")
    @patch("ovos_dinkum_listener.plugins.load_fallback_stt")
    @patch("ovos_dinkum_listener.plugins.load_stt_module")
    def _make_service(self, load_stt, load_fallback, voice_loop, vad, mic_factory):
        from ovos_dinkum_listener.service import OVOSDinkumVoiceService
        from ovos_plugin_manager.templates.vad import VADEngine
        load_stt.return_value = Mock(shutdown=Mock())
        load_fallback.return_value = Mock(shutdown=Mock())
        vad.return_value = MagicMock(spec=VADEngine)
        return OVOSDinkumVoiceService(mic=Mock(stop=Mock()), bus=self.bus)

    def setUp(self):
        self.bus = FakeBus()
        self.bus.started_running = True
        self.service = self._make_service()
        self.seen = {}
        for topic in ("recognizer_loop:utterance", "ovos.utterance.handle"):
            self.bus.on(topic, lambda m: self.seen.__setitem__(m.msg_type, m))

    def tearDown(self):
        Configuration()["legacy_namespace"] = True

    def test_legacy_namespace_emits_only_legacy_topic(self):
        Configuration()["legacy_namespace"] = True
        self.service._stt_text([("hello world", 0.9)], {"lang": "en-US"})
        self.assertIn("recognizer_loop:utterance", self.seen)
        self.assertNotIn("ovos.utterance.handle", self.seen)
        self.assertEqual(self.seen["recognizer_loop:utterance"].data["utterances"],
                         ["hello world"])

    def test_spec_namespace_emits_only_spec_topic(self):
        Configuration()["legacy_namespace"] = False
        self.service._stt_text([("hello world", 0.9)], {"lang": "en-US"})
        self.assertIn("ovos.utterance.handle", self.seen)
        self.assertNotIn("recognizer_loop:utterance", self.seen)
        self.assertEqual(self.seen["ovos.utterance.handle"].data["utterances"],
                         ["hello world"])


if __name__ == "__main__":
    unittest.main()
