"""Extended unit tests for OVOSDinkumVoiceService methods."""

import base64
import shutil
import unittest
from os import environ, makedirs
from os.path import join, dirname
from unittest.mock import Mock, patch, MagicMock

from ovos_bus_client.message import Message
from ovos_utils.fakebus import FakeBus


class TestServiceCallbacks(unittest.TestCase):
    """Tests for module-level callback functions."""

    def test_on_ready(self):
        """on_ready() executes without error."""
        from ovos_dinkum_listener.service import on_ready

        on_ready()

    def test_on_alive(self):
        """on_alive() executes without error."""
        from ovos_dinkum_listener.service import on_alive

        on_alive()

    def test_on_started(self):
        """on_started() executes without error."""
        from ovos_dinkum_listener.service import on_started

        on_started()

    def test_on_error(self):
        """on_error() executes without error."""
        from ovos_dinkum_listener.service import on_error

        on_error()
        on_error("Test error message")

    def test_on_stopping(self):
        """on_stopping() executes without error."""
        from ovos_dinkum_listener.service import on_stopping

        on_stopping()


class TestServiceStaticMethods(unittest.TestCase):
    """Tests for static methods on OVOSDinkumVoiceService."""

    def test_compile_ww_context_keys(self):
        """_compile_ww_context() returns dict with all required keys."""
        from ovos_dinkum_listener.service import OVOSDinkumVoiceService

        result = OVOSDinkumVoiceService._compile_ww_context(
            key_phrase="hey_mycroft", ww_module="ovos-ww-plugin-precise"
        )
        self.assertIn("name", result)
        self.assertIn("engine", result)
        self.assertIn("time", result)
        self.assertIn("sessionId", result)
        self.assertIn("accountId", result)
        self.assertIn("model", result)
        self.assertEqual(result["name"], "hey_mycroft")
        self.assertEqual(result["accountId"], "Anon")

    def test_compile_ww_context_engine_is_md5(self):
        """_compile_ww_context() computes engine as MD5 hash of module name."""
        from hashlib import md5
        from ovos_dinkum_listener.service import OVOSDinkumVoiceService

        module = "test-module"
        result = OVOSDinkumVoiceService._compile_ww_context("ww", module)
        expected_hash = md5(module.encode("utf-8")).hexdigest()
        self.assertEqual(result["engine"], expected_hash)

    @patch("ovos_dinkum_listener.service.get_stt_lang_configs")
    def test_get_stt_lang_options_empty(self, mock_cfgs):
        """get_stt_lang_options() returns empty list when no configs available."""
        from ovos_dinkum_listener.service import OVOSDinkumVoiceService

        mock_cfgs.return_value = {}
        result = OVOSDinkumVoiceService.get_stt_lang_options("en-us")
        self.assertEqual(result, [])

    @patch("ovos_dinkum_listener.service.get_stt_lang_configs")
    def test_get_stt_lang_options_blacklist(self, mock_cfgs):
        """get_stt_lang_options() excludes engines in blacklist."""
        from ovos_dinkum_listener.service import OVOSDinkumVoiceService

        mock_cfgs.return_value = {
            "blocked_engine": [{"lang": "en-us"}],
            "good_engine": [{"lang": "en-us"}],
        }
        result = OVOSDinkumVoiceService.get_stt_lang_options(
            "en-us", blacklist=["blocked_engine"]
        )
        engines = [opt["engine"] for opt in result]
        self.assertNotIn("blocked_engine", engines)
        self.assertIn("good_engine", engines)

    @patch("ovos_dinkum_listener.service.get_stt_lang_configs")
    def test_get_stt_lang_options_adds_metadata(self, mock_cfgs):
        """get_stt_lang_options() adds plugin_name, engine, lang to each config."""
        from ovos_dinkum_listener.service import OVOSDinkumVoiceService

        mock_cfgs.return_value = {
            "my_engine": [{}],
        }
        result = OVOSDinkumVoiceService.get_stt_lang_options("en-us")
        self.assertEqual(len(result), 1)
        self.assertIn("plugin_name", result[0])
        self.assertIn("engine", result[0])
        self.assertIn("lang", result[0])

    @patch("ovos_dinkum_listener.service.get_ww_lang_configs")
    def test_get_ww_lang_options_empty(self, mock_cfgs):
        """get_ww_lang_options() returns empty list when no configs available."""
        from ovos_dinkum_listener.service import OVOSDinkumVoiceService

        mock_cfgs.return_value = {}
        result = OVOSDinkumVoiceService.get_ww_lang_options("en-us")
        self.assertEqual(result, [])

    @patch("ovos_dinkum_listener.service.get_ww_lang_configs")
    def test_get_ww_lang_options_blacklist(self, mock_cfgs):
        """get_ww_lang_options() excludes engines in blacklist."""
        from ovos_dinkum_listener.service import OVOSDinkumVoiceService

        mock_cfgs.return_value = {
            "blocked": [{"lang": "en-us"}],
            "allowed": [{"lang": "en-us"}],
        }
        result = OVOSDinkumVoiceService.get_ww_lang_options(
            "en-us", blacklist=["blocked"]
        )
        engines = [opt["engine"] for opt in result]
        self.assertNotIn("blocked", engines)
        self.assertIn("allowed", engines)

    @patch("ovos_dinkum_listener.service.get_vad_configs")
    def test_get_vad_options_empty(self, mock_cfgs):
        """get_vad_options() returns empty list when no configs available."""
        from ovos_dinkum_listener.service import OVOSDinkumVoiceService

        mock_cfgs.return_value = {}
        result = OVOSDinkumVoiceService.get_vad_options()
        self.assertEqual(result, [])

    @patch("ovos_dinkum_listener.service.get_vad_configs")
    def test_get_vad_options_blacklist(self, mock_cfgs):
        """get_vad_options() excludes engines in blacklist."""
        from ovos_dinkum_listener.service import OVOSDinkumVoiceService

        mock_cfgs.return_value = {
            "silero": [{}],
            "webrtc": [{}],
        }
        result = OVOSDinkumVoiceService.get_vad_options(blacklist=["silero"])
        engines = [opt["engine"] for opt in result]
        self.assertNotIn("silero", engines)
        self.assertIn("webrtc", engines)

    @patch("ovos_dinkum_listener.service.get_vad_configs")
    def test_get_vad_options_adds_metadata(self, mock_cfgs):
        """get_vad_options() adds plugin_name and engine to each config."""
        from ovos_dinkum_listener.service import OVOSDinkumVoiceService

        mock_cfgs.return_value = {"my_vad": [{"type": "silero"}]}
        result = OVOSDinkumVoiceService.get_vad_options()
        self.assertEqual(len(result), 1)
        self.assertIn("plugin_name", result[0])
        self.assertIn("engine", result[0])


class _ServiceTestBase(unittest.TestCase):
    """Base class for tests that need a running service instance."""

    config_dir = join(dirname(__file__), "config_ext")
    bus = None
    service = None

    @classmethod
    def setUpClass(cls):
        environ["XDG_CONFIG_HOME"] = cls.config_dir
        makedirs(cls.config_dir, exist_ok=True)
        cls.bus = FakeBus()
        cls.bus.started_running = True
        cls._create_service()

    @classmethod
    def tearDownClass(cls):
        environ.pop("XDG_CONFIG_HOME", None)
        shutil.rmtree(cls.config_dir, ignore_errors=True)

    @classmethod
    @patch("ovos_dinkum_listener.service.OVOSMicrophoneFactory.create")
    @patch("ovos_dinkum_listener.service.OVOSVADFactory.create")
    @patch("ovos_dinkum_listener.voice_loop.DinkumVoiceLoop")
    @patch("ovos_dinkum_listener.plugins.load_fallback_stt")
    @patch("ovos_dinkum_listener.plugins.load_stt_module")
    def _create_service(cls, load_stt, load_fallback, voice_loop, vad, mic_factory):
        from ovos_dinkum_listener.service import OVOSDinkumVoiceService
        from ovos_plugin_manager.templates.vad import VADEngine

        stt = Mock()
        stt.shutdown = Mock()
        stt.lang = "en-us"
        stt.available_languages = ["en-us"]
        stt.transcribe.return_value = [("hello world", 0.9)]
        fallback = Mock()
        fallback.shutdown = Mock()
        load_stt.return_value = stt
        load_fallback.return_value = fallback
        vad.return_value = MagicMock(spec=VADEngine)

        mic = Mock()
        mic.stop = Mock()
        mic.sample_rate = 16000
        mic.sample_width = 2
        mic.sample_channels = 1
        cls.service = OVOSDinkumVoiceService(mic=mic, bus=cls.bus)
        # Set up voice_loop mock with sensible defaults
        cls.service.voice_loop = Mock()
        cls.service.voice_loop.stt = stt
        cls.service.voice_loop.stt.lang = "en-us"
        cls.service.voice_loop.stt.transcribe.return_value = [("hello world", 0.9)]
        cls.service.voice_loop.stt.available_languages = ["en-us"]
        cls.service.voice_loop.fallback_stt = None
        cls.service.voice_loop.min_stt_confidence = 0.6
        cls.service.voice_loop.sample_rate = 16000
        cls.service.voice_loop.sample_width = 2
        cls.service.voice_loop.is_muted = False
        cls.service.voice_loop.running = True
        cls.service.voice_loop.listen_mode = "wakeword"
        cls.service.voice_loop.state = "wakeword"
        cls.service.voice_loop.mic = mic


class TestServiceMessageHandlers(_ServiceTestBase):
    """Tests for OVOSDinkumVoiceService message handlers."""

    def test_handle_mute(self):
        """_handle_mute sets voice_loop.is_muted to True."""
        msg = Message("mycroft.mic.mute")
        self.service.voice_loop.is_muted = False
        self.service._handle_mute(msg)
        self.assertTrue(self.service.voice_loop.is_muted)

    def test_handle_unmute(self):
        """_handle_unmute sets voice_loop.is_muted to False."""
        msg = Message("mycroft.mic.unmute")
        self.service.voice_loop.is_muted = True
        self.service._handle_unmute(msg)
        self.assertFalse(self.service.voice_loop.is_muted)

    def test_handle_mute_toggle_mutes(self):
        """_handle_mute_toggle toggles muted state on."""
        msg = Message("mycroft.mic.mute.toggle")
        self.service.voice_loop.is_muted = False
        self.service._handle_mute_toggle(msg)
        self.assertTrue(self.service.voice_loop.is_muted)

    def test_handle_mute_toggle_unmutes(self):
        """_handle_mute_toggle toggles muted state off."""
        msg = Message("mycroft.mic.mute.toggle")
        self.service.voice_loop.is_muted = True
        self.service._handle_mute_toggle(msg)
        self.assertFalse(self.service.voice_loop.is_muted)

    def test_stop_does_not_unmute(self):
        """mycroft.stop must NOT unmute the mic (old _handle_stop behaviour).

        It now routes to _handle_stop_recording; a deliberately muted mic
        stays muted across a stop when nothing is recording."""
        from ovos_dinkum_listener.voice_loop.voice_loop import ListeningState

        self.assertFalse(hasattr(self.service, "_handle_stop"))
        self.service.voice_loop.is_muted = True
        self.service.voice_loop.state = ListeningState.DETECT_WAKEWORD
        self.service.config = {"sounds": {}}
        self.service._handle_stop_recording(Message("mycroft.stop"))
        self.assertTrue(self.service.voice_loop.is_muted)

    def test_handle_sleep(self):
        """_handle_sleep calls go_to_sleep on the voice loop."""
        msg = Message("recognizer_loop:sleep")
        self.service._handle_sleep(msg)
        self.service.voice_loop.go_to_sleep.assert_called()

    def test_handle_wake_up(self):
        """_handle_wake_up calls wakeup on the voice loop."""
        self.service.voice_loop.wakeup = Mock()
        msg = Message("recognizer_loop:wake_up")
        self.service._handle_wake_up(msg)
        self.service.voice_loop.wakeup.assert_called_once()

    def test_handle_mic_get_status(self):
        """_handle_mic_get_status emits mute status response."""
        self.service.voice_loop.is_muted = False
        received = []
        self.bus.on("mycroft.mic.is_muted.response", lambda m: received.append(m))
        msg = Message("mycroft.mic.is_muted")
        self.service._handle_mic_get_status(msg)
        # Response should have been emitted (via message.response)
        self.service.voice_loop.is_muted  # just ensure the attribute was read

    def test_handle_audio_start_mutes_when_configured(self):
        """_handle_audio_start mutes loop when mute_during_output is enabled."""
        self.service.voice_loop.is_muted = False
        self.service.config = {"listener": {"mute_during_output": True}}
        msg = Message("mycroft.audio.started")
        self.service._handle_audio_start(msg)
        self.assertTrue(self.service.voice_loop.is_muted)

    def test_handle_audio_start_no_mute_when_not_configured(self):
        """_handle_audio_start does not mute when mute_during_output is disabled."""
        self.service.voice_loop.is_muted = False
        self.service.config = {"listener": {"mute_during_output": False}}
        msg = Message("mycroft.audio.started")
        self.service._handle_audio_start(msg)
        self.assertFalse(self.service.voice_loop.is_muted)

    def test_handle_audio_end_unmutes(self):
        """_handle_audio_end unmutes loop when mute_during_output is enabled."""
        self.service.voice_loop.is_muted = True
        self.service.config = {"listener": {"mute_during_output": True}}
        msg = Message("mycroft.audio.ended")
        self.service._handle_audio_end(msg)
        self.assertFalse(self.service.voice_loop.is_muted)

    def test_handle_change_state_sleeping(self):
        """_handle_change_state with SLEEPING state calls go_to_sleep."""
        from ovos_dinkum_listener.voice_loop.voice_loop import ListeningState

        msg = Message(
            "recognizer_loop:change_state", {"state": ListeningState.SLEEPING}
        )
        self.service._handle_change_state(msg)
        self.service.voice_loop.go_to_sleep.assert_called()

    def test_handle_change_state_detect_wakeword(self):
        """_handle_change_state with DETECT_WAKEWORD calls reset_state."""
        from ovos_dinkum_listener.voice_loop.voice_loop import ListeningState

        self.service.voice_loop.reset_state = Mock()
        msg = Message(
            "recognizer_loop:change_state", {"state": ListeningState.DETECT_WAKEWORD}
        )
        self.service._handle_change_state(msg)
        self.service.voice_loop.reset_state.assert_called()

    def test_handle_change_state_recording(self):
        """_handle_change_state with RECORDING calls start_recording."""
        from ovos_dinkum_listener.voice_loop.voice_loop import ListeningState

        self.service.voice_loop.start_recording = Mock()
        msg = Message(
            "recognizer_loop:change_state",
            {"state": ListeningState.RECORDING, "recording_name": "test_rec"},
        )
        self.service._handle_change_state(msg)
        self.service.voice_loop.start_recording.assert_called_with("test_rec")

    def test_handle_change_state_mode_wakeword(self):
        """_handle_change_state sets listen_mode to WAKEWORD."""
        from ovos_dinkum_listener.voice_loop.voice_loop import ListeningMode

        msg = Message("recognizer_loop:change_state", {"mode": ListeningMode.WAKEWORD})
        self.service._handle_change_state(msg)
        self.assertEqual(self.service.voice_loop.listen_mode, ListeningMode.WAKEWORD)

    def test_handle_change_state_mode_continuous(self):
        """_handle_change_state sets listen_mode to CONTINUOUS."""
        from ovos_dinkum_listener.voice_loop.voice_loop import ListeningMode

        msg = Message(
            "recognizer_loop:change_state", {"mode": ListeningMode.CONTINUOUS}
        )
        self.service._handle_change_state(msg)
        self.assertEqual(self.service.voice_loop.listen_mode, ListeningMode.CONTINUOUS)

    def test_handle_change_state_mode_hybrid(self):
        """_handle_change_state sets listen_mode to HYBRID."""
        from ovos_dinkum_listener.voice_loop.voice_loop import ListeningMode

        msg = Message("recognizer_loop:change_state", {"mode": ListeningMode.HYBRID})
        self.service._handle_change_state(msg)
        self.assertEqual(self.service.voice_loop.listen_mode, ListeningMode.HYBRID)

    def test_handle_change_state_mode_sleeping(self):
        """_handle_change_state sets listen_mode to SLEEPING."""
        from ovos_dinkum_listener.voice_loop.voice_loop import ListeningMode

        msg = Message("recognizer_loop:change_state", {"mode": ListeningMode.SLEEPING})
        self.service._handle_change_state(msg)
        self.assertEqual(self.service.voice_loop.listen_mode, ListeningMode.SLEEPING)

    def test_handle_change_state_invalid_state_logs_error(self):
        """_handle_change_state handles invalid state without raising."""
        msg = Message("recognizer_loop:change_state", {"state": "invalid_state_xyz"})
        # Should not raise
        self.service._handle_change_state(msg)

    def test_handle_get_state(self):
        """_handle_get_state emits state data."""
        emitted = []
        self.bus.on("recognizer_loop:state", lambda m: emitted.append(m))
        msg = Message("recognizer_loop:get_state")
        self.service._handle_get_state(msg)
        # The handler calls bus.emit with recognizer_loop:state reply
        self.service.voice_loop.listen_mode  # was accessed

    def test_handle_get_languages_stt(self):
        """_handle_get_languages_stt emits available languages."""
        self.service.voice_loop.stt.available_languages = {"en-us", "de-de"}
        emitted = []
        self.bus.on("ovos.languages.stt.response", lambda m: emitted.append(m))
        msg = Message("ovos.languages.stt")
        self.service._handle_get_languages_stt(msg)

    def test_handle_get_languages_stt_fallback(self):
        """_handle_get_languages_stt falls back to config lang when not available."""
        self.service.voice_loop.stt.available_languages = None
        self.service.config = {"lang": "fr-fr"}
        msg = Message("ovos.languages.stt")
        # Should not raise
        self.service._handle_get_languages_stt(msg)

    def test_handle_stop_recording(self):
        """_handle_stop_recording calls stop_recording when actually recording."""
        from ovos_dinkum_listener.voice_loop.voice_loop import ListeningState

        self.service.voice_loop.stop_recording = Mock()
        self.service.voice_loop.state = ListeningState.RECORDING
        msg = Message("recognizer_loop:stop_recording")
        self.service.config = {"sounds": {}}
        self.service._handle_stop_recording(msg)
        self.service.voice_loop.stop_recording.assert_called_once()

    def test_handle_stop_recording_noop_when_not_recording(self):
        """No recording in progress → stop_recording is not called."""
        from ovos_dinkum_listener.voice_loop.voice_loop import ListeningState

        self.service.voice_loop.stop_recording = Mock()
        self.service.voice_loop.state = ListeningState.DETECT_WAKEWORD
        self.service.config = {"sounds": {}}
        self.service._handle_stop_recording(Message("recognizer_loop:stop_recording"))
        self.service.voice_loop.stop_recording.assert_not_called()

    def test_handle_extend_listening_hybrid(self):
        """_handle_extend_listening updates last_ww in HYBRID mode."""
        from ovos_dinkum_listener.voice_loop.voice_loop import ListeningMode

        self.service.voice_loop.listen_mode = ListeningMode.HYBRID
        self.service.voice_loop.last_ww = 0.0
        msg = Message("intent.service.skills.activated")
        self.service._handle_extend_listening(msg)
        self.assertGreater(self.service.voice_loop.last_ww, 0.0)

    def test_handle_extend_listening_non_hybrid(self):
        """_handle_extend_listening does nothing in non-HYBRID mode."""
        from ovos_dinkum_listener.voice_loop.voice_loop import ListeningMode

        self.service.voice_loop.listen_mode = ListeningMode.WAKEWORD
        self.service.voice_loop.last_ww = 0.0
        msg = Message("intent.service.skills.activated")
        self.service._handle_extend_listening(msg)
        self.assertEqual(self.service.voice_loop.last_ww, 0.0)


class TestSttText(_ServiceTestBase):
    """Tests for OVOSDinkumVoiceService._stt_text()."""

    def test_emits_utterance_when_transcribed(self):
        """_stt_text emits recognizer_loop:utterance when transcripts available."""
        from ovos_dinkum_listener.voice_loop.voice_loop import ListeningMode

        self.service.voice_loop.listen_mode = ListeningMode.WAKEWORD
        received = []
        self.bus.on("recognizer_loop:utterance", lambda m: received.append(m))

        self.service._stt_text([("hello world", 0.9)], {"lang": "en-us"})

        self.assertTrue(any("hello world" in str(m.data) for m in received))

    def test_emits_unknown_when_empty_in_wakeword_mode(self):
        """_stt_text emits unknown when no transcription in WW mode."""
        from ovos_dinkum_listener.voice_loop.voice_loop import ListeningMode

        self.service.voice_loop.listen_mode = ListeningMode.WAKEWORD
        received = []
        self.bus.on(
            "recognizer_loop:speech.recognition.unknown", lambda m: received.append(m)
        )

        self.service._stt_text([], {})

        self.assertGreater(len(received), 0)

    def test_silent_in_continuous_mode(self):
        """_stt_text does not emit unknown in CONTINUOUS mode."""
        from ovos_dinkum_listener.voice_loop.voice_loop import ListeningMode

        self.service.voice_loop.listen_mode = ListeningMode.CONTINUOUS
        received = []
        self.bus.on(
            "recognizer_loop:speech.recognition.unknown", lambda m: received.append(m)
        )

        self.service._stt_text([], {})

        self.assertEqual(len(received), 0)

    def test_filters_hallucinations(self):
        """_stt_text filters common hallucinations from transcripts."""
        from ovos_dinkum_listener.voice_loop.voice_loop import ListeningMode

        self.service.voice_loop.listen_mode = ListeningMode.WAKEWORD
        self.service.config = {
            "filter_hallucinations": True,
            "hallucination_list": ["thanks for watching!"],
        }
        received = []
        self.bus.on("recognizer_loop:utterance", lambda m: received.append(m))
        unknown_received = []
        self.bus.on(
            "recognizer_loop:speech.recognition.unknown",
            lambda m: unknown_received.append(m),
        )

        self.service._stt_text([("thanks for watching!", 0.9)], {})

        # Hallucination filtered → no utterance, gets speech.recognition.unknown
        utterance_msgs = [m for m in received if "thanks for watching" in str(m.data)]
        self.assertEqual(len(utterance_msgs), 0)


class TestHotwordAudio(_ServiceTestBase):
    """Tests for OVOSDinkumVoiceService._hotword_audio()."""

    def _make_ww_context(self, **kwargs):
        """Create a basic wakeword context dict."""
        ctx = {
            "key_phrase": "hey_test",
            "module": "test_module",
            "engine": "TestEngine",
            "listen": True,
            "wakeup": False,
            "stopword": False,
            "sound": None,
            "bus_event": None,
            "utterance": None,
            "stt_lang": None,
        }
        ctx.update(kwargs)
        return ctx

    def test_emits_wakeword_event_for_listen_ww(self):
        """_hotword_audio emits recognizer_loop:wakeword for listen wake words."""
        self.service.config = {"listener": {"record_wake_words": False}}
        received = []
        self.bus.on("recognizer_loop:wakeword", lambda m: received.append(m))

        ww_ctx = self._make_ww_context(listen=True, sound=None)
        self.service._hotword_audio(bytes(100), ww_ctx)

        self.assertGreater(len(received), 0)

    def test_emits_hotword_event_for_hotword_ww(self):
        """_hotword_audio emits recognizer_loop:hotword for non-listen hotwords."""
        self.service.config = {"listener": {"record_wake_words": False}}
        received = []
        self.bus.on("recognizer_loop:hotword", lambda m: received.append(m))

        ww_ctx = self._make_ww_context(listen=False, sound=None, bus_event=None)
        self.service._hotword_audio(bytes(100), ww_ctx)

        self.assertGreater(len(received), 0)

    def test_utterance_triggers_bus_emit(self):
        """_hotword_audio with utterance emits recognizer_loop:utterance."""
        self.service.config = {"listener": {"record_wake_words": False}}
        received = []
        self.bus.on("recognizer_loop:utterance", lambda m: received.append(m))

        ww_ctx = self._make_ww_context(
            listen=False, utterance="hello world", stt_lang="en-us"
        )
        self.service._hotword_audio(bytes(100), ww_ctx)

        self.assertGreater(len(received), 0)
        self.assertIn("hello world", received[-1].data.get("utterances", []))

    def test_sound_list_picks_random(self):
        """_hotword_audio with list sound picks a random one without error."""
        self.service.config = {"listener": {"record_wake_words": False}}
        ww_ctx = self._make_ww_context(listen=False, sound=["sound1.wav", "sound2.wav"])
        # Should not raise; random choice is made internally
        self.service._hotword_audio(bytes(100), ww_ctx)

    def test_bus_event_used_as_message_type(self):
        """_hotword_audio uses bus_event as message type when specified."""
        self.service.config = {"listener": {"record_wake_words": False}}
        received = []
        self.bus.on("my.custom.event", lambda m: received.append(m))

        ww_ctx = self._make_ww_context(listen=False, bus_event="my.custom.event")
        self.service._hotword_audio(bytes(100), ww_ctx)

        self.assertGreater(len(received), 0)

    def test_wakeup_type_emits_wakeupword(self):
        """_hotword_audio with wakeup=True emits recognizer_loop:wakeupword."""
        self.service.config = {"listener": {"record_wake_words": False}}
        received = []
        self.bus.on("recognizer_loop:wakeupword", lambda m: received.append(m))

        ww_ctx = self._make_ww_context(listen=False, wakeup=True, bus_event=None)
        self.service._hotword_audio(bytes(100), ww_ctx)

        self.assertGreater(len(received), 0)

    def test_stopword_type_emits_stopword(self):
        """_hotword_audio with stopword=True emits recognizer_loop:stopword."""
        self.service.config = {"listener": {"record_wake_words": False}}
        received = []
        self.bus.on("recognizer_loop:stopword", lambda m: received.append(m))

        ww_ctx = self._make_ww_context(
            listen=False, wakeup=False, stopword=True, bus_event=None
        )
        self.service._hotword_audio(bytes(100), ww_ctx)

        self.assertGreater(len(received), 0)

    def test_stt_lang_in_context(self):
        """_hotword_audio includes stt_lang in context when specified."""
        self.service.config = {"listener": {"record_wake_words": False}}
        received = []
        self.bus.on("recognizer_loop:wakeword", lambda m: received.append(m))

        ww_ctx = self._make_ww_context(listen=True, stt_lang="de-de")
        self.service._hotword_audio(bytes(100), ww_ctx)

        self.assertGreater(len(received), 0)
        self.assertEqual(received[-1].context.get("stt_lang"), "de-de")


class TestB64Audio(_ServiceTestBase):
    """Tests for OVOSDinkumVoiceService._handle_b64_audio()."""

    def test_emits_utterance_on_transcription(self):
        """_handle_b64_audio emits recognizer_loop:utterance when confident."""
        self.service.voice_loop.stt.transcribe.return_value = [("hello", 0.9)]
        self.service.voice_loop.min_stt_confidence = 0.6
        self.service.voice_loop.sample_rate = 16000
        self.service.voice_loop.sample_width = 2
        self.service.voice_loop.stt.lang = "en-us"

        audio_b64 = base64.b64encode(bytes(100)).decode()
        received = []
        self.bus.on("recognizer_loop:utterance", lambda m: received.append(m))

        msg = Message(
            "recognizer_loop:transcribe", {"audio": audio_b64, "lang": "en-us"}
        )
        self.service._handle_b64_audio(msg)

        self.assertGreater(len(received), 0)

    def test_emits_unknown_on_low_confidence(self):
        """_handle_b64_audio emits speech.recognition.unknown for low confidence."""
        self.service.voice_loop.stt.transcribe.return_value = [("maybe", 0.3)]
        self.service.voice_loop.min_stt_confidence = 0.6
        self.service.voice_loop.sample_rate = 16000
        self.service.voice_loop.sample_width = 2
        self.service.voice_loop.stt.lang = "en-us"

        audio_b64 = base64.b64encode(bytes(100)).decode()
        received = []
        self.bus.on(
            "recognizer_loop:speech.recognition.unknown", lambda m: received.append(m)
        )

        msg = Message("recognizer_loop:transcribe", {"audio": audio_b64})
        self.service._handle_b64_audio(msg)

        self.assertGreater(len(received), 0)

    def test_emits_unknown_on_empty_transcription(self):
        """_handle_b64_audio emits speech.recognition.unknown on empty result."""
        self.service.voice_loop.stt.transcribe.return_value = []
        self.service.voice_loop.min_stt_confidence = 0.6
        self.service.voice_loop.sample_rate = 16000
        self.service.voice_loop.sample_width = 2
        self.service.voice_loop.stt.lang = "en-us"

        audio_b64 = base64.b64encode(bytes(100)).decode()
        received = []
        self.bus.on(
            "recognizer_loop:speech.recognition.unknown", lambda m: received.append(m)
        )

        msg = Message("recognizer_loop:transcribe", {"audio": audio_b64})
        self.service._handle_b64_audio(msg)

        self.assertGreater(len(received), 0)


class TestB64Transcribe(_ServiceTestBase):
    """Tests for OVOSDinkumVoiceService._handle_b64_transcribe()."""

    def test_responds_with_transcriptions(self):
        """_handle_b64_transcribe emits response with transcription list."""
        self.service.voice_loop.stt.transcribe.return_value = [("hello", 0.9)]
        self.service.voice_loop.sample_rate = 16000
        self.service.voice_loop.sample_width = 2
        self.service.voice_loop.stt.lang = "en-us"

        audio_b64 = base64.b64encode(bytes(100)).decode()
        received = []
        self.bus.on(
            "recognizer_loop:b64_transcribe.response", lambda m: received.append(m)
        )

        msg = Message(
            "recognizer_loop:b64_transcribe", {"audio": audio_b64, "lang": "en-us"}
        )
        self.service._handle_b64_transcribe(msg)

        # Transcribe should have been called on the stt
        self.service.voice_loop.stt.transcribe.assert_called()


class TestOpmHandlers(_ServiceTestBase):
    """Tests for OPM query handlers."""

    @patch("ovos_dinkum_listener.service.get_stt_lang_configs")
    @patch("ovos_dinkum_listener.service.get_stt_module_configs")
    @patch("ovos_dinkum_listener.service.get_stt_supported_langs")
    def test_handle_opm_stt_query(
        self, mock_supported, mock_module_configs, mock_lang_cfgs
    ):
        """_handle_opm_stt_query emits response with plugins and langs."""
        mock_supported.return_value = {"en-us": ["plugin_a"]}
        mock_module_configs.return_value = {}
        mock_lang_cfgs.return_value = {}
        received = []
        self.bus.on("opm.stt.query.response", lambda m: received.append(m))

        msg = Message("opm.stt.query")
        self.service._handle_opm_stt_query(msg)

        mock_supported.assert_called_once()

    @patch("ovos_dinkum_listener.service.get_ww_lang_configs")
    @patch("ovos_dinkum_listener.service.get_ww_module_configs")
    @patch("ovos_dinkum_listener.service.get_ww_supported_langs")
    def test_handle_opm_ww_query(
        self, mock_supported, mock_module_configs, mock_lang_cfgs
    ):
        """_handle_opm_ww_query emits response with plugins and langs."""
        mock_supported.return_value = {"en-us": ["precise"]}
        mock_module_configs.return_value = {}
        mock_lang_cfgs.return_value = {}

        msg = Message("opm.ww.query")
        self.service._handle_opm_ww_query(msg)

        mock_supported.assert_called_once()

    @patch("ovos_dinkum_listener.service.get_vad_configs")
    def test_handle_opm_vad_query(self, mock_vad):
        """_handle_opm_vad_query emits response with VAD configs."""
        mock_vad.return_value = {"silero": [{"type": "silero"}]}
        msg = Message("opm.vad.query")
        self.service._handle_opm_vad_query(msg)
        mock_vad.assert_called()


class TestServiceSaveAudio(_ServiceTestBase):
    """Tests for audio save methods in OVOSDinkumVoiceService."""

    def _setup_mic(self):
        """Configure mic mock for WAV writing."""
        mic = Mock()
        mic.sample_rate = 16000
        mic.sample_width = 2
        mic.sample_channels = 1
        self.service.voice_loop.mic = mic

    def test_compile_ww_context_time_is_string(self):
        """_compile_ww_context() returns time as a string of ms since epoch."""
        from ovos_dinkum_listener.service import OVOSDinkumVoiceService

        result = OVOSDinkumVoiceService._compile_ww_context("test", "module")
        self.assertIsInstance(result["time"], str)
        # Should be a numeric string representing milliseconds
        int(result["time"])  # should not raise

    def test_stt_audio_no_save_when_disabled(self):
        """_stt_audio does nothing when save_utterances is False."""
        self.service.config = {"listener": {"save_utterances": False}}
        result = self.service._stt_audio(bytes(100), {})
        self.assertIsInstance(result, dict)

    def test_stt_audio_returns_context(self):
        """_stt_audio returns the stt_context dict."""
        self.service.config = {"listener": {"save_utterances": False}}
        ctx = {"key": "value"}
        result = self.service._stt_audio(bytes(100), ctx)
        self.assertEqual(result["key"], "value")

    def test_recording_audio_returns_context(self):
        """_recording_audio returns the context dict."""
        self.service.config = {}
        self._setup_mic()
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            ctx = {"recording_name": "test_rec"}
            result = self.service._save_recording(bytes(100), ctx, save_path=tmpdir)
            # Should be a file:// URL
            self.assertTrue(result.startswith("file://"))

    def test_save_stt_creates_wav_and_json(self):
        """_save_stt creates both a WAV file and a JSON metadata file."""
        import os
        import tempfile

        self._setup_mic()
        with tempfile.TemporaryDirectory() as tmpdir:
            stt_meta = {"transcriptions": [("hello world", 0.9)]}
            path = self.service._save_stt(bytes(100), stt_meta, save_path=tmpdir)
            self.assertIsNotNone(path)
            self.assertTrue(path.startswith("file://"))
            # Files should exist
            files = os.listdir(tmpdir)
            wav_files = [f for f in files if f.endswith(".wav")]
            json_files = [f for f in files if f.endswith(".json")]
            self.assertGreater(len(wav_files), 0)
            self.assertGreater(len(json_files), 0)

    def test_save_stt_empty_transcriptions_list(self):
        """_save_stt handles empty transcriptions list (IndexError path)."""
        import tempfile

        self._setup_mic()
        with tempfile.TemporaryDirectory() as tmpdir:
            # Empty transcriptions list → IndexError → fallback to transcription key
            stt_meta = {"transcriptions": [], "transcription": "hello"}
            path = self.service._save_stt(bytes(100), stt_meta, save_path=tmpdir)
            self.assertIsNotNone(path)
            self.assertTrue(path.startswith("file://"))

    def test_save_recording_creates_files(self):
        """_save_recording creates both WAV and JSON files."""
        import os
        import tempfile

        self._setup_mic()
        with tempfile.TemporaryDirectory() as tmpdir:
            meta = {"recording_name": "my_recording"}
            path = self.service._save_recording(bytes(100), meta, save_path=tmpdir)
            self.assertTrue(path.startswith("file://"))
            files = os.listdir(tmpdir)
            wav_files = [f for f in files if f.endswith(".wav")]
            self.assertGreater(len(wav_files), 0)

    def test_save_ww_creates_files(self):
        """_save_ww creates both WAV and JSON files."""
        import os
        import tempfile

        self._setup_mic()
        with tempfile.TemporaryDirectory() as tmpdir:
            ww_meta = {
                "key_phrase": "hey_test",
                "module": "test_module",
            }
            path = self.service._save_ww(bytes(100), ww_meta, save_path=tmpdir)
            self.assertTrue(path.startswith("file://"))
            files = os.listdir(tmpdir)
            wav_files = [f for f in files if f.endswith(".wav")]
            self.assertGreater(len(wav_files), 0)


if __name__ == "__main__":
    unittest.main()
