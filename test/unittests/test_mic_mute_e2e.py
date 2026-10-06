"""End-to-end bus tests for microphone mute / stop handling.

These drive the *real* ``register_event_handlers`` wiring on a ``FakeBus`` and
assert behaviour purely through bus messages — so they cover both the handler
logic and the event→handler routing (notably that ``mycroft.stop`` is routed to
``_handle_stop_recording``, not the old unmute handler).

Why this matters (the bug these tests pin down):

1. ``mute_during_output`` used to unconditionally **unmute** the mic when audio
   playback ended. If a user had deliberately muted the mic, a single TTS reply
   would silently un-mute them. The fix remembers the pre-playback mute state
   and restores *that*.
2. ``mycroft.stop`` (e.g. a hardware button press) used to call a handler whose
   only effect was ``is_muted = False`` — so "stop" doubled as a surprise
   unmute. It now stops an in-progress recording instead, and is a no-op when
   nothing is recording.
"""
import unittest
from unittest.mock import Mock

from ovos_utils.messagebus import FakeBus
from ovos_bus_client.message import Message
from ovos_spec_tools import SpecMessage

from ovos_dinkum_listener.voice_loop import ListeningState


class _FakeVoiceLoop:
    """Minimal stand-in exposing the attributes the mute/stop handlers touch."""

    def __init__(self):
        self.is_muted = False
        self.running = True
        self.state = ListeningState.DETECT_WAKEWORD
        self.stop_recording = Mock()


def _make_service(mute_during_output=True):
    """Bare service wired to a FakeBus, without the heavy __init__."""
    from ovos_dinkum_listener.service import OVOSDinkumVoiceService

    svc = OVOSDinkumVoiceService.__new__(OVOSDinkumVoiceService)
    svc.bus = FakeBus()
    svc.voice_loop = _FakeVoiceLoop()
    svc._tmp_muted = None
    svc.config = {
        "listener": {"mute_during_output": mute_during_output},
        "sounds": {"end_listening": "snd/end_listening.mp3"},
    }
    # avoid the bus round-trip _query_volume() does at registration time
    svc._query_volume = lambda: None
    svc.register_event_handlers()
    return svc


class TestMuteUnmuteBus(unittest.TestCase):
    def test_mute_unmute_toggle(self):
        svc = _make_service()
        svc.bus.emit(Message("mycroft.mic.mute"))
        self.assertTrue(svc.voice_loop.is_muted)
        svc.bus.emit(Message("mycroft.mic.unmute"))
        self.assertFalse(svc.voice_loop.is_muted)
        svc.bus.emit(Message("mycroft.mic.mute.toggle"))
        self.assertTrue(svc.voice_loop.is_muted)
        svc.bus.emit(Message("mycroft.mic.mute.toggle"))
        self.assertFalse(svc.voice_loop.is_muted)

    def test_get_status_reports_mute_state(self):
        svc = _make_service()
        replies = []
        svc.bus.on("mycroft.mic.get_status.response", lambda m: replies.append(m))
        svc.bus.emit(Message("mycroft.mic.mute"))
        svc.bus.emit(Message("mycroft.mic.get_status"))
        self.assertTrue(replies and replies[-1].data["muted"])


class TestMuteDuringOutput(unittest.TestCase):
    def test_unmuted_user_restored_to_unmuted(self):
        """Default case: mic unmuted, output mutes it, then restores to unmuted."""
        svc = _make_service(mute_during_output=True)
        self.assertFalse(svc.voice_loop.is_muted)
        svc.bus.emit(Message(SpecMessage.AUDIO_OUTPUT_STARTED))
        self.assertTrue(svc.voice_loop.is_muted)
        svc.bus.emit(Message(SpecMessage.AUDIO_OUTPUT_ENDED))
        self.assertFalse(svc.voice_loop.is_muted)

    def test_user_muted_stays_muted_after_output(self):
        """The fix: a deliberately muted mic must NOT be unmuted by playback."""
        svc = _make_service(mute_during_output=True)
        svc.bus.emit(Message("mycroft.mic.mute"))
        self.assertTrue(svc.voice_loop.is_muted)

        svc.bus.emit(Message(SpecMessage.AUDIO_OUTPUT_STARTED))
        self.assertTrue(svc.voice_loop.is_muted)
        svc.bus.emit(Message(SpecMessage.AUDIO_OUTPUT_ENDED))
        # Regression: old code set is_muted = False here.
        self.assertTrue(svc.voice_loop.is_muted)

    def test_disabled_does_not_touch_mute_state(self):
        svc = _make_service(mute_during_output=False)
        svc.bus.emit(Message("mycroft.mic.mute"))
        svc.bus.emit(Message(SpecMessage.AUDIO_OUTPUT_STARTED))
        self.assertTrue(svc.voice_loop.is_muted)
        svc.bus.emit(Message(SpecMessage.AUDIO_OUTPUT_ENDED))
        self.assertTrue(svc.voice_loop.is_muted)


class TestStopRouting(unittest.TestCase):
    def test_mycroft_stop_while_recording_stops_recording(self):
        svc = _make_service()
        svc.voice_loop.state = ListeningState.RECORDING
        sounds = []
        svc.bus.on("mycroft.audio.play_sound", lambda m: sounds.append(m))

        svc.bus.emit(Message("mycroft.stop"))
        svc.voice_loop.stop_recording.assert_called_once()
        self.assertTrue(sounds, "end_listening sound should be emitted")

    def test_record_stop_while_recording_stops_recording(self):
        svc = _make_service()
        svc.voice_loop.state = ListeningState.RECORDING
        svc.bus.emit(Message("recognizer_loop:record_stop"))
        svc.voice_loop.stop_recording.assert_called_once()

    def test_stop_is_noop_when_not_recording(self):
        svc = _make_service()
        svc.voice_loop.state = ListeningState.DETECT_WAKEWORD
        sounds = []
        svc.bus.on("mycroft.audio.play_sound", lambda m: sounds.append(m))

        svc.bus.emit(Message("mycroft.stop"))
        svc.voice_loop.stop_recording.assert_not_called()
        self.assertFalse(sounds, "no end_listening sound when not recording")

    def test_mycroft_stop_does_not_unmute(self):
        """Regression: the old mycroft.stop handler force-unmuted the mic."""
        svc = _make_service()
        svc.voice_loop.state = ListeningState.DETECT_WAKEWORD
        svc.bus.emit(Message("mycroft.mic.mute"))
        self.assertTrue(svc.voice_loop.is_muted)

        svc.bus.emit(Message("mycroft.stop"))
        self.assertTrue(svc.voice_loop.is_muted)


if __name__ == "__main__":
    unittest.main()
