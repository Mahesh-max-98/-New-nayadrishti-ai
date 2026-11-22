import base64
import io
import os
import re

import requests
from PIL import Image, ImageDraw, ImageFont

MODEL = "@cf/black-forest-labs/flux-1-schnell"
API_BASE = "https://api.cloudflare.com/client/v4/accounts"


def _compose_news_card(image, title, summary, ai_generated=False):
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

    # Only AI-created scenes receive this label. Original publisher photos
    # must not be mislabeled as AI-generated.
    if ai_generated:
        label = "AI-GENERATED ILLUSTRATION"
        label_font = ImageFont.load_default()
        for path in (
            os.getenv("NEWS_FONT_PATH", ""),
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
        ):
            if path and os.path.isfile(path):
                try:
                    label_font = ImageFont.truetype(path, 25)
                    break
                except OSError:
                    pass
        label_box = draw.textbbox((0, 0), label, font=label_font)
        label_width = label_box[2] - label_box[0]
        label_height = label_box[3] - label_box[1]
        badge_padding_x, badge_padding_y = 18, 13
        badge_right, badge_top = 1200 - 36, 28
        badge_left = max(24, badge_right - label_width - badge_padding_x * 2)
        badge_bottom = badge_top + label_height + badge_padding_y * 2
        draw.rounded_rectangle(
            (badge_left, badge_top, badge_right, badge_bottom),
            radius=12, fill=(8, 15, 32, 225)
        )
        draw.text(
            (badge_left + badge_padding_x, badge_top + badge_padding_y - 1),
            label, font=label_font, fill=(255, 255, 255, 255)
        )

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
    return _compose_news_card(photo, headline, summary, ai_generated=False)


def _visual_direction(title, description, summary, visual_brief=""):
    """Rank story topics using headline-first signals to avoid broad keyword mistakes."""
    headline = re.sub(r"\s+", " ", title or "").lower()
    details = re.sub(r"\s+", " ", " ".join((description or "", summary or "", visual_brief or ""))).lower()
    # Normalize typographic hyphens commonly used in player names.
    headline = re.sub(r"[\u2010-\u2015\u2212]", "-", headline)
    details = re.sub(r"[\u2010-\u2015\u2212]", "-", details)

    # Headline matches matter most. Supporting text can clarify a story, but
    # generic words such as "result", "team", or "news" must not decide the image.
    categories = [
        ("cricket", [
            "cricket", "ipl", "t20", "odi", "test match", "wicket", "batsman",
            "batter", "bowler", " रन", "क्रिकेट", "विकेट", "ब्याट्सम्यान",
            "बलर", "रन बनाए", "क्रिकेटर", "imam-ul-haq", "imam ul haq",
            "pakistan opener", "pakistani opener", "county cricket",
            "headingley cricket", "yorkshire cricket",
        ], "A recognisable cricket scene: cricket pitch, stumps, bat and ball, with players only if the report concerns a match or players. For administration, selection, finance or scheduling stories, show the relevant cricket context without inventing match action."),
        ("football", [
            "football", "soccer", "premier league", "champions league",
            "goalkeeper", "striker", "फुटबल", "गोलकिपर", "फुटबल खेल",
        ], "A recognisable association-football scene with a football pitch and ball. Show match action only when the report is about a match; for transfers or club business, use a relevant club/transfer editorial concept, not a fabricated match."),
        ("education", [
            "school", "student", "education", "exam", "neb", "university",
            "college", "विद्यालय", "विद्यार्थी", "शिक्षा", "परीक्षा",
            "एसईई", "कक्षा", "विश्वविद्यालय", "शैक्षिक",
        ], "A specific education setting such as an examination hall, classroom, school building or university, choosing only the setting supported by the report. Do not add readable exam results or fabricated certificates."),
        ("weather", [
            "weather", "rainfall", "rain", "flood", "storm", "cyclone",
            "earthquake", "landslide", "heatwave", "मौसम", "वर्षा", "बाढी",
            "पहिरो", "भूकम्प", "हावाहुरी", "चक्रवात", "तापलहर",
        ], "A restrained illustration of the exact weather or natural event reported, such as rain clouds, a river flood, a landslide slope or earthquake-safe buildings. Do not invent damage, casualties or a location."),
        ("technology", [
            "technology", "software", "artificial intelligence", "cyber attack",
            "cybersecurity", "smartphone", "mobile app", "computer", "प्रविधि",
            "कृत्रिम बुद्धिमत्ता", "साइबर", "मोबाइल", "एप", "कम्प्युटर",
        ], "Show the specific technology, device or digital concept stated in the story. Avoid generic glowing circuit boards if the report is about a particular app, phone, software launch or cyber incident."),
        ("health", [
            "health", "hospital", "doctor", "disease", "medical", "vaccine",
            "medicine", "स्वास्थ्य", "अस्पताल", "चिकित्सक", "रोग", "खोप",
            "औषधि",
        ], "A calm, respectful healthcare visual matching the specific reported subject. Do not depict identifiable patients, graphic symptoms, specific treatments or outcomes not stated."),
        ("politics", [
            "government", "minister", "parliament", "election", "politic",
            "prime minister", "cabinet", "सरकार", "मन्त्री", "संसद",
            "निर्वाचन", "राजनीति", "प्रधानमन्त्री", "मन्त्रिपरिषद्",
        ], "A neutral civic visual matching the actual topic: public institution for government policy, ballot box for elections, or parliamentary chamber for parliamentary decisions. Never fabricate a real politician, party symbol, flag, speech or event."),
        ("business", [
            "business", "economy", "market", "bank", "stock market", "shares",
            "inflation", "budget", "trade", "company", "व्यवसाय", "अर्थतन्त्र",
            "बजार", "बैंक", "सेयर", "मुद्रास्फीति", "बजेट", "व्यापार", "कम्पनी",
        ], "A specific business or economic visual relevant to the report, such as a market, bank, shop, factory or neutral financial concept. Avoid random office meetings and fake charts or numbers."),
        ("transport", [
            "flight", "airline", "airport", "road", "traffic", "bus", "train",
            "railway", "vehicle", "यात्रुवाहक", "विमान", "उडान", "विमानस्थल",
            "सडक", "यातायात", "बस", "रेल", "सवारी",
        ], "Show the specific transport mode or infrastructure named in the story. Do not invent crashes, damage, airline branding, registration numbers or emergency scenes."),
        ("entertainment", [
            "movie", "film", "actor", "actress", "singer", "concert", "music",
            "album", "चलचित्र", "फिल्म", "अभिनेता", "अभिनेत्री", "गायक",
            "गायिका", "संगीत", "कन्सर्ट",
        ], "A relevant entertainment editorial visual such as a cinema, stage or music performance. Do not invent or imitate a real celebrity's face, film poster, logo or specific event."),
        ("crime", [
            "police", "arrest", "crime", "court", "fraud", "theft", "investigation",
            "प्रहरी", "पक्राउ", "अपराध", "अदालत", "ठगी", "चोरी", "अनुसन्धान",
        ], "A non-graphic, neutral public-safety or justice visual, such as a courthouse or generic police tape-free investigation scene. Do not depict identifiable suspects, victims, weapons, injuries or presume guilt."),
    ]

    scores = {}
    for name, terms, _direction in categories:
        headline_hits = sum(1 for term in terms if term in headline)
        detail_hits = sum(1 for term in terms if term in details)
        scores[name] = headline_hits * 4 + detail_hits

    best_name = max(scores, key=scores.get)
    best_score = scores[best_name]
    if best_score <= 0:
        return (
            "GENERAL NEWS: identify the main subject, action and named place/object "
            "from the headline and description, then depict only that subject. If the "
            "story is about a person, use a non-identifiable editorial silhouette or "
            "symbol rather than inventing a face. If facts are too thin, use a restrained "
            "symbolic visual based on the headline's central noun."
        )

    # Require a clear lead over the runner-up. Ambiguous stories get a neutral,
    # headline-led visual rather than an incorrectly specific scene.
    ranked = sorted(scores.values(), reverse=True)
    if len(ranked) > 1 and best_score == ranked[1]:
        return (
            "AMBIGUOUS TOPIC: do not choose a sport or industry from a weak keyword. "
            "Depict the main concrete subject explicitly stated in the headline, "
            "using a neutral editorial illustration and no invented details."
        )

    direction = next(item[2] for item in categories if item[0] == best_name)
    return direction


def _build_image_prompt(title, description, summary, visual_brief=""):
    """Build a compact, fact-prioritised prompt for a clearly labelled news reconstruction."""
    headline = re.sub(r"\s+", " ", (title or "").strip())[:260]
    facts = re.sub(r"\s+", " ", (description or "").strip())[:520]
    post = re.sub(r"\s+", " ", (summary or "").strip())[:260]
    brief = re.sub(r"\s+", " ", (visual_brief or "").strip())[:320]
    direction = _visual_direction(headline, facts, post, brief)

    # Put story facts before photographic styling. The 2,048-character API limit
    # must never cut off the actual event and leave only generic camera language.
    prompt = (
        "NEWS EDITORIAL RECONSTRUCTION — NOT A REAL NEWS PHOTOGRAPH. "
        "Create one exceptionally natural, believable, photorealistic editorial "
        "illustration. ACCURACY FIRST: depict only the central subject and event "
        "explicitly stated in the HEADLINE and supported by SOURCE FACTS. Do not "
        "turn a preview into a result, rumour into fact, proposal into a decision, "
        "transfer into match action, announcement into a staged event, or allegation "
        "into proven guilt. Do not infer what happened outside the supplied facts. "
        "If the exact scene, people, place, timing or action is not confirmed, do not "
        "recreate it; use a restrained symbolic or environmental visual of the "
        "confirmed subject instead. Never invent or imitate a real person's face. "
        "No fabricated names, identifiable people, uniforms, club/company logos, "
        "flags, signs, readable text, numbers, scores, documents, quotes, locations, "
        "crowd size, damage, casualties, injuries, weapons, emergency scenes or "
        "dramatic actions. No graphic, sensational or partisan framing. "
        "SCENE: one clear main subject, correct object type and scale, physically "
        "plausible arrangement, accurate sport/equipment/infrastructure when relevant, "
        "natural background details that do not imply unreported facts. Match the "
        "visual category to the event, not merely a broad keyword. For cricket, "
        "show cricket-only equipment such as bat, ball, stumps and a cricket pitch; "
        "never show hockey sticks, pucks, ice rinks or hockey goals. For hockey, do "
        "not show cricket bats, wickets or cricket pitches. No unrelated props, no "
        "food or tea, no mixed sports, no collage, no split-screen. "
        "PHOTO REALISM: professional documentary-editorial photography aesthetic; "
        "natural available light, realistic exposure and white balance, physically "
        "consistent shadows, true-to-life materials and textures, subtle fine grain, "
        "credible lens perspective, restrained depth of field, realistic atmospheric "
        "detail, balanced contrast, natural colour, sharp but not oversharpened. "
        "Avoid CGI, 3D render, illustration brush strokes, plastic/waxy surfaces, "
        "over-smooth skin, distorted anatomy, extra fingers, duplicated objects, "
        "impossible geometry, oversaturation, cinematic fantasy lighting and fake "
        "HDR. Keep the scene calm, clear, contextually accurate and non-deceptive. "
        "COMPOSITION: landscape 4:3, strong readable subject, uncluttered frame, "
        "professional editorial crop, sufficient clean lower space for the news-card "
        "overlay. No text, letters, watermark or fake camera/date stamp. "
        "HEADLINE (primary facts): " + headline + " "
        "SOURCE FACTS (only evidence to depict): " + facts + " "
        "VISUAL BRIEF: " + brief + " "
        "CATEGORY DIRECTION: " + direction[:260] + " "
        "CAPTION CONTEXT: " + post
    )
    return prompt[:2048]

def generate_news_image(title, description="", summary="", visual_brief=""):
    """Generate a story-specific editorial illustration when no licensed photo is available."""
    account_id = os.getenv("CLOUDFLARE_ACCOUNT_ID", "").strip()
    api_token = os.getenv("CLOUDFLARE_API_TOKEN", "").strip()
    if not account_id or not api_token:
        raise RuntimeError(
            "CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_API_TOKEN are not configured."
        )

    prompt = _build_image_prompt(title, description, summary, visual_brief)
    url = f"{API_BASE}/{account_id}/ai/run/{MODEL}"
    last_error = None
    raw = None
    for attempt in range(2):
        try:
            response = requests.post(
                url,
                headers={
                    "Authorization": f"Bearer {api_token}",
                    "Content-Type": "application/json",
                    "Accept": "application/json, image/jpeg, image/png",
                },
                json={"prompt": prompt, "steps": 4},
                timeout=(20, 150),
            )
            if not response.ok:
                detail = response.text[:500].replace(chr(10), " ")
                raise RuntimeError(
                    f"Cloudflare image API error {response.status_code}: {detail}"
                )

            content_type = (response.headers.get("Content-Type") or "").lower()
            if content_type.startswith("image/"):
                raw = response.content
            else:
                try:
                    data = response.json()
                except ValueError as exc:
                    raise RuntimeError(
                        "Cloudflare returned neither image bytes nor valid JSON "
                        f"(Content-Type: {content_type or 'unknown'})."
                    ) from exc
                if data.get("success") is False:
                    errors = data.get("errors") or []
                    message = "; ".join(
                        str(item.get("message", item)) for item in errors[:3]
                    )
                    raise RuntimeError("Cloudflare rejected image generation: " + (message or "unknown API error"))
                result = data.get("result") or {}
                encoded = result.get("image") if isinstance(result, dict) else None
                if not encoded and isinstance(data.get("image"), str):
                    encoded = data["image"]
                if not encoded:
                    raise RuntimeError(
                        "Cloudflare response had no base64 image. "
                        "Check Workers AI token permissions, account ID and usage limits."
                    )
                raw = base64.b64decode(encoded, validate=True)

            if not raw:
                raise RuntimeError("Cloudflare returned an empty image.")
            with Image.open(io.BytesIO(raw)) as check:
                check.verify()
            break
        except Exception as exc:
            last_error = exc
            raw = None
            if attempt == 0:
                time.sleep(2)
    if raw is None:
        raise RuntimeError(f"Image generation failed after retry: {last_error}") from last_error

    try:
        image = Image.open(io.BytesIO(raw)).convert("RGB")
        if image.width < 256 or image.height < 256:
            raise ValueError(f"Generated image is unexpectedly small: {image.size}")
        lines = [line.strip() for line in (summary or "").splitlines() if line.strip()]
        headline = lines[2] if len(lines) >= 3 else title
        return _compose_news_card(image, headline, summary, ai_generated=True)
    except Exception as exc:
        raise RuntimeError(f"Generated image could not be decoded or branded: {type(exc).__name__}: {exc}") from exc
