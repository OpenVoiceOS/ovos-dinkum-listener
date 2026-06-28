import unittest
from unittest.mock import Mock, patch
from ovos_utils.messagebus import FakeBus
from ovos_plugin_manager.templates.transformers import AudioTransformer


class MockTransformer(AudioTransformer):
    feed_audio_chunk = Mock()
    feed_hotword_chunk = Mock()
    feed_speech_chunk = Mock()
    feed_speech_utterance = Mock(return_value=b"0")
    transform = Mock(return_value=(b"1", {"handled": True}))
    shutdown = Mock()

    def __init__(self):
        AudioTransformer.__init__(self, "mock")


class TestTransformers(unittest.TestCase):
    bus = FakeBus()

    @patch("ovos_plugin_manager.audio_transformers.find_audio_transformer_plugins")
    def test_audio_transformer_service_no_plugins(self, find_transformers):
        import ovos_dinkum_listener.transformers

        ovos_dinkum_listener.transformers.find_audio_transformer_plugins = (
            find_transformers
        )
        from ovos_dinkum_listener.transformers import AudioTransformersService

        find_transformers.return_value = {"mock": MockTransformer}

        # Init service, transformer disabled
        config = {"listener": {"audio_transformers": {"mock": {"active": False}}}}
        service = AudioTransformersService(self.bus, config)
        self.assertEqual(service.bus, self.bus)
        self.assertEqual(service.config, config["listener"]["audio_transformers"])
        self.assertTrue(service.has_loaded)
        self.assertEqual(service.loaded_plugins, dict())
        self.assertEqual(service.plugins, list())

        # Call methods to ensure no exceptions are raised
        service.feed_audio(b"00")
        service.feed_hotword(b"00")
        service.feed_speech(b"00")
        returned = service.transform(b"00")
        self.assertEqual(returned[0], b"00")
        context = returned[1]
        self.assertIsInstance(context, dict)
        self.assertIsInstance(context["client_name"], str)  # Allow name change
        self.assertEqual(context["source"], "audio")
        self.assertIn("skills", context["destination"])

    @patch("ovos_plugin_manager.audio_transformers.find_audio_transformer_plugins")
    def test_audio_transformer_service_with_plugin(self, find_transformers):
        import ovos_dinkum_listener.transformers

        ovos_dinkum_listener.transformers.find_audio_transformer_plugins = (
            find_transformers
        )
        from ovos_dinkum_listener.transformers import AudioTransformersService

        find_transformers.return_value = {"mock": MockTransformer}

        # Init service, transformer disabled
        config = {"listener": {"audio_transformers": {"mock": {"active": True}}}}
        service = AudioTransformersService(self.bus, config)
        self.assertEqual(service.bus, self.bus)
        self.assertEqual(service.config, config["listener"]["audio_transformers"])
        self.assertTrue(service.has_loaded)
        self.assertEqual(set(service.loaded_plugins.keys()), {"mock"})
        self.assertEqual(len(service.plugins), 1)

        # Call methods
        service.feed_audio(b"01")
        service.feed_hotword(b"02")
        service.feed_speech(b"03")
        MockTransformer.feed_audio_chunk.assert_called_once_with(b"01")
        MockTransformer.feed_hotword_chunk.assert_called_once_with(b"02")
        MockTransformer.feed_speech_chunk.assert_called_once_with(b"03")

        returned = service.transform(b"04")
        MockTransformer.feed_speech_utterance.assert_called_once_with(b"04")
        MockTransformer.transform.assert_called_once_with(b"0")
        self.assertEqual(returned[0], b"1")
        context = returned[1]
        self.assertIsInstance(context, dict)
        self.assertIsInstance(context["client_name"], str)  # Allow name change
        self.assertEqual(context["source"], "audio")
        self.assertIn("skills", context["destination"])
        self.assertTrue(context["handled"])

        service.shutdown()
        MockTransformer.shutdown.assert_called_once()

    @patch("ovos_plugin_manager.audio_transformers.find_audio_transformer_plugins")
    def test_audio_transformer_ascending_priority_order(self, find_transformers):
        """Per OVOS-TRANSFORM-1 §4 the chain runs in ascending priority order:
        lower `priority` number = earlier in the chain."""
        import ovos_dinkum_listener.transformers

        ovos_dinkum_listener.transformers.find_audio_transformer_plugins = (
            find_transformers
        )
        from ovos_dinkum_listener.transformers import AudioTransformersService

        def _make(name, prio):
            return type(
                f"Mock_{name}",
                (AudioTransformer,),
                {
                    "feed_audio_chunk": Mock(),
                    "feed_hotword_chunk": Mock(),
                    "feed_speech_chunk": Mock(),
                    "feed_speech_utterance": Mock(return_value=b"0"),
                    "transform": Mock(return_value=(b"1", {})),
                    "shutdown": Mock(),
                    "__init__": lambda self, n=name, p=prio: AudioTransformer.__init__(
                        self, n, priority=p
                    ),
                },
            )

        # declare out of priority order on purpose
        find_transformers.return_value = {
            "late": _make("late", 90),
            "early": _make("early", 5),
            "mid": _make("mid", 50),
        }
        config = {
            "listener": {
                "audio_transformers": {
                    "late": {"active": True},
                    "early": {"active": True},
                    "mid": {"active": True},
                }
            }
        }
        service = AudioTransformersService(self.bus, config)

        ordered = [p.priority for p in service.plugins]
        self.assertEqual(ordered, sorted(ordered))  # ascending
        self.assertEqual(ordered, [5, 50, 90])
        names = [p.name for p in service.plugins]
        self.assertEqual(names, ["early", "mid", "late"])  # low prio runs first


if __name__ == "__main__":
    unittest.main()
