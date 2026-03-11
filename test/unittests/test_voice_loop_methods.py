"""Comprehensive unit tests for DinkumVoiceLoop methods."""
import time
import unittest
from collections import deque
from unittest.mock import Mock, patch, MagicMock


CHUNK_SIZE = 4096
SECONDS_PER_CHUNK = 0.256  # large so timers expire quickly in tests
SAMPLE_WIDTH = 2

SILENT_CHUNK = bytes(CHUNK_SIZE)
SPEECH_CHUNK = bytes([0x7F, 0x00] * (CHUNK_SIZE // 2))


def _make_loop(**kwargs):
    """
    Create a DinkumVoiceLoop with fully mocked dependencies.

    @param kwargs: Optional overrides for DinkumVoiceLoop fields.
    @return: Configured DinkumVoiceLoop instance.
    """
    from ovos_dinkum_listener.voice_loop.voice_loop import DinkumVoiceLoop

    mic = Mock()
    mic.chunk_size = CHUNK_SIZE
    mic.seconds_per_chunk = SECONDS_PER_CHUNK
    mic.sample_width = SAMPLE_WIDTH

    hotwords = Mock()
    hotwords.found.return_value = None
    hotwords.verify.return_value = True
    hotwords.reload_on_failure = False
    hotwords.get_ww.return_value = {
        'engine': 'TestEngine',
        'key_phrase': 'hey_test',
        'module': 'test',
        'listen': True,
        'wakeup': False,
        'stopword': False,
        'sound': None,
        'bus_event': None,
        'utterance': None,
        'stt_lang': 'en-us',
    }

    stt = Mock()
    stt.lang = "en-us"
    stt.stream = Mock()
    stt.stream.language = "en-us"
    stt.stream.buffer = Mock()
    stt.transcribe.return_value = [("hello world", 0.9)]

    vad = Mock()
    vad.is_silence.return_value = True
    vad.extract_speech.return_value = None

    transformers = Mock()
    transformers.transform.return_value = (SILENT_CHUNK, {})

    defaults = dict(
        mic=mic,
        hotwords=hotwords,
        stt=stt,
        fallback_stt=None,
        vad=vad,
        transformers=transformers,
        speech_seconds=0.1,
        silence_seconds=0.2,
        timeout_seconds=1.0,
        timeout_seconds_with_silence=0.5,
        confirmation_seconds=0.3,
        min_stt_confidence=0.6,
        max_transcripts=1,
    )
    defaults.update(kwargs)
    return DinkumVoiceLoop(**defaults)


class TestDebiasedEnergy(unittest.TestCase):
    """Tests for VoiceLoop.debiased_energy() static method."""

    def test_silent_audio_low_energy(self):
        """All-zero audio produces energy of zero."""
        from ovos_dinkum_listener.voice_loop.voice_loop import VoiceLoop
        energy = VoiceLoop.debiased_energy(SILENT_CHUNK, SAMPLE_WIDTH)
        self.assertAlmostEqual(energy, 0.0, places=1)

    def test_ac_audio_nonzero_energy(self):
        """Alternating +/-1 signal (zero mean) produces non-zero debiased energy."""
        import struct
        from ovos_dinkum_listener.voice_loop.voice_loop import VoiceLoop
        # Alternating +1 and -1 samples → zero DC bias, non-zero AC energy
        n_samples = CHUNK_SIZE // SAMPLE_WIDTH
        samples = [127, -127] * (n_samples // 2)
        audio = struct.pack('<' + 'h' * n_samples, *samples)
        energy = VoiceLoop.debiased_energy(audio, SAMPLE_WIDTH)
        self.assertGreater(energy, 0)

    def test_returns_float(self):
        """debiased_energy always returns a numeric value."""
        from ovos_dinkum_listener.voice_loop.voice_loop import VoiceLoop
        result = VoiceLoop.debiased_energy(SILENT_CHUNK, SAMPLE_WIDTH)
        self.assertIsInstance(result, (int, float))


class TestPreWakeVad(unittest.TestCase):
    """Tests for DinkumVoiceLoop._pre_wake_vad()."""

    def test_speech_detected_switches_state(self):
        """When VAD detects speech, state changes to DETECT_WAKEWORD."""
        from ovos_dinkum_listener.voice_loop.voice_loop import ListeningState
        loop = _make_loop()
        loop.state = ListeningState.PRE_WAKE_VAD
        loop.vad.is_silence.return_value = False  # speech

        loop._pre_wake_vad(SPEECH_CHUNK)

        self.assertEqual(loop.state, ListeningState.DETECT_WAKEWORD)
        self.assertTrue(loop._chunk_info.is_speech)
        # hotword_chunks is drained during speech detection (rewound to hotwords.update)
        self.assertEqual(len(loop.hotword_chunks), 0)

    def test_silence_feeds_transformers(self):
        """When VAD detects silence, audio is fed to transformers."""
        from ovos_dinkum_listener.voice_loop.voice_loop import ListeningState
        loop = _make_loop()
        loop.state = ListeningState.PRE_WAKE_VAD
        loop.vad.is_silence.return_value = True  # silence

        loop._pre_wake_vad(SILENT_CHUNK)

        self.assertEqual(loop.state, ListeningState.PRE_WAKE_VAD)
        self.assertFalse(loop._chunk_info.is_speech)
        loop.transformers.feed_audio.assert_called_once_with(SILENT_CHUNK)

    def test_vad_exception_treated_as_silence(self):
        """VAD exception causes is_speech to be False; state unchanged."""
        from ovos_dinkum_listener.voice_loop.voice_loop import ListeningState
        loop = _make_loop()
        loop.state = ListeningState.PRE_WAKE_VAD
        loop.vad.is_silence.side_effect = RuntimeError("vad error")

        loop._pre_wake_vad(SILENT_CHUNK)

        self.assertFalse(loop._chunk_info.is_speech)
        self.assertEqual(loop.state, ListeningState.PRE_WAKE_VAD)


class TestConfirmationSound(unittest.TestCase):
    """Tests for DinkumVoiceLoop._confirmation_sound()."""

    def test_instant_listen_skips_to_before_cmd(self):
        """With instant_listen=True, state immediately becomes BEFORE_COMMAND."""
        from ovos_dinkum_listener.voice_loop.voice_loop import ListeningState
        loop = _make_loop(instant_listen=True)
        loop.state = ListeningState.CONFIRMATION
        loop.confirmation_seconds_left = 1.0
        # _before_cmd needs stt_chunks; prime it
        loop.stt_chunks = deque()
        loop.timeout_seconds_left = 0.1  # so _before_cmd times out immediately
        loop.timeout_seconds_with_silence_left = 0.1

        loop._confirmation_sound(SILENT_CHUNK)

        self.assertEqual(loop.confirmation_seconds_left, 0)

    def test_timeout_moves_to_before_cmd(self):
        """When confirmation_seconds_left drops to 0, state → BEFORE_COMMAND."""
        from ovos_dinkum_listener.voice_loop.voice_loop import ListeningState
        loop = _make_loop()
        loop.state = ListeningState.CONFIRMATION
        loop.confirmation_seconds_left = SECONDS_PER_CHUNK * 0.5  # less than one chunk

        loop._confirmation_sound(SILENT_CHUNK)

        self.assertEqual(loop.state, ListeningState.BEFORE_COMMAND)

    def test_still_in_confirmation_when_not_expired(self):
        """State remains CONFIRMATION while timer has not expired."""
        from ovos_dinkum_listener.voice_loop.voice_loop import ListeningState
        loop = _make_loop()
        loop.state = ListeningState.CONFIRMATION
        loop.confirmation_seconds_left = 99.0

        loop._confirmation_sound(SILENT_CHUNK)

        self.assertEqual(loop.state, ListeningState.CONFIRMATION)
        loop.transformers.feed_audio.assert_called_once_with(SILENT_CHUNK)

    def test_is_listen_sound_set(self):
        """_chunk_info.is_listen_sound is set to True during confirmation."""
        loop = _make_loop()
        loop.confirmation_seconds_left = 99.0

        loop._confirmation_sound(SILENT_CHUNK)

        self.assertTrue(loop._chunk_info.is_listen_sound)


class TestBeforeCmd(unittest.TestCase):
    """Tests for DinkumVoiceLoop._before_cmd()."""

    def test_timeout_moves_to_after_cmd(self):
        """When timeout expires, state → AFTER_COMMAND."""
        from ovos_dinkum_listener.voice_loop.voice_loop import ListeningState
        loop = _make_loop()
        loop.state = ListeningState.BEFORE_COMMAND
        # One chunk worth of timer → will expire after first pop
        loop.timeout_seconds_left = SECONDS_PER_CHUNK * 0.5
        loop.timeout_seconds_with_silence_left = SECONDS_PER_CHUNK * 0.5
        loop.stt_chunks = deque([SILENT_CHUNK])
        loop.stt_audio_bytes = bytes()

        loop._before_cmd(SILENT_CHUNK)

        self.assertEqual(loop.state, ListeningState.AFTER_COMMAND)

    def test_speech_moves_to_in_cmd(self):
        """When speech is detected long enough, state → IN_COMMAND."""
        from ovos_dinkum_listener.voice_loop.voice_loop import ListeningState
        loop = _make_loop()
        loop.state = ListeningState.BEFORE_COMMAND
        loop.timeout_seconds_left = 99.0
        loop.timeout_seconds_with_silence_left = 99.0
        # speech_seconds = 0.1; one chunk of SECONDS_PER_CHUNK=0.256 exceeds it
        loop.speech_seconds_left = 0.0  # already at 0 → will immediately detect speech
        loop.vad.is_silence.return_value = False  # VAD says speech
        loop.stt_chunks = deque([SPEECH_CHUNK])
        loop.stt_audio_bytes = bytes()

        loop._before_cmd(SILENT_CHUNK)

        self.assertEqual(loop.state, ListeningState.IN_COMMAND)

    def test_silence_resets_speech_timer(self):
        """Silence in BEFORE_COMMAND resets speech_seconds_left."""
        from ovos_dinkum_listener.voice_loop.voice_loop import ListeningState
        loop = _make_loop()
        loop.state = ListeningState.BEFORE_COMMAND
        loop.timeout_seconds_left = 99.0
        loop.timeout_seconds_with_silence_left = 99.0
        loop.speech_seconds_left = 0.05  # partially decremented
        loop.vad.is_silence.return_value = True  # silence
        loop.stt_chunks = deque([SILENT_CHUNK])
        loop.stt_audio_bytes = bytes()

        loop._before_cmd(SILENT_CHUNK)

        # speech_seconds_left reset to speech_seconds
        self.assertAlmostEqual(loop.speech_seconds_left, loop.speech_seconds)

    def test_feeds_audio_to_transformers(self):
        """_before_cmd feeds audio to transformers."""
        from ovos_dinkum_listener.voice_loop.voice_loop import ListeningState
        loop = _make_loop()
        loop.state = ListeningState.BEFORE_COMMAND
        loop.timeout_seconds_left = 99.0
        loop.timeout_seconds_with_silence_left = 99.0
        loop.stt_chunks = deque()
        loop.stt_audio_bytes = bytes()

        loop._before_cmd(SILENT_CHUNK)

        loop.transformers.feed_audio.assert_called_once_with(SILENT_CHUNK)

    def test_streams_chunks_to_stt(self):
        """_before_cmd streams queued chunks to STT."""
        from ovos_dinkum_listener.voice_loop.voice_loop import ListeningState
        loop = _make_loop()
        loop.timeout_seconds_left = 99.0
        loop.timeout_seconds_with_silence_left = 99.0
        loop.stt_chunks = deque([SILENT_CHUNK])
        loop.stt_audio_bytes = bytes()
        loop.vad.is_silence.return_value = True

        loop._before_cmd(SILENT_CHUNK)

        loop.stt.stream_data.assert_called()


class TestInCmd(unittest.TestCase):
    """Tests for DinkumVoiceLoop._in_cmd()."""

    def test_timeout_moves_to_after_cmd(self):
        """Overall timeout expires → AFTER_COMMAND."""
        from ovos_dinkum_listener.voice_loop.voice_loop import ListeningState
        loop = _make_loop()
        loop.state = ListeningState.IN_COMMAND
        loop.timeout_seconds_left = SECONDS_PER_CHUNK * 0.5
        loop.silence_seconds_left = 99.0
        loop.vad.is_silence.return_value = False  # speech → no silence countdown
        loop.stt_chunks = deque([SPEECH_CHUNK])
        loop.stt_audio_bytes = bytes()

        loop._in_cmd(SPEECH_CHUNK)

        self.assertEqual(loop.state, ListeningState.AFTER_COMMAND)

    def test_silence_countdown_ends_command(self):
        """Enough silence → AFTER_COMMAND."""
        from ovos_dinkum_listener.voice_loop.voice_loop import ListeningState
        loop = _make_loop()
        loop.state = ListeningState.IN_COMMAND
        loop.timeout_seconds_left = 99.0
        loop.silence_seconds_left = SECONDS_PER_CHUNK * 0.5  # will expire after 1 chunk
        loop.vad.is_silence.return_value = True  # silence
        loop.stt_chunks = deque([SILENT_CHUNK])
        loop.stt_audio_bytes = bytes()

        loop._in_cmd(SILENT_CHUNK)

        self.assertEqual(loop.state, ListeningState.AFTER_COMMAND)

    def test_speech_resets_silence_countdown(self):
        """Speech resets silence_seconds_left."""
        from ovos_dinkum_listener.voice_loop.voice_loop import ListeningState
        loop = _make_loop()
        loop.state = ListeningState.IN_COMMAND
        loop.timeout_seconds_left = 99.0
        loop.silence_seconds_left = 0.01  # almost expired
        loop.vad.is_silence.return_value = False  # speech
        loop.stt_chunks = deque([SPEECH_CHUNK])
        loop.stt_audio_bytes = bytes()

        loop._in_cmd(SPEECH_CHUNK)

        # silence_seconds_left reset
        self.assertAlmostEqual(loop.silence_seconds_left, loop.silence_seconds)

    def test_feeds_speech_to_transformers(self):
        """_in_cmd feeds audio to transformers.feed_speech."""
        from ovos_dinkum_listener.voice_loop.voice_loop import ListeningState
        loop = _make_loop()
        loop.timeout_seconds_left = 99.0
        loop.silence_seconds_left = 99.0
        loop.stt_chunks = deque()
        loop.stt_audio_bytes = bytes()
        loop.vad.is_silence.return_value = False

        loop._in_cmd(SPEECH_CHUNK)

        loop.transformers.feed_speech.assert_called_once_with(SPEECH_CHUNK)

    def test_with_fallback_stt(self):
        """_in_cmd streams data to fallback STT when configured."""
        from ovos_dinkum_listener.voice_loop.voice_loop import ListeningState
        fallback = Mock()
        loop = _make_loop(fallback_stt=fallback)
        loop.timeout_seconds_left = 99.0
        loop.silence_seconds_left = 99.0
        loop.stt_chunks = deque([SILENT_CHUNK])
        loop.stt_audio_bytes = bytes()
        loop.vad.is_silence.return_value = False

        loop._in_cmd(SILENT_CHUNK)

        fallback.stream_data.assert_called()


class TestAfterCmd(unittest.TestCase):
    """Tests for DinkumVoiceLoop._after_cmd()."""

    def test_calls_text_callback_with_utts(self):
        """_after_cmd calls text_callback with STT transcripts."""
        from ovos_dinkum_listener.voice_loop.voice_loop import ListeningState, ListeningMode
        loop = _make_loop()
        loop.state = ListeningState.AFTER_COMMAND
        loop.listen_mode = ListeningMode.WAKEWORD
        text_cb = Mock()
        loop.text_callback = text_cb
        loop.stt.transcribe.return_value = [("hey test", 0.9)]
        loop.transformers.transform.return_value = (SILENT_CHUNK, {})
        loop.stt_audio_bytes = bytes(100)
        loop.stt_chunks = deque()

        loop._after_cmd(SILENT_CHUNK)

        text_cb.assert_called_once()
        called_utts, called_ctx = text_cb.call_args[0]
        self.assertEqual(called_utts, [("hey test", 0.9)])

    def test_calls_stt_audio_callback(self):
        """_after_cmd calls stt_audio_callback with recorded audio."""
        from ovos_dinkum_listener.voice_loop.voice_loop import ListeningState, ListeningMode
        loop = _make_loop()
        loop.listen_mode = ListeningMode.WAKEWORD
        audio_cb = Mock()
        loop.stt_audio_callback = audio_cb
        loop.stt_audio_bytes = b'\xFF' * 200
        loop.stt_chunks = deque()
        loop.transformers.transform.return_value = (SILENT_CHUNK, {})

        loop._after_cmd(SILENT_CHUNK)

        audio_cb.assert_called_once()
        recorded_audio = audio_cb.call_args[0][0]
        self.assertEqual(recorded_audio, b'\xFF' * 200)

    def test_calls_record_end_callback(self):
        """_after_cmd calls record_end_callback."""
        from ovos_dinkum_listener.voice_loop.voice_loop import ListeningState, ListeningMode
        loop = _make_loop()
        loop.listen_mode = ListeningMode.WAKEWORD
        end_cb = Mock()
        loop.record_end_callback = end_cb
        loop.stt_audio_bytes = bytes()
        loop.stt_chunks = deque()
        loop.transformers.transform.return_value = (SILENT_CHUNK, {})

        loop._after_cmd(SILENT_CHUNK)

        end_cb.assert_called_once()

    def test_resets_to_detect_wakeword_in_wakeword_mode(self):
        """In WAKEWORD mode, _after_cmd resets state to DETECT_WAKEWORD."""
        from ovos_dinkum_listener.voice_loop.voice_loop import ListeningState, ListeningMode
        loop = _make_loop()
        loop.listen_mode = ListeningMode.WAKEWORD
        loop.stt_audio_bytes = bytes()
        loop.stt_chunks = deque()
        loop.transformers.transform.return_value = (SILENT_CHUNK, {})

        loop._after_cmd(SILENT_CHUNK)

        self.assertEqual(loop.state, ListeningState.DETECT_WAKEWORD)

    def test_resets_to_waiting_cmd_in_continuous_mode(self):
        """In CONTINUOUS mode, _after_cmd resets state to WAITING_CMD."""
        from ovos_dinkum_listener.voice_loop.voice_loop import ListeningState, ListeningMode
        loop = _make_loop()
        loop.listen_mode = ListeningMode.CONTINUOUS
        loop.stt_audio_bytes = bytes()
        loop.stt_chunks = deque()
        loop.transformers.transform.return_value = (SILENT_CHUNK, {})

        loop._after_cmd(SILENT_CHUNK)

        self.assertEqual(loop.state, ListeningState.WAITING_CMD)

    def test_resets_to_waiting_cmd_in_hybrid_mode(self):
        """In HYBRID mode, _after_cmd resets state to WAITING_CMD."""
        from ovos_dinkum_listener.voice_loop.voice_loop import ListeningState, ListeningMode
        loop = _make_loop()
        loop.listen_mode = ListeningMode.HYBRID
        loop.stt_audio_bytes = bytes()
        loop.stt_chunks = deque()
        loop.transformers.transform.return_value = (SILENT_CHUNK, {})

        loop._after_cmd(SILENT_CHUNK)

        self.assertEqual(loop.state, ListeningState.WAITING_CMD)

    def test_no_callbacks_when_not_set(self):
        """_after_cmd runs without error when no callbacks are configured."""
        from ovos_dinkum_listener.voice_loop.voice_loop import ListeningMode
        loop = _make_loop()
        loop.listen_mode = ListeningMode.WAKEWORD
        loop.stt_audio_bytes = bytes()
        loop.stt_chunks = deque()
        loop.transformers.transform.return_value = (SILENT_CHUNK, {})
        # All callbacks are None by default
        loop._after_cmd(SILENT_CHUNK)

    def test_clears_stt_chunks(self):
        """_after_cmd clears the STT chunk queue."""
        from ovos_dinkum_listener.voice_loop.voice_loop import ListeningMode
        loop = _make_loop()
        loop.listen_mode = ListeningMode.WAKEWORD
        loop.stt_audio_bytes = bytes()
        loop.stt_chunks = deque([SILENT_CHUNK, SILENT_CHUNK])
        loop.transformers.transform.return_value = (SILENT_CHUNK, {})

        loop._after_cmd(SILENT_CHUNK)

        self.assertEqual(len(loop.stt_chunks), 0)

    def test_calls_vad_reset_if_available(self):
        """_after_cmd calls vad.reset() when the method exists."""
        from ovos_dinkum_listener.voice_loop.voice_loop import ListeningMode
        loop = _make_loop()
        loop.listen_mode = ListeningMode.WAKEWORD
        loop.stt_audio_bytes = bytes()
        loop.stt_chunks = deque()
        loop.transformers.transform.return_value = (SILENT_CHUNK, {})
        loop.vad.reset = Mock()

        loop._after_cmd(SILENT_CHUNK)

        loop.vad.reset.assert_called_once()

    def test_stt_transcription_empty_does_not_crash(self):
        """_after_cmd handles empty STT result gracefully."""
        from ovos_dinkum_listener.voice_loop.voice_loop import ListeningMode
        loop = _make_loop()
        loop.listen_mode = ListeningMode.WAKEWORD
        loop.stt.transcribe.return_value = []
        loop.stt_audio_bytes = bytes()
        loop.stt_chunks = deque()
        loop.transformers.transform.return_value = (SILENT_CHUNK, {})
        text_cb = Mock()
        loop.text_callback = text_cb

        loop._after_cmd(SILENT_CHUNK)

        text_cb.assert_called_once()
        utts, _ = text_cb.call_args[0]
        self.assertEqual(utts, [])


class TestValidateLang(unittest.TestCase):
    """Tests for DinkumVoiceLoop._validate_lang()."""

    @patch("ovos_dinkum_listener.voice_loop.voice_loop.Configuration")
    def test_default_lang_returned_unchanged(self, mock_config):
        """Default language passes validation and is returned as-is."""
        mock_config.return_value = {"lang": "en-us", "secondary_langs": []}
        loop = _make_loop()
        result = loop._validate_lang("en-us")
        self.assertEqual(result, "en-us")

    @patch("ovos_dinkum_listener.voice_loop.voice_loop.Configuration")
    def test_secondary_lang_accepted(self, mock_config):
        """A language in secondary_langs is accepted and returned."""
        mock_config.return_value = {"lang": "en-us", "secondary_langs": ["de-de"]}
        loop = _make_loop()
        result = loop._validate_lang("de-de")
        self.assertEqual(result, "de-de")

    @patch("ovos_dinkum_listener.voice_loop.voice_loop.Configuration")
    def test_unknown_lang_returns_default(self, mock_config):
        """An unknown language code falls back to the default."""
        mock_config.return_value = {"lang": "en-us", "secondary_langs": []}
        loop = _make_loop()
        result = loop._validate_lang("xx-xx")
        self.assertEqual(result, "en-us")

    @patch("ovos_dinkum_listener.voice_loop.voice_loop.Configuration")
    def test_case_insensitive_matching(self, mock_config):
        """Language matching is case-insensitive (BCP-47 base check)."""
        mock_config.return_value = {"lang": "en-US", "secondary_langs": []}
        loop = _make_loop()
        result = loop._validate_lang("EN-US")
        # EN matches en → default is returned
        self.assertEqual(result, "en-US")


class TestGetTx(unittest.TestCase):
    """Tests for DinkumVoiceLoop._get_tx()."""

    def test_primary_stt_success(self):
        """_get_tx returns transcripts from primary STT."""
        loop = _make_loop()
        loop.stt.transcribe.return_value = [("hello", 0.9)]

        utts, ctx = loop._get_tx({})

        self.assertEqual(utts, [("hello", 0.9)])

    def test_primary_stt_empty_uses_fallback(self):
        """_get_tx tries fallback when primary returns empty."""
        fallback = Mock()
        fallback.transcribe.return_value = [("fallback result", 0.85)]
        fallback.stream = Mock()
        fallback.stream.language = "en-us"
        loop = _make_loop(fallback_stt=fallback)
        loop.stt.transcribe.return_value = []

        utts, ctx = loop._get_tx({})

        self.assertEqual(utts, [("fallback result", 0.85)])

    def test_both_stt_fail_returns_empty(self):
        """_get_tx returns empty when both primary and fallback fail."""
        loop = _make_loop()
        loop.stt.transcribe.return_value = []

        utts, ctx = loop._get_tx({})

        self.assertEqual(utts, [])

    def test_primary_stt_exception_uses_fallback(self):
        """_get_tx uses fallback STT when primary raises exception."""
        fallback = Mock()
        fallback.transcribe.return_value = [("fallback result", 0.85)]
        fallback.stream = Mock()
        fallback.stream.language = "en-us"
        loop = _make_loop(fallback_stt=fallback)
        loop.stt.transcribe.side_effect = RuntimeError("stt error")

        utts, ctx = loop._get_tx({})

        self.assertEqual(utts, [("fallback result", 0.85)])

    def test_confidence_filtering(self):
        """_get_tx filters out transcripts below min_stt_confidence."""
        loop = _make_loop(min_stt_confidence=0.7)
        loop.stt.transcribe.return_value = [
            ("good", 0.9),
            ("bad", 0.5),
        ]

        utts, ctx = loop._get_tx({})

        self.assertEqual(utts, [("good", 0.9)])

    def test_all_low_confidence_keeps_best(self):
        """_get_tx ensures at least 1 transcript even if all below threshold."""
        loop = _make_loop(min_stt_confidence=0.99)
        loop.stt.transcribe.return_value = [
            ("best", 0.5),
            ("worst", 0.1),
        ]

        utts, ctx = loop._get_tx({})

        # Must keep the best one
        self.assertEqual(len(utts), 1)
        self.assertEqual(utts[0][0], "best")

    def test_max_transcripts_limits_results(self):
        """_get_tx limits results to max_transcripts."""
        loop = _make_loop(max_transcripts=1, min_stt_confidence=0.0)
        loop.stt.transcribe.return_value = [
            ("first", 0.9),
            ("second", 0.8),
        ]

        utts, ctx = loop._get_tx({})

        self.assertEqual(len(utts), 1)
        self.assertEqual(utts[0][0], "first")

    @patch("ovos_dinkum_listener.voice_loop.voice_loop.Configuration")
    def test_stt_context_lang_overrides_stream(self, mock_config):
        """_get_tx updates stream language from stt_context['stt_lang']."""
        mock_config.return_value = {"lang": "en-us", "secondary_langs": ["de-de"]}
        loop = _make_loop()
        loop.stt.transcribe.return_value = [("hallo", 0.9)]
        ctx = {"stt_lang": "de-de"}

        utts, out_ctx = loop._get_tx(ctx)

        self.assertEqual(loop.stt.stream.language, "de-de")
        self.assertEqual(out_ctx["stt_lang"], "de-de")

    def test_transcriptions_stored_in_context(self):
        """_get_tx stores filtered transcriptions in stt_context."""
        loop = _make_loop(min_stt_confidence=0.0)
        loop.stt.transcribe.return_value = [("hello", 0.9)]
        ctx = {}

        utts, out_ctx = loop._get_tx(ctx)

        self.assertIn("transcriptions", out_ctx)
        self.assertEqual(out_ctx["transcriptions"], utts)


class TestVadRemoveSilence(unittest.TestCase):
    """Tests for DinkumVoiceLoop._vad_remove_silence()."""

    def test_short_audio_skips_trimming(self):
        """Audio shorter than 1 second is not trimmed."""
        loop = _make_loop()
        # Make audio short (< 1 second): chunk_size=4096, spc=0.256 → 0.256s per chunk
        loop.stt_audio_bytes = bytes(CHUNK_SIZE)  # 0.256 seconds → skip

        loop._vad_remove_silence()

        loop.vad.extract_speech.assert_not_called()

    def test_long_audio_calls_vad(self):
        """Audio longer than 1 second triggers VAD silence removal."""
        loop = _make_loop()
        # Need seconds > 1: chunks * spc > 1 → chunks > 1/0.256 ≈ 4 chunks
        loop.stt_audio_bytes = bytes(CHUNK_SIZE * 6)  # 6 * 0.256 = 1.536 seconds
        loop.vad.extract_speech.return_value = None  # all silence, skip

        loop._vad_remove_silence()

        loop.vad.extract_speech.assert_called_once()

    def test_extracted_speech_replaces_buffer(self):
        """Extracted speech replaces the STT stream buffer."""
        from ovos_dinkum_listener.plugins import FakeStreamingSTT
        loop = _make_loop()
        # Set stt to FakeStreamingSTT type so the isinstance check passes
        loop.stt = Mock(spec=FakeStreamingSTT)
        loop.stt.stream = Mock()
        loop.stt.stream.buffer = Mock()
        loop.stt.lang = "en-us"
        loop.stt.stream.language = "en-us"

        # Long audio: 6 chunks → 1.536s
        long_audio = bytes(CHUNK_SIZE * 6)
        loop.stt_audio_bytes = long_audio
        # Extracted speech > 1 second
        extracted = bytes(CHUNK_SIZE * 5)  # 1.28 seconds
        loop.vad.extract_speech.return_value = extracted

        loop._vad_remove_silence()

        loop.stt.stream.buffer.clear.assert_called_once()
        loop.stt.stream.update.assert_called_once_with(extracted)

    def test_short_extracted_speech_skips_replacement(self):
        """If extracted speech < 1 second, buffer is NOT replaced."""
        from ovos_dinkum_listener.plugins import FakeStreamingSTT
        loop = _make_loop()
        loop.stt = Mock(spec=FakeStreamingSTT)
        loop.stt.stream = Mock()
        loop.stt.stream.buffer = Mock()
        loop.stt.lang = "en-us"
        loop.stt.stream.language = "en-us"

        loop.stt_audio_bytes = bytes(CHUNK_SIZE * 6)  # 1.536s
        # Extracted speech < 1 second → should not replace
        loop.vad.extract_speech.return_value = bytes(CHUNK_SIZE * 2)  # 0.512s

        loop._vad_remove_silence()

        loop.stt.stream.buffer.clear.assert_not_called()


class TestWaitCmd(unittest.TestCase):
    """Tests for DinkumVoiceLoop._wait_cmd()."""

    def test_speech_decrements_timer_and_moves_to_in_cmd_continuous(self):
        """Enough speech in CONTINUOUS mode → IN_COMMAND state."""
        from ovos_dinkum_listener.voice_loop.voice_loop import ListeningState, ListeningMode
        loop = _make_loop()
        loop.listen_mode = ListeningMode.CONTINUOUS
        loop.state = ListeningState.WAITING_CMD
        loop.speech_seconds_left = 0.0  # already at 0 → threshold met
        loop.vad.is_silence.return_value = False  # speech

        loop._wait_cmd(SPEECH_CHUNK)

        self.assertEqual(loop.state, ListeningState.IN_COMMAND)

    def test_silence_resets_speech_timer(self):
        """Silence in _wait_cmd resets speech_seconds_left."""
        from ovos_dinkum_listener.voice_loop.voice_loop import ListeningState, ListeningMode
        loop = _make_loop()
        loop.listen_mode = ListeningMode.WAKEWORD
        loop.speech_seconds_left = 0.01  # partially decremented
        loop.vad.is_silence.return_value = True  # silence
        loop.hotwords.found.return_value = None

        loop._wait_cmd(SILENT_CHUNK)

        self.assertAlmostEqual(loop.speech_seconds_left, loop.speech_seconds)

    def test_silence_checks_hotwords(self):
        """Silence in _wait_cmd checks for hotwords."""
        from ovos_dinkum_listener.voice_loop.voice_loop import ListeningState, ListeningMode
        loop = _make_loop()
        loop.listen_mode = ListeningMode.WAKEWORD
        loop.speech_seconds_left = 99.0
        loop.vad.is_silence.return_value = True

        loop._wait_cmd(SILENT_CHUNK)

        loop.hotwords.update.assert_called()
        loop.hotwords.found.assert_called()

    def test_feeds_audio_to_transformers_when_no_hotword(self):
        """When no hotword found, audio is fed to transformers."""
        from ovos_dinkum_listener.voice_loop.voice_loop import ListeningMode
        loop = _make_loop()
        loop.listen_mode = ListeningMode.WAKEWORD
        loop.vad.is_silence.return_value = True
        loop.hotwords.found.return_value = None

        loop._wait_cmd(SILENT_CHUNK)

        loop.transformers.feed_audio.assert_called_with(SILENT_CHUNK)

    def test_continuous_mode_accumulates_audio(self):
        """In CONTINUOUS mode silence, audio is accumulated in stt buffers."""
        from ovos_dinkum_listener.voice_loop.voice_loop import ListeningMode
        loop = _make_loop()
        loop.listen_mode = ListeningMode.CONTINUOUS
        loop.speech_seconds_left = 99.0
        loop.vad.is_silence.return_value = True
        loop.hotwords.found.return_value = None
        loop.stt_audio_bytes = bytes()
        loop.stt_chunks = deque()

        loop._wait_cmd(SILENT_CHUNK)

        self.assertEqual(loop.stt_audio_bytes, SILENT_CHUNK)
        self.assertIn(SILENT_CHUNK, loop.stt_chunks)


class TestInRecording(unittest.TestCase):
    """Tests for DinkumVoiceLoop._in_recording()."""

    def test_stop_word_triggers_stop_recording(self):
        """Stop word detection triggers stop_recording()."""
        from ovos_dinkum_listener.voice_loop.voice_loop import ListeningState
        from ovos_dinkum_listener.voice_loop.hotwords import HotwordState
        loop = _make_loop()
        loop.state = ListeningState.RECORDING
        loop.hotwords.found.return_value = 'stop'
        loop.hotwords.state = HotwordState.LISTEN
        record_end_cb = Mock()
        loop.record_end_callback = record_end_cb
        loop.stt_audio_bytes = bytes(100)
        loop.hotword_chunks = deque()

        loop._in_recording(SILENT_CHUNK)

        # stop_recording resets state
        record_end_cb.assert_called_once()

    def test_no_stop_word_accumulates_audio(self):
        """Without stop word, audio is accumulated in stt_audio_bytes."""
        from ovos_dinkum_listener.voice_loop.voice_loop import ListeningState
        loop = _make_loop()
        loop.state = ListeningState.RECORDING
        loop.hotwords.found.return_value = None
        loop.vad.is_silence.return_value = False  # speech
        loop.recording_seconds_with_silence_left = 30.0
        loop.stt_audio_bytes = bytes()

        loop._in_recording(SPEECH_CHUNK)

        self.assertIn(SPEECH_CHUNK, loop.stt_audio_bytes)

    def test_silence_decrements_silence_timer(self):
        """Silence during recording decrements recording_seconds_with_silence_left."""
        from ovos_dinkum_listener.voice_loop.voice_loop import ListeningState
        loop = _make_loop()
        loop.state = ListeningState.RECORDING
        loop.hotwords.found.return_value = None
        loop.vad.is_silence.return_value = True  # silence
        loop.recording_seconds_with_silence_left = 99.0
        loop.stt_audio_bytes = bytes()

        loop._in_recording(SILENT_CHUNK)

        self.assertLess(loop.recording_seconds_with_silence_left, 99.0)

    def test_max_silence_stops_recording(self):
        """When silence timer expires, recording is stopped."""
        from ovos_dinkum_listener.voice_loop.voice_loop import ListeningState
        loop = _make_loop()
        loop.state = ListeningState.RECORDING
        loop.hotwords.found.return_value = None
        loop.vad.is_silence.return_value = True
        loop.recording_seconds_with_silence_left = 0  # already expired
        loop.stt_audio_bytes = bytes(10)
        record_end_cb = Mock()
        loop.record_end_callback = record_end_cb

        loop._in_recording(SILENT_CHUNK)

        record_end_cb.assert_called_once()

    def test_stop_word_fires_stopword_audio_callback(self):
        """Stop word triggers stopword_audio_callback with accumulated audio."""
        from ovos_dinkum_listener.voice_loop.voice_loop import ListeningState
        loop = _make_loop()
        loop.state = ListeningState.RECORDING
        loop.hotwords.found.return_value = 'stop'
        stop_cb = Mock()
        loop.stopword_audio_callback = stop_cb
        loop.stt_audio_bytes = bytes()
        # Pre-fill hotword_chunks
        loop.hotword_chunks = deque([bytes(100), bytes(100)])

        loop._in_recording(SILENT_CHUNK)

        stop_cb.assert_called_once()
        audio_arg = stop_cb.call_args[0][0]
        self.assertEqual(audio_arg, bytes(200))


class TestDetectWakeup(unittest.TestCase):
    """Tests for DinkumVoiceLoop._detect_wakeup()."""

    def test_wakeup_word_found_exits_sleep(self):
        """Wakeup word detection exits sleep and calls wakeup_callback."""
        from ovos_dinkum_listener.voice_loop.voice_loop import ListeningState
        from ovos_dinkum_listener.voice_loop.hotwords import HotwordState
        loop = _make_loop()
        loop.state = ListeningState.CHECK_WAKE_UP
        loop.last_ww = time.time()  # recent
        loop.hotwords.found.return_value = 'wake_up'
        wakeup_cb = Mock()
        loop.wakeup_callback = wakeup_cb
        loop.hotword_chunks = deque()

        result = loop._detect_wakeup(SPEECH_CHUNK)

        self.assertTrue(result)
        self.assertEqual(loop.state, ListeningState.DETECT_WAKEWORD)
        wakeup_cb.assert_called_once()

    def test_wakeup_not_found_and_timeout_returns_to_sleep(self):
        """No wakeup word after 10s returns to SLEEPING."""
        from ovos_dinkum_listener.voice_loop.voice_loop import ListeningState
        loop = _make_loop()
        loop.state = ListeningState.CHECK_WAKE_UP
        loop.last_ww = time.time() - 15  # 15 seconds ago → timeout
        loop.hotwords.found.return_value = None

        result = loop._detect_wakeup(SILENT_CHUNK)

        self.assertFalse(result)
        self.assertEqual(loop.state, ListeningState.SLEEPING)

    def test_wakeup_audio_callback_fired(self):
        """Wakeup word fires wakeupword_audio_callback with audio data."""
        from ovos_dinkum_listener.voice_loop.voice_loop import ListeningState
        loop = _make_loop()
        loop.state = ListeningState.CHECK_WAKE_UP
        loop.last_ww = time.time()
        loop.hotwords.found.return_value = 'wake_up'
        wakeup_audio_cb = Mock()
        loop.wakeupword_audio_callback = wakeup_audio_cb
        loop.hotword_chunks = deque([bytes(100), bytes(100)])

        loop._detect_wakeup(SPEECH_CHUNK)

        wakeup_audio_cb.assert_called_once()
        audio_arg = wakeup_audio_cb.call_args[0][0]
        self.assertEqual(audio_arg, bytes(200))


class TestDetectWw(unittest.TestCase):
    """Tests for DinkumVoiceLoop._detect_ww()."""

    def test_ww_detected_starts_stt_and_calls_callbacks(self):
        """Wake word detection starts STT streaming and calls wake_callback."""
        from ovos_dinkum_listener.voice_loop.voice_loop import ListeningState, ListeningMode
        loop = _make_loop()
        loop.listen_mode = ListeningMode.WAKEWORD
        loop.state = ListeningState.DETECT_WAKEWORD
        loop.hotwords.found.return_value = 'hey_test'
        loop.hotwords.get_ww.return_value = {
            'engine': 'TestEngine',
            'key_phrase': 'hey_test',
            'module': 'test',
            'listen': True,
            'sound': None,
        }
        wake_cb = Mock()
        loop.wake_callback = wake_cb
        loop.hotword_chunks = deque([bytes(100)])

        result = loop._detect_ww(SPEECH_CHUNK)

        self.assertTrue(result)
        wake_cb.assert_called_once()
        loop.stt.stream_start.assert_called_once()

    def test_ww_not_detected_returns_false(self):
        """No wake word → returns False, state unchanged."""
        from ovos_dinkum_listener.voice_loop.voice_loop import ListeningState
        loop = _make_loop()
        loop.state = ListeningState.DETECT_WAKEWORD
        loop.hotwords.found.return_value = None

        result = loop._detect_ww(SILENT_CHUNK)

        self.assertFalse(result)

    def test_ww_with_sound_goes_to_confirmation(self):
        """Wake word with 'sound' configured → CONFIRMATION state."""
        from ovos_dinkum_listener.voice_loop.voice_loop import ListeningState, ListeningMode
        loop = _make_loop()
        loop.listen_mode = ListeningMode.WAKEWORD
        loop.state = ListeningState.DETECT_WAKEWORD
        loop.hotwords.found.return_value = 'hey_test'
        loop.hotwords.get_ww.return_value = {
            'key_phrase': 'hey_test',
            'module': 'test',
            'engine': 'Test',
            'listen': True,
            'sound': 'beep.wav',
            'sound_duration': 0.5,
        }
        loop.hotword_chunks = deque()

        loop._detect_ww(SPEECH_CHUNK)

        self.assertEqual(loop.state, ListeningState.CONFIRMATION)

    def test_ww_without_sound_goes_to_before_command(self):
        """Wake word without 'sound' → BEFORE_COMMAND state."""
        from ovos_dinkum_listener.voice_loop.voice_loop import ListeningState, ListeningMode
        loop = _make_loop()
        loop.listen_mode = ListeningMode.WAKEWORD
        loop.state = ListeningState.DETECT_WAKEWORD
        loop.hotwords.found.return_value = 'hey_test'
        loop.hotwords.get_ww.return_value = {
            'key_phrase': 'hey_test',
            'module': 'test',
            'engine': 'Test',
            'listen': True,
            'sound': None,
        }
        loop.hotword_chunks = deque()

        loop._detect_ww(SPEECH_CHUNK)

        self.assertEqual(loop.state, ListeningState.BEFORE_COMMAND)

    def test_ww_found_with_listen_returns_true(self):
        """When WW found and listen=True, _detect_ww returns True."""
        from ovos_dinkum_listener.voice_loop.voice_loop import ListeningState
        loop = _make_loop()
        loop.state = ListeningState.DETECT_WAKEWORD
        loop.hotwords.found.return_value = 'hey_test'
        loop.hotwords.get_ww.return_value = {
            'key_phrase': 'hey_test',
            'module': 'test',
            'engine': 'Test',
            'listen': True,
            'sound': None,
        }
        loop.hotword_chunks = deque()

        result = loop._detect_ww(SPEECH_CHUNK)

        self.assertTrue(result)

    def test_sleeping_mode_goes_to_check_wake_up(self):
        """In SLEEPING mode, WW detection → CHECK_WAKE_UP state."""
        from ovos_dinkum_listener.voice_loop.voice_loop import ListeningState, ListeningMode
        loop = _make_loop()
        loop.listen_mode = ListeningMode.SLEEPING
        loop.state = ListeningState.SLEEPING
        loop.hotwords.found.return_value = 'hey_test'
        loop.hotwords.get_ww.return_value = {
            'key_phrase': 'hey_test',
            'module': 'test',
            'engine': 'Test',
            'listen': True,
            'sound': None,
        }
        loop.hotword_chunks = deque()

        loop._detect_ww(SPEECH_CHUNK)

        self.assertEqual(loop.state, ListeningState.CHECK_WAKE_UP)

    def test_listenword_audio_callback_fired(self):
        """Listenword audio callback is fired on WW detection."""
        from ovos_dinkum_listener.voice_loop.voice_loop import ListeningMode
        loop = _make_loop()
        loop.listen_mode = ListeningMode.WAKEWORD
        loop.hotwords.found.return_value = 'hey_test'
        loop.hotwords.get_ww.return_value = {
            'key_phrase': 'hey_test',
            'module': 'test',
            'engine': 'Test',
            'listen': True,
            'sound': None,
        }
        cb = Mock()
        loop.listenword_audio_callback = cb
        loop.hotword_chunks = deque([bytes(50)])

        loop._detect_ww(SPEECH_CHUNK)

        cb.assert_called_once()

    def test_fallback_stt_started_on_ww(self):
        """Fallback STT stream_start() is called on WW detection."""
        from ovos_dinkum_listener.voice_loop.voice_loop import ListeningMode
        fallback = Mock()
        loop = _make_loop(fallback_stt=fallback)
        loop.listen_mode = ListeningMode.WAKEWORD
        loop.hotwords.found.return_value = 'hey_test'
        loop.hotwords.get_ww.return_value = {
            'key_phrase': 'hey_test',
            'module': 'test',
            'engine': 'Test',
            'listen': True,
            'sound': None,
        }
        loop.hotword_chunks = deque()

        loop._detect_ww(SPEECH_CHUNK)

        fallback.stream_start.assert_called_once()


class TestResetState(unittest.TestCase):
    """Tests for DinkumVoiceLoop.reset_state()."""

    def test_wakeword_mode_resets_to_detect_wakeword(self):
        """In WAKEWORD mode, reset_state() → DETECT_WAKEWORD."""
        from ovos_dinkum_listener.voice_loop.voice_loop import ListeningState, ListeningMode
        from ovos_dinkum_listener.voice_loop.hotwords import HotwordState
        loop = _make_loop()
        loop.listen_mode = ListeningMode.WAKEWORD

        loop.reset_state()

        self.assertEqual(loop.state, ListeningState.DETECT_WAKEWORD)
        loop.hotwords.__setattr__('state', HotwordState.LISTEN)

    def test_continuous_mode_resets_to_waiting_cmd(self):
        """In CONTINUOUS mode, reset_state() → WAITING_CMD."""
        from ovos_dinkum_listener.voice_loop.voice_loop import ListeningState, ListeningMode
        loop = _make_loop()
        loop.listen_mode = ListeningMode.CONTINUOUS

        loop.reset_state()

        self.assertEqual(loop.state, ListeningState.WAITING_CMD)


class TestGoToSleep(unittest.TestCase):
    """Tests for DinkumVoiceLoop.go_to_sleep()."""

    def test_go_to_sleep_sets_sleeping_state(self):
        """go_to_sleep() sets state to SLEEPING."""
        from ovos_dinkum_listener.voice_loop.voice_loop import ListeningState
        loop = _make_loop()
        loop.go_to_sleep()
        self.assertEqual(loop.state, ListeningState.SLEEPING)


class TestStartStopRecording(unittest.TestCase):
    """Tests for start_recording() and stop_recording()."""

    def test_start_recording_sets_state(self):
        """start_recording() sets state to RECORDING and calls wake_callback."""
        from ovos_dinkum_listener.voice_loop.voice_loop import ListeningState
        loop = _make_loop()
        wake_cb = Mock()
        loop.wake_callback = wake_cb

        loop.start_recording(filename="test_rec")

        self.assertEqual(loop.state, ListeningState.RECORDING)
        self.assertEqual(loop.recording_filename, "test_rec")
        wake_cb.assert_called_once()

    def test_stop_recording_fires_callbacks_and_resets(self):
        """stop_recording() fires recording_audio_callback and record_end_callback."""
        from ovos_dinkum_listener.voice_loop.voice_loop import ListeningMode
        loop = _make_loop()
        loop.listen_mode = ListeningMode.WAKEWORD
        rec_audio_cb = Mock()
        rec_end_cb = Mock()
        loop.recording_audio_callback = rec_audio_cb
        loop.record_end_callback = rec_end_cb
        loop.stt_audio_bytes = b'\xAA' * 100
        loop.recording_filename = "test"

        loop.stop_recording()

        rec_audio_cb.assert_called_once_with(b'\xAA' * 100, {"recording_name": "test"})
        rec_end_cb.assert_called_once()


class TestStop(unittest.TestCase):
    """Tests for DinkumVoiceLoop.stop()."""

    def test_stop_sets_is_running_false(self):
        """stop() sets _is_running to False."""
        loop = _make_loop()
        loop._is_running = True

        loop.stop()

        self.assertFalse(loop._is_running)
        self.assertFalse(loop.running)


if __name__ == '__main__':
    unittest.main()
