import unittest

from bulk_drafts import validate_bulk_dismiss_ids


class BulkDraftValidationTests(unittest.TestCase):
    def test_deduplicates_and_trims_ids(self):
        self.assertEqual(validate_bulk_dismiss_ids([" a ", "a", "b"]), ["a", "b"])

    def test_rejects_empty_values(self):
        with self.assertRaises(ValueError):
            validate_bulk_dismiss_ids(["  "])

    def test_rejects_overlong_id(self):
        with self.assertRaises(ValueError):
            validate_bulk_dismiss_ids(["x" * 501])

    def test_limits_to_fifty(self):
        with self.assertRaises(ValueError):
            validate_bulk_dismiss_ids([f"id-{i}" for i in range(51)])

    def test_accepts_fifty(self):
        self.assertEqual(len(validate_bulk_dismiss_ids([f"id-{i}" for i in range(50)])), 50)


if __name__ == "__main__":
    unittest.main()
