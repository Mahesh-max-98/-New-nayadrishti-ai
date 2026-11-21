import logging
import os
from urllib.parse import urlencode

import feedparser
import requests

log = logging.getLogger("nayadrishti.google_research")
GOOGLE_NEWS_RSS = "https://news.google.com/rss/search"
GOOGLE_CSE_URL = "https://www.googleapis.com/customsearch/v1"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"


def _clean(value, limit=350):
    import re
    import html
    value = html.unescape(re.sub(r"<[^>]+>", " ", value or ""))
    return re.sub(r"\s+", " ", value).strip()[:limit]


def _google_news_results(title):
    params = {
        "q": title[:180],
        "hl": "en",
        "gl": "US",
        "ceid": "US:en",
    }
    response = requests.get(
        GOOGLE_NEWS_RSS + "?" + urlencode(params),
        headers={"User-Agent": "NayaDrishtiAI/1.0 (news research)"},
        timeout=(8, 15),
    )
    response.raise_for_status()
    parsed = feedparser.parse(response.content)
    results = []
    for entry in parsed.entries[:5]:
        headline = _clean(entry.get("title"), 220)
        summary = _clean(entry.get("summary") or entry.get("description"), 350)
        if headline:
            results.append({
                "title": headline,
                "summary": summary,
                "link": (entry.get("link") or "")[:500],
            })
    return results


def _commons_image_candidates(title):
    """Find openly licensed visual-reference images on Wikimedia Commons."""
    params = {
        "action": "query",
        "generator": "search",
        "gsrsearch": title[:180],
        "gsrnamespace": 6,
        "gsrlimit": 5,
        "prop": "imageinfo",
        "iiprop": "url|extmetadata",
        "iiurlwidth": 640,
        "format": "json",
    }
    response = requests.get(
        "https://commons.wikimedia.org/w/api.php",
        params=params,
        headers={"User-Agent": "NayaDrishtiAI/1.0 (news visual research)"},
        timeout=(8, 20),
    )
    response.raise_for_status()
    pages = (response.json().get("query") or {}).get("pages") or {}
    candidates = []
    for page in pages.values():
        info_list = page.get("imageinfo") or []
        if not info_list:
            continue
        info = info_list[0]
        metadata = info.get("extmetadata") or {}
        license_name = _clean(
            (metadata.get("LicenseShortName") or {}).get("value")
            or (metadata.get("UsageTerms") or {}).get("value"),
            100,
        )
        image_url = info.get("thumburl") or info.get("url") or ""
        if not image_url.startswith("https://") or not license_name:
            continue
        candidates.append({
            "title": _clean(page.get("title"), 180),
            "image_url": image_url,
            "context_url": "https://commons.wikimedia.org/wiki/" + page.get("title", "").replace(" ", "_"),
            "source": "Wikimedia Commons",
            "license": license_name,
        })
    return candidates


def _google_image_candidates(title):
    api_key = os.getenv("GOOGLE_API_KEY", "").strip()
    engine_id = os.getenv("GOOGLE_CSE_ID", "").strip()
    if not api_key or not engine_id:
        # Google Custom Search JSON API is not open to new customers.
        # Use Commons as a no-key, licence-labelled visual-reference fallback.
        return _commons_image_candidates(title)

    # Search results are used only as visual-reference candidates. The app
    # does not republish these images. A rights filter narrows the search,
    # but it is not a substitute for checking the original licence.
    params = {
        "key": api_key,
        "cx": engine_id,
        "q": title[:180],
        "searchType": "image",
        "num": 5,
        "safe": "active",
        "rights": "cc_publicdomain,cc_attribute,cc_sharealike",
    }
    response = requests.get(
        GOOGLE_CSE_URL,
        params=params,
        headers={"User-Agent": "NayaDrishtiAI/1.0 (image reference search)"},
        timeout=(8, 20),
    )
    response.raise_for_status()
    payload = response.json()
    candidates = []
    for item in payload.get("items", [])[:5]:
        image_url = item.get("link") or ""
        if not image_url.startswith("https://"):
            continue
        image_meta = item.get("image") or {}
        candidates.append({
            "title": _clean(item.get("title"), 180),
            "image_url": image_url,
            "context_url": (image_meta.get("contextLink") or "")[:500],
            "source": _clean(item.get("displayLink"), 100),
            "license": "Google usage-rights-filtered result; verify source terms",
        })
    return candidates


def _inspect_reference_image(title, candidate):
    api_key = os.getenv("GROQ_API_KEY", "").strip()
    if not api_key:
        return ""
    model = os.getenv(
        "GROQ_VISION_MODEL",
        "qwen/qwen3.8-27b",
    ).strip()

    # Ask a vision model to identify only visible objects and sport category.
    # It must not identify a person or treat the image as proof of the news.
    prompt = (
        "Inspect this image only as a visual reference for a news illustration. "
        "Do not identify people or infer news facts. Report the dominant sport "
        "and visible equipment in this exact format: SPORT: cricket/hockey/football/"
        "other/unclear; OBJECTS: short list; MATCH: yes/no/unclear. MATCH means "
        "the image visibly matches the sport named in the headline, not that it "
        "depicts the reported event or named person. If uncertain, say unclear. "
        "Never treat the image as evidence that the news happened. Headline: "
        + _clean(title, 180)
    )
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
                    "content": (
                        "You are a cautious visual-reference checker. Describe "
                        "only visible content. Never guess identity or news facts."
                    ),
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {"url": candidate["image_url"]},
                        },
                    ],
                },
            ],
            "temperature": 0,
            "max_completion_tokens": 180,
        },
        timeout=35,
    )
    if not response.ok:
        raise RuntimeError(
            "Groq vision error "
            + str(response.status_code)
            + ": "
            + response.text[:250].replace("\n", " ")
        )
    choices = response.json().get("choices") or []
    if not choices:
        return ""
    message = choices[0].get("message") or {}
    content = message.get("content") or ""
    if isinstance(content, list):
        content = " ".join(
            part.get("text", "")
            for part in content
            if isinstance(part, dict) and part.get("type") == "text"
        )
    return _clean(str(content), 700)


def research_story(title, description=""):
    """Research a headline with Google News and optionally inspect Google Images."""
    result = {
        "news_results": [],
        "image_candidates": 0,
        "visual_reference": "",
        "news_context": "",
        "research_status": "partial",
    }
    try:
        result["news_results"] = _google_news_results(title)
    except Exception as exc:
        log.warning("Google News research unavailable: %s", exc)

    if result["news_results"]:
        lines = []
        for item in result["news_results"][:4]:
            line = "- " + item["title"]
            if item["summary"]:
                line += ": " + item["summary"]
            lines.append(line)
        result["news_context"] = "Google News search leads (not independent proof): " + " ".join(lines)
        result["research_status"] = "google_news_found"

    try:
        candidates = _google_image_candidates(title)
        result["image_candidates"] = len(candidates)
        # Inspect one candidate to keep free-tier vision usage low. A visual
        # search result is a reference only, never a photo to republish.
        if candidates:
            inspection = _inspect_reference_image(title, candidates[0])
            lower_title = title.lower().replace("‑", "-").replace("–", "-")
            expected_sport = ""
            if any(term in lower_title for term in (
                "cricket", "imam-ul-haq", "imam ul haq", "pakistan opener",
                "pakistani opener", "county cricket", "headingley cricket",
                "yorkshire cricket",
            )):
                expected_sport = "cricket"
            elif any(term in lower_title for term in ("hockey", "field hockey", "ice hockey")):
                expected_sport = "hockey"
            elif any(term in lower_title for term in ("football", "soccer", "premier league")):
                expected_sport = "football"

            inspection_lower = inspection.lower()
            sport_match = expected_sport and ("sport: " + expected_sport) in inspection_lower
            explicitly_mismatch = expected_sport and (
                "match: no" in inspection_lower or "match: unclear" in inspection_lower
            )
            if sport_match and not explicitly_mismatch:
                result["visual_reference"] = (
                    "Visual reference category checked: " + inspection
                    + ". Use only the sport equipment/context; do not copy the person or event."
                )
            elif expected_sport:
                result["visual_reference"] = (
                    "REJECT the candidate visual reference. It did not reliably match "
                    + expected_sport + ". Generate a neutral " + expected_sport
                    + "-only scene using the verified news facts; do not imitate the candidate."
                )
            elif inspection and "match: yes" in inspection_lower:
                result["visual_reference"] = (
                    "Visual reference category checked: " + inspection
                    + ". Use only generic visual context, never the person or event."
                )
            if result["visual_reference"]:
                result["research_status"] = "news_and_image_reference_checked" if result["news_results"] else "image_reference_checked"
    except Exception as exc:
        log.warning("Google image reference check unavailable: %s", exc)

    if not result["news_context"] and description:
        result["news_context"] = _clean(description, 500)
    return result
