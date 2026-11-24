import unittest

from draft_edit import validate_draft_text


class DraftEditTests(unittest.TestCase):
    def test_trims_surrounding_whitespace(self):
        self.assertEqual(validate_draft_text("  नमुना समाचार  "), "नमुना समाचार")

    def test_rejects_empty_text(self):
        with self.assertRaises(ValueError):
            validate_draft_text("   ")

    def test_rejects_non_string(self):
        with self.assertRaises(ValueError):
            validate_draft_text(None)

    def test_rejects_text_over_limit(self):
        with self.assertRaises(ValueError):
            validate_draft_text("क" * 10001)

    def test_accepts_maximum_length(self):
        self.assertEqual(len(validate_draft_text("क" * 10000)), 10000)


if __name__ == "__main__":
    unittest.main()
