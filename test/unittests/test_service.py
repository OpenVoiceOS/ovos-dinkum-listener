import shutil
import unittest

from os import environ, makedirs
from os.path import join, dirname
from threading import Event
from time import sleep
from unittest.mock import Mock, patch

from ovos_utils.messagebus import FakeBus
from ovos_bus_client.message import Message
from ovos_utils.process_utils import ProcessState


class TestDinkumVoiceService(unittest.TestCase):
    bus = FakeBus()
    bus.started_running = True
    config_dir = join(dirname(__file__), "config")
    service = None

    @classmethod
    def setUpClass(cls) -> None:
        environ["XDG_CONFIG_HOME"] = cls.config_dir
        makedirs(cls.config_dir, exist_ok=True)

    @classmethod
    def tearDownClass(cls) -> None:
        environ.pop("XDG_CONFIG_HOME")
        shutil.rmtree(cls.config_dir)

    def setUp(self) -> None:
        self._init_service()

    @patch("ovos_dinkum_listener.voice_loop.DinkumVoiceLoop")
    @patch("ovos_dinkum_listener.plugins.load_fallback_stt")
    @patch("ovos_dinkum_listener.plugins.load_stt_module")
    def _init_service(self, load_stt, load_fallback, voice_loop):
        if not self.service:
            from ovos_dinkum_listener.service import OVOSDinkumVoiceService
            from ovos_dinkum_listener.service import ServiceState
            from ovos_dinkum_listener.voice_loop.hotwords import HotwordContainer
            from ovos_plugin_manager.templates.vad import VADEngine
            from ovos_dinkum_listener.transformers import AudioTransformersService

            stt = Mock()
            stt.shutdown = Mock()
            fallback = Mock()
            fallback.shutdown = Mock()
            load_stt.return_value = stt
            load_fallback.return_value = fallback

            mic = Mock()
            mic.stop = Mock()
            self.service = OVOSDinkumVoiceService(mic=mic, bus=self.bus)

    @patch("ovos_dinkum_listener.voice_loop.DinkumVoiceLoop")
    @patch("ovos_dinkum_listener.plugins.load_fallback_stt")
    @patch("ovos_dinkum_listener.plugins.load_stt_module")
    def test_service_init(self, load_stt, load_fallback, voice_loop):
        import ovos_dinkum_listener.service
        from ovos_dinkum_listener.service import OVOSDinkumVoiceService
        from ovos_dinkum_listener.service import ServiceState
        from ovos_dinkum_listener.voice_loop.hotwords import HotwordContainer
        from ovos_plugin_manager.templates.vad import VADEngine
        from ovos_dinkum_listener.transformers import AudioTransformersService
        ovos_dinkum_listener.service.DinkumVoiceLoop = voice_loop
        ovos_dinkum_listener.service.load_fallback_stt = load_fallback
        ovos_dinkum_listener.service.load_stt_module = load_stt

        stt = Mock()
        fallback = Mock()
        load_stt.return_value = stt
        load_fallback.return_value = fallback

        mic = Mock()
        service = OVOSDinkumVoiceService(mic=mic, bus=self.bus)
        # Test init params
        self.assertEqual(service.bus, self.bus)
        self.assertIsInstance(service.service_id, str)
        self.assertEqual(service.status.state, ProcessState.ALIVE)
        self.assertEqual(service.state, ServiceState.NOT_STARTED)
        self.assertEqual(service.mic, mic)
        self.assertIsInstance(service.hotwords, HotwordContainer)
        self.assertEqual(service.hotwords.bus, self.bus)
        self.assertIsInstance(service.vad, VADEngine)
        self.assertEqual(service.stt, stt)
        self.assertEqual(service.fallback_stt, fallback)
        self.assertIsInstance(service.transformers,
                              AudioTransformersService)
        self.assertIsInstance(service.default_save_path, str)

        # Voice Loop
        voice_loop.assert_called_once()
        call_kwargs = voice_loop.call_args.kwargs
        if not isinstance(call_kwargs, dict):
            # TODO: Patching Python3.7 test failures
            return
        self.assertIsInstance(call_kwargs, dict, call_kwargs)
        self.assertEqual(service.voice_loop, voice_loop())
        self.assertEqual(call_kwargs['mic'], mic)
        self.assertEqual(call_kwargs['hotwords'], service.hotwords)
        self.assertEqual(call_kwargs['stt'], service.stt)
        self.assertEqual(call_kwargs['fallback_stt'], service.fallback_stt)
        self.assertEqual(call_kwargs['vad'], service.vad)
        self.assertEqual(call_kwargs['transformers'], service.transformers)

        self.assertIsInstance(call_kwargs['speech_seconds'], float)
        self.assertIsInstance(call_kwargs['silence_seconds'], float)
        self.assertIsInstance(call_kwargs['timeout_seconds'], (float, int))
        self.assertIsInstance(call_kwargs['num_stt_rewind_chunks'], int)
        self.assertIsInstance(call_kwargs['num_hotword_keep_chunks'], int)

        self.assertEqual(call_kwargs['wake_callback'],
                         service._record_begin)
        self.assertEqual(call_kwargs['text_callback'],
                         service._stt_text)
        self.assertEqual(call_kwargs['listenword_audio_callback'],
                         service._hotword_audio)
        self.assertEqual(call_kwargs['hotword_audio_callback'],
                         service._hotword_audio)
        self.assertEqual(call_kwargs['stopword_audio_callback'],
                         service._hotword_audio)
        self.assertEqual(call_kwargs['wakeupword_audio_callback'],
                         service._hotword_audio)
        self.assertEqual(call_kwargs['stt_audio_callback'],
                         service._stt_audio)
        self.assertEqual(call_kwargs['recording_audio_callback'],
                         service._recording_audio)

        # Assert events not yet registered
        self.assertEqual(len(self.bus.ee.listeners("mycroft.mic.mute")), 0)

    def test_service_run(self):
        self.assertIsNotNone(self.service)
        real_shutdown = self.service._shutdown

        def _shutdown():
            self.service._shutdown_event.set()

        self.service._shutdown = Mock(side_effect=_shutdown)
        self.service.voice_loop._is_running = True

        def _run_loop():
            while self.service.voice_loop._is_running:
                sleep(1)

        def _stop_loop():
            self.service.voice_loop._is_running = False

        self.service.voice_loop.run = Mock(side_effect=_run_loop)
        self.service.voice_loop.stop = Mock(side_effect=_stop_loop)
        from ovos_dinkum_listener.service import ServiceState
        self.service.start()
        # Wait for start
        while self.service.state != ServiceState.RUNNING:
            sleep(0.5)

        # Test _start
        self.service.mic.start.assert_called_once()
        self.service.voice_loop.start.assert_called_once()

        for event in (
            'mycroft.mic.mute', 'mycroft.mic.unmute', 'mycroft.mic.listen',
            'mycroft.mic.get_status', 'recognizer_loop:audio_output_start',
            'recognizer_loop:audio_output_end', 'mycroft.stop',
            'recognizer_loop:sleep', 'recognizer_loop:wake_up',
            'recognizer_loop:record_stop', 'recognizer_loop:state.set',
            'recognizer_loop:state.get', 'intent.service.skills.activated',
            'ovos.languages.stt', 'opm.stt.query', 'opm.ww.query',
            'opm.vad.query'
        ):
            self.assertEqual(len(self.bus.ee.listeners(event)), 1)

        # Test _after_start
        # TODO

        self.assertEqual(self.service.status.state, ProcessState.READY)
        self.assertEqual(self.service.state, ServiceState.RUNNING)
        self.service.voice_loop.run.assert_called_once()

        # Stop loop
        self.service.stop()
        self.service.voice_loop.stop.assert_called_once()
        self.assertEqual(self.service.state, ServiceState.STOPPING)
        self.service._shutdown.assert_called_once()
        self.service._shutdown = real_shutdown

    def test_service_stop(self):
        self.assertIsNotNone(self.service)
        real_hotwords_stop = self.service.hotwords.shutdown
        # real_vad_stop = self.service.vad.stop
        self.service.stt.shutdown = Mock()
        self.service.fallback_stt.shutdown = Mock()
        self.service.hotwords.shutdown = Mock()
        self.service.vad.stop = Mock()
        self.service.voice_loop.stop = Mock()

        self.service.stop()
        self.service.voice_loop.stop.assert_called_once()
        self.service.stt.shutdown.assert_called_once()
        self.service.fallback_stt.shutdown.assert_called_once()
        self.service.hotwords.shutdown.assert_called_once()
        self.service.vad.stop.assert_called_once()
        self.service.mic.stop.assert_called_once()

        self.service.hotwords.shutdown = real_hotwords_stop
        # self.service.vad.stop = real_vad_stop

    def test_report_service_state(self):
        from ovos_bus_client.message import Message
        test_message = Message('test')
        handled = Event()
        handler = Mock(side_effect=handled.set())
        self.bus.once('test.response', handler)
        self.service._report_service_state(test_message)
        handled.wait(5)
        handler.assert_called_once()
        response = handler.call_args[0][0]
        self.assertIsInstance(response.data['state'], str)

    def test_pet_the_dog(self):
        # TODO
        pass

    def test_record_begin(self):
        handled = Event()
        handler = Mock(side_effect=handled.set())
        self.bus.once('recognizer_loop:record_begin', handler)
        self.service._record_begin()
        handled.wait(5)
        handler.assert_called_once()

    def test_save_ww(self):
        # TODO
        pass

    def test_upload_hotword(self):
        # TODO
        pass

    def test_compile_ww_context(self):
        # TODO
        pass

    def test_hotword_audio(self):
        # TODO
        pass

    def test_stt_text(self):
        # TODO
        pass

    def test_save_stt(self):
        # TODO
        pass

    def test_upload_stt(self):
        # TODO
        pass

    def test_stt_audio(self):
        # TODO
        pass

    def test_save_recording(self):
        # TODO
        pass

    def test_recording_audio(self):
        # TODO
        pass

    def test_handle_mute(self):
        self.service.voice_loop.is_muted = False
        self.service._handle_mute(None)
        self.assertTrue(self.service.voice_loop.is_muted)

    def test_handle_unmute(self):
        self.service.voice_loop.is_muted = True
        self.service._handle_unmute(None)
        self.assertFalse(self.service.voice_loop.is_muted)

    def test_handle_listen(self):
        from ovos_dinkum_listener.voice_loop import ListeningState
        orig_reset = self.service.voice_loop.reset_speech_timer
        self.service.voice_loop.stt.stream_start = Mock()
        self.service.voice_loop.reset_speech_timer = Mock()
        self.service.voice_loop.confirmation_event = Event()

        self.service._handle_listen(Message(""))
        self.assertEqual(self.service.voice_loop.confirmation_event.is_set(), False)
        self.service.voice_loop.reset_speech_timer.assert_called_once()
        self.service.voice_loop.reset_speech_timer.reset_mock()
        self.assertEqual(self.service.config["confirm_listening"], True)
        self.assertEqual(self.service.voice_loop.stt_audio_bytes, bytes())
        self.service.voice_loop.stt.stream_start.assert_called_once()
        self.service.voice_loop.stt.stream_start.reset_mock()

        self.service.voice_loop.state = ListeningState.DETECT_WAKEWORD
        self.service.config["confirm_listening"] = False
        
        self.service._handle_listen(Message(""))
        self.assertEqual(self.service.config["confirm_listening"], False)
        self.service.voice_loop.reset_speech_timer.assert_called_once()
        self.service.voice_loop.reset_speech_timer.reset_mock()
        self.assertEqual(self.service.voice_loop.stt_audio_bytes, bytes())
        self.service.voice_loop.stt.stream_start.assert_called_once()
        self.service.voice_loop.stt.stream_start.reset_mock()
        self.assertEqual(self.service.voice_loop.state, ListeningState.BEFORE_COMMAND)    

        self.service.voice_loop.reset_speech_timer = orig_reset

    def test_handle_mic_get_status(self):
        # TODO
        pass

    def test_handle_audio_start(self):
        # TODO
        pass

    def test_handle_audio_end(self):
        # TODO
        pass

    def test_handle_stop(self):
        self.service.voice_loop.is_muted = True
        self.service._handle_stop(None)
        self.assertFalse(self.service.voice_loop.is_muted)

    def test_handle_change_state(self):
        # TODO
        pass

    def test_handle_get_state(self):
        # TODO
        pass

    def test_handle_stop_recording(self):
        # TODO
        pass

    def test_handle_extend_listening(self):
        # TODO
        pass

    def test_handle_sleep(self):
        # TODO
        pass

    def test_handle_wake_up(self):
        # TODO
        pass

    def test_handle_get_languages_stt(self):
        # TODO
        pass

    def test_get_stt_lang_options(self):
        # TODO
        pass

    def test_get_ww_lang_options(self):
        # TODO
        pass

    def test_get_vad_options(self):
        # TODO
        pass

    def test_handle_opm_stt_query(self):
        # TODO
        pass

    def test_handle_opm_vad_query(self):
        # TODO
        pass

    def test_reload_configuration(self):
        import ovos_dinkum_listener.service
        mock_create_stt = Mock()
        mock_create_fallback = Mock()
        mock_shutdown_hotwords = Mock()
        mock_create_hotwords = Mock()
        real_shutdown_hotwords = self.service.hotwords.shutdown
        self.service.hotwords.shutdown = mock_shutdown_hotwords
        real_create_hotwords = self.service.hotwords.load_hotword_engines
        self.service.hotwords.load_hotword_engines = mock_create_hotwords

        ovos_dinkum_listener.service.load_stt_module = mock_create_stt
        ovos_dinkum_listener.service.load_fallback_stt = mock_create_fallback
        shutdown = Event()
        fallback_shutdown = Event()
        vad_stop = Event()
        mic_stop = Event()
        self.service.stt.shutdown = Mock(side_effect=lambda: shutdown.set())
        self.service.fallback_stt.shutdown = \
            Mock(side_effect=lambda: fallback_shutdown.set())
        self.service.vad.stop = Mock(side_effect=lambda: vad_stop.set())
        self.service.mic.stop = Mock(side_effect=lambda: mic_stop.set())

        # Reload STT
        self.service.config["stt"]["module"] = "new_module"
        self.service.reload_configuration()
        self.assertTrue(shutdown.is_set())
        mock_create_stt.assert_called_once()

        # Reload Fallback STT
        self.service.config["stt"]["fallback_module"] = "new_fallback"
        self.service.reload_configuration()
        self.assertTrue(fallback_shutdown.is_set())
        mock_create_fallback.assert_called_once()

        # Reload Hotwords
        self.service.config["hotwords"]["test"] = {"module": "test"}
        self.service.reload_configuration()
        mock_shutdown_hotwords.assert_called_once()
        mock_create_hotwords.assert_called_once()

        # Reload Listener
        from ovos_plugin_manager.templates.vad import VADEngine
        new_mic = Mock()
        ovos_dinkum_listener.service.OVOSVADFactory.create = \
            Mock(return_value=VADEngine())
        ovos_dinkum_listener.service.OVOSMicrophoneFactory.create = \
            Mock(return_value=new_mic)
        self.service.config["VAD"] = {'module': 'test'}
        self.service.config["microphone"] = {'module': 'mock_mic'}
        self.service.reload_configuration()
        self.assertTrue(vad_stop.is_set())
        self.assertIsInstance(self.service.vad, VADEngine)
        self.assertTrue(mic_stop.is_set())
        self.assertEqual(self.service.mic, new_mic)
        self.service.mic.start.assert_called_once()

        # Reload no relevant change
        config_hash = self.service._config_hash()
        self.service.config['new_section'] = {'test': True}
        self.assertEqual(config_hash, self.service._applied_config_hash)
        self.service.reload_configuration()
        self.assertTrue(self.service.config['new_section']['test'])
        self.assertEqual(config_hash, self.service._config_hash())

        # Reload no change
        config_hash = self.service._config_hash()
        self.assertEqual(config_hash, self.service._applied_config_hash)
        self.service.reload_configuration()
        self.assertEqual(config_hash, self.service._config_hash())
        self.service.reload_configuration()
        self.assertEqual(config_hash, self.service._config_hash())

        mock_create_stt.assert_called_once()
        mock_create_fallback.assert_called_once()
        mock_shutdown_hotwords.assert_called_once()
        mock_create_hotwords.assert_called_once()

        self.service.hotwords.shutdown = real_shutdown_hotwords
        self.service.hotwords.load_hotword_engines = real_create_hotwords


if __name__ == '__main__':
    unittest.main()
