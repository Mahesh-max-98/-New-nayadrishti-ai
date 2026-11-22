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
    headline = re.sub(r"\\s+", " ", (title or "").strip())[:260]
    facts = re.sub(r"\\s+", " ", (description or "").strip())[:520]
    post = re.sub(r"\\s+", " ", (summary or "").strip())[:260]
    brief = re.sub(r"\\s+", " ", (visual_brief or "").strip())[:320]
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
    """Create a topic-specific illustrated scene when AI image service is unavailable."""
    headline = re.sub(r"\s+", " ", (title or "Latest News").strip())[:180]
    low = (headline + " " + (summary or "")).lower()
    canvas = Image.new("RGB", (1200, 900), (8, 20, 42))
    draw = ImageDraw.Draw(canvas)

    # Smooth editorial background and a large illustration stage.
    for y in range(900):
        t = y / 900
        shade = int(18 + 34 * t)
        draw.line((0, y, 1200, y), fill=(7, shade, min(105, shade + 40)))
    draw.rounded_rectangle((42, 38, 1158, 620), radius=34,
                           fill=(14, 43, 76), outline=(67, 132, 174), width=3)

    # Select a clear visual motif from the headline. These are illustrations,
    # not claims that a specific photographed event took place.
    category = "general"
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
    elif any(k in low for k in ("road", "traffic", "bus", "airport", "flight", "सडक", "यातायात", "विमान")):
        category = "transport"

    white = (238, 246, 255)
    cyan = (63, 196, 215)
    gold = (246, 190, 75)
    pale = (176, 211, 230)

    # Common illustrated scene framing.
    draw.ellipse((770, 65, 1110, 405), outline=(38, 91, 133), width=5)
    draw.ellipse((820, 115, 1060, 355), outline=(38, 91, 133), width=3)

    if category == "education":
        # School + open book + result sheet motif (no fabricated marks/text).
        draw.polygon([(180, 330), (600, 155), (1015, 330)], fill=(45, 112, 150), outline=cyan)
        draw.rectangle((245, 325, 955, 520), fill=(26, 76, 111), outline=white, width=4)
        for x in (340, 485, 630, 775):
            draw.rectangle((x, 375, x + 75, 455), fill=(112, 185, 207), outline=white, width=3)
        draw.rectangle((545, 425, 655, 520), fill=(9, 35, 62), outline=gold, width=3)
        draw.rounded_rectangle((75, 455, 350, 570), radius=14, fill=(235, 244, 249), outline=gold, width=4)
        draw.line((105, 490, 305, 490), fill=(54, 101, 134), width=5)
        draw.line((105, 520, 270, 520), fill=(54, 101, 134), width=5)
        draw.line((105, 548, 245, 548), fill=(54, 101, 134), width=5)
        draw.text((390, 545), "EDUCATION", fill=white)
    elif category == "cricket":
        draw.rectangle((90, 390, 1110, 565), fill=(29, 104, 79), outline=(113, 190, 135), width=4)
        draw.ellipse((360, 350, 840, 580), fill=(48, 133, 92), outline=(176, 220, 169), width=4)
        draw.polygon([(260, 505), (335, 485), (585, 180), (640, 195)], fill=(204, 166, 111), outline=white)
        draw.ellipse((775, 420, 835, 480), fill=(196, 54, 53), outline=white, width=3)
        for x in (870, 895, 920):
            draw.line((x, 430, x, 555), fill=white, width=9)
        draw.line((860, 430, 930, 430), fill=white, width=8)
        draw.text((430, 545), "CRICKET", fill=white)
    elif category == "football":
        draw.rectangle((100, 155, 1100, 570), outline=(130, 207, 160), width=5)
        draw.line((600, 155, 600, 570), fill=(130, 207, 160), width=4)
        draw.ellipse((430, 260, 770, 600), outline=(130, 207, 160), width=4)
        draw.rectangle((100, 270, 260, 455), outline=white, width=5)
        draw.rectangle((940, 270, 1100, 455), outline=white, width=5)
        draw.ellipse((540, 330, 660, 450), fill=white, outline=(26, 44, 61), width=5)
        draw.text((485, 545), "FOOTBALL", fill=white)
    elif category == "weather":
        draw.ellipse((270, 210, 550, 470), fill=(205, 225, 236))
        draw.ellipse((430, 145, 720, 465), fill=(205, 225, 236))
        draw.ellipse((650, 220, 930, 470), fill=(205, 225, 236))
        draw.rectangle((390, 320, 810, 455), fill=(205, 225, 236))
        for x in (390, 520, 650, 780):
            draw.line((x, 470, x - 45, 555), fill=cyan, width=9)
        draw.text((495, 555), "WEATHER", fill=white)
    elif category == "politics":
        draw.polygon([(220, 300), (600, 130), (980, 300)], fill=(53, 119, 155), outline=gold)
        draw.rectangle((260, 300, 940, 520), fill=(35, 81, 116), outline=white, width=4)
        for x in (350, 475, 600, 725, 850):
            draw.rectangle((x, 330, x + 35, 520), fill=(153, 194, 210))
        draw.rectangle((210, 520, 990, 555), fill=gold)
        draw.text((470, 565), "CIVIC UPDATE", fill=white)
    elif category == "health":
        draw.rounded_rectangle((380, 145, 820, 555), radius=30, fill=(222, 239, 246), outline=cyan, width=5)
        draw.rectangle((560, 220, 640, 475), fill=(36, 155, 166))
        draw.rectangle((470, 305, 730, 390), fill=(36, 155, 166))
        draw.text((475, 565), "HEALTH", fill=white)
    elif category == "transport":
        draw.polygon([(120, 460), (1080, 460), (900, 220), (300, 220)], fill=(48, 65, 82))
        draw.line((600, 245, 600, 450), fill=gold, width=9)
        for y in (290, 365, 435):
            draw.line((570, y, 630, y), fill=white, width=5)
        draw.text((480, 565), "TRANSPORT", fill=white)
    else:
        # General news visual: globe and newspaper, never a blank title card.
        draw.ellipse((270, 125, 730, 585), outline=cyan, width=12)
        draw.arc((380, 125, 620, 585), 0, 360, fill=cyan, width=7)
        draw.arc((270, 245, 730, 465), 0, 360, fill=cyan, width=7)
        draw.rounded_rectangle((700, 300, 1040, 535), radius=16, fill=(232, 241, 246), outline=gold, width=5)
        draw.line((750, 350, 990, 350), fill=(45, 83, 111), width=8)
        draw.line((750, 395, 980, 395), fill=(45, 83, 111), width=7)
        draw.line((750, 440, 940, 440), fill=(45, 83, 111), width=7)
        draw.text((455, 565), "NEWS UPDATE", fill=white)

    font_candidates = [
        os.getenv("NEWS_FONT_PATH", ""),
        "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
    ]
    font_path = next((p for p in font_candidates if p and os.path.isfile(p)), None)
    try:
        headline_font = ImageFont.truetype(font_path, 43) if font_path else ImageFont.load_default()
        label_font = ImageFont.truetype(font_path, 23) if font_path else ImageFont.load_default()
    except OSError:
        headline_font = label_font = ImageFont.load_default()

    # Headline is a separate lower overlay; the upper half always contains a
    # category illustration, so provider failure never results in text-only art.
    draw.rounded_rectangle((42, 650, 1158, 858), radius=28, fill=(5, 16, 33), outline=(64, 117, 153), width=2)
    draw.text((78, 672), "NAYADRISHTI  •  EDITORIAL ILLUSTRATION", font=label_font, fill=gold)
    words, lines, line = headline.split(), [], ""
    for word in words:
        candidate = (line + " " + word).strip()
        if draw.textbbox((0, 0), candidate, font=headline_font)[2] <= 1030:
            line = candidate
        else:
            if line:
                lines.append(line)
            line = word
    if line:
        lines.append(line)
    y = 715
    for line in lines[:2]:
        draw.text((78, y), line, font=headline_font, fill=white)
        y += 55
    draw.text((78, 865), "EDITORIAL GRAPHIC — NOT A NEWS PHOTOGRAPH", font=label_font, fill=pale)
    output = io.BytesIO()
    canvas.save(output, format="JPEG", quality=91, optimize=True)
    return output.getvalue()
