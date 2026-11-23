import unittest

from ai_writer import prepare_news_post


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
            + "\\nमिति: 2026-11-23"
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


if __name__ == "__main__":
    unittest.main()
