import unittest

from ai_writer import _bounded_article_text, _post_to_groq, prepare_news_post


class PrepareNewsPostTests(unittest.TestCase):
    def setUp(self):
        self.valid_nepali_post = (
            "सरकारले नयाँ नीति सार्वजनिक गरेको छ।\n"
            "यसबाट देशभरका नागरिकलाई सेवा लिन सहज हुने बताइएको छ।\n"
            "सम्बन्धित निकायले कार्यान्वयन प्रक्रिया अघि बढाउने जनाएको छ।"
        )

    def test_removes_source_credit_timestamp_url_and_hashtag(self):
        generated = (
            "सरकारले नयाँ नीति सार्वजनिक गरेको छ।\n"
            "यसबाट देशभरका नागरिकलाई सेवा लिन सहज हुने बताइएको छ।\n"
            "सम्बन्धित निकायले कार्यान्वयन प्रक्रिया अघि बढाउने जनाएको छ। "
            "https://example.com #समाचार\n"
            "स्रोत: Onlinekhabar\nप्रकाशित समय: 10:30 AM"
        )
        result = prepare_news_post(generated, "Onlinekhabar")
        self.assertNotIn("स्रोत", result)
        self.assertNotIn("Onlinekhabar", result)
        self.assertNotIn("10:30", result)
        self.assertNotIn("https://", result)
        self.assertNotIn("#", result)
        self.assertTrue(result.endswith("📰"))
        self.assertEqual(len(result.splitlines()), 3)

    def test_removes_publication_date_line(self):
        generated = (
            self.valid_nepali_post
            + "\nमिति: 2026-11-23"
        )
        result = prepare_news_post(generated)
        self.assertNotIn("2026-11-23", result)
        self.assertEqual(len(result.splitlines()), 3)

    def test_rejects_english_dominated_output(self):
        generated = (
            "Government announces a new policy.\n"
            "The policy will affect many citizens.\n"
            "Officials say implementation will begin soon."
        )
        with self.assertRaisesRegex(RuntimeError, "Nepali script"):
            prepare_news_post(generated)

    def test_rejects_too_few_useful_lines(self):
        generated = "सरकारले नयाँ नीति सार्वजनिक गरेको छ।\nनयाँ नीति लागू हुने भएको छ।"
        with self.assertRaisesRegex(RuntimeError, "3 to 6"):
            prepare_news_post(generated)

    def test_removes_headline_label_and_markdown_formatting(self):
        generated = (
            "**शीर्षक: सरकारले नयाँ शिक्षा नीति सार्वजनिक गर्‍यो**\n"
            "नीतिमा विद्यालय शिक्षासम्बन्धी नयाँ व्यवस्था समेटिएको छ।\n"
            "सम्बन्धित निकायले कार्यान्वयनको तयारी अघि बढाएको जनाएको छ।"
        )
        result = prepare_news_post(generated)
        self.assertTrue(result.startswith("सरकारले नयाँ शिक्षा नीति सार्वजनिक गर्‍यो"))
        self.assertNotIn("शीर्षक:", result)
        self.assertNotIn("**", result)

    def test_rejects_incomplete_headline(self):
        generated = (
            "सरकारले नयाँ नीति सार्वजनिक गर्‍यो तर\n"
            "यसबाट नागरिकलाई सेवा लिन सहज हुने बताइएको छ।\n"
            "सम्बन्धित निकायले कार्यान्वयन प्रक्रिया अघि बढाउने जनाएको छ।"
        )
        with self.assertRaisesRegex(RuntimeError, "headline"):
            prepare_news_post(generated)

    def test_rejects_repeated_headline_as_body(self):
        generated = (
            "सरकारले नयाँ नीति सार्वजनिक गरेको छ।\n"
            "सरकारले नयाँ नीति सार्वजनिक गरेको छ\n"
            "सम्बन्धित निकायले कार्यान्वयन प्रक्रिया अघि बढाउने जनाएको छ।"
        )
        with self.assertRaisesRegex(RuntimeError, "repeats its headline"):
            prepare_news_post(generated)

    def test_removes_publisher_mention_from_post(self):
        generated = (
            "सरकारले नयाँ नीति सार्वजनिक गरेको छ।\n"
            "यसबाट नागरिकलाई सेवा लिन सहज हुने बताइएको छ।\n"
            "Onlinekhabar ले थप विवरण सार्वजनिक गरेको छ।\n"
            "सम्बन्धित निकायले कार्यान्वयन गर्ने जनाएको छ।"
        )
        result = prepare_news_post(generated, "Onlinekhabar")
        self.assertNotIn("Onlinekhabar", result)
        self.assertEqual(len(result.splitlines()), 3)



class GroqRequestResilienceTests(unittest.TestCase):
    def test_article_context_is_bounded(self):
        from unittest.mock import patch
        with patch.dict("os.environ", {"GROQ_MAX_ARTICLE_CHARS": "1200"}):
            result = _bounded_article_text("क" * 5000)
        self.assertEqual(len(result), 1200)

    def test_temporary_rate_limit_is_retried(self):
        from unittest.mock import Mock, patch
        limited = Mock(ok=False, status_code=429, headers={"Retry-After": "0"}, text="rate limited")
        success = Mock(ok=True, status_code=200, headers={}, text="")
        with patch("ai_writer.requests.post", side_effect=[limited, success]) as post, \\
             patch("ai_writer.time.sleep"):
            response = _post_to_groq(headers={}, payload={}, timeout=1)
        self.assertTrue(response.ok)
        self.assertEqual(post.call_count, 2)

    def test_permanent_client_error_is_not_retried(self):
        from unittest.mock import Mock, patch
        too_large = Mock(ok=False, status_code=413, headers={}, text="payload too large")
        with patch("ai_writer.requests.post", return_value=too_large) as post:
            response = _post_to_groq(headers={}, payload={}, timeout=1)
        self.assertEqual(response.status_code, 413)
        self.assertEqual(post.call_count, 1)



class NewsDiscoveryRankingTests(unittest.TestCase):
    def test_named_publishers_receive_priority_a(self):
        from news_monitor import source_priority, source_priority_label
        self.assertEqual(source_priority("Google News - Kantipur/eKantipur"), 100)
        self.assertEqual(source_priority("Nagarik News"), 100)
        self.assertTrue(source_priority_label("BBC Nepali").startswith("A"))

    def test_community_and_unknown_sources_are_not_ranked_as_top_tier(self):
        from news_monitor import source_priority
        self.assertLess(source_priority("Troll Cricket Nepal"), 100)
        self.assertEqual(source_priority("Unknown Blog"), 40)

    def test_topic_classifier_can_return_multiple_topics(self):
        from news_monitor import classify_story
        topics = classify_story("क्रिकेट प्रतियोगिता र नयाँ बजेट")
        self.assertIn("खेलकुद", topics)
        self.assertIn("अर्थतन्त्र", topics)

    def test_story_priority_prefers_higher_priority_source(self):
        from news_monitor import _story_priority
        item = {
            "title": "सरकारले नयाँ बजेट सार्वजनिक गर्‍यो",
            "summary": "अर्थतन्त्र र बजेटसम्बन्धी समाचार",
            "published_parsed": None,
        }
        community = _story_priority(item, "Troll Cricket Nepal")
        established = _story_priority(item, "Kantipur")
        self.assertGreater(established[0], community[0])



class CrossSourceCoverageTests(unittest.TestCase):
    def assess(self, title, rows):
        from contextlib import nullcontext
        from unittest.mock import patch
        import database

        class FakeResult:
            def fetchall(self):
                return rows

        class FakeConnection:
            def execute(self, *args, **kwargs):
                return FakeResult()

        with patch.object(database, "connection", return_value=nullcontext(FakeConnection())):
            return database.assess_cross_source_coverage(title, "Kantipur")

    def test_similar_independent_headline_is_flagged_as_matching_coverage(self):
        result = self.assess(
            "सरकारले नयाँ बजेट सार्वजनिक गर्‍यो",
            [{"title": "नयाँ बजेट सरकारले सार्वजनिक गर्‍यो", "source": "Onlinekhabar"}],
        )
        self.assertEqual(result["status"], "matching_coverage")
        self.assertEqual(result["count"], 2)

    def test_numeric_difference_is_flagged_for_manual_comparison(self):
        result = self.assess(
            "सरकारले २० करोड बजेट सार्वजनिक गर्‍यो",
            [{"title": "सरकारले ३० करोड बजेट सार्वजनिक गर्‍यो", "source": "Onlinekhabar"}],
        )
        self.assertEqual(result["status"], "needs_comparison")

    def test_single_source_is_not_marked_verified(self):
        result = self.assess("नयाँ विद्यालय भवन उद्घाटन", [])
        self.assertEqual(result["status"], "single_source")
        self.assertIn("not proof", result["note"])


class BreakingNewsDetectionTests(unittest.TestCase):
    def test_breaking_signal_requires_recent_urgent_story_from_priority_source(self):
        from news_monitor import detect_breaking_news
        current = 1_800_000_000
        flagged, reason = detect_breaking_news(
            "नेपालमा ठूलो भूकम्प गएको छ", "उद्धार कार्य जारी छ",
            "Kantipur", published_timestamp=current - 900, now=current,
        )
        self.assertTrue(flagged)
        self.assertIn("verify", reason)

    def test_breaking_signal_rejects_old_or_low_priority_story(self):
        from news_monitor import detect_breaking_news
        current = 1_800_000_000
        old, _ = detect_breaking_news(
            "नेपालमा बाढी आएको छ", "", "Kantipur",
            published_timestamp=current - 3 * 3600, now=current,
        )
        low_priority, _ = detect_breaking_news(
            "नेपालमा बाढी आएको छ", "", "Troll Cricket Nepal",
            published_timestamp=current - 300, now=current,
        )
        self.assertFalse(old)
        self.assertFalse(low_priority)

    def test_topic_classifier_returns_other_for_unmatched_story(self):
        from news_monitor import classify_story
        self.assertEqual(classify_story("स्थानीय कार्यक्रम सम्पन्न भयो"), ["अन्य"])


if __name__ == "__main__":
    unittest.main()
