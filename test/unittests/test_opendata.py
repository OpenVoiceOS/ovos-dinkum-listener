"""Unit tests for ovos_dinkum_listener.opendata (opt-in open_data uploads)."""
import unittest
from unittest.mock import patch, Mock

from ovos_dinkum_listener import opendata


class TestUploadWakeWordSample(unittest.TestCase):
    @patch("ovos_dinkum_listener.opendata.requests.post")
    @patch("ovos_dinkum_listener.opendata.Configuration")
    def test_no_op_when_not_configured(self, mock_config, mock_post):
        mock_config.return_value.get.return_value = {}
        opendata.upload_wake_word_sample(b"RIFF....WAVE", name="hey_mycroft")
        mock_post.assert_not_called()

    @patch("ovos_dinkum_listener.opendata.requests.post")
    @patch("ovos_dinkum_listener.opendata.Configuration")
    def test_uploads_to_configured_urls(self, mock_config, mock_post):
        mock_config.return_value.get.return_value = {
            "ww_urls": ["http://a.example/ww", "http://b.example/ww"],
            "user_agent": "custom-agent",
        }
        mock_post.return_value = Mock(status_code=200)

        opendata._upload_wake_word_sample(
            b"RIFF....WAVE", name="hey_mycroft", lang="en-us",
            model="1234", plugin="ovos-ww-plugin-precise",
        )

        self.assertEqual(mock_post.call_count, 2)
        for call in mock_post.call_args_list:
            args, kwargs = call
            self.assertIn(args[0], ("http://a.example/ww", "http://b.example/ww"))
            self.assertEqual(kwargs["data"]["name"], "hey_mycroft")
            self.assertEqual(kwargs["data"]["lang"], "en-us")
            self.assertEqual(kwargs["data"]["model"], "1234")
            self.assertEqual(kwargs["data"]["plugin"], "ovos-ww-plugin-precise")
            self.assertIn("audio", kwargs["files"])
            self.assertEqual(kwargs["headers"]["User-Agent"], "custom-agent")
            self.assertNotIn("X-API-Key", kwargs["headers"])
            self.assertEqual(kwargs["timeout"], 5)

    @patch("ovos_dinkum_listener.opendata.requests.post")
    @patch("ovos_dinkum_listener.opendata.Configuration")
    def test_api_key_header_included(self, mock_config, mock_post):
        mock_config.return_value.get.return_value = {
            "ww_urls": ["http://a.example/ww"],
            "api_key": "secret123",
        }
        mock_post.return_value = Mock(status_code=200)

        opendata._upload_wake_word_sample(b"RIFF....WAVE", name="hey_mycroft")

        kwargs = mock_post.call_args.kwargs
        self.assertEqual(kwargs["headers"]["X-API-Key"], "secret123")

    @patch("ovos_dinkum_listener.opendata.requests.post")
    @patch("ovos_dinkum_listener.opendata.Configuration")
    def test_no_op_without_name(self, mock_config, mock_post):
        mock_config.return_value.get.return_value = {"ww_urls": ["http://a.example/ww"]}
        opendata._upload_wake_word_sample(b"RIFF....WAVE", name=None)
        mock_post.assert_not_called()

    @patch("ovos_dinkum_listener.opendata.requests.post")
    @patch("ovos_dinkum_listener.opendata.Configuration")
    def test_errors_are_swallowed(self, mock_config, mock_post):
        mock_config.return_value.get.return_value = {"ww_urls": ["http://a.example/ww"]}
        mock_post.side_effect = Exception("boom")
        # should not raise
        opendata._upload_wake_word_sample(b"RIFF....WAVE", name="hey_mycroft")

    @patch("ovos_dinkum_listener.opendata.create_daemon")
    @patch("ovos_dinkum_listener.opendata.Configuration")
    def test_public_upload_wake_word_uses_daemon_thread(self, mock_config, mock_daemon):
        mock_config.return_value.get.return_value = {"ww_urls": ["http://a.example/ww"]}
        opendata.upload_wake_word_sample(b"RIFF....WAVE", name="hey_mycroft")
        mock_daemon.assert_called_once()
        self.assertEqual(mock_daemon.call_args[0][0], opendata._upload_wake_word_sample)

    @patch("ovos_dinkum_listener.opendata.create_daemon")
    @patch("ovos_dinkum_listener.opendata.Configuration")
    def test_public_upload_wake_word_no_op_without_urls(self, mock_config, mock_daemon):
        mock_config.return_value.get.return_value = {}
        opendata.upload_wake_word_sample(b"RIFF....WAVE", name="hey_mycroft")
        mock_daemon.assert_not_called()


class TestUploadSttSample(unittest.TestCase):
    @patch("ovos_dinkum_listener.opendata.requests.post")
    @patch("ovos_dinkum_listener.opendata.Configuration")
    def test_no_op_when_not_configured(self, mock_config, mock_post):
        mock_config.return_value.get.return_value = {}
        opendata.upload_stt_sample(b"RIFF....WAVE", transcript="hello", lang="en-us")
        mock_post.assert_not_called()

    @patch("ovos_dinkum_listener.opendata.requests.post")
    @patch("ovos_dinkum_listener.opendata.Configuration")
    def test_uploads_to_configured_urls(self, mock_config, mock_post):
        mock_config.return_value.get.return_value = {
            "stt_urls": ["http://a.example/stt"],
        }
        mock_post.return_value = Mock(status_code=200)

        opendata._upload_stt_sample(
            b"RIFF....WAVE", transcript="hello world", lang="en-us",
            model="whisper-base", plugin="ovos-stt-plugin-server",
        )

        mock_post.assert_called_once()
        kwargs = mock_post.call_args.kwargs
        self.assertEqual(kwargs["data"]["transcript"], "hello world")
        self.assertEqual(kwargs["data"]["lang"], "en-us")
        self.assertEqual(kwargs["data"]["model"], "whisper-base")
        self.assertEqual(kwargs["data"]["plugin"], "ovos-stt-plugin-server")
        self.assertIn("audio", kwargs["files"])
        self.assertEqual(kwargs["headers"]["User-Agent"], "ovos-metrics")

    @patch("ovos_dinkum_listener.opendata.requests.post")
    @patch("ovos_dinkum_listener.opendata.Configuration")
    def test_no_op_without_transcript_or_lang(self, mock_config, mock_post):
        mock_config.return_value.get.return_value = {"stt_urls": ["http://a.example/stt"]}
        opendata._upload_stt_sample(b"RIFF....WAVE", transcript=None, lang="en-us")
        opendata._upload_stt_sample(b"RIFF....WAVE", transcript="hi", lang=None)
        mock_post.assert_not_called()

    @patch("ovos_dinkum_listener.opendata.requests.post")
    @patch("ovos_dinkum_listener.opendata.Configuration")
    def test_errors_are_swallowed(self, mock_config, mock_post):
        mock_config.return_value.get.return_value = {"stt_urls": ["http://a.example/stt"]}
        mock_post.side_effect = Exception("boom")
        opendata._upload_stt_sample(b"RIFF....WAVE", transcript="hello", lang="en-us")

    @patch("ovos_dinkum_listener.opendata.create_daemon")
    @patch("ovos_dinkum_listener.opendata.Configuration")
    def test_public_upload_stt_no_op_without_urls(self, mock_config, mock_daemon):
        mock_config.return_value.get.return_value = {}
        opendata.upload_stt_sample(b"RIFF....WAVE", transcript="hello", lang="en-us")
        mock_daemon.assert_not_called()


if __name__ == "__main__":
    unittest.main()
