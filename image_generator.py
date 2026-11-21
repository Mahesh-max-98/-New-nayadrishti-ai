import base64
import io
import os
import re

import requests
from PIL import Image, ImageDraw, ImageFont

MODEL = "@cf/black-forest-labs/flux-1-schnell"
API_BASE = "https://api.cloudflare.com/client/v4/accounts"


def _compose_news_card(image, title, summary):
    """Create a consistent NayaDrishti card with a readable headline."""
    image = image.convert("RGB")
    width, height = image.size
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
        "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Bold.ttf",
        "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Bold.ttf",
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

    small_font = ImageFont.load_default()
    for path in (
        os.getenv("NEWS_FONT_PATH", ""),
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    ):
        if path and os.path.isfile(path):
            try:
                small_font = ImageFont.truetype(path, 24)
                break
            except OSError:
                pass

    margin = 62
    if title:
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
            draw.text((margin, 900 - panel_height + 30 + index * 58), line,
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
    """Brand an authorized publisher photo without changing its subject."""
    photo = Image.open(io.BytesIO(image_bytes))
    if photo.width < 400 or photo.height < 250:
        raise ValueError("Publisher image is too small for a news card.")
    lines = [line.strip() for line in (summary or "").splitlines() if line.strip()]
    headline = lines[2] if len(lines) >= 3 else title
    return _compose_news_card(photo, headline, summary)


def _visual_direction(title, description, summary):
    """Choose a topic-specific visual direction from the supplied story facts."""
    text = " ".join((title or "", description or "", summary or "")).lower()

    cricket_terms = ("cricket", "ipl", "t20", "odi", "test match", "wicket",
                     "batsman", "bowler", " रन", "विश्वकप", "क्रिकेट", "विकेट",
                     "ब्याट्सम्यान", "बलर")
    football_terms = ("football", "soccer", "premier league", "champions league",
                      "goalkeeper", "striker", "फुटबल", "गोल")
    education_terms = ("school", "student", "education", "exam", "neb", "university",
                       "college", "विद्यालय", "विद्यार्थी", "शिक्षा", "परीक्षा", "नतिजा")
    weather_terms = ("weather", "rainfall", "flood", "storm", "earthquake",
                     "मौसम", "वर्षा", "बाढी", "भूकम्प")
    technology_terms = ("technology", "software", "artificial intelligence", "ai ",
                        "cyber", "mobile", "प्रविधि", "कृत्रिम बुद्धिमत्ता")
    health_terms = ("health", "hospital", "doctor", "disease", "medical",
                    "स्वास्थ्य", "अस्पताल", "चिकित्सक", "रोग")
    politics_terms = ("government", "minister", "parliament", "election", "politic",
                      "सरकार", "मन्त्री", "संसद", "निर्वाचन", "राजनीति")

    if any(term in text for term in cricket_terms):
        return (
            "CRICKET STORY: show a believable cricket match scene with a cricket pitch, "
            "stumps, bat and ball, and cricketers in action. If the supplied facts name "
            "teams, reflect only generic kit colors when clearly stated; do not invent "
            "team logos, player identities, scores, or match events. Absolutely no food, "
            "restaurants, tea, cafés, unrelated sports, or generic city scenes."
        )
    if any(term in text for term in football_terms):
        return (
            "FOOTBALL STORY: show a believable association-football scene with a football "
            "pitch, goal, ball and players in action. Use only team details explicitly "
            "provided; no invented logos, player identities, scores, or match events. "
            "Absolutely no cricket, food, restaurants, tea, cafés, or unrelated sports."
        )
    if any(term in text for term in education_terms):
        return (
            "EDUCATION STORY: show a relevant school, classroom, university, examination "
            "or learning setting, choosing only the setting supported by the story. "
            "Do not show sports, food, restaurants, tea, cafés, or unrelated subjects."
        )
    if any(term in text for term in weather_terms):
        return (
            "WEATHER OR DISASTER STORY: show a restrained, geographically neutral "
            "illustration of the specific weather or natural event named in the facts. "
            "Do not add damage, casualties, dramatic danger, or details not stated."
        )
    if any(term in text for term in technology_terms):
        return (
            "TECHNOLOGY STORY: show the specific device, software, digital concept, or "
            "technology context named in the story. Avoid unrelated gadgets or generic "
            "business meetings."
        )
    if any(term in text for term in health_terms):
        return (
            "HEALTH STORY: show a calm, respectful healthcare or medical setting relevant "
            "to the reported topic. Do not depict identifiable patients, diagnoses, "
            "treatments, or medical outcomes not stated in the facts."
        )
    if any(term in text for term in politics_terms):
        return (
            "GOVERNMENT OR POLITICS STORY: show a neutral civic setting such as a "
            "parliament building, public institution, or official meeting room only when "
            "that setting fits the facts. Do not fabricate identifiable politicians, "
            "speeches, flags, documents, or events."
        )
    return (
        "GENERAL NEWS STORY: create a restrained editorial illustration of the exact "
        "subject, place, object, or event stated in the headline and description. "
        "If the details are insufficient, use a simple neutral symbolic illustration "
        "rather than guessing a specific scene."
    )


def _build_image_prompt(title, description, summary):
    # Limit source text and remove line breaks that could confuse prompt structure.
    facts = re.sub(r"\s+", " ", (description or "").strip())[:900]
    post = re.sub(r"\s+", " ", (summary or "").strip())[:500]
    headline = re.sub(r"\s+", " ", (title or "").strip())[:250]
    direction = _visual_direction(headline, facts, post)
    return (
        "Create ONE polished editorial illustration for a real news story. "
        "The image is a visual companion to the supplied facts, not proof or a documentary photograph. "
        "FIRST follow this story-specific visual direction: " + direction + " "
        "ACCURACY RULES: depict only the subject and details supported by the supplied headline "
        "and description. Keep the main subject unmistakable and central. Do not combine "
        "unrelated topics. Never replace the actual story with a random stock-photo scene. "
        "Do not invent named people, recognizable public figures, team crests, brands, "
        "logos, uniforms, scores, dates, locations, signs, or events. If an exact scene "
        "cannot be safely inferred, use a simple topic-specific symbolic illustration. "
        "STYLE: high-quality modern editorial illustration, realistic proportions, clear "
        "focal point, professional news visual, balanced composition, landscape 4:3 framing. "
        "No text, letters, numbers, captions, watermarks, fake screenshots, or logos. "
        "Do not add decorative food, tea, cafés, or unrelated objects unless they are "
        "actually central to the story. "
        "STORY HEADLINE: " + headline + " "
        "STORY DESCRIPTION: " + facts + " "
        "POST CONTEXT: " + post
    )[:3500]


def generate_news_image(title, description="", summary=""):
    """Generate a story-specific editorial illustration when no licensed photo is available."""
    account_id = os.getenv("CLOUDFLARE_ACCOUNT_ID", "").strip()
    api_token = os.getenv("CLOUDFLARE_API_TOKEN", "").strip()
    if not account_id or not api_token:
        raise RuntimeError(
            "CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_API_TOKEN are not configured."
        )

    prompt = _build_image_prompt(title, description, summary)
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
