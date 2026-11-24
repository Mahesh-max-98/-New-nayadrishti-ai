import unittest

from review_gate import review_checklist_complete


class EditorialReviewGateTests(unittest.TestCase):
    def test_accepts_all_three_review_confirmations(self):
        form = {
            "read_original": "yes",
            "checked_key_facts": "yes",
            "summary_matches_source": "yes",
        }
        self.assertTrue(review_checklist_complete(form))

    def test_rejects_missing_confirmation(self):
        form = {
            "read_original": "yes",
            "checked_key_facts": "yes",
        }
        self.assertFalse(review_checklist_complete(form))

    def test_rejects_unchecked_or_forged_values(self):
        form = {
            "read_original": "yes",
            "checked_key_facts": "on",
            "summary_matches_source": "no",
        }
        self.assertFalse(review_checklist_complete(form))

    def test_rejects_empty_form(self):
        self.assertFalse(review_checklist_complete({}))


if __name__ == "__main__":
    unittest.main()
