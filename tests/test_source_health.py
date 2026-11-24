import unittest
from types import SimpleNamespace

from source_health import classify_feed_health


class SourceHealthClassificationTests(unittest.TestCase):
    def test_valid_feed_is_healthy_even_when_it_has_no_new_entries(self):
        parsed = SimpleNamespace(bozo=False, entries=[])
        self.assertEqual(classify_feed_health(parsed), "healthy")

    def test_malformed_feed_with_some_entries_is_warning(self):
        parsed = SimpleNamespace(bozo=True, entries=[{"title": "Example"}])
        self.assertEqual(classify_feed_health(parsed), "warning")

    def test_fetch_failure_is_error(self):
        self.assertEqual(classify_feed_health(error=TimeoutError("timeout")), "error")

    def test_missing_parse_result_is_error(self):
        self.assertEqual(classify_feed_health(), "error")


if __name__ == "__main__":
    unittest.main()
