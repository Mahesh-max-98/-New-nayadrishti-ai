import unittest

from database import pending_retry_delay_seconds


class PendingStoryRetryScheduleTests(unittest.TestCase):
    def test_retry_wait_grows_over_time(self):
        self.assertEqual(
            [pending_retry_delay_seconds(i) for i in range(1, 9)],
            [900, 1800, 3600, 7200, 14400, 28800, 43200, 86400],
        )

    def test_retry_wait_is_capped_at_one_day(self):
        self.assertEqual(pending_retry_delay_seconds(9), 86400)
        self.assertEqual(pending_retry_delay_seconds(100), 86400)

    def test_invalid_attempt_number_uses_first_retry(self):
        self.assertEqual(pending_retry_delay_seconds(0), 900)
        self.assertEqual(pending_retry_delay_seconds("not-a-number"), 900)


class NonAiHeadlineFallbackTests(unittest.TestCase):
    def test_post_is_transparent_and_links_to_original(self):
        from news_monitor import _build_non_ai_headline_post
        post = _build_non_ai_headline_post(
            "नेपालमा नयाँ समाचार शीर्षक",
            "https://example.com/story",
        )
        self.assertIn("AI सारांश उपलब्ध छैन", post)
        self.assertIn("नेपालमा नयाँ समाचार शीर्षक", post)
        self.assertIn("https://example.com/story", post)
        self.assertIn("मूल समाचारको शीर्षक", post)

    def test_only_fresh_high_priority_nonbreaking_https_items_are_eligible(self):
        from news_monitor import _non_ai_headline_is_eligible
        now = 1_800_000_000
        self.assertTrue(_non_ai_headline_is_eligible(
            "नेपालमा नयाँ शिक्षा नीति सार्वजनिक", "Onlinekhabar",
            "https://www.onlinekhabar.com/story", now - 3600, now=now,
        ))
        self.assertFalse(_non_ai_headline_is_eligible(
            "नेपालमा नयाँ शिक्षा नीति सार्वजनिक", "Unknown Feed",
            "https://example.com/story", now - 3600, now=now,
        ))
        self.assertFalse(_non_ai_headline_is_eligible(
            "नेपालमा नयाँ शिक्षा नीति सार्वजनिक", "Onlinekhabar",
            "http://www.onlinekhabar.com/story", now - 3600, now=now,
        ))
        self.assertFalse(_non_ai_headline_is_eligible(
            "नेपालमा नयाँ शिक्षा नीति सार्वजनिक", "Onlinekhabar",
            "https://www.onlinekhabar.com/story", now - 13 * 3600, now=now,
        ))
        self.assertFalse(_non_ai_headline_is_eligible(
            "भूकम्पपछि उद्धार कार्य जारी", "Onlinekhabar",
            "https://www.onlinekhabar.com/story", now - 1800, now=now,
        ))


if __name__ == "__main__":
    unittest.main()
