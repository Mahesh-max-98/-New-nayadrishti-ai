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
    def test_short_source_headline_is_skipped_instead_of_saved_as_one_line(self):
        from unittest.mock import patch
        import news_monitor

        result = {"short_source_skipped": 0}
        callback_items = []
        with patch.object(news_monitor, "save_draft") as save_draft, \
             patch.object(news_monitor, "set_draft_status") as set_status, \
             patch.object(news_monitor, "remember") as remember:
            saved = news_monitor._save_short_source_headline_for_review(
                "item-1", "नेपालमा नयाँ समाचार शीर्षक", "Onlinekhabar",
                "https://example.com/story", published_timestamp=1800000000,
                on_new_draft=callback_items.append, result=result,
            )

        self.assertFalse(saved)
        self.assertEqual(result["short_source_skipped"], 1)
        self.assertEqual(callback_items, [])
        save_draft.assert_not_called()
        set_status.assert_not_called()
        remember.assert_not_called()


    def test_only_fresh_configured_nonbreaking_https_items_are_eligible(self):
        from news_monitor import _non_ai_headline_is_eligible
        now = 1_800_000_000
        self.assertTrue(_non_ai_headline_is_eligible(
            "नेपालमा नयाँ शिक्षा नीति सार्वजनिक", "Onlinekhabar",
            "https://www.onlinekhabar.com/story", now - 3600, now=now,
        ))
        self.assertTrue(_non_ai_headline_is_eligible(
            "नेपालमा नयाँ शिक्षा नीति सार्वजनिक", "Troll Cricket Nepal",
            "https://example.com/story", now - 3600, now=now,
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
            "https://www.onlinekhabar.com/story", now - 25 * 3600, now=now,
        ))
        self.assertFalse(_non_ai_headline_is_eligible(
            "भूकम्पपछि उद्धार कार्य जारी", "Onlinekhabar",
            "https://www.onlinekhabar.com/story", now - 1800, now=now,
        ))
        self.assertFalse(_non_ai_headline_is_eligible(
            "Major policy announced by government", "Onlinekhabar",
            "https://www.onlinekhabar.com/story", now - 1800, now=now,
        ))
        self.assertFalse(_non_ai_headline_is_eligible(
            "बाढी प्रभावित क्षेत्रमा राहत वितरण", "Onlinekhabar",
            "https://www.onlinekhabar.com/story", now - 5 * 3600, now=now,
        ))


if __name__ == "__main__":
    unittest.main()
