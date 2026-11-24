import unittest

from story_quality import (
    has_unsupported_numbers,
    normalize_story_title,
    similar_story_titles,
)


class StoryQualityTests(unittest.TestCase):
    def test_normalizes_nepali_digits_and_punctuation(self):
        self.assertEqual(
            normalize_story_title("आज १२३ जनाको — परीक्षा!"),
            normalize_story_title("आज 123 जनाको परीक्षा"),
        )

    def test_detects_same_story_with_small_headline_edits(self):
        self.assertTrue(similar_story_titles(
            "गोरखामा हावाहुरीले घर र विद्यालयका छाना उडायो",
            "गोरखामा हावाहुरीका कारण घर तथा विद्यालयका छाना उडे",
            similarity_threshold=0.70,
        ))

    def test_does_not_merge_headlines_with_conflicting_numbers(self):
        self.assertFalse(similar_story_titles(
            "सरकारले १० अर्ब रुपैयाँ बजेट विनियोजन गर्‍यो",
            "सरकारले २० अर्ब रुपैयाँ बजेट विनियोजन गर्‍यो",
        ))

    def test_rejects_numbers_not_in_source(self):
        self.assertTrue(has_unsupported_numbers(
            "आज २५ जनालाई पुरस्कार दिइयो",
            "आज २० जनालाई पुरस्कार दिइयो",
        ))

    def test_accepts_same_number_in_devanagari_or_latin(self):
        self.assertFalse(has_unsupported_numbers(
            "आज 25 जनालाई पुरस्कार दिइयो",
            "आज २५ जनालाई पुरस्कार दिइयो",
        ))

    def test_accepts_summary_without_new_numbers(self):
        self.assertFalse(has_unsupported_numbers(
            "विद्यालयमा नयाँ कार्यक्रम सुरु भयो",
            "विद्यालयमा आज नयाँ कार्यक्रम सुरु भएको छ।",
        ))


if __name__ == "__main__":
    unittest.main()
