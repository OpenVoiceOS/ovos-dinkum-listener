import unittest

from unittest.mock import patch, Mock
from copy import copy
from ovos_plugin_manager.templates import StreamingSTT

_MOCK_CONFIG = {
    "lang": "global",
    "stt": {
        "module": "test_module",
        "fallback_module": "test_fallback",
        "test_module": {"config": True, "lang": "module"},
    },
}


class MockStreamingSTT(StreamingSTT):
    def create_streaming_thread(self):
        pass


class TestPlugins(unittest.TestCase):
    def test_fake_stream_thread_empty_buffer_returns_empty(self):
        """FakeStreamThread.finalize() returns empty string when buffer is empty."""
        from queue import Queue
        from ovos_dinkum_listener.plugins import FakeStreamThread

        engine = Mock()
        thread = FakeStreamThread(Queue(), "en-us", engine, 16000, 2)
        # Buffer is empty by default → finalize should return ""
        result = thread.finalize()
        self.assertEqual(result, "")
        engine.execute.assert_not_called()

    def test_fake_stream_thread_finalize_with_audio(self):
        """FakeStreamThread.finalize() transcribes buffered audio via engine."""
        from queue import Queue
        from ovos_dinkum_listener.plugins import FakeStreamThread

        engine = Mock()
        engine.execute.return_value = "hello world"
        thread = FakeStreamThread(Queue(), "en-us", engine, 16000, 2)
        # Write some audio data
        thread.update(b"\x00" * 100)
        result = thread.finalize()
        self.assertEqual(result, "hello world")
        engine.execute.assert_called_once()

    def test_fake_stream_thread_finalize_engine_exception(self):
        """FakeStreamThread.finalize() returns None on engine exception."""
        from queue import Queue
        from ovos_dinkum_listener.plugins import FakeStreamThread

        engine = Mock()
        engine.execute.side_effect = RuntimeError("engine error")
        thread = FakeStreamThread(Queue(), "en-us", engine, 16000, 2)
        thread.update(b"\x00" * 100)
        result = thread.finalize()
        self.assertIsNone(result)

    def test_fake_stream_thread_update_accumulates(self):
        """FakeStreamThread.update() accumulates audio in buffer."""
        from queue import Queue
        from ovos_dinkum_listener.plugins import FakeStreamThread

        engine = Mock()
        engine.execute.return_value = ""
        thread = FakeStreamThread(Queue(), "en-us", engine, 16000, 2)
        thread.update(b"\xaa" * 50)
        thread.update(b"\xbb" * 50)
        # Verify both chunks are in buffer by finalizing
        thread.finalize()
        call_args = engine.execute.call_args
        audio_data = call_args[0][0]
        self.assertEqual(len(audio_data.get_wav_data()), 100 + 44)  # 44-byte WAV header

    def test_fake_streaming_stt_transcribe_with_bytes(self):
        """FakeStreamingSTT.transcribe() accepts bytes and wraps in AudioData."""
        from ovos_dinkum_listener.plugins import FakeStreamingSTT

        engine = Mock()
        engine.transcribe.return_value = [("test", 0.9)]
        stt = FakeStreamingSTT(engine=engine, config={})
        # Create a mock stream
        stream = Mock()
        stream.sample_rate = 16000
        stream.sample_width = 2
        stt.stream = stream
        result = stt.transcribe(audio=b"\x00" * 100, lang="en-us")
        self.assertEqual(result, [("test", 0.9)])
        engine.transcribe.assert_called_once()

    def test_fake_streaming_stt_transcribe_invalid_type_raises(self):
        """FakeStreamingSTT.transcribe() raises ValueError for unsupported types."""
        from ovos_dinkum_listener.plugins import FakeStreamingSTT

        engine = Mock()
        stt = FakeStreamingSTT(engine=engine, config={})
        stream = Mock()
        stream.sample_rate = 16000
        stream.sample_width = 2
        stt.stream = stream
        with self.assertRaises(ValueError):
            stt.transcribe(audio=12345)

    def test_fake_streaming_stt_transcribe_with_audio_data(self):
        """FakeStreamingSTT.transcribe() passes AudioData directly to engine."""
        from ovos_dinkum_listener.plugins import FakeStreamingSTT
        from ovos_plugin_manager.utils.audio import AudioData

        engine = Mock()
        engine.transcribe.return_value = [("direct", 0.95)]
        stt = FakeStreamingSTT(engine=engine, config={})
        stream = Mock()
        stream.sample_rate = 16000
        stream.sample_width = 2
        stt.stream = stream
        audio = AudioData(b"\x00" * 100, sample_rate=16000, sample_width=2)
        result = stt.transcribe(audio=audio, lang="en-us")
        self.assertEqual(result, [("direct", 0.95)])

    def test_fake_streaming_stt_transcribe_with_none_reads_buffer(self):
        """FakeStreamingSTT.transcribe(None) reads audio from stream buffer."""
        from ovos_dinkum_listener.plugins import FakeStreamingSTT

        engine = Mock()
        engine.transcribe.return_value = [("from buffer", 0.8)]
        stt = FakeStreamingSTT(engine=engine, config={})
        stream = Mock()
        stream.sample_rate = 16000
        stream.sample_width = 2
        stream.buffer = Mock()
        stream.buffer.read.return_value = b"\x00" * 100
        stt.stream = stream
        result = stt.transcribe(audio=None, lang="en-us")
        stream.buffer.clear.assert_called_once()

    def test_fake_stream_thread_handle_audio_stream(self):
        """FakeStreamThread.handle_audio_stream() calls update() for each chunk."""
        from queue import Queue
        from ovos_dinkum_listener.plugins import FakeStreamThread

        engine = Mock()
        thread = FakeStreamThread(Queue(), "en-us", engine, 16000, 2)
        chunks = [b"\xaa" * 10, b"\xbb" * 10, b"\xcc" * 10]
        thread.handle_audio_stream(iter(chunks), "en-us")
        # All chunks should be written to buffer (total 30 bytes)
        engine.execute.return_value = ""
        thread.finalize()
        engine.execute.assert_called_once()

    @patch("ovos_dinkum_listener.plugins.Configuration")
    def test_fake_streaming_stt_create_streaming_thread(self, mock_config):
        """FakeStreamingSTT.create_streaming_thread() returns FakeStreamThread."""
        from queue import Queue
        from ovos_dinkum_listener.plugins import FakeStreamingSTT, FakeStreamThread

        mock_config.return_value = {
            "listener": {"sample_rate": 16000, "sample_width": 2}
        }
        engine = Mock()
        stt = FakeStreamingSTT(engine=engine, config={})
        stt.queue = Queue()  # StreamThread requires queue from stream_start
        thread = stt.create_streaming_thread()
        self.assertIsInstance(thread, FakeStreamThread)

    @patch("ovos_dinkum_listener.plugins.Configuration")
    @patch("ovos_plugin_manager.stt.OVOSSTTFactory.create")
    def test_load_stt_module_wraps_non_streaming(self, create, config):
        """load_stt_module wraps non-StreamingSTT plugins in FakeStreamingSTT."""
        from ovos_dinkum_listener.plugins import load_stt_module, FakeStreamingSTT

        config.return_value = {"lang": "en-us", "stt": {"module": "test"}}
        # Return a non-StreamingSTT mock
        non_streaming = Mock()  # not StreamingSTT
        create.return_value = non_streaming
        stt = load_stt_module({"module": "test", "lang": "en-us"})
        self.assertIsInstance(stt, FakeStreamingSTT)

    @patch("ovos_dinkum_listener.plugins.Configuration")
    @patch("ovos_plugin_manager.stt.OVOSSTTFactory.create")
    def test_load_stt_module(self, create, config):
        original_config = copy(_MOCK_CONFIG)
        config.return_value = _MOCK_CONFIG
        create.return_value = MockStreamingSTT()
        from ovos_dinkum_listener.plugins import load_stt_module

        # Test passed config
        stt = load_stt_module(_MOCK_CONFIG["stt"])
        create.assert_called_once_with({"lang": "global", **_MOCK_CONFIG["stt"]})
        self.assertIsInstance(stt, StreamingSTT)

        # Test default config
        stt = load_stt_module()
        create.assert_called_with({"lang": "global", **_MOCK_CONFIG["stt"]})
        self.assertIsInstance(stt, StreamingSTT)

        # Assert configuration was not changed
        self.assertEqual(original_config, _MOCK_CONFIG)

        # Test configuration uses default lang
        global_lang_config = copy(_MOCK_CONFIG)
        global_lang_config["stt"]["test_module"].pop("lang")
        global_lang_config["stt"].pop("fallback_module")
        config.return_value = global_lang_config
        stt = load_stt_module()
        create.assert_called_with(
            {
                "module": "test_module",
                "lang": global_lang_config["lang"],
                "test_module": global_lang_config["stt"]["test_module"],
            }
        )
        self.assertIsNone(global_lang_config["stt"]["test_module"].get("lang"))
        self.assertIsNone(global_lang_config["stt"].get("lang"))
        self.assertIsInstance(stt, StreamingSTT)

        # Test module init raises exception
        # TODO

    @patch("ovos_dinkum_listener.plugins.Configuration")
    @patch("ovos_plugin_manager.stt.OVOSSTTFactory.create")
    def test_load_fallback_stt(self, create, config):
        original_config = copy(_MOCK_CONFIG)
        config.return_value = _MOCK_CONFIG
        create.return_value = MockStreamingSTT()
        from ovos_dinkum_listener.plugins import load_fallback_stt

        # Test passed config global lang
        fallback = _MOCK_CONFIG["stt"]["fallback_module"]
        stt = load_fallback_stt(_MOCK_CONFIG["stt"])
        create.assert_called_once_with(
            {"module": fallback, fallback: {"lang": _MOCK_CONFIG["lang"]}}
        )
        self.assertIsInstance(stt, StreamingSTT)

        # Test passed config module lang
        test_config = copy(_MOCK_CONFIG.get("stt"))
        module = "test_module"
        test_config["fallback_module"] = module
        stt = load_fallback_stt(test_config)
        create.assert_called_with(
            {"module": module, module: _MOCK_CONFIG["stt"][module]}
        )
        self.assertIsInstance(stt, StreamingSTT)

        # Test default config
        fallback = _MOCK_CONFIG["stt"]["fallback_module"]
        stt = load_fallback_stt()
        create.assert_called_with(
            {"module": fallback, fallback: {"lang": _MOCK_CONFIG["lang"]}}
        )
        self.assertIsInstance(stt, StreamingSTT)

        # Test no module configured
        create.reset_mock()
        test_config = _MOCK_CONFIG.get("stt")
        module = ""
        test_config["fallback_module"] = module
        stt = load_fallback_stt(test_config)
        self.assertIsNone(stt)
        create.assert_not_called()

        # Assert configuration was not changed
        self.assertEqual(original_config, _MOCK_CONFIG)
        # Test module init raises exception
        # TODO


if __name__ == "__main__":
    unittest.main()
