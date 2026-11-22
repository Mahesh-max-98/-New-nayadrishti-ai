import base64
import io
import os
import re
import time

import requests
from PIL import Image, ImageDraw, ImageFont

MODEL = "@cf/black-forest-labs/flux-1-schnell"
API_BASE = "https://api.cloudflare.com/client/v4/accounts"

# Avoid repeatedly calling a quota-exhausted free image endpoint.
_CLOUDFLARE_COOLDOWN_UNTIL = 0.0
_CLOUDFLARE_COOLDOWN_REASON = ""


def _compose_news_card(image, title, summary, ai_generated=False):
    """Render every image in a consistent, premium NayaDrishti editorial identity."""
    image = image.convert("RGB")
    width, height = image.size
    target_ratio = 4 / 3
    if width / height > target_ratio:
        new_width = int(height * target_ratio)
        left = (width - new_width) // 2
        image = image.crop((left, 0, left + new_width, height))
    else:
        new_height = int(width / target_ratio)
        top = max(0, (height - new_height) // 2)
        image = image.crop((0, top, width, top + new_height))
    image = image.resize((1200, 900), Image.Resampling.LANCZOS).convert("RGBA")

    # Brand palette: midnight navy, clean white, and restrained signal-gold.
    navy = (7, 18, 36, 238)
    white = (255, 255, 255, 255)
    muted = (207, 220, 236, 255)
    gold = (244, 184, 64, 255)
    cyan = (55, 190, 210, 255)

    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    # Soft bottom gradient keeps the story image visible while making headlines readable.
    for y in range(570, 900):
        alpha = int(25 + 220 * ((y - 570) / 330))
        od.line((0, y, 1200, y), fill=(5, 15, 31, alpha))
    # Slim masthead backdrop gives every post a recognisable brand signature.
    for y in range(0, 112):
        alpha = int(205 - 35 * (y / 112))
        od.line((0, y, 1200, y), fill=(5, 17, 35, alpha))
    image = Image.alpha_composite(image, overlay)
    draw = ImageDraw.Draw(image)

    def load_font(size, bold=False):
        candidates = [
            os.getenv("NEWS_FONT_PATH", ""),
            "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Bold.ttf" if bold else "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Regular.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
        ]
        for path in candidates:
            if path and os.path.isfile(path):
                try:
                    return ImageFont.truetype(path, size)
                except OSError:
                    continue
        return ImageFont.load_default()

    brand_font = load_font(42, True)
    tagline_font = load_font(19, False)
    headline_font = load_font(49, True)
    footer_font = load_font(22, True)
    badge_font = load_font(19, True)

    # Draw a simple original globe + eye + signal mark (not a copied third-party logo).
    cx, cy = 62, 55
    draw.ellipse((cx - 34, cy - 34, cx + 34, cy + 34), outline=cyan, width=4)
    draw.arc((cx - 20, cy - 34, cx + 20, cy + 34), 0, 360, fill=cyan, width=2)
    draw.arc((cx - 34, cy - 18, cx + 34, cy + 18), 0, 360, fill=cyan, width=2)
    draw.ellipse((cx - 15, cy - 7, cx + 15, cy + 7), outline=white, width=3)
    draw.ellipse((cx - 4, cy - 4, cx + 4, cy + 4), fill=gold)
    draw.arc((cx + 13, cy - 40, cx + 48, cy - 5), 205, 315, fill=gold, width=3)
    draw.arc((cx + 19, cy - 46, cx + 58, cy - 1), 205, 315, fill=gold, width=2)

    draw.text((112, 18), "NayaDrishti", font=brand_font, fill=white,
              stroke_width=1, stroke_fill=(0, 0, 0, 90))
    draw.text((115, 68), "A NEW PERSPECTIVE ON EVERY STORY", font=tagline_font, fill=muted)
    draw.rounded_rectangle((110, 101, 330, 106), radius=2, fill=gold)

    if ai_generated:
        label = "AI-GENERATED EDITORIAL ILLUSTRATION"
        bbox = draw.textbbox((0, 0), label, font=badge_font)
        label_width = bbox[2] - bbox[0]
        badge_left = max(560, 1164 - label_width - 30)
        draw.rounded_rectangle((badge_left, 28, 1164, 75), radius=12,
                               fill=(7, 18, 36, 225), outline=(244, 184, 64, 210), width=2)
        draw.text((badge_left + 15, 40), label, font=badge_font, fill=white)

    # Headline is limited to two measured lines to avoid clipping or tiny text.
    headline = re.sub(r"\s+", " ", (title or "").strip())
    max_width = 1080
    lines, line = [], ""
    for word in headline.split():
        candidate = (line + " " + word).strip()
        if draw.textbbox((0, 0), candidate, font=headline_font)[2] <= max_width:
            line = candidate
        else:
            if line:
                lines.append(line)
            line = word
    if line:
        lines.append(line)
    lines = lines[:2]
    headline_y = 690 if len(lines) <= 1 else 650
    draw.rounded_rectangle((62, headline_y - 17, 72, 900 - 104),
                           radius=4, fill=gold)
    for index, text_line in enumerate(lines):
        draw.text((96, headline_y + index * 62), text_line, font=headline_font,
                  fill=white, stroke_width=1, stroke_fill=(0, 0, 0, 150))

    # Footer lockup is consistent across publisher photos and AI illustrations.
    draw.line((96, 824, 1104, 824), fill=(207, 220, 236, 135), width=1)
    draw.text((96, 842), "नयाँ दृष्टि, नयाँ खबर।", font=footer_font, fill=gold)
    draw.text((1104, 847), "NAYADRISHTI", font=badge_font, fill=muted, anchor="ra")

    output = io.BytesIO()
    image.convert("RGB").save(output, format="JPEG", quality=93, optimize=True)
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

    # Detect the kind of event, not only the sport/category. This prevents
    # transfer, signing, return and appointment stories from becoming fake match scenes.
    if any(term in headline for term in ("cricket", "imam-ul-haq", "imam ul haq", "pakistan opener", "pakistani opener", "yorkshire", "county cricket")):
        if any(term in headline for term in ("return", "returns", "back to", "rejoins", "join", "joins", "sign", "signs", "deal", "2027")):
            return (
                "CRICKET PLAYER RETURN / SIGNING STORY: depict a quiet, authentic "
                "English county-cricket ground with a well-kept cricket pitch, pavilion, "
                "boundary rope and a single cricket bat with red ball near the pavilion. "
                "The visual communicates a player's return to county cricket, NOT a live "
                "match. No identifiable player, no invented action, no scoreboard, no "
                "team crest, no hockey equipment, no unrelated sport."
            )

    if any(term in headline for term in ("football", "soccer", "premier league", "champions league")) and any(
        term in headline for term in ("transfer", "signs", "joins", "loan", "deal", "2027")
    ):
        return (
            "FOOTBALL TRANSFER STORY: depict a professional football stadium exterior "
            "or empty pitch with one football in the foreground, suggesting a club move. "
            "Do not show a fabricated match, identifiable player, shirt number, club crest "
            "or transfer announcement text."
        )

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
    """Build a detailed, headline-faithful editorial photography prompt."""
    headline = re.sub(r"\s+", " ", (title or "").strip())[:260]
    facts = re.sub(r"\s+", " ", (description or "").strip())[:520]
    post = re.sub(r"\s+", " ", (summary or "").strip())[:260]
    brief = re.sub(r"\s+", " ", (visual_brief or "").strip())[:320]
    direction = _visual_direction(headline, facts, post, brief)

    # Image models perform better when the story subject and physical scene are
    # specified before style. Headline typography is always composed separately.
    prompt = (
        "Create ONE premium photorealistic editorial NEWS IMAGE for a professional "
        "Nepali digital newsroom. This is an illustrative reconstruction, NOT documentary "
        "evidence and NOT a real photograph of an event. Output image background only. "
        "ABSOLUTELY NO TEXT: no headline, letters, numbers, captions, signs, logos, "
        "watermarks, badges, fake writing or typography. NayaDrishti will add all text "
        "afterward. "
        "STORY MATCH IS THE HIGHEST PRIORITY. Read the headline and facts carefully. "
        "Show the actual central subject and the specific action or situation described; "
        "do not merely show a generic image for the category. Use one clear visual idea, "
        "one coherent location and physically plausible objects. "
        "SCENE DESIGN: clear main subject in the foreground, relevant setting in the "
        "middle distance, restrained contextual background. Make the subject immediately "
        "recognisable at phone-screen size. Use a natural decisive moment, believable "
        "human scale, realistic perspective and uncluttered composition. "
        "PHOTOCRAFT: high-end international newspaper photo-desk aesthetic, full-frame "
        "camera look, natural 35mm or 50mm lens perspective, realistic depth of field, "
        "soft available daylight or believable venue lighting, accurate skin and material "
        "texture, balanced highlights, detailed shadows, subtle film grain, restrained "
        "colour grading and lifelike contrast. Beautiful but credible, crisp subject, "
        "no plastic skin, no glossy CGI, no 3D render, no fantasy lighting, no artificial "
        "HDR, no oversaturation, no painterly look, no collage, no split screen. "
        "FACTUAL SAFETY: never invent a person's identity or face, a score, result, "
        "uniform, logo, location, crowd, injury, damage, quote or event detail not stated. "
        "For a result announcement, show a plausible school/exam context and a blank "
        "generic result sheet; never fabricate marks, names or certificates. For a "
        "transfer/signing/return, show a grounded club-ground or equipment context, not "
        "a fake match. For an actual match report, show only the sport and scene supported "
        "by the facts. Cricket uses cricket equipment only; football uses football "
        "equipment only; hockey uses hockey equipment only. "
        "SPECIFIC SCENE DIRECTION: " + direction[:500] + " "
        "EXACT HEADLINE: " + headline + " "
        "SOURCE FACTS (do not add facts): " + facts + " "
        "SHORT NEWS SUMMARY: " + post + " "
        "VISUAL RESEARCH NOTES (reference only, not proof): " + brief + " "
        "Landscape 4:3, editorial framing, reserve clean lower 22 percent for the app's "
        "separate headline overlay."
    )
    return prompt[:2048]

def generate_news_image(title, description="", summary="", visual_brief=""):
    global _CLOUDFLARE_COOLDOWN_UNTIL, _CLOUDFLARE_COOLDOWN_REASON
    if time.monotonic() < _CLOUDFLARE_COOLDOWN_UNTIL:
        raise RuntimeError("Free AI image quota is temporarily paused after a provider daily-limit response; using the branded fallback instead. " + _CLOUDFLARE_COOLDOWN_REASON)
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
                lower_detail = detail.lower()
                quota_exhausted = (
                    response.status_code == 429
                    and (
                        "4006" in lower_detail
                        or "daily free allocation" in lower_detail
                        or ("used up" in lower_detail and "neurons" in lower_detail)
                    )
                )
                if quota_exhausted:
                    # Provider controls the exact daily reset time.
                    _CLOUDFLARE_COOLDOWN_UNTIL = time.monotonic() + 6 * 60 * 60
                    _CLOUDFLARE_COOLDOWN_REASON = "Cloudflare reported that its daily free AI allocation is exhausted."
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
            if attempt == 0 and time.monotonic() >= _CLOUDFLARE_COOLDOWN_UNTIL:
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


def create_fallback_news_graphic(title, summary=""):
    """Create a polished, colourful NayaDrishti editorial poster when image APIs are unavailable."""
    headline = re.sub(r"\s+", " ", (title or "Latest News").strip())[:180]
    low = (headline + " " + (summary or "")).lower()

    # Distinct, restrained palettes by beat: colourful without turning every post blue.
    themes = {
        "education": ((249, 241, 220), (42, 57, 48), (184, 91, 49), (232, 190, 105), (255, 250, 238)),
        "cricket": ((239, 242, 218), (35, 58, 44), (181, 65, 49), (220, 166, 76), (250, 247, 230)),
        "football": ((247, 231, 219), (53, 43, 39), (178, 66, 53), (218, 137, 76), (255, 247, 236)),
        "weather": ((231, 239, 231), (43, 59, 53), (71, 123, 103), (210, 160, 86), (249, 248, 235)),
        "politics": ((242, 231, 218), (48, 45, 43), (151, 67, 53), (196, 145, 83), (255, 248, 237)),
        "health": ((235, 241, 224), (43, 61, 51), (71, 128, 104), (205, 158, 91), (250, 249, 235)),
        "transport": ((242, 234, 218), (48, 53, 53), (181, 92, 48), (210, 164, 85), (255, 249, 237)),
        "technology": ((235, 231, 219), (43, 48, 54), (126, 91, 133), (205, 155, 87), (250, 247, 237)),
        "business": ((244, 235, 216), (51, 53, 43), (137, 105, 55), (204, 151, 79), (255, 250, 238)),
        "entertainment": ((246, 229, 220), (57, 43, 48), (164, 77, 105), (218, 151, 91), (255, 247, 238)),
        "general": ((239, 232, 216), (48, 49, 44), (157, 83, 55), (207, 157, 82), (255, 249, 237)),
    }
    if any(k in low for k in ("neb", "result", "exam", "education", "school", "student", "परीक्षा", "नतिजा", "शिक्षा", "विद्यार्थी", "कक्षा")):
        category = "education"
    elif any(k in low for k in ("cricket", "wicket", "batter", "bowler", "क्रिकेट", "विकेट", "रन")):
        category = "cricket"
    elif any(k in low for k in ("football", "soccer", "goal", "फुटबल", "गोल")):
        category = "football"
    elif any(k in low for k in ("weather", "rain", "flood", "storm", "भूकम्प", "बाढी", "वर्षा", "मौसम")):
        category = "weather"
    elif any(k in low for k in ("government", "minister", "parliament", "election", "सरकार", "मन्त्री", "संसद", "निर्वाचन")):
        category = "politics"
    elif any(k in low for k in ("hospital", "health", "doctor", "स्वास्थ्य", "अस्पताल", "चिकित्सक")):
        category = "health"
    elif any(k in low for k in ("technology", "software", "artificial intelligence", "cyber", "smartphone", "mobile app", "प्रविधि", "कृत्रिम बुद्धिमत्ता", "मोबाइल")):
        category = "technology"
    elif any(k in low for k in ("business", "economy", "market", "bank", "stock", "budget", "व्यवसाय", "अर्थतन्त्र", "बजार", "बैंक", "बजेट")):
        category = "business"
    elif any(k in low for k in ("movie", "film", "actor", "singer", "concert", "music", "चलचित्र", "अभिनेता", "गायक", "संगीत")):
        category = "entertainment"
    elif any(k in low for k in ("road", "traffic", "bus", "airport", "flight", "सडक", "यातायात", "विमान")):
        category = "transport"
    else:
        category = "general"

    bg, ink, accent, secondary, paper = themes[category]
    canvas = Image.new("RGB", (1200, 900), bg)
    d = ImageDraw.Draw(canvas)

    # Editorial magazine layout: warm paper, oversized colour fields, restrained details.
    d.rectangle((0, 0, 1200, 18), fill=accent)
    d.ellipse((785, -180, 1320, 355), fill=secondary)
    d.ellipse((905, -105, 1285, 275), fill=bg)
    d.ellipse((1000, 65, 1140, 205), fill=accent)
    d.rounded_rectangle((55, 48, 1145, 830), radius=30, fill=paper, outline=(222, 211, 190), width=2)

    font_candidates = [
        os.getenv("NEWS_FONT_PATH", ""),
        "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
    ]
    regular_candidates = [
        os.getenv("NEWS_FONT_PATH", ""),
        "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Regular.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    ]
    def font_from(paths, size):
        for path in paths:
            if path and os.path.isfile(path):
                try:
                    return ImageFont.truetype(path, size)
                except OSError:
                    pass
        return ImageFont.load_default()

    brand_font = font_from(font_candidates, 38)
    kicker_font = font_from(regular_candidates, 20)
    headline_font = font_from(font_candidates, 51)
    small_font = font_from(font_candidates, 19)

    # Brand masthead and category tag.
    d.text((92, 83), "NayaDrishti", font=brand_font, fill=ink)
    d.text((94, 132), "नयाँ दृष्टि, नयाँ खबर।", font=kicker_font, fill=accent)
    d.rounded_rectangle((92, 180, 245, 188), radius=4, fill=accent)
    d.rounded_rectangle((92, 220, 320, 266), radius=18, fill=ink)
    d.text((112, 230), category.upper() + "  /  NEWS", font=small_font, fill=paper)

    # Large topic emblem, drawn as a clean editorial symbol rather than fake reportage.
    cx, cy = 875, 430
    d.ellipse((650, 245, 1095, 690), fill=bg, outline=(225, 215, 196), width=3)
    if category == "cricket":
        d.ellipse((760, 330, 1000, 570), fill=(65, 112, 76), outline=ink, width=5)
        d.rectangle((865, 355, 895, 545), fill=(205, 166, 111), outline=ink, width=3)
        d.line((825, 525, 845, 525), fill=paper, width=7)
        d.line((915, 525, 935, 525), fill=paper, width=7)
        d.ellipse((950, 390, 1000, 440), fill=(177, 56, 48), outline=paper, width=4)
        for x in (820, 850, 880):
            d.line((x, 470, x, 550), fill=paper, width=8)
    elif category == "football":
        d.ellipse((755, 310, 995, 550), fill=(247, 245, 231), outline=ink, width=6)
        d.polygon([(875, 365), (925, 402), (906, 462), (844, 462), (825, 402)], fill=ink)
        d.line((875, 365, 875, 330), fill=accent, width=5)
        d.arc((775, 330, 975, 530), 20, 155, fill=accent, width=6)
    elif category == "education":
        d.polygon([(735, 405), (875, 330), (1015, 405)], fill=accent)
        d.rectangle((755, 405, 995, 535), fill=(225, 210, 177), outline=ink, width=4)
        for x in (790, 855, 920):
            d.rectangle((x, 430, x + 38, 480), fill=paper, outline=ink, width=2)
        d.polygon([(810, 565), (875, 520), (940, 565), (875, 605)], fill=secondary, outline=ink)
    elif category == "weather":
        d.ellipse((760, 360, 920, 520), fill=secondary)
        d.ellipse((835, 320, 1010, 510), fill=secondary)
        d.ellipse((900, 380, 1040, 520), fill=secondary)
        d.rectangle((815, 435, 990, 520), fill=secondary)
        for x in (810, 875, 940):
            d.line((x, 530, x - 22, 585), fill=accent, width=9)
    elif category in ("politics", "business"):
        d.polygon([(735, 405), (875, 335), (1015, 405)], fill=accent)
        d.rectangle((755, 405, 995, 535), fill=(221, 208, 181), outline=ink, width=4)
        for x in (790, 850, 910, 970):
            d.rectangle((x, 430, x + 20, 530), fill=paper, outline=ink, width=2)
        d.rectangle((735, 535, 1015, 552), fill=secondary)
    elif category == "health":
        d.rounded_rectangle((785, 310, 965, 555), radius=24, fill=(226, 237, 220), outline=ink, width=5)
        d.rectangle((850, 355, 900, 510), fill=accent)
        d.rectangle((800, 407, 950, 457), fill=accent)
    elif category == "transport":
        d.polygon([(740, 520), (830, 350), (920, 350), (1010, 520)], fill=(95, 99, 92))
        d.line((875, 380, 875, 510), fill=paper, width=8)
        d.line((820, 480, 930, 480), fill=secondary, width=6)
    elif category == "technology":
        d.rounded_rectangle((790, 300, 960, 565), radius=25, fill=ink, outline=accent, width=6)
        d.rounded_rectangle((810, 330, 940, 515), radius=12, fill=(222, 220, 204))
        d.ellipse((865, 530, 885, 550), fill=secondary)
        d.line((830, 380, 920, 380), fill=accent, width=6)
        d.line((830, 415, 900, 415), fill=ink, width=5)
    elif category == "entertainment":
        d.ellipse((755, 320, 995, 560), fill=accent, outline=ink, width=5)
        d.polygon([(845, 380), (845, 500), (940, 440)], fill=paper)
    else:
        d.ellipse((755, 315, 995, 555), outline=accent, width=12)
        d.arc((805, 315, 945, 555), 0, 360, fill=ink, width=6)
        d.arc((755, 375, 995, 495), 0, 360, fill=ink, width=6)
        d.ellipse((855, 415, 895, 455), fill=secondary)

    # Headline block: automatic font reduction and safe wrapping prevent clipped text.
    d.line((92, 610, 1108, 610), fill=(218, 207, 187), width=2)
    d.rectangle((92, 640, 101, 755), fill=accent)
    max_width, max_lines = 960, 3
    chosen_font, lines = headline_font, []
    for size in (51, 46, 41, 36, 32):
        chosen_font = font_from(font_candidates, size)
        lines, current = [], ""
        for word in headline.split():
            candidate = (current + " " + word).strip()
            if d.textbbox((0, 0), candidate, font=chosen_font)[2] <= max_width:
                current = candidate
            else:
                if current:
                    lines.append(current)
                current = word
        if current:
            lines.append(current)
        if len(lines) <= max_lines:
            break
    y = 635
    line_height = 57 if size >= 46 else 49
    for line in lines[:max_lines]:
        d.text((125, y), line, font=chosen_font, fill=ink)
        y += line_height

    d.text((92, 780), "EDITORIAL GRAPHIC  •  NOT A NEWS PHOTOGRAPH", font=small_font, fill=accent)
    d.text((1108, 780), "NAYADRISHTI", font=small_font, fill=ink, anchor="ra")
    output = io.BytesIO()
    canvas.save(output, format="JPEG", quality=94, optimize=True)
    return output.getvalue()
