"""Unit tests for ovos_dinkum_listener._util module."""

import unittest


class TestTemplateFilenameFormatter(unittest.TestCase):
    """Tests for _TemplateFilenameFormatter."""

    def setUp(self):
        from ovos_dinkum_listener._util import _TemplateFilenameFormatter

        self.formatter = _TemplateFilenameFormatter()

    def test_init_has_builtin_builders(self):
        """Formatter initialises with uuid4, now, utcnow builders."""
        self.assertIn("uuid4", self.formatter.builders)
        self.assertIn("now", self.formatter.builders)
        self.assertIn("utcnow", self.formatter.builders)
        self.assertTrue(callable(self.formatter.builders["uuid4"]))
        self.assertTrue(callable(self.formatter.builders["now"]))
        self.assertTrue(callable(self.formatter.builders["utcnow"]))

    def test_format_uuid4(self):
        """Template with {uuid4} produces a UUID string."""
        result = self.formatter.format("prefix_{uuid4}")
        self.assertTrue(result.startswith("prefix_"))
        uuid_part = result[len("prefix_") :]
        # UUID4 pattern: 8-4-4-4-12 hex chars
        self.assertRegex(
            uuid_part,
            r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
        )

    def test_format_now(self):
        """Template with {now} produces a non-empty string."""
        result = self.formatter.format("{now}")
        self.assertIsInstance(result, str)
        self.assertGreater(len(result), 0)

    def test_format_now_with_format_spec(self):
        """Template with {now:%Y-%m-%d} produces a date string."""
        result = self.formatter.format("{now:%Y-%m-%d}")
        self.assertRegex(result, r"^\d{4}-\d{2}-\d{2}$")

    def test_format_utcnow(self):
        """Template with {utcnow} produces a non-empty string."""
        result = self.formatter.format("{utcnow}")
        self.assertIsInstance(result, str)
        self.assertGreater(len(result), 0)

    def test_format_single_known_key(self):
        """Template with a single known key produces a non-empty result."""
        result = self.formatter.format("{uuid4}")
        self.assertIsInstance(result, str)
        self.assertGreater(len(result), 0)

    def test_format_missing_key_raises(self):
        """Template with unsupported key raises KeyError with helpful message."""
        with self.assertRaises(KeyError) as ctx:
            self.formatter.format("{doesnotexist}")
        self.assertIn("doesnotexist", str(ctx.exception))
        self.assertIn("unsupported keys", str(ctx.exception))

    def test_register_callable(self):
        """register() adds a callable builder that is invoked during format."""

        @self.formatter.register("mykey")
        def _build():
            return "myvalue"

        result = self.formatter.format("{mykey}")
        self.assertEqual(result, "myvalue")

    def test_register_overwrites_existing_key(self):
        """Registering the same key twice replaces the old builder."""

        @self.formatter.register("mykey")
        def _first():
            return "first"

        @self.formatter.register("mykey")
        def _second():
            return "second"

        result = self.formatter.format("{mykey}")
        self.assertEqual(result, "second")

    def test_register_non_callable_via_kwargs(self):
        """Non-callable values can be supplied via kwargs to format()."""
        result = self.formatter.format("{literal}", literal="hello")
        self.assertEqual(result, "hello")

    def test_build_fmtkw_callable_builder(self):
        """_build_fmtkw calls callable builders and stores result."""
        fmtkw = self.formatter._build_fmtkw("{now}")
        self.assertIn("now", fmtkw)

    def test_build_fmtkw_non_callable_builder(self):
        """_build_fmtkw stores non-callable values directly."""
        self.formatter.builders["static"] = "static_value"
        fmtkw = self.formatter._build_fmtkw("{static}")
        self.assertEqual(fmtkw["static"], "static_value")

    def test_format_combined_template(self):
        """Template combining multiple keys works correctly."""

        @self.formatter.register("name")
        def _name():
            return "test"

        result = self.formatter.format("{name}_{now:%Y}")
        self.assertTrue(result.startswith("test_"))
        year_part = result[len("test_") :]
        self.assertRegex(year_part, r"^\d{4}$")

    def test_missing_key_message_lists_supported_keys(self):
        """KeyError message includes the list of supported keys."""
        with self.assertRaises(KeyError) as ctx:
            self.formatter.format("{bogus}")
        msg = str(ctx.exception)
        self.assertIn("uuid4", msg)
        self.assertIn("now", msg)
        self.assertIn("utcnow", msg)


if __name__ == "__main__":
    unittest.main()
