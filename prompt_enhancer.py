"""Groq-powered prompt enhancement for the standalone NayaDrishti Image Studio.

Groq is used only to interpret and improve text instructions. It never generates
image pixels; those come from image_provider.py.
"""
import os
import re

import requests

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

SUPPORTED_STYLES = {
    "photorealistic", "portrait", "cinematic", "editorial", "digital-art",
    "illustration", "cartoon", "3d", "product", "architecture", "landscape",
    "poster", "social-graphic", "logo"
}


class PromptEnhancementError(RuntimeError):
    """Safe prompt-enhancement error."""


def enhance_prompt(prompt, style="auto", language="auto"):
    original = re.sub(r"\s+", " ", str(prompt or "")).strip()
    if not original:
        raise PromptEnhancementError("Enter a prompt before asking for enhancement.")
    if len(original) > 6000:
        raise PromptEnhancementError("The prompt is too long (maximum 6,000 characters).")

    api_key = os.getenv("GROQ_API_KEY", "").strip()
    if not api_key:
        raise PromptEnhancementError(
            "Prompt enhancement needs GROQ_API_KEY. Image generation itself uses the separate image provider."
        )
    model = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b").strip()
    requested_style = str(style or "auto").strip().lower()
    if requested_style not in SUPPORTED_STYLES and requested_style != "auto":
        requested_style = "auto"

    instruction = (
        "Improve this image-generation prompt without changing the user's intent. "
        "Return JSON only with keys enhanced_prompt, detected_style, and notes. "
        "The enhanced prompt must be a single clear instruction for an image model. "
        "Preserve all named subjects, requested actions, locations, objects and constraints. "
        "Do not invent people, events, brands, text, logos or extra objects. "
        "If the user asks for a photograph, explicitly request a photograph and never call it "
        "an illustration, vector, poster or 3D render. If the user asks for an illustration, "
        "preserve illustration. Add only helpful composition, lighting, lens, material and "
        "detail guidance that follows from the request. Preserve the language of the user's "
        "prompt where practical; English technical camera terms are acceptable. "
        "Do not claim that the model can guarantee perfection. "
        "User style selection: " + requested_style + ". "
        "User language preference: " + str(language or "auto")[:30] + ". "
        "Original user prompt: " + original
    )
    try:
        response = requests.post(
            GROQ_URL,
            headers={
                "Authorization": "Bearer " + api_key,
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "messages": [
                    {
                        "role": "system",
                        "content": "You are a careful image-prompt editor. Preserve user intent. Output valid JSON only.",
                    },
                    {"role": "user", "content": instruction},
                ],
                "temperature": 0.25,
                "max_completion_tokens": 900,
            },
            timeout=(10, 35),
        )
    except requests.Timeout as exc:
        raise PromptEnhancementError("Prompt enhancement timed out. Please retry.") from exc
    except requests.RequestException as exc:
        raise PromptEnhancementError("Could not connect to the prompt-enhancement service.") from exc

    if not response.ok:
        raise PromptEnhancementError(
            f"Prompt enhancement service failed (HTTP {response.status_code})."
        )
    choices = (response.json() or {}).get("choices") or []
    content = (choices[0].get("message") or {}).get("content") if choices else ""
    if isinstance(content, list):
        content = " ".join(
            part.get("text", "") for part in content if isinstance(part, dict)
        )
    content = str(content or "").strip()
    # Tolerate fenced JSON from providers while rejecting free-form hallucinated output.
    content = re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", content, flags=re.I)
    import json
    try:
        parsed = json.loads(content)
    except (ValueError, TypeError) as exc:
        raise PromptEnhancementError("The prompt service returned an invalid response. Please retry.") from exc

    enhanced = re.sub(r"\s+", " ", str(parsed.get("enhanced_prompt") or "")).strip()
    detected = str(parsed.get("detected_style") or requested_style).strip().lower()
    notes = str(parsed.get("notes") or "").strip()[:300]
    if not enhanced:
        raise PromptEnhancementError("The prompt service returned an empty enhanced prompt.")
    if len(enhanced) > 8000:
        enhanced = enhanced[:8000]
    if detected not in SUPPORTED_STYLES:
        detected = requested_style if requested_style in SUPPORTED_STYLES else "photorealistic"
    return {
        "original_prompt": original,
        "enhanced_prompt": enhanced,
        "detected_style": detected,
        "notes": notes,
        "model": model,
    }
