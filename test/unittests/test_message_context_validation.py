"""OVOS-MSG-1 3.4 and 7: `destination` is compared by string equality.

The listener must not ascribe structure to `destination`. A value that
contains a configured native source name as a substring, `audioplayer` for
example, is a different consumer and must not read as the local device.
A legacy emitter that still sends a list of consumers must keep working:
each entry of the list is compared by equality.
"""
import shutil
import unittest
from os import environ, makedirs
from os.path import join, dirname
from unittest.mock import MagicMock, Mock, patch

from ovos_bus_client.message import Message
from ovos_utils.messagebus import FakeBus

_NATIVE = ["debug_cli", "audio", "mycroft-gui"]


class TestValidateMessageContext(unittest.TestCase):
    config_dir = join(dirname(__file__), "config_msg1_destination")
    service = None

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
    def setUp(self, load_stt, load_fallback, voice_loop, vad, mic_factory):
        from ovos_dinkum_listener.service import OVOSDinkumVoiceService
        from ovos_plugin_manager.templates.vad import VADEngine
        load_stt.return_value = Mock(shutdown=Mock())
        load_fallback.return_value = Mock(shutdown=Mock())
        vad.return_value = MagicMock(spec=VADEngine)
        bus = FakeBus()
        bus.started_running = True
        self.service = OVOSDinkumVoiceService(mic=Mock(stop=Mock()), bus=bus)
        self.service.validate_source = True

    def _check(self, destination):
        msg = Message("mycroft.mic.listen", {},
                      {"destination": destination})
        return self.service._validate_message_context(msg, _NATIVE)

    def test_exact_native_source_is_accepted(self):
        for name in _NATIVE:
            self.assertTrue(self._check(name), name)

    def test_substring_of_a_native_source_is_refused(self):
        # the bug this test pins: `audio` is a substring of each of these
        for name in ["audioplayer", "audio-peer", "peer-audio",
                     "mycroft-gui-remote", "debug_cli_proxy"]:
            self.assertFalse(self._check(name), name)

    def test_unrelated_destination_is_refused(self):
        self.assertFalse(self._check("some_other_consumer"))

    def test_absent_destination_is_a_broadcast(self):
        self.assertTrue(
            self.service._validate_message_context(
                Message("mycroft.mic.listen", {}, {}), _NATIVE))

    def test_legacy_list_destination_still_matches(self):
        # a pre OVOS-MSG-1 3.3 emitter addresses a list of consumers
        self.assertTrue(self._check(["audio"]))
        self.assertTrue(self._check(["skills", "audio"]))

    def test_legacy_list_destination_of_other_consumers_is_refused(self):
        self.assertFalse(self._check(["audioplayer"]))
        self.assertFalse(self._check(["skills", "some_other_consumer"]))

    def test_validate_source_off_accepts_everything(self):
        self.service.validate_source = False
        self.assertTrue(self._check("audioplayer"))


if __name__ == "__main__":
    unittest.main()
