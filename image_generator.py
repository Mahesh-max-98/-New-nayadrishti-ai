import base64
import io
import os

import requests
from PIL import Image, ImageDraw, ImageFont

MODEL = "@cf/black-forest-labs/flux-1-schnell"
API_BASE = "https://api.cloudflare.com/client/v4/accounts"


def _brand_image(image_bytes):
    """Add the NayaDrishti wordmark and tagline without covering the news scene."""
    image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    draw = ImageDraw.Draw(image)
    width, height = image.size
    band_height = max(58, int(height * 0.14))
    y0 = height - band_height
    draw.rectangle((0, y0, width, height), fill=(15, 23, 42))
    try:
        title_font = ImageFont.truetype("DejaVuSans-Bold.ttf", max(18, int(height * 0.045)))
        tagline_font = ImageFont.truetype("DejaVuSans.ttf", max(10, int(height * 0.022)))
    except OSError:
        title_font = ImageFont.load_default()
        tagline_font = ImageFont.load_default()
    margin = max(14, int(width * 0.025))
    draw.text((margin, y0 + 7), "NayaDrishti", fill=(255, 255, 255), font=title_font)
    draw.text(
        (margin, y0 + band_height * 0.62),
        "A New Perspective on Every Story.",
        fill=(203, 213, 225),
        font=tagline_font,
    )
    output = io.BytesIO()
    image.save(output, format="JPEG", quality=88, optimize=True)
    return output.getvalue()


def generate_news_image(title, description="", summary=""):
    """Generate an editorial image grounded in the exact post for this story."""
    account_id = os.getenv("CLOUDFLARE_ACCOUNT_ID", "").strip()
    api_token = os.getenv("CLOUDFLARE_API_TOKEN", "").strip()
    if not account_id or not api_token:
        raise RuntimeError(
            "CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_API_TOKEN are not configured."
        )

    # Prefer the AI-edited post: it is the same factual content shown to readers.
    # RSS descriptions can contain unrelated site boilerplate or recommendations.
    story_context = summary.strip() or description.strip()
    context = (
        f"Original news headline: {title.strip()}\n"
        f"News facts and summary: {story_context}"
    )[:1400]

    prompt = (
        "Create ONE accurate editorial illustration for a professional digital news post. "
        "The illustration MUST match the specific news story described below. "
        "First identify the main subject, action, location or setting, and topic from the "
        "headline and summary. Show that subject and action clearly. Do not substitute a "
        "generic news scene or introduce a different sport, country, team, event, object, "
        "or topic. If the story is about a named sports team or match, depict the correct "
        "sport and a relevant match/stadium scene; do not invent player likenesses or logos. "
        "If it is about education, show a relevant school, classroom, exam, or students scene. "
        "If it is about government or public affairs, use a relevant neutral institutional "
        "or symbolic scene and do not invent real people. For other topics, depict the "
        "specific subject described. If details are insufficient, make a restrained "
        "topic-specific symbolic illustration rather than guessing. "
        "Use a polished, bold, modern editorial illustration style, clear focal subject, "
        "balanced composition, and realistic lighting. No text, letters, numbers, logos, "
        "watermarks, fake screenshots, or fabricated identifiable people. This is an "
        "illustration, not documentary evidence.\n"
        f"STORY TO ILLUSTRATE:\n{context}"
    )[:3000]

    url = f"{API_BASE}/{account_id}/ai/run/{MODEL}"
    response = requests.post(
        url,
        headers={
            "Authorization": f"Bearer {api_token}",
            "Content-Type": "application/json",
        },
        json={"prompt": prompt, "steps": 4},
        timeout=120,
    )
    if not response.ok:
        raise RuntimeError(
            f"Cloudflare image API error {response.status_code}: "
            f"{response.text[:350].replace(chr(10), ' ')}"
        )

    data = response.json()
    result = data.get("result", {})
    encoded = result.get("image") if isinstance(result, dict) else result
    if not encoded and isinstance(data.get("image"), str):
        encoded = data["image"]
    if not encoded:
        raise RuntimeError("Cloudflare returned no image data.")

    try:
        raw = base64.b64decode(encoded)
        return _brand_image(raw)
    except Exception as exc:
        raise RuntimeError("Could not decode or brand the generated image.") from exc
