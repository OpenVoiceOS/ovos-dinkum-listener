"""Namespace bus-message tests.

The listener emits the OVOS-AUDIO-IN-1 §5 spec utterance topic
``ovos.utterance.handle`` (``SpecMessage.UTTERANCE``). Legacy compatibility on
the ``recognizer_loop:utterance`` namespace is provided transparently by the
bus-namespace translation layer (``ovos_bus_client.MessageBusClient`` /
``ovos_utils.fakebus.FakeBus``) — both flags ON by default during migration — so
that mirroring is verified there, not here. These tests pin the service-side
contract: the spec topic is emitted with the transcribed utterances.
"""
import shutil
import unittest
from os import environ, makedirs
from os.path import join, dirname
from unittest.mock import Mock, MagicMock, patch

from ovos_spec_tools import SpecMessage
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
        self.seen = {}
        # listen on the spec topic; the bus may also mirror it onto the legacy
        # topic, which is the translation layer's concern, not this service's.
        self.bus.on(str(SpecMessage.UTTERANCE),
                    lambda m: self.seen.__setitem__(m.msg_type, m))

    def test_emits_spec_utterance_topic(self):
        self.service._stt_text([("hello world", 0.9)], {"lang": "en-US"})
        self.assertIn(str(SpecMessage.UTTERANCE), self.seen)
        self.assertEqual(
            self.seen[str(SpecMessage.UTTERANCE)].data["utterances"],
            ["hello world"])


if __name__ == "__main__":
    unittest.main()
