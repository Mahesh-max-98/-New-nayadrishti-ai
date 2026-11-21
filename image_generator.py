import base64
import io
import os
import textwrap

import requests
from PIL import Image, ImageDraw, ImageFont

MODEL = "@cf/black-forest-labs/flux-1-schnell"
API_BASE = "https://api.cloudflare.com/client/v4/accounts"


def _brand_image(image_bytes):
    """Brand an AI illustration without changing its editorial subject."""
    image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    return _compose_news_card(image, "", "")


def _compose_news_card(image, title, summary):
    """Create a consistent photo card with readable headline typography."""
    image = image.convert("RGB")
    width, height = image.size
    # Crop to a consistent landscape card.
    target_ratio = 1200 / 900
    ratio = width / height
    if ratio > target_ratio:
        new_width = int(height * target_ratio)
        left = (width - new_width) // 2
        image = image.crop((left, 0, left + new_width, height))
    else:
        new_height = int(width / target_ratio)
        top = max(0, (height - new_height) // 2)
        image = image.crop((0, top, width, top + new_height))
    image = image.resize((1200, 900), Image.Resampling.LANCZOS)

    # A dark lower panel keeps the headline legible without hiding the photo.
    panel_height = 255 if title else 130
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw_overlay = ImageDraw.Draw(overlay)
    for y in range(900 - panel_height, 900):
        alpha = int(20 + 205 * ((y - (900 - panel_height)) / panel_height))
        draw_overlay.line((0, y, 1200, y), fill=(8, 15, 32, alpha))
    image = Image.alpha_composite(image.convert("RGBA"), overlay)
    draw = ImageDraw.Draw(image)

    font_candidates = [
        os.getenv("NEWS_FONT_PATH", ""),
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
    ]
    font = None
    for path in font_candidates:
        if path and os.path.isfile(path):
            try:
                font = ImageFont.truetype(path, 43 if title else 42)
                break
            except OSError:
                pass
    if font is None:
        font = ImageFont.load_default()

    small_candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    ]
    small_font = ImageFont.load_default()
    for path in small_candidates:
        if os.path.isfile(path):
            try:
                small_font = ImageFont.truetype(path, 24)
                break
            except OSError:
                pass

    margin = 62
    if title:
        # Prefer the English headline line from the bilingual caption for a
        # dependable Latin-font overlay; the complete bilingual caption remains
        # in the Facebook post text.
        headline = title.strip()
        words = headline.split()
        lines, line = [], ""
        for word in words:
            candidate = (line + " " + word).strip()
            if draw.textbbox((0, 0), candidate, font=font)[2] <= 1060:
                line = candidate
            else:
                if line:
                    lines.append(line)
                line = word
        if line:
            lines.append(line)
        for index, line in enumerate(lines[:2]):
            draw.text((margin, 900 - panel_height + 34 + index * 58), line,
                      font=font, fill=(255, 255, 255, 255),
                      stroke_width=1, stroke_fill=(0, 0, 0, 150))

    brand_y = 900 - 62
    draw.text((margin, brand_y), "NayaDrishti", font=small_font,
              fill=(255, 255, 255, 255))
    draw.text((margin + 190, brand_y + 2), "A New Perspective on Every Story.",
              font=small_font, fill=(210, 220, 235, 255))
    output = io.BytesIO()
    image.convert("RGB").save(output, format="JPEG", quality=90, optimize=True)
    return output.getvalue()


def create_publisher_news_card(image_bytes, title, summary=""):
    """Place an authorized publisher photo into NayaDrishti's branded layout."""
    photo = Image.open(io.BytesIO(image_bytes))
    if photo.width < 400 or photo.height < 250:
        raise ValueError("Publisher image is too small for a news card.")
    # Use the English headline from line 3 when available.
    lines = [line.strip() for line in (summary or "").splitlines() if line.strip()]
    headline = lines[2] if len(lines) >= 3 else title
    return _compose_news_card(photo, headline, summary)


def generate_news_image(title, description="", summary=""):
    """Generate an editorial illustration only when no approved photo is available."""
    account_id = os.getenv("CLOUDFLARE_ACCOUNT_ID", "").strip()
    api_token = os.getenv("CLOUDFLARE_API_TOKEN", "").strip()
    if not account_id or not api_token:
        raise RuntimeError(
            "CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_API_TOKEN are not configured."
        )

    story_context = summary.strip() or description.strip()
    context = (
        f"Original news headline: {title.strip()}\\n"
        f"News facts and summary: {story_context}"
    )[:1400]
    prompt = (
        "Create ONE accurate editorial illustration for a professional digital news post. "
        "The illustration MUST match the specific news story described below. "
        "Do not substitute a generic news scene or introduce a different sport, country, "
        "team, event, object, or topic. Use a restrained topic-specific symbolic illustration "
        "if details are insufficient. Use a polished modern editorial illustration style, "
        "clear focal subject and balanced composition. No text, letters, logos, watermarks, "
        "fake screenshots, or fabricated identifiable people. This is an illustration, "
        "not documentary evidence.\\n"
        f"STORY TO ILLUSTRATE:\\n{context}"
    )[:3000]

    url = f"{API_BASE}/{account_id}/ai/run/{MODEL}"
    response = requests.post(
        url,
        headers={"Authorization": f"Bearer {api_token}", "Content-Type": "application/json"},
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
        image = Image.open(io.BytesIO(raw))
        lines = [line.strip() for line in (summary or "").splitlines() if line.strip()]
        headline = lines[2] if len(lines) >= 3 else title
        return _compose_news_card(image, headline, summary)
    except Exception as exc:
        raise RuntimeError("Could not decode or brand the generated image.") from exc
