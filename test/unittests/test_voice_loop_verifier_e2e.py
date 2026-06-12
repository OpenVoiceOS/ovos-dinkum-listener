"""End-to-end bus-sequence tests for the wake-word verifier gate.

Where ``test_hotwords.py`` unit-tests ``HotwordContainer.verify()`` and
``test_voice_loop_methods.py`` exercises ``_detect_ww()`` with mocked hotwords,
these tests drive a *real* ``DinkumVoiceLoop`` end-to-end: PCM chunks are fed
through ``_detect_ww`` and the bus events emitted as side-effects
(``recognizer_loop:wakeword`` / ``recognizer_loop:record_begin``) are asserted as
a function of the verifier-chain outcome.

This is the coverage gap described in ovoscope#64 — it is driven by ovoscope's
``MiniVoiceLoop`` harness, which wires a real ``DinkumVoiceLoop`` to a
``FakeBus`` and emits the same record-begin/wakeword events as the listener
service.
"""
import unittest
from unittest.mock import Mock

try:
    from ovoscope.voice_loop import MiniVoiceLoop, MockHotWordEngine
    HAS_OVOSCOPE = True
except ImportError:
    HAS_OVOSCOPE = False

SILENT_CHUNK = b"\x00" * 512


def _verifier(returns=True, raises=None):
    """Build a verifier-like mock (``verify(audio) -> bool``)."""
    v = Mock()
    if raises is not None:
        v.verify.side_effect = raises
    else:
        v.verify.return_value = returns
    return v


@unittest.skipUnless(HAS_OVOSCOPE, "ovoscope (>=0.19.0a1) not installed")
class TestVerifierGateE2E(unittest.TestCase):
    """Feed PCM through a real DinkumVoiceLoop and assert the bus sequence."""

    def _loop(self, verifiers):
        ww = MockHotWordEngine("hey_mycroft", trigger_after=3)
        return MiniVoiceLoop(ww_instances={"hey_mycroft": ww}, verifiers=verifiers)

    def test_all_verifiers_accept_emits_record_begin(self):
        """WW detected + every verifier accepts → recording begins."""
        with self._loop([_verifier(returns=True), _verifier(returns=True)]) as vl:
            msgs = vl.feed_chunks([SILENT_CHUNK] * 5)
            vl.assert_wakeword_detected(msgs)

    def test_verifier_rejects_suppresses_detection(self):
        """WW detected + a verifier rejects → no record-begin / wakeword."""
        with self._loop([_verifier(returns=False)]) as vl:
            msgs = vl.feed_chunks([SILENT_CHUNK] * 5)
            vl.assert_wakeword_suppressed(msgs)

    def test_second_verifier_rejects_suppresses(self):
        """A later verifier rejecting still suppresses the detection."""
        with self._loop([_verifier(returns=True), _verifier(returns=False)]) as vl:
            msgs = vl.feed_chunks([SILENT_CHUNK] * 5)
            vl.assert_wakeword_suppressed(msgs)

    def test_raising_verifier_is_fail_open(self):
        """A verifier that raises must NOT suppress the detection (fail-open)."""
        with self._loop([_verifier(raises=RuntimeError("sensor error"))]) as vl:
            msgs = vl.feed_chunks([SILENT_CHUNK] * 5)
            vl.assert_record_begin_emitted(msgs)

    def test_raise_then_reject_suppresses(self):
        """Fail-open on a raise does not stop a later verifier from rejecting."""
        verifiers = [_verifier(raises=ValueError("bad")), _verifier(returns=False)]
        with self._loop(verifiers) as vl:
            msgs = vl.feed_chunks([SILENT_CHUNK] * 5)
            vl.assert_wakeword_suppressed(msgs)

    def test_no_verifiers_emits_record_begin(self):
        """With no verifiers configured, a detection proceeds normally."""
        with self._loop([]) as vl:
            msgs = vl.feed_chunks([SILENT_CHUNK] * 5)
            vl.assert_record_begin_emitted(msgs)

    def test_no_wakeword_no_recognizer_events(self):
        """No wake word detected → no recognizer_loop:* events at all."""
        ww = MockHotWordEngine("hey_mycroft", trigger_after=100)
        with MiniVoiceLoop(
            ww_instances={"hey_mycroft": ww}, verifiers=[_verifier(returns=True)]
        ) as vl:
            msgs = vl.feed_chunks([SILENT_CHUNK] * 5)
            self.assertFalse(
                any(m.msg_type.startswith("recognizer_loop:") for m in msgs)
            )


if __name__ == "__main__":
    unittest.main()
