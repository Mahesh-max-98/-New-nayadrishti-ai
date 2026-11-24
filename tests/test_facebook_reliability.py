import os
import unittest
from unittest.mock import Mock, patch

import requests

import facebook_publisher
import news_monitor


class FacebookPublisherSafetyTests(unittest.TestCase):
    def test_configuration_reports_presence_without_values(self):
        with patch.dict(os.environ, {
            "ENABLE_FACEBOOK_PUBLISHING": "true",
            "FB_PAGE_ID": "page-secret-id",
            "FB_PAGE_ACCESS_TOKEN": "token-secret-value",
        }, clear=False):
            status = facebook_publisher.facebook_configuration_status()
        self.assertTrue(status["credentials_present"])
        self.assertNotIn("page-secret-id", str(status))
        self.assertNotIn("token-secret-value", str(status))

    def test_graph_error_does_not_expose_raw_response_body(self):
        response = Mock()
        response.ok = False
        response.status_code = 400
        response.json.return_value = {"error": {"code": 190, "message": "invalid token"}}
        with patch.dict(os.environ, {
            "ENABLE_FACEBOOK_PUBLISHING": "true",
            "FB_PAGE_ID": "123",
            "FB_PAGE_ACCESS_TOKEN": "private-token",
        }, clear=False), patch.object(facebook_publisher.requests, "post", return_value=response):
            with self.assertRaises(facebook_publisher.FacebookPublishError) as caught:
                facebook_publisher.publish_text("test")
        self.assertEqual(caught.exception.error_code, "190")
        self.assertNotIn("private-token", str(caught.exception))
        self.assertNotIn("invalid token", str(caught.exception))

    def test_network_failure_is_not_reported_as_definitive_rejection(self):
        with patch.dict(os.environ, {
            "ENABLE_FACEBOOK_PUBLISHING": "true",
            "FB_PAGE_ID": "123",
            "FB_PAGE_ACCESS_TOKEN": "private-token",
        }, clear=False), patch.object(
            facebook_publisher.requests, "post",
            side_effect=requests.Timeout("request timed out"),
        ):
            with self.assertRaises(requests.Timeout):
                facebook_publisher.publish_text("test")


class FacebookDuplicateProtectionTests(unittest.TestCase):
    @patch.object(news_monitor, "begin_facebook_publication")
    def test_existing_attempt_is_never_retried(self, reserve):
        reserve.return_value = {
            "reserved": False, "status": "unknown", "attempts": 1,
        }
        with patch.object(news_monitor, "publish_text") as publish:
            outcome = news_monitor._publish_facebook_tracked("story-1", "post")
        publish.assert_not_called()
        self.assertEqual(outcome["status"], "unknown")

    @patch.object(news_monitor, "finish_facebook_publication")
    @patch.object(news_monitor, "begin_facebook_publication")
    @patch.object(news_monitor, "publish_text")
    def test_success_is_recorded(self, publish, reserve, finish):
        reserve.return_value = {"reserved": True, "status": "publishing"}
        publish.return_value = {"published": True, "post_id": "page_123"}
        with patch.object(news_monitor, "set_draft_status"), patch.object(news_monitor, "remember"):
            outcome = news_monitor._publish_facebook_tracked("story-2", "post")
        self.assertEqual(outcome["status"], "published")
        finish.assert_called_once_with("story-2", "published", post_id="page_123")

    @patch.object(news_monitor, "finish_facebook_publication")
    @patch.object(news_monitor, "begin_facebook_publication")
    @patch.object(news_monitor, "publish_text", side_effect=requests.Timeout("timed out"))
    def test_timeout_is_unknown_and_not_retryable(self, publish, reserve, finish):
        reserve.return_value = {"reserved": True, "status": "publishing"}
        outcome = news_monitor._publish_facebook_tracked("story-3", "post")
        self.assertEqual(outcome["status"], "unknown")
        self.assertIn("Check the Page", finish.call_args.kwargs["error_message"])


if __name__ == "__main__":
    unittest.main()
