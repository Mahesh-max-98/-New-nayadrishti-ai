import unittest
from unittest.mock import patch

from news_monitor import (
    _build_non_ai_summary_post,
    _save_short_source_headline_for_review,
    _save_undated_review_summary,
)


class NewsPostLengthTests(unittest.TestCase):
    def test_non_ai_fallback_uses_multiple_source_sentences(self):
        title = "सरकारले नयाँ शिक्षा नीति सार्वजनिक गरेको छ।"
        source = (
            "शिक्षा मन्त्रालयले विद्यालय तहको पाठ्यक्रम परिवर्तन गर्ने निर्णय गरेको जनाएको छ। "
            "नयाँ व्यवस्था आगामी शैक्षिक सत्रदेखि देशभरका सामुदायिक विद्यालयमा लागू गरिनेछ।"
        )
        post = _build_non_ai_summary_post(title, source)
        self.assertIsNotNone(post)
        self.assertGreaterEqual(len(post.splitlines()), 4)
        self.assertLessEqual(len(post.splitlines()), 7)
        self.assertTrue(post.endswith("📰"))
        self.assertNotIn("http", post)
        self.assertNotIn("स्रोत:", post)

    def test_non_ai_fallback_rejects_insufficient_source_text(self):
        title = "सरकारले नयाँ शिक्षा नीति सार्वजनिक गरेको छ।"
        source = "शिक्षा मन्त्रालयले नयाँ नीति सार्वजनिक गरेको जनाएको छ।"
        self.assertIsNone(_build_non_ai_summary_post(title, source))

    def test_short_source_never_creates_one_line_draft(self):
        result = {"short_source_skipped": 0}
        with patch("news_monitor.save_draft") as save_draft:
            saved = _save_short_source_headline_for_review(
                "item-1", "सरकारले नयाँ नीति सार्वजनिक गरेको छ।",
                "Test publisher", "https://example.com/story", result=result,
            )
        self.assertFalse(saved)
        self.assertEqual(result["short_source_skipped"], 1)
        save_draft.assert_not_called()

    def test_undated_story_is_saved_only_as_multiline_manual_review(self):
        title = "सरकारले नयाँ शिक्षा नीति सार्वजनिक गरेको छ।"
        source = (
            "शिक्षा मन्त्रालयले विद्यालय तहको पाठ्यक्रम परिवर्तन गर्ने निर्णय गरेको जनाएको छ। "
            "नयाँ व्यवस्था आगामी शैक्षिक सत्रदेखि देशभरका सामुदायिक विद्यालयमा लागू गरिनेछ।"
        )
        result = {
            "drafts_saved": 0, "new_items": 0, "undated_review_drafts": 0,
            "undated_skipped": 0,
        }
        callback_items = []
        with patch("news_monitor.assess_cross_source_coverage", return_value={"status":"single_source"}), \
             patch("news_monitor.save_draft") as save_draft, \
             patch("news_monitor.set_draft_status") as set_status, \
             patch("news_monitor.remember") as remember:
            saved = _save_undated_review_summary(
                "item-2", title, "Test publisher", "https://example.com/story",
                source, on_new_draft=callback_items.append, result=result,
            )
        self.assertTrue(saved)
        self.assertEqual(result["drafts_saved"], 1)
        self.assertEqual(result["undated_review_drafts"], 1)
        self.assertEqual(len(callback_items), 1)
        saved_post = save_draft.call_args.args[4]
        self.assertGreaterEqual(len(saved_post.splitlines()), 4)
        self.assertLessEqual(len(saved_post.splitlines()), 7)
        set_status.assert_called_once_with("item-2", "draft")
        remember.assert_called_once()

    def test_undated_story_without_details_is_not_saved(self):
        result = {
            "drafts_saved": 0, "new_items": 0, "undated_review_drafts": 0,
            "undated_skipped": 0,
        }
        with patch("news_monitor.save_draft") as save_draft:
            saved = _save_undated_review_summary(
                "item-3", "सरकारले नयाँ शिक्षा नीति सार्वजनिक गरेको छ।",
                "Test publisher", "https://example.com/story",
                "एउटा छोटो वाक्य मात्र छ।", result=result,
            )
        self.assertFalse(saved)
        self.assertEqual(result["undated_skipped"], 1)
        save_draft.assert_not_called()


if __name__ == "__main__":
    unittest.main()
