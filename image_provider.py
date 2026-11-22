"""Free-first image provider router for NayaDrishti AI Studio.

Primary: Cloudflare Workers AI (when configured and within its free allowance).
Fallback: AI Horde community workers for text-to-image requests.

Privacy guard: reference-image requests are sent only to Cloudflare. AI Horde is a
public volunteer network; anonymous requests may be shared publicly. Never forward
private uploads or reference photos to it automatically.
"""
import base64
import io
import os
import re
import time
import threading
from urllib.parse import urlparse

import requests
from PIL import Image, ImageOps, UnidentifiedImageError

CLOUDFLARE_API_BASE = "https://api.cloudflare.com/client/v4/accounts"
DEFAULT_MODEL = "@cf/black-forest-labs/flux-2-klein-9b"
HORDE_API_BASE = "https://aihorde.net/api/v2"
HORDE_ANONYMOUS_KEY = "0000000000"
MAX_REFERENCE_IMAGES = 4
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
REFERENCE_MAX_EDGE = 511
REQUEST_TIMEOUT = (20, 180)
_provider_state = threading.local()


def get_last_provider():
    """Return the provider used by the current worker thread."""
    return getattr(_provider_state, "name", None)


class ImageProviderError(RuntimeError):
    """Safe, user-displayable provider error."""


def _safe_model_name():
    model = (os.getenv("IMAGE_MODEL") or DEFAULT_MODEL).strip()
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


def _validate_image_bytes(image_bytes):
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
    return bytes(image_bytes)


def _decode_provider_response(response):
    content_type = (response.headers.get("Content-Type") or "").lower()
    if content_type.startswith("image/"):
        return _validate_image_bytes(response.content)
    try:
        payload = response.json()
    except ValueError as exc:
        raise ImageProviderError("The image provider returned an unreadable response.") from exc
    if payload.get("success") is False:
        errors = payload.get("errors") or []
        message = "; ".join(str(item.get("message", item)) for item in errors[:2])
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
    return _validate_image_bytes(image_bytes)


def _cloudflare_generate(prompt, references, width, height, guidance):
    account_id = os.getenv("CLOUDFLARE_ACCOUNT_ID", "").strip()
    api_token = os.getenv("CLOUDFLARE_API_TOKEN", "").strip()
    if not account_id or not api_token:
        raise ImageProviderError("Cloudflare is not configured.")
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
        fields[f"input_image_{index}"] = (f"reference-{index + 1}.jpg", prepared, "image/jpeg")
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
        raise ImageProviderError("Cloudflare image generation timed out.") from exc
    except requests.RequestException as exc:
        raise ImageProviderError("Could not connect to Cloudflare Workers AI.") from exc
    if not response.ok:
        detail = re.sub(r"\s+", " ", response.text or "")[:220]
        if response.status_code == 429:
            raise ImageProviderError("Cloudflare free allowance or rate limit reached. " + detail)
        if response.status_code in (401, 403):
            raise ImageProviderError("Cloudflare rejected the API credentials or permissions.")
        raise ImageProviderError(f"Cloudflare request failed (HTTP {response.status_code}). {detail}")
    return _decode_provider_response(response)


def _horde_headers():
    api_key = os.getenv("AI_HORDE_API_KEY", "").strip() or HORDE_ANONYMOUS_KEY
    return {
        "apikey": api_key,
        "Client-Agent": "NayaDrishtiAI:1.0:ImageStudio",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def _horde_image_bytes(image_value):
    if not isinstance(image_value, str) or not image_value:
        raise ImageProviderError("AI Horde completed the request but returned no image.")
    if image_value.startswith("data:image/"):
        try:
            image_value = image_value.split(",", 1)[1]
            return _validate_image_bytes(base64.b64decode(image_value, validate=True))
        except (ValueError, TypeError, IndexError) as exc:
            raise ImageProviderError("AI Horde returned invalid image data.") from exc
    if image_value.startswith("http://") or image_value.startswith("https://"):
        parsed = urlparse(image_value)
        host = (parsed.hostname or "").lower()
        # Accept only the public object-storage hosts used for Horde image delivery.
        allowed = host == "r2.dev" or host.endswith(".r2.dev") or host == "r2.cloudflarestorage.com" or host.endswith(".r2.cloudflarestorage.com")
        if parsed.scheme != "https" or not allowed:
            raise ImageProviderError("AI Horde returned an untrusted image download address.")
        try:
            response = requests.get(image_value, timeout=(10, 45), allow_redirects=False)
        except requests.RequestException as exc:
            raise ImageProviderError("Could not download the completed AI Horde image.") from exc
        if not response.ok:
            raise ImageProviderError("AI Horde image download failed.")
        return _validate_image_bytes(response.content)
    try:
        return _validate_image_bytes(base64.b64decode(image_value, validate=True))
    except (ValueError, TypeError) as exc:
        raise ImageProviderError("AI Horde returned an unsupported image format.") from exc


def _horde_generate(prompt, width, height):
    if os.getenv("AI_HORDE_ENABLED", "true").strip().lower() not in ("1", "true", "yes", "on"):
        raise ImageProviderError("AI Horde fallback is disabled.")
    # Horde requires dimensions divisible by 64. Keep aspect ratio approximately.
    width = max(256, min(1024, int(round(width / 64) * 64)))
    height = max(256, min(1024, int(round(height / 64) * 64)))
    payload = {
        "prompt": prompt,
        "params": {
            "width": width,
            "height": height,
            "steps": 25,
            "n": 1,
            "cfg_scale": 7.5,
            "sampler_name": "k_euler_a",
        },
        "nsfw": False,
        "censor_nsfw": True,
        "trusted_workers": False,
        "validated_backends": True,
        "slow_workers": True,
        "extra_slow_workers": False,
        "shared": False,
        "r2": True,
    }
    headers = _horde_headers()
    try:
        response = requests.post(
            f"{HORDE_API_BASE}/generate/async",
            headers=headers,
            json=payload,
            timeout=(10, 30),
        )
    except requests.RequestException as exc:
        raise ImageProviderError("Could not connect to the free AI Horde community service.") from exc
    if response.status_code not in (200, 202):
        detail = re.sub(r"\s+", " ", response.text or "")[:220]
        raise ImageProviderError(f"AI Horde could not accept the request (HTTP {response.status_code}). {detail}")
    try:
        request_id = response.json().get("id")
    except ValueError as exc:
        raise ImageProviderError("AI Horde returned an unreadable queue response.") from exc
    if not request_id:
        raise ImageProviderError("AI Horde did not return a generation request ID.")

    try:
        max_wait = max(30, min(240, int(os.getenv("AI_HORDE_MAX_WAIT_SECONDS", "180"))))
    except ValueError:
        max_wait = 180
    deadline = time.monotonic() + max_wait
    last_queue = None
    while time.monotonic() < deadline:
        try:
            check = requests.get(
                f"{HORDE_API_BASE}/generate/check/{request_id}",
                headers={"Client-Agent": headers["Client-Agent"]},
                timeout=(8, 15),
            )
            if not check.ok:
                raise ImageProviderError(f"AI Horde status check failed (HTTP {check.status_code}).")
            status = check.json()
        except ImageProviderError:
            raise
        except (requests.RequestException, ValueError) as exc:
            raise ImageProviderError("Could not check the AI Horde generation queue.") from exc

        if status.get("faulted"):
            raise ImageProviderError("AI Horde workers could not complete this image request.")
        if status.get("done"):
            break
        queue_position = status.get("queue_position")
        if queue_position is not None:
            last_queue = queue_position
        time.sleep(3)
    else:
        suffix = f" Queue position at timeout: {last_queue}." if last_queue is not None else ""
        raise ImageProviderError("AI Horde is busy. The free community queue did not finish in time." + suffix)

    try:
        result_response = requests.get(
            f"{HORDE_API_BASE}/generate/status/{request_id}",
            headers={"Client-Agent": headers["Client-Agent"]},
            timeout=(8, 30),
        )
        if not result_response.ok:
            raise ImageProviderError(f"AI Horde result retrieval failed (HTTP {result_response.status_code}).")
        result = result_response.json()
    except ImageProviderError:
        raise
    except (requests.RequestException, ValueError) as exc:
        raise ImageProviderError("Could not retrieve the completed AI Horde image.") from exc
    generations = result.get("generations") or []
    if not generations:
        raise ImageProviderError("AI Horde finished without returning an image.")
    return _horde_image_bytes(generations[0].get("img"))


def generate_image(prompt, reference_images=None, width=1024, height=768, guidance=None):
    """Generate an image using Cloudflare first, then AI Horde for safe text-only fallback.

    Uploaded/reference images are never forwarded to the public AI Horde worker network.
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

    cloudflare_configured = bool(
        os.getenv("CLOUDFLARE_ACCOUNT_ID", "").strip()
        and os.getenv("CLOUDFLARE_API_TOKEN", "").strip()
    )
    cloudflare_error = None
    if cloudflare_configured:
        try:
            image = _cloudflare_generate(prompt, references, width, height, guidance)
            _provider_state.name = "Cloudflare Workers AI"
            return image
        except ImageProviderError as exc:
            cloudflare_error = str(exc)
            # Never send a user's uploaded/reference photo to a public volunteer network.
            if references:
                raise ImageProviderError(
                    cloudflare_error + " AI Horde fallback was skipped to protect your reference images."
                ) from exc
    elif references:
        raise ImageProviderError(
            "Cloudflare is not configured. AI Horde fallback is text-to-image only, "
            "so reference-image editing is unavailable until Cloudflare is restored."
        )

    try:
        image = _horde_generate(prompt, width, height)
        _provider_state.name = "AI Horde community workers"
        return image
    except ImageProviderError as horde_error:
        if cloudflare_error:
            raise ImageProviderError(
                "All free image providers are currently unavailable. "
                f"Cloudflare: {cloudflare_error[:180]} AI Horde: {str(horde_error)[:180]}"
            ) from horde_error
        raise
