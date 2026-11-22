import base64
import io
import os
import unittest
from unittest.mock import patch

from PIL import Image

from image_provider import (
    ImageProviderError,
    _prepare_reference,
    generate_image,
)


def make_png(width=1024, height=768):
    output = io.BytesIO()
    Image.new("RGB", (width, height), (120, 160, 190)).save(output, format="PNG")
    return output.getvalue()


class FakeResponse:
    def __init__(self, status=200, image_bytes=None):
        self.status_code = status
        self.ok = status < 400
        self.headers = {"Content-Type": "application/json"}
        self.text = "provider test response"
        self._image_bytes = image_bytes or make_png(64, 64)

    def json(self):
        return {
            "success": True,
            "result": {"image": base64.b64encode(self._image_bytes).decode("ascii")},
        }


class ImageProviderTests(unittest.TestCase):
    def test_reference_is_oriented_and_resized_below_512(self):
        prepared = _prepare_reference(make_png(), 0)
        with Image.open(io.BytesIO(prepared)) as image:
            self.assertEqual(image.format, "JPEG")
            self.assertLess(image.width, 512)
            self.assertLess(image.height, 512)

    @patch.dict(os.environ, {
        "CLOUDFLARE_ACCOUNT_ID": "test-account",
        "CLOUDFLARE_API_TOKEN": "test-token",
        "IMAGE_MODEL": "@cf/black-forest-labs/flux-2-klein-9b",
    }, clear=False)
    @patch("image_provider.requests.post")
    def test_generation_uses_documented_multipart_reference_field(self, post):
        expected = make_png(96, 64)
        post.return_value = FakeResponse(image_bytes=expected)
        result = generate_image(
            "A realistic mountain landscape",
            reference_images=[make_png()],
            width=1024,
            height=768,
        )
        self.assertEqual(result, expected)
        kwargs = post.call_args.kwargs
        self.assertIn("files", kwargs)
        self.assertIn("input_image_0", kwargs["files"])
        self.assertEqual(kwargs["files"]["input_image_0"][2], "image/jpeg")
        self.assertEqual(kwargs["files"]["width"], (None, "1024"))
        self.assertEqual(kwargs["files"]["height"], (None, "768"))

    @patch.dict(os.environ, {}, clear=True)
    def test_missing_cloudflare_credentials_fail_closed(self):
        with self.assertRaises(ImageProviderError):
            generate_image("A simple test image")

    @patch.dict(os.environ, {
        "CLOUDFLARE_ACCOUNT_ID": "test-account",
        "CLOUDFLARE_API_TOKEN": "test-token",
    }, clear=False)
    @patch("image_provider.requests.post")
    def test_provider_429_does_not_use_paid_fallback(self, post):
        response = FakeResponse(status=429)
        response.text = "daily free allocation used up"
        post.return_value = response
        with self.assertRaisesRegex(ImageProviderError, "No paid fallback was used"):
            generate_image("A test image")

    @patch.dict(os.environ, {
        "CLOUDFLARE_ACCOUNT_ID": "test-account",
        "CLOUDFLARE_API_TOKEN": "test-token",
        "IMAGE_MODEL": "https://example.com/not-a-model",
    }, clear=False)
    def test_model_setting_rejects_arbitrary_url(self):
        with self.assertRaisesRegex(ImageProviderError, "IMAGE_MODEL is invalid"):
            generate_image("A test image")


if __name__ == "__main__":
    unittest.main()
