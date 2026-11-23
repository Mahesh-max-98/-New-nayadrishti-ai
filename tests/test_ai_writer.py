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
            self.valid_nepali_post
            + "\nस्रोत: Onlinekhabar\nप्रकाशित समय: 10:30 AM"
            + "\nथप जानकारी https://example.com #समाचार"
        )
        # The extra promotional line is removed by the link/hashtag cleaner only
        # if it becomes empty, so keep the post to three substantive lines.
        result = prepare_news_post(generated, "Onlinekhabar")
        self.assertNotIn("स्रोत", result)
        self.assertNotIn("Onlinekhabar", result)
        self.assertNotIn("10:30", result)
        self.assertNotIn("https://", result)
        self.assertNotIn("#", result)
        self.assertTrue(result.endswith("📰"))
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


if __name__ == "__main__":
    unittest.main()
