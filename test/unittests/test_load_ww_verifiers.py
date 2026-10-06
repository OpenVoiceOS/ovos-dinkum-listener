"""Unit tests for ``OVOSDinkumVoiceService._load_ww_verifiers``.

This covers the config-parsing layer that turns ``listener.ww_verifiers`` into
instantiated verifier plugins — the seam between user config and the verifier
chain exercised by ``test_hotwords.py`` / ``test_voice_loop_verifier_e2e.py``.

The method only reads ``self.config`` and the module-level
``find_wake_word_verifier_plugins`` discovery helper, so the tests drive it on a
bare instance (``__new__``) instead of building the whole listener service.
"""
import unittest
from unittest.mock import patch

from ovos_dinkum_listener.service import OVOSDinkumVoiceService


class _FakeVerifier:
    """Minimal HotWordVerifier-like stand-in that records its config."""

    def __init__(self, config=None):
        self.config = config or {}

    def verify(self, ww_audio):  # pragma: no cover - not exercised here
        return True


class _BoomVerifier:
    """Verifier whose constructor raises, to exercise the fail-open path."""

    def __init__(self, config=None):
        raise RuntimeError("cannot init")


def _service(ww_verifiers):
    """Bare service instance with only the config the method needs."""
    svc = OVOSDinkumVoiceService.__new__(OVOSDinkumVoiceService)
    svc.config = {"listener": {"ww_verifiers": ww_verifiers}}
    return svc

_DISCOVERY = "ovos_dinkum_listener.service.find_wake_word_verifier_plugins"


class TestLoadWwVerifiers(unittest.TestCase):
    def test_no_config_no_plugins(self):
        """Empty config and nothing installed → no verifiers."""
        svc = _service({})
        with patch(_DISCOVERY, return_value={}):
            self.assertEqual(svc._load_ww_verifiers(), [])

    def test_installed_plugin_enabled_by_default(self):
        """An installed plugin with no config entry is loaded with `{}`."""
        svc = _service({})
        with patch(_DISCOVERY, return_value={"ovos-ww-verifier-speaker": _FakeVerifier}):
            result = svc._load_ww_verifiers()
        self.assertEqual(len(result), 1)
        self.assertIsInstance(result[0], _FakeVerifier)
        self.assertEqual(result[0].config, {})

    def test_config_is_passed_to_plugin(self):
        """The per-plugin config dict reaches the plugin constructor."""
        cfg = {"threshold": 0.7, "fail_open": False}
        svc = _service({"ovos-ww-verifier-speaker": cfg})
        with patch(_DISCOVERY, return_value={"ovos-ww-verifier-speaker": _FakeVerifier}):
            result = svc._load_ww_verifiers()
        self.assertEqual(result[0].config, cfg)

    def test_disabled_plugin_is_skipped(self):
        """`enabled: false` skips an installed plugin."""
        svc = _service({"ovos-ww-verifier-speaker": {"enabled": False}})
        with patch(_DISCOVERY, return_value={"ovos-ww-verifier-speaker": _FakeVerifier}):
            self.assertEqual(svc._load_ww_verifiers(), [])

    def test_only_enabled_of_several_loaded(self):
        svc = _service({
            "ovos-ww-verifier-speaker": {"enabled": True},
            "ovos-ww-verifier-silero": {"enabled": False},
        })
        installed = {
            "ovos-ww-verifier-speaker": _FakeVerifier,
            "ovos-ww-verifier-silero": _FakeVerifier,
        }
        with patch(_DISCOVERY, return_value=installed):
            result = svc._load_ww_verifiers()
        self.assertEqual(len(result), 1)

    def test_enabled_but_not_installed_warns(self):
        """A plugin enabled in config but not installed warns and is skipped."""
        svc = _service({"ovos-ww-verifier-speaker": {"threshold": 0.5}})
        with patch(_DISCOVERY, return_value={}), \
                patch("ovos_dinkum_listener.service.LOG.warning") as warn:
            result = svc._load_ww_verifiers()
        self.assertEqual(result, [])
        warn.assert_called_once()
        self.assertIn("ovos-ww-verifier-speaker", warn.call_args[0][0])

    def test_disabled_and_missing_does_not_warn(self):
        """A missing plugin that is also disabled should not warn."""
        svc = _service({"ovos-ww-verifier-speaker": {"enabled": False}})
        with patch(_DISCOVERY, return_value={}), \
                patch("ovos_dinkum_listener.service.LOG.warning") as warn:
            result = svc._load_ww_verifiers()
        self.assertEqual(result, [])
        warn.assert_not_called()

    def test_plugin_constructor_failure_is_fail_open(self):
        """A plugin that raises on construction is skipped, not fatal."""
        svc = _service({"ovos-ww-verifier-speaker": {}})
        with patch(_DISCOVERY, return_value={"ovos-ww-verifier-speaker": _BoomVerifier}):
            # must not raise
            result = svc._load_ww_verifiers()
        self.assertEqual(result, [])


if __name__ == "__main__":
    unittest.main()
