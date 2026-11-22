"""Provider adapter for NayaDrishti's standalone AI Image Studio.

This module intentionally does not share the news-card prompt pipeline. It returns
unaltered model output bytes so general-purpose images are not cropped, branded,
or forced into an editorial-illustration style.
"""
import base64
import io
import os
import re
from urllib.parse import quote

import requests
from PIL import Image, ImageOps, UnidentifiedImageError

CLOUDFLARE_API_BASE = "https://api.cloudflare.com/client/v4/accounts"
DEFAULT_MODEL = "@cf/black-forest-labs/flux-2-klein-9b"
MAX_REFERENCE_IMAGES = 4
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
REFERENCE_MAX_EDGE = 511
REQUEST_TIMEOUT = (20, 180)


class ImageProviderError(RuntimeError):
    """Safe, user-displayable provider error."""


def _safe_model_name():
    model = (os.getenv("IMAGE_MODEL") or DEFAULT_MODEL).strip()
    # Model IDs are configuration, not arbitrary URLs or paths.
    if not re.fullmatch(r"@[a-zA-Z0-9._/-]{3,140}", model):
        raise ImageProviderError("IMAGE_MODEL is invalid. Check the Render environment variable.")
    return model


def _prepare_reference(image_bytes, index):
    if not isinstance(image_bytes, (bytes, bytearray)) or not image_bytes:
        raise ImageProviderError(f"Reference image {index + 1} is empty.")
    if len(image_bytes) > MAX_UPLOAD_BYTES:
        raise ImageProviderError(f"Reference image {index + 1} exceeds the 10 MB limit.")

    try:
        with Image.open(io.BytesIO(image_bytes)) as opened:
            original_format = opened.format
            if original_format not in ("JPEG", "PNG", "WEBP"):
                raise ImageProviderError("Only JPG, PNG and WebP reference images are supported.")
            if opened.width < 32 or opened.height < 32:
                raise ImageProviderError("Reference images must be at least 32 × 32 pixels.")
            if opened.width * opened.height > 40000000:
                raise ImageProviderError("Reference image dimensions are too large (maximum 40 megapixels).")
            source = ImageOps.exif_transpose(opened)
            source.thumbnail((REFERENCE_MAX_EDGE, REFERENCE_MAX_EDGE), Image.Resampling.LANCZOS)
            output = io.BytesIO()
            source.convert("RGB").save(output, format="JPEG", quality=90, optimize=True)
            return output.getvalue()
    except ImageProviderError:
        raise
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise ImageProviderError(f"Reference image {index + 1} is not a valid image.") from exc


def _decode_provider_response(response):
    content_type = (response.headers.get("Content-Type") or "").lower()
    if content_type.startswith("image/"):
        image_bytes = response.content
    else:
        try:
            payload = response.json()
        except ValueError as exc:
            raise ImageProviderError("The image provider returned an unreadable response.") from exc
        if payload.get("success") is False:
            errors = payload.get("errors") or []
            message = "; ".join(
                str(item.get("message", item)) for item in errors[:2]
            )
            raise ImageProviderError("Image provider rejected the request: " + (message or "unknown error"))
        result = payload.get("result") or {}
        encoded = result.get("image") if isinstance(result, dict) else None
        if not encoded and isinstance(payload.get("image"), str):
            encoded = payload["image"]
        if not encoded:
            raise ImageProviderError("The image provider response did not contain an image.")
        try:
            image_bytes = base64.b64decode(encoded, validate=True)
        except (ValueError, TypeError) as exc:
            raise ImageProviderError("The image provider returned invalid image data.") from exc

    if not image_bytes:
        raise ImageProviderError("The image provider returned an empty image.")
    try:
        with Image.open(io.BytesIO(image_bytes)) as image:
            image.verify()
        with Image.open(io.BytesIO(image_bytes)) as image:
            if image.format not in ("JPEG", "PNG", "WEBP"):
                raise ImageProviderError("The provider returned an unsupported image format.")
    except ImageProviderError:
        raise
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise ImageProviderError("The provider response was not a valid image.") from exc
    return image_bytes


def generate_image(prompt, reference_images=None, width=1024, height=768, guidance=None):
    """Generate a new image, optionally conditioned on up to four reference images.

    FLUX.2 Klein 9B accepts multipart/form-data. Reference image inputs must be
    smaller than 512×512 pixels; they are safely oriented and resized here.
    """
    prompt = re.sub(r"\s+", " ", str(prompt or "")).strip()
    if not prompt:
        raise ImageProviderError("Please enter an image prompt.")
    if len(prompt) > 8000:
        raise ImageProviderError("The image prompt is too long (maximum 8,000 characters).")

    try:
        width, height = int(width), int(height)
    except (TypeError, ValueError) as exc:
        raise ImageProviderError("Image dimensions must be whole numbers.") from exc
    if not (256 <= width <= 1920 and 256 <= height <= 1920):
        raise ImageProviderError("Image dimensions must be between 256 and 1920 pixels.")

    references = list(reference_images or [])
    if len(references) > MAX_REFERENCE_IMAGES:
        raise ImageProviderError("This model supports up to four reference images.")

    account_id = os.getenv("CLOUDFLARE_ACCOUNT_ID", "").strip()
    api_token = os.getenv("CLOUDFLARE_API_TOKEN", "").strip()
    if not account_id or not api_token:
        raise ImageProviderError(
            "Cloudflare image generation is not configured. Set CLOUDFLARE_ACCOUNT_ID "
            "and CLOUDFLARE_API_TOKEN in Render."
        )

    model = _safe_model_name()
    url = f"{CLOUDFLARE_API_BASE}/{account_id}/ai/run/{model}"
    fields = {
        "prompt": (None, prompt),
        "width": (None, str(width)),
        "height": (None, str(height)),
    }
    if guidance is not None:
        try:
            guidance_value = float(guidance)
        except (TypeError, ValueError) as exc:
            raise ImageProviderError("Guidance must be a valid number.") from exc
        if not 1.0 <= guidance_value <= 20.0:
            raise ImageProviderError("Guidance must be between 1 and 20.")
        fields["guidance"] = (None, str(guidance_value))

    for index, original in enumerate(references):
        prepared = _prepare_reference(original, index)
        fields[f"input_image_{index}"] = (
            f"reference-{index + 1}.jpg", prepared, "image/jpeg"
        )

    try:
        response = requests.post(
            url,
            headers={
                "Authorization": f"Bearer {api_token}",
                "Accept": "application/json, image/jpeg, image/png",
            },
            files=fields,
            timeout=REQUEST_TIMEOUT,
        )
    except requests.Timeout as exc:
        raise ImageProviderError("Image generation timed out. Please retry.") from exc
    except requests.RequestException as exc:
        raise ImageProviderError("Could not connect to the image provider. Please retry later.") from exc

    if not response.ok:
        detail = re.sub(r"\\s+", " ", response.text or "")[:320]
        if response.status_code in (429, 500, 502, 503, 504):
            # Best-effort free fallback for text-only generations. It cannot
            # preserve reference images, so edits never silently switch providers.
            if not references:
                try:
                    return _generate_pollinations_fallback(prompt, width, height)
                except ImageProviderError as fallback_error:
                    raise ImageProviderError(
                        "Cloudflare is unavailable or its free limit was reached. "
                        "The free Pollinations fallback was also unavailable. "
                        "No paid provider was used. Try again later. "
                        + str(fallback_error)[:180]
                    ) from fallback_error
            raise ImageProviderError(
                "Cloudflare is busy or its free limit was reached. Reference-image "
                "requests cannot use the text-only fallback. No paid provider was used. "
                "Try again later. " + detail
            )
        if response.status_code in (401, 403):
            raise ImageProviderError(
                "Cloudflare rejected the image API credentials or permissions. Check the token "
                "and Workers AI access in Cloudflare."
            )
        raise ImageProviderError(
            f"Image provider request failed (HTTP {response.status_code}). {detail}"
        )

    return _decode_provider_response(response)


def _generate_pollinations_fallback(prompt, width, height):
    """Best-effort Pollinations fallback for text-only image generation."""
    url = "https://gen.pollinations.ai/image/" + quote(prompt, safe="")
    try:
        response = requests.get(
            url,
            params={"model": "flux", "width": str(width), "height": str(height)},
            headers={"Accept": "image/*"},
            timeout=REQUEST_TIMEOUT,
        )
    except requests.Timeout as exc:
        raise ImageProviderError("Pollinations fallback timed out.") from exc
    except requests.RequestException as exc:
        raise ImageProviderError("Could not connect to the free Pollinations fallback.") from exc

    if not response.ok:
        raise ImageProviderError(
            f"Pollinations fallback returned HTTP {response.status_code}."
        )
    return _decode_provider_response(response)
