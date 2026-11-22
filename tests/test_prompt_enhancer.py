import json
import os
import unittest
from unittest.mock import patch

from prompt_enhancer import enhance_prompt, PromptEnhancementError


class FakeGroqResponse:
    ok = True
    status_code = 200

    def json(self):
        return {
            "choices": [{
                "message": {
                    "content": json.dumps({
                        "enhanced_prompt": "A realistic photograph with natural daylight.",
                        "detected_style": "photorealistic",
                        "notes": "Kept the original request.",
                    })
                }
            }]
        }


class PromptEnhancerTests(unittest.TestCase):
    @patch.dict(os.environ, {
        "GROQ_API_KEY": "test-key",
        "GROQ_MODEL": "test-text-model",
    }, clear=False)
    @patch("prompt_enhancer.requests.post")
    def test_returns_original_and_enhanced_prompt_separately(self, post):
        post.return_value = FakeGroqResponse()
        result = enhance_prompt("A school in Kathmandu", "auto")
        self.assertEqual(result["original_prompt"], "A school in Kathmandu")
        self.assertIn("realistic photograph", result["enhanced_prompt"])
        self.assertEqual(result["detected_style"], "photorealistic")

    @patch.dict(os.environ, {}, clear=True)
    def test_missing_groq_key_returns_clear_error(self):
        with self.assertRaises(PromptEnhancementError):
            enhance_prompt("A mountain landscape")


if __name__ == "__main__":
    unittest.main()
