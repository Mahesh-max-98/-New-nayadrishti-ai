"""Roman Nepali humour-to-photo creator. Text is rendered locally for legibility."""
import io
import json
import os
import re
import textwrap

import requests
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageEnhance
from image_provider import generate_image, ImageProviderError

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

def _write_joke(topic="", kind="mixed"):
    key = os.getenv("GROQ_API_KEY", "").strip()
    if not key:
        raise RuntimeError("GROQ_API_KEY is not configured.")
    model = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b").strip()
    categories = {
        "joke": "short original jokes",
        "observation": "relatable funny everyday observations",
        "savage": "witty, playful savage one-liners that do not target protected traits",
        "caption": "entertaining short Facebook captions",
        "mixed": "a varied mix of jokes, funny observations, playful savage one-liners and entertaining captions",
    }
    brief = categories.get(kind, categories["mixed"])
    prompt = (
        "Create one original, shareable Facebook quote in Roman Nepali (Nepali language written in Latin letters). "
        "Do not write in English or Devanagari. Keep it natural, conversational, funny and understandable in Nepal. "
        "No hateful, sexual, threatening or humiliating content; no targeting a real person. "
        "Use at most 2 short sentences, ideally 8-20 words. Include emojis only if they improve the joke. "
        "Also write a concise English image prompt for a natural everyday photographic background that visually "
        "fits the joke. The photo must be clean and realistic, with no text, letters, logos, signs or watermark. "
        "Return JSON only with keys quote and photo_prompt. Content type: " + brief +
        ". Optional topic: " + (topic[:180] or "surprise me")
    )
    response = requests.post(
        GROQ_URL,
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
        json={
            "model": model,
            "messages": [
                {"role": "system", "content": "You write funny, original Roman Nepali social posts. Return valid JSON only."},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.9,
            "max_completion_tokens": 350,
        },
        timeout=(10, 35),
    )
    if not response.ok:
        raise RuntimeError("Joke-writing service failed (HTTP " + str(response.status_code) + ").")
    choices = (response.json() or {}).get("choices") or []
    raw = (choices[0].get("message") or {}).get("content") if choices else ""
    raw = re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", str(raw or "").strip(), flags=re.I)
    try:
        data = json.loads(raw)
    except (ValueError, TypeError) as exc:
        raise RuntimeError("The joke writer returned an invalid response. Please retry.") from exc
    quote = re.sub(r"\s+", " ", str(data.get("quote") or "")).strip()
    photo_prompt = re.sub(r"\s+", " ", str(data.get("photo_prompt") or "")).strip()
    if not quote or not photo_prompt:
        raise RuntimeError("The joke writer did not return both a quote and photo idea.")
    return quote[:360], photo_prompt[:900]

def _font(size, bold=False):
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            pass
    return ImageFont.load_default()

def _wrap(draw, text, font, max_width):
    words = text.split()
    lines, line = [], ""
    for word in words:
        candidate = (line + " " + word).strip()
        if draw.textbbox((0, 0), candidate, font=font)[2] <= max_width:
            line = candidate
        else:
            if line:
                lines.append(line)
            line = word
    if line:
        lines.append(line)
    return lines

def _render_quote_photo(photo_bytes, quote):
    # Keep overlay text compatible with the server fonts; emojis remain optional in the text preview.
    quote = "".join(ch for ch in quote if ord(ch) <= 0xFFFF and ch not in "\uFE0F")
    with Image.open(io.BytesIO(photo_bytes)) as source:
        image = source.convert("RGB")
    # Square Facebook/Instagram feed image; retain a natural photo background.
    w, h = image.size
    side = min(w, h)
    left, top = (w-side)//2, (h-side)//2
    image = image.crop((left, top, left+side, top+side)).resize((1080, 1080), Image.Resampling.LANCZOS)
    image = ImageEnhance.Color(image).enhance(1.04)
    image = image.convert("RGBA")
    # Subtle dark translucent panel, not a meme/cartoon template.
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    od.rounded_rectangle((70, 250, 1010, 830), radius=34, fill=(0, 0, 0, 142), outline=(255, 255, 255, 85), width=2)
    image = Image.alpha_composite(image, overlay)
    draw = ImageDraw.Draw(image)
    font_size = 68
    while font_size > 38:
        font = _font(font_size, True)
        lines = _wrap(draw, quote, font, 820)
        line_h = int(font_size * 1.35)
        if len(lines) <= 6 and len(lines) * line_h <= 480:
            break
        font_size -= 3
    line_h = int(font_size * 1.35)
    total_h = len(lines) * line_h
    y = 540 - total_h // 2
    for line in lines:
        box = draw.textbbox((0, 0), line, font=font, stroke_width=1)
        x = (1080 - (box[2] - box[0])) // 2
        draw.text((x, y), line, font=font, fill=(255,255,255,255),
                  stroke_width=2, stroke_fill=(0,0,0,150))
        y += line_h
    # Small unobtrusive footer branding.
    footer = _font(25, True)
    draw.text((75, 965), "FUNNY ROMAN NEPALI", font=footer, fill=(255,255,255,225),
              stroke_width=1, stroke_fill=(0,0,0,150))
    output = io.BytesIO()
    image.convert("RGB").save(output, format="JPEG", quality=92, optimize=True)
    return output.getvalue()

def create_funny_post(topic="", kind="mixed"):
    quote, photo_prompt = _write_joke(topic, kind)
    image_prompt = (
        "Create a natural, high-quality square everyday photograph for a light-hearted Nepali social media post. "
        + photo_prompt +
        " Realistic photography, candid natural light, uncluttered composition, visually calm area for readable overlay. "
        "Absolutely no text, typography, letters, captions, logos, watermark, poster layout or cartoon style."
    )
    try:
        photo = generate_image(image_prompt, width=576, height=576)
        return quote, _render_quote_photo(photo, quote), None
    except ImageProviderError as exc:
        # Never lose the generated joke when an image provider is temporarily unavailable.
        return quote, None, str(exc)[:260]
