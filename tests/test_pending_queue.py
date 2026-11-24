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


if __name__ == "__main__":
    unittest.main()
