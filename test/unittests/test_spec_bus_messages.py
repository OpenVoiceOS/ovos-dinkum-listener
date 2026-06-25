"""Namespace bus-message tests.

During the namespace migration the utterance entry is dual-emitted on BOTH the
legacy ``recognizer_loop:utterance`` and the OVOS-AUDIO-IN-1 §5
``ovos.utterance.handle`` topics so nodes on either version interoperate;
consumers dedup on content. When ``legacy_namespace`` is False only the new
topic is emitted. Both modes are covered here.
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
    @patch("ovos_dinkum_listener.service.DinkumVoiceLoop")
    @patch("ovos_dinkum_listener.service.load_fallback_stt")
    @patch("ovos_dinkum_listener.service.load_stt_module")
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
        self.seen = {"recognizer_loop:utterance": [],
                     "ovos.utterance.handle": []}
        for topic in self.seen:
            self.bus.on(topic, lambda m: self.seen[m.msg_type].append(m))
        self._orig_legacy_ns = Configuration().get("legacy_namespace", True)

    def tearDown(self):
        Configuration()["legacy_namespace"] = self._orig_legacy_ns

    def test_legacy_namespace_dual_emits_both_topics(self):
        Configuration()["legacy_namespace"] = True
        self.service._stt_text([("hello world", 0.9)], {"lang": "en-US"})
        self.assertEqual(len(self.seen["recognizer_loop:utterance"]), 1)
        self.assertEqual(len(self.seen["ovos.utterance.handle"]), 1)
        # both carry the same payload
        for topic in self.seen:
            self.assertEqual(self.seen[topic][0].data["utterances"],
                             ["hello world"])
            self.assertEqual(self.seen[topic][0].data["lang"], "en-US")

    def test_spec_namespace_emits_only_spec_topic(self):
        Configuration()["legacy_namespace"] = False
        self.service._stt_text([("hello world", 0.9)], {"lang": "en-US"})
        self.assertEqual(len(self.seen["ovos.utterance.handle"]), 1)
        self.assertEqual(len(self.seen["recognizer_loop:utterance"]), 0)
        self.assertEqual(self.seen["ovos.utterance.handle"][0].data["utterances"],
                         ["hello world"])


if __name__ == "__main__":
    unittest.main()
