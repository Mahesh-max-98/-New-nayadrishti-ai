import unittest
from unittest.mock import MagicMock, patch

from database import restore_pending_draft_revision


class RestoreDraftRevisionTests(unittest.TestCase):
    @patch("database.connection")
    def test_restores_and_saves_current_text_to_history(self, connection):
        db = MagicMock()
        connection.return_value.__enter__.return_value = db
        db.execute.side_effect = [
            {"summary": "current text"},
            {"previous_summary": "older text"},
            None,
            None,
        ]
        self.assertTrue(restore_pending_draft_revision("story-1", 7))
        calls = db.execute.call_args_list
        self.assertIn("INSERT INTO draft_revision_history", calls[2].args[0])
        self.assertEqual(calls[2].args[1], ("story-1", "current text"))
        self.assertIn("UPDATE drafts SET summary", calls[3].args[0])
        self.assertEqual(calls[3].args[1], ("older text", "story-1"))

    @patch("database.connection")
    def test_rejects_missing_or_reviewed_draft(self, connection):
        db = MagicMock()
        connection.return_value.__enter__.return_value = db
        db.execute.return_value = None
        self.assertFalse(restore_pending_draft_revision("story-1", 7))

    @patch("database.connection")
    def test_rejects_invalid_revision_id(self, connection):
        self.assertFalse(restore_pending_draft_revision("story-1", "bad"))
        connection.assert_not_called()


if __name__ == "__main__":
    unittest.main()
