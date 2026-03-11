"""Unit tests for ovos_dinkum_listener.voice_loop.hotwords module."""
import unittest
from threading import Event
from unittest.mock import Mock, patch, MagicMock


class TestCyclicAudioBuffer(unittest.TestCase):
    """Tests for CyclicAudioBuffer."""

    def setUp(self):
        from ovos_dinkum_listener.voice_loop.hotwords import CyclicAudioBuffer
        self.CyclicAudioBuffer = CyclicAudioBuffer

    def test_duration_to_bytes(self):
        """duration_to_bytes converts seconds to byte count correctly."""
        result = self.CyclicAudioBuffer.duration_to_bytes(1.0, 16000, 2)
        self.assertEqual(result, 32000)

        result = self.CyclicAudioBuffer.duration_to_bytes(0.5, 16000, 2)
        self.assertEqual(result, 16000)

        result = self.CyclicAudioBuffer.duration_to_bytes(1.0, 8000, 2)
        self.assertEqual(result, 16000)

    def test_get_silence(self):
        """get_silence returns the requested number of null bytes."""
        silence = self.CyclicAudioBuffer.get_silence(100)
        self.assertEqual(len(silence), 100)
        self.assertEqual(silence, b'\x00' * 100)

    def test_init_default(self):
        """Default init creates a buffer filled with silence."""
        buf = self.CyclicAudioBuffer(duration=0.1, sample_rate=16000,
                                     sample_width=2)
        expected_size = int(0.1 * 16000) * 2
        self.assertEqual(len(buf.get()), expected_size)
        self.assertEqual(buf.get(), b'\x00' * expected_size)

    def test_init_with_initial_data_shorter_than_buffer(self):
        """Initial data shorter than buffer is stored as-is (right-padded by buffer)."""
        data = b'\x01' * 100
        buf = self.CyclicAudioBuffer(duration=1.0, sample_rate=16000,
                                     sample_width=2, initial_data=data)
        # buffer should end with the initial data
        self.assertEqual(buf.get()[-len(data):], data)

    def test_init_with_initial_data_longer_than_buffer(self):
        """Initial data longer than buffer is truncated to last size bytes."""
        buf_size = int(0.1 * 16000) * 2  # 3200 bytes
        data = b'\xAA' * (buf_size + 500)
        buf = self.CyclicAudioBuffer(duration=0.1, sample_rate=16000,
                                     sample_width=2, initial_data=data)
        # Should contain only last buf_size bytes of initial_data
        self.assertEqual(len(buf.get()), buf_size)
        self.assertEqual(buf.get(), data[-buf_size:])

    def test_append_within_capacity(self):
        """Appending data that fits leaves previous data intact."""
        buf = self.CyclicAudioBuffer(duration=1.0, sample_rate=16000,
                                     sample_width=2)
        chunk = b'\xFF' * 100
        buf.append(chunk)
        result = buf.get()
        self.assertEqual(result[-100:], chunk)

    def test_append_overflow_drops_oldest(self):
        """Appending data exceeding capacity drops oldest bytes."""
        buf_size = int(0.1 * 16000) * 2  # 3200 bytes
        buf = self.CyclicAudioBuffer(duration=0.1, sample_rate=16000,
                                     sample_width=2)
        new_data = b'\xBB' * (buf_size + 200)
        buf.append(new_data)
        result = buf.get()
        self.assertEqual(len(result), buf_size)
        # The result should be the tail of the combined data
        self.assertEqual(result, new_data[-buf_size:])

    def test_get_returns_bytes(self):
        """get() returns bytes object."""
        buf = self.CyclicAudioBuffer(duration=0.1, sample_rate=16000,
                                     sample_width=2)
        self.assertIsInstance(buf.get(), bytes)

    def test_clear_resets_to_silence(self):
        """clear() fills the buffer with null bytes."""
        buf = self.CyclicAudioBuffer(duration=0.1, sample_rate=16000,
                                     sample_width=2)
        buf.append(b'\xFF' * 100)
        buf.clear()
        result = buf.get()
        self.assertEqual(result, b'\x00' * len(result))

    def test_size_attribute(self):
        """size attribute matches computed byte count."""
        buf = self.CyclicAudioBuffer(duration=0.5, sample_rate=16000,
                                     sample_width=2)
        expected = int(0.5 * 16000) * 2
        self.assertEqual(buf.size, expected)


class TestHotwordState(unittest.TestCase):
    """Tests for HotwordState enum."""

    def test_hotword_state(self):
        from ovos_dinkum_listener.voice_loop.hotwords import HotwordState
        for state in (HotwordState.LISTEN, HotwordState.HOTWORD,
                      HotwordState.RECORDING, HotwordState.WAKEUP):
            self.assertIsInstance(state, HotwordState)
            self.assertIsInstance(state, str)
            self.assertIsInstance(state.value, str)


class TestHotwordContainer(unittest.TestCase):
    """Tests for HotwordContainer."""

    def _make_container(self):
        """Create a fresh HotwordContainer with isolated class state."""
        from ovos_dinkum_listener.voice_loop.hotwords import HotwordContainer
        # Reset class-level state for isolation
        HotwordContainer._plugins = {}
        HotwordContainer._loaded = Event()
        from ovos_utils.fakebus import FakeBus
        container = HotwordContainer(bus=FakeBus())
        return container

    def _make_mock_engine(self):
        """Create a mock HotWordEngine."""
        from ovos_plugin_manager.wakewords import HotWordEngine
        engine = Mock(spec=HotWordEngine)
        engine.config = {"module": "test_module"}
        engine.found_wake_word.return_value = False
        return engine

    def test_init_defaults(self):
        """HotwordContainer initialises with expected defaults."""
        from ovos_dinkum_listener.voice_loop.hotwords import (
            HotwordContainer, HotwordState)
        container = self._make_container()
        self.assertEqual(container.state, HotwordState.HOTWORD)
        self.assertFalse(container.reload_on_failure)
        self.assertIsNone(container.applied_hotwords_config)

    def test_ww_names_empty(self):
        """ww_names returns empty list when no plugins loaded."""
        container = self._make_container()
        self.assertEqual(container.ww_names, [])

    def test_found_listen_state_no_engines_raises(self):
        """found() in LISTEN state with no listen_words raises HotWordException."""
        from ovos_dinkum_listener.voice_loop.hotwords import (
            HotwordContainer, HotwordState, HotWordException)
        container = self._make_container()
        container._loaded.set()  # mark as loaded
        container.state = HotwordState.LISTEN
        with self.assertRaises(HotWordException):
            container.found()

    def test_found_returns_none_when_no_engines(self):
        """found() in HOTWORD state with no hot_words returns None."""
        from ovos_dinkum_listener.voice_loop.hotwords import (
            HotwordContainer, HotwordState)
        container = self._make_container()
        container._loaded.set()
        container.state = HotwordState.HOTWORD
        result = container.found()
        self.assertIsNone(result)

    def test_found_listen_state_engine_returns_true(self):
        """found() returns ww name when listen engine detects wakeword."""
        from ovos_dinkum_listener.voice_loop.hotwords import (
            HotwordContainer, HotwordState)
        container = self._make_container()
        container._loaded.set()
        engine = self._make_mock_engine()
        engine.found_wake_word.return_value = True
        container._plugins['hey_test'] = {
            'engine': engine,
            'listen': True,
            'wakeup': False,
            'stopword': False,
            'sound': None,
            'bus_event': None,
            'utterance': None,
            'stt_lang': 'en-us',
        }
        container.state = HotwordState.LISTEN
        result = container.found()
        self.assertEqual(result, 'hey_test')

    def test_found_wakeup_state(self):
        """found() in WAKEUP state checks wakeup_words engines."""
        from ovos_dinkum_listener.voice_loop.hotwords import (
            HotwordContainer, HotwordState)
        container = self._make_container()
        container._loaded.set()
        engine = self._make_mock_engine()
        engine.found_wake_word.return_value = True
        container._plugins['wake_up'] = {
            'engine': engine,
            'listen': False,
            'wakeup': True,
            'stopword': False,
            'sound': None,
            'bus_event': None,
            'utterance': None,
            'stt_lang': 'en-us',
        }
        container.state = HotwordState.WAKEUP
        result = container.found()
        self.assertEqual(result, 'wake_up')

    def test_found_recording_state(self):
        """found() in RECORDING state checks stop_words engines."""
        from ovos_dinkum_listener.voice_loop.hotwords import (
            HotwordContainer, HotwordState)
        container = self._make_container()
        container._loaded.set()
        engine = self._make_mock_engine()
        engine.found_wake_word.return_value = True
        container._plugins['stop'] = {
            'engine': engine,
            'listen': False,
            'wakeup': False,
            'stopword': True,
            'sound': None,
            'bus_event': None,
            'utterance': None,
            'stt_lang': 'en-us',
        }
        container.state = HotwordState.RECORDING
        result = container.found()
        self.assertEqual(result, 'stop')

    def test_found_handles_non_hotword_engine(self):
        """found() logs error but doesn't crash when engine is not HotWordEngine."""
        from ovos_dinkum_listener.voice_loop.hotwords import (
            HotwordContainer, HotwordState)
        container = self._make_container()
        container._loaded.set()
        # Put a non-HotWordEngine object
        bad_engine = object()
        container._plugins['bad_ww'] = {
            'engine': bad_engine,
            'listen': False,
            'wakeup': False,
            'stopword': False,
            'sound': None,
            'bus_event': None,
            'utterance': None,
            'stt_lang': 'en-us',
        }
        container.state = HotwordState.HOTWORD
        result = container.found()
        self.assertIsNone(result)

    def test_found_handles_engine_exception(self):
        """found() catches exceptions from engine.found_wake_word() and returns None."""
        from ovos_dinkum_listener.voice_loop.hotwords import (
            HotwordContainer, HotwordState)
        from ovos_plugin_manager.wakewords import HotWordEngine
        container = self._make_container()
        container._loaded.set()
        engine = Mock(spec=HotWordEngine)
        engine.config = {"module": "test"}
        engine.found_wake_word.side_effect = RuntimeError("engine error")
        container._plugins['buggy_ww'] = {
            'engine': engine,
            'listen': False,
            'wakeup': False,
            'stopword': False,
            'sound': None,
            'bus_event': None,
            'utterance': None,
            'stt_lang': 'en-us',
        }
        container.state = HotwordState.HOTWORD
        result = container.found()
        self.assertIsNone(result)

    def test_update_listen_state(self):
        """update() calls engine.update() for listen_words in LISTEN state."""
        from ovos_dinkum_listener.voice_loop.hotwords import (
            HotwordContainer, HotwordState)
        from ovos_plugin_manager.wakewords import HotWordEngine
        container = self._make_container()
        container._loaded.set()
        engine = Mock(spec=HotWordEngine)
        container._plugins['hey_test'] = {
            'engine': engine,
            'listen': True,
            'wakeup': False,
            'stopword': False,
            'sound': None,
            'bus_event': None,
            'utterance': None,
            'stt_lang': 'en-us',
        }
        container.state = HotwordState.LISTEN
        container.update(b'\x00' * 100)
        engine.update.assert_called_once_with(b'\x00' * 100)

    def test_update_wakeup_state(self):
        """update() calls engine.update() for wakeup_words in WAKEUP state."""
        from ovos_dinkum_listener.voice_loop.hotwords import (
            HotwordContainer, HotwordState)
        from ovos_plugin_manager.wakewords import HotWordEngine
        container = self._make_container()
        container._loaded.set()
        engine = Mock(spec=HotWordEngine)
        container._plugins['wake_up'] = {
            'engine': engine,
            'listen': False,
            'wakeup': True,
            'stopword': False,
            'sound': None,
            'bus_event': None,
            'utterance': None,
            'stt_lang': 'en-us',
        }
        container.state = HotwordState.WAKEUP
        container.update(b'\xFF' * 50)
        engine.update.assert_called_once_with(b'\xFF' * 50)

    def test_update_recording_state(self):
        """update() calls engine.update() for stop_words in RECORDING state."""
        from ovos_dinkum_listener.voice_loop.hotwords import (
            HotwordContainer, HotwordState)
        from ovos_plugin_manager.wakewords import HotWordEngine
        container = self._make_container()
        container._loaded.set()
        engine = Mock(spec=HotWordEngine)
        container._plugins['stop'] = {
            'engine': engine,
            'listen': False,
            'wakeup': False,
            'stopword': True,
            'sound': None,
            'bus_event': None,
            'utterance': None,
            'stt_lang': 'en-us',
        }
        container.state = HotwordState.RECORDING
        container.update(b'\x01' * 50)
        engine.update.assert_called_once_with(b'\x01' * 50)

    def test_update_hotword_state(self):
        """update() calls engine.update() for hot_words in HOTWORD state."""
        from ovos_dinkum_listener.voice_loop.hotwords import (
            HotwordContainer, HotwordState)
        from ovos_plugin_manager.wakewords import HotWordEngine
        container = self._make_container()
        container._loaded.set()
        engine = Mock(spec=HotWordEngine)
        container._plugins['hot'] = {
            'engine': engine,
            'listen': False,
            'wakeup': False,
            'stopword': False,
            'sound': None,
            'bus_event': None,
            'utterance': None,
            'stt_lang': 'en-us',
        }
        container.state = HotwordState.HOTWORD
        container.update(b'\x01' * 50)
        engine.update.assert_called_once_with(b'\x01' * 50)

    def test_update_handles_engine_exception(self):
        """update() catches exceptions from engine.update() and continues."""
        from ovos_dinkum_listener.voice_loop.hotwords import (
            HotwordContainer, HotwordState)
        from ovos_plugin_manager.wakewords import HotWordEngine
        container = self._make_container()
        container._loaded.set()
        engine = Mock(spec=HotWordEngine)
        engine.update.side_effect = RuntimeError("update error")
        container._plugins['bad'] = {
            'engine': engine,
            'listen': True,
            'wakeup': False,
            'stopword': False,
            'sound': None,
            'bus_event': None,
            'utterance': None,
            'stt_lang': 'en-us',
        }
        container.state = HotwordState.LISTEN
        # Should not raise
        container.update(b'\x00' * 100)

    def test_reset_calls_engine_reset(self):
        """reset() calls reset() on each plugin engine that supports it."""
        from ovos_dinkum_listener.voice_loop.hotwords import (
            HotwordContainer, HotwordState)
        from ovos_plugin_manager.wakewords import HotWordEngine
        container = self._make_container()
        container._loaded.set()
        engine = Mock(spec=HotWordEngine)
        engine.reset = Mock()
        container._plugins['test_ww'] = {
            'engine': engine,
            'listen': True,
            'wakeup': False,
            'stopword': False,
            'sound': None,
            'bus_event': None,
            'utterance': None,
            'stt_lang': 'en-us',
        }
        container.reset()
        engine.reset.assert_called_once()

    def test_reset_skips_engines_without_reset(self):
        """reset() skips engines that don't implement reset()."""
        from ovos_dinkum_listener.voice_loop.hotwords import (
            HotwordContainer, HotwordState)
        from ovos_plugin_manager.wakewords import HotWordEngine
        container = self._make_container()
        container._loaded.set()
        engine = Mock(spec=HotWordEngine)
        del engine.reset  # Remove reset attribute
        container._plugins['test_ww'] = {
            'engine': engine,
            'listen': True,
            'wakeup': False,
            'stopword': False,
            'sound': None,
            'bus_event': None,
            'utterance': None,
            'stt_lang': 'en-us',
        }
        # Should not raise
        container.reset()

    def test_reset_handles_engine_exception(self):
        """reset() catches exceptions from engine.reset() and continues."""
        from ovos_dinkum_listener.voice_loop.hotwords import (
            HotwordContainer, HotwordState)
        from ovos_plugin_manager.wakewords import HotWordEngine
        container = self._make_container()
        container._loaded.set()
        engine = Mock(spec=HotWordEngine)
        engine.reset.side_effect = RuntimeError("reset error")
        container._plugins['buggy_ww'] = {
            'engine': engine,
            'listen': True,
            'wakeup': False,
            'stopword': False,
            'sound': None,
            'bus_event': None,
            'utterance': None,
            'stt_lang': 'en-us',
        }
        # Should not raise
        container.reset()

    def test_shutdown_calls_engine_shutdown(self):
        """shutdown() calls shutdown() on each plugin engine."""
        from ovos_dinkum_listener.voice_loop.hotwords import (
            HotwordContainer, HotwordState)
        from ovos_plugin_manager.wakewords import HotWordEngine
        container = self._make_container()
        container._loaded.set()
        engine = Mock(spec=HotWordEngine)
        container._plugins['test_ww'] = {
            'engine': engine,
            'listen': True,
            'wakeup': False,
            'stopword': False,
            'sound': None,
            'bus_event': None,
            'utterance': None,
            'stt_lang': 'en-us',
        }
        container.shutdown()
        engine.shutdown.assert_called_once()
        self.assertNotIn('test_ww', container._plugins)

    def test_shutdown_handles_engine_exception(self):
        """shutdown() catches exceptions from engine.shutdown() and continues."""
        from ovos_dinkum_listener.voice_loop.hotwords import (
            HotwordContainer, HotwordState)
        from ovos_plugin_manager.wakewords import HotWordEngine
        container = self._make_container()
        container._loaded.set()
        engine = Mock(spec=HotWordEngine)
        engine.shutdown.side_effect = RuntimeError("shutdown error")
        container._plugins['buggy_ww'] = {
            'engine': engine,
            'listen': True,
            'wakeup': False,
            'stopword': False,
            'sound': None,
            'bus_event': None,
            'utterance': None,
            'stt_lang': 'en-us',
        }
        # Should not raise
        container.shutdown()

    def test_verify_returns_true(self):
        """verify() always returns True (placeholder implementation)."""
        container = self._make_container()
        self.assertTrue(container.verify(b'\x00' * 100))

    def test_get_ww_missing_raises_value_error(self):
        """get_ww() raises ValueError for unknown wake word."""
        container = self._make_container()
        with self.assertRaises(ValueError):
            container.get_ww('unknown_ww')

    def test_load_hotword_engines_skip_reload(self):
        """load_hotword_engines() skips reload when not allowed and already loaded."""
        from ovos_dinkum_listener.voice_loop.hotwords import HotwordContainer
        container = self._make_container()
        container.reload_allowed = False
        container._loaded.set()
        # Should not fail, should log and return early
        container.load_hotword_engines()

    @patch("ovos_dinkum_listener.voice_loop.hotwords.Configuration")
    @patch("ovos_dinkum_listener.voice_loop.hotwords.OVOSWakeWordFactory.create_hotword")
    def test_load_hotword_engines_loads_enabled(self, mock_create, mock_config):
        """load_hotword_engines() loads hotwords that are explicitly enabled."""
        from ovos_dinkum_listener.voice_loop.hotwords import HotwordContainer
        from ovos_plugin_manager.wakewords import HotWordEngine
        container = self._make_container()
        engine = Mock(spec=HotWordEngine)
        engine.config = {"module": "test"}
        mock_create.return_value = engine
        mock_config.return_value = {
            'lang': 'en-us',
            'hotwords': {
                'hey_test': {
                    'module': 'test_module',
                    'active': True,
                    'listen': True,
                }
            },
            'listener': {'wake_word': 'hey_mycroft', 'stand_up_word': 'wake_up'},
            'confirm_listening': False,
            'sounds': {},
        }
        container.load_hotword_engines()
        self.assertIn('hey_test', container._plugins)
        self.assertTrue(container._loaded.is_set())

    @patch("ovos_dinkum_listener.voice_loop.hotwords.Configuration")
    @patch("ovos_dinkum_listener.voice_loop.hotwords.OVOSWakeWordFactory.create_hotword")
    def test_load_hotword_engines_skips_disabled(self, mock_create, mock_config):
        """load_hotword_engines() skips hotwords with active=False."""
        from ovos_dinkum_listener.voice_loop.hotwords import HotwordContainer
        container = self._make_container()
        mock_config.return_value = {
            'lang': 'en-us',
            'hotwords': {
                'hey_disabled': {
                    'module': 'test_module',
                    'active': False,
                    'listen': True,
                }
            },
            'listener': {'wake_word': 'hey_mycroft', 'stand_up_word': 'wake_up'},
            'confirm_listening': False,
            'sounds': {},
        }
        container.load_hotword_engines()
        self.assertNotIn('hey_disabled', container._plugins)

    @patch("ovos_dinkum_listener.voice_loop.hotwords.Configuration")
    @patch("ovos_dinkum_listener.voice_loop.hotwords.OVOSWakeWordFactory.create_hotword")
    def test_load_hotword_engines_auto_enables_main_ww(self, mock_create, mock_config):
        """load_hotword_engines() auto-enables main wake word when active is None."""
        from ovos_dinkum_listener.voice_loop.hotwords import HotwordContainer
        from ovos_plugin_manager.wakewords import HotWordEngine
        container = self._make_container()
        engine = Mock(spec=HotWordEngine)
        engine.config = {"module": "test"}
        mock_create.return_value = engine
        mock_config.return_value = {
            'lang': 'en-us',
            'hotwords': {
                'hey_mycroft': {
                    'module': 'test_module',
                    # active not set → should be auto-enabled as main ww
                }
            },
            'listener': {'wake_word': 'hey_mycroft', 'stand_up_word': 'wake_up'},
            'confirm_listening': False,
            'sounds': {},
        }
        container.load_hotword_engines()
        self.assertIn('hey_mycroft', container._plugins)

    @patch("ovos_dinkum_listener.voice_loop.hotwords.Configuration")
    @patch("ovos_dinkum_listener.voice_loop.hotwords.OVOSWakeWordFactory.create_hotword")
    def test_load_hotword_engines_space_normalization(self, mock_create, mock_config):
        """load_hotword_engines() replaces spaces with underscores in hotword names."""
        from ovos_dinkum_listener.voice_loop.hotwords import HotwordContainer
        from ovos_plugin_manager.wakewords import HotWordEngine
        container = self._make_container()
        engine = Mock(spec=HotWordEngine)
        engine.config = {"module": "test"}
        mock_create.return_value = engine
        mock_config.return_value = {
            'lang': 'en-us',
            'hotwords': {
                'hey mycroft': {  # space in name
                    'module': 'test_module',
                    'active': True,
                    'listen': True,
                }
            },
            'listener': {'wake_word': 'hey_mycroft', 'stand_up_word': 'wake_up'},
            'confirm_listening': False,
            'sounds': {},
        }
        container.load_hotword_engines()
        # Should be stored as 'hey_mycroft'
        self.assertIn('hey_mycroft', container._plugins)
        self.assertNotIn('hey mycroft', container._plugins)

    @patch("ovos_dinkum_listener.voice_loop.hotwords.Configuration")
    @patch("ovos_dinkum_listener.voice_loop.hotwords.OVOSWakeWordFactory.create_hotword")
    def test_load_hotword_engines_handles_engine_error(self, mock_create, mock_config):
        """load_hotword_engines() continues when an individual engine fails to load."""
        from ovos_dinkum_listener.voice_loop.hotwords import HotwordContainer
        container = self._make_container()
        mock_create.side_effect = RuntimeError("engine init failed")
        mock_config.return_value = {
            'lang': 'en-us',
            'hotwords': {
                'bad_ww': {
                    'module': 'test_module',
                    'active': True,
                    'listen': True,
                }
            },
            'listener': {'wake_word': 'hey_mycroft', 'stand_up_word': 'wake_up'},
            'confirm_listening': False,
            'sounds': {},
        }
        # Should not raise
        container.load_hotword_engines()
        self.assertTrue(container._loaded.is_set())


if __name__ == '__main__':
    unittest.main()
