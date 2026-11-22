import calendar
import hashlib
import html
import logging
import os
import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse, urlencode

import feedparser
import requests

from ai_writer import write_summary
from database import already_seen, already_seen_title, get_draft_for_image_retry, remember, save_draft, set_draft_status
from facebook_publisher import publish_image
from image_generator import create_publisher_news_card, create_fallback_news_graphic, generate_news_image
from google_research import research_story

log = logging.getLogger("nayadrishti")
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))


class _ImageMetaParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.image = None

    def handle_starttag(self, tag, attrs):
        if tag.lower() != "meta" or self.image:
            return
        values = {key.lower(): value for key, value in attrs if key and value}
        prop = (values.get("property") or values.get("name") or "").lower()
        if prop in ("og:image", "og:image:secure_url", "twitter:image", "twitter:image:src"):
            self.image = values.get("content")


def configured_feeds():
    raw = os.getenv("NEWS_FEEDS", "")
    feeds = []
    for part in raw.split("|"):
        if "=" not in part:
            continue
        name, url = part.split("=", 1)
        name, url = name.strip(), url.strip()
        if name and url.startswith(("https://", "http://")):
            feeds.append((name, url))
    # NEB Result & News is a high-relevance education/results publisher.
    # Its RSS feed is machine-readable; Facebook posts remain discovery leads only.
    feeds.append(("NEB Result & News", "https://nebnews.net/feed/"))
    feeds.append(("The Kathmandu Post", "https://kathmandupost.com/rss"))
    # Add Google News RSS discovery alongside configured publishers.
    # Results are discovery leads, not independent verification.
    google_queries = [
        ("Google News - Nepal", "Nepal news when:1d", "en", "NP", "NP:en"),
        ("Google News - Nepal Nepali", "नेपाल समाचार when:1d", "ne", "NP", "NP:ne"),
        ("Google News - NEB Result & News", '"NEB Result & News" OR NEB result Nepal when:1d', "en", "NP", "NP:en"),
        ("Google News - NEB Nepali", 'NEB नतिजा OR कक्षा १२ नतिजा OR SEE result when:1d', "ne", "NP", "NP:ne"),
        ("Google News - Routine of Nepal Banda", '"Routine of Nepal Banda" OR RONB Nepal when:1d', "en", "NP", "NP:en"),
        ("Google News - BBC News Nepali", 'site:bbc.com/nepali OR "BBC News नेपाली" Nepal when:1d', "ne", "NP", "NP:ne"),
        ("Google News - eKantipur", 'site:ekantipur.com Nepal when:1d', "ne", "NP", "NP:ne"),
        ("Google News - Onlinekhabar", 'site:onlinekhabar.com Nepal when:1d', "ne", "NP", "NP:ne"),
        ("Google News - Setopati", 'site:setopati.com Nepal when:1d', "ne", "NP", "NP:ne"),
        ("Google News - Ratopati", 'site:ratopati.com Nepal when:1d', "ne", "NP", "NP:ne"),
        ("Google News - Nepal News", 'site:nepalnews.com Nepal when:1d', "en", "NP", "NP:en"),
        ("Google News - Nagarik News", 'site:nagariknews.nagariknetwork.com Nepal when:1d', "ne", "NP", "NP:ne"),
        ("Google News - myRepublica", 'site:myrepublica.nagariknetwork.com Nepal when:1d', "en", "NP", "NP:en"),
        ("Google News - The Himalayan Times", 'site:thehimalayantimes.com Nepal when:1d', "en", "NP", "NP:en"),
        ("Google News - Annapurna Post", 'site:annapurnapost.com Nepal when:1d', "ne", "NP", "NP:ne"),
        ("Google News - Nepal Cricket", "Nepal cricket OR Nepali cricket when:1d", "en", "NP", "NP:en"),
        ("Google News - World", "world breaking news when:1d", "en", "US", "US:en"),
        ("Google News - Football", "football soccer latest when:1d", "en", "GB", "GB:en"),
    ]
    for source_name, query, language, country, edition in google_queries:
        params = urlencode({"q": query, "hl": language, "gl": country, "ceid": edition})
        feeds.append((source_name, "https://news.google.com/rss/search?" + params))
    return feeds


def clean_text(value, max_length=5000):
    value = re.sub(r"<[^>]+>", " ", value or "")
    value = html.unescape(value)
    return re.sub(r"\s+", " ", value).strip()[:max_length]


def _entry_timestamp(item):
    """Return the feed's published/updated time as a UTC timestamp."""
    parsed = item.get("published_parsed") or item.get("updated_parsed")
    if not parsed:
        return None
    try:
        return float(calendar.timegm(parsed))
    except (TypeError, ValueError, OverflowError):
        return None


def _is_fresh_entry(item, max_age_hours=24):
    """Reject undated, stale, or implausibly future-dated feed entries."""
    timestamp = _entry_timestamp(item)
    if timestamp is None:
        return False, "missing publication date"
    age_seconds = datetime.now(timezone.utc).timestamp() - timestamp
    if age_seconds < -7200:
        return False, "publication date is more than 2 hours in the future"
    if age_seconds > max_age_hours * 3600:
        return False, f"older than {max_age_hours} hours"
    return True, ""
def fetch_feed(url):
    response = requests.get(
        url,
        headers={"User-Agent": "NayaDrishtiAI/1.0 (+RSS feed reader)"},
        timeout=(8, 20),
    )
    response.raise_for_status()
    parsed = feedparser.parse(response.content)
    if parsed.bozo and not parsed.entries:
        raise ValueError("The URL did not return a readable RSS/Atom feed.")
    return parsed


def _allowed_host(url, allowed_domains):
    host = (urlparse(url).hostname or "").lower().rstrip(".")
    return bool(host) and any(
        host == domain or host.endswith("." + domain)
        for domain in allowed_domains
    )


def _publisher_image_domains():
    # Deliberately opt-in: a photo being visible in an RSS feed does NOT by itself
    # grant permission to republish it. Add only domains whose reuse terms allow it.
    return {
        value.strip().lower().lstrip(".").rstrip(".")
        for value in os.getenv("PUBLISHER_IMAGE_ALLOWED_DOMAINS", "").split(",")
        if value.strip()
    }


def _candidate_feed_images(item, article_url):
    candidates = []
    for key in ("media_content", "media_thumbnail"):
        for media in item.get(key, []) or []:
            if isinstance(media, dict) and media.get("url"):
                candidates.append(urljoin(article_url, media["url"]))
    for enclosure in item.get("enclosures", []) or []:
        if isinstance(enclosure, dict):
            mime = (enclosure.get("type") or "").lower()
            href = enclosure.get("href") or enclosure.get("url")
            if href and (mime.startswith("image/") or not mime):
                candidates.append(urljoin(article_url, href))
    for link in item.get("links", []) or []:
        if isinstance(link, dict) and link.get("href"):
            if (link.get("type") or "").lower().startswith("image/"):
                candidates.append(urljoin(article_url, link["href"]))
    raw_html = item.get("summary") or item.get("description") or ""
    match = re.search(r"""<img[^>]+src=["']([^"']+)["']""", raw_html, re.I)
    if match:
        candidates.append(urljoin(article_url, html.unescape(match.group(1))))
    # Keep order but remove duplicates.
    return list(dict.fromkeys(candidates))


def _article_meta_image(article_url):
    response = requests.get(
        article_url,
        headers={"User-Agent": "Mozilla/5.0 (compatible; NayaDrishtiAI/1.0)"},
        timeout=(8, 15),
        stream=True,
    )
    response.raise_for_status()
    content_type = response.headers.get("Content-Type", "").lower()
    if "text/html" not in content_type:
        return None
    chunks = []
    total = 0
    for chunk in response.iter_content(32768):
        if not chunk:
            continue
        chunks.append(chunk)
        total += len(chunk)
        if total >= 700_000:
            break
    parser = _ImageMetaParser()
    parser.feed(b"".join(chunks).decode(response.encoding or "utf-8", errors="ignore"))
    return urljoin(article_url, parser.image) if parser.image else None


def fetch_permitted_publisher_photo(item, article_url):
    allowed = _publisher_image_domains()
    if not allowed or not _allowed_host(article_url, allowed):
        return None

    candidates = _candidate_feed_images(item, article_url)
    if not candidates:
        try:
            meta_image = _article_meta_image(article_url)
            if meta_image:
                candidates.append(meta_image)
        except Exception:
            log.info("Could not read article image metadata for %s", article_url)

    for image_url in candidates:
        if not image_url.startswith("https://") or not _allowed_host(image_url, allowed):
            continue
        try:
            response = requests.get(
                image_url,
                headers={"User-Agent": "NayaDrishtiAI/1.0 (publisher image preview)"},
                timeout=(8, 20),
                stream=True,
            )
            response.raise_for_status()
            if not response.headers.get("Content-Type", "").lower().startswith("image/"):
                continue
            chunks, total = [], 0
            for chunk in response.iter_content(65536):
                if not chunk:
                    continue
                total += len(chunk)
                if total > 8 * 1024 * 1024:
                    raise ValueError("Publisher image exceeds the 8 MB limit.")
                chunks.append(chunk)
            return b"".join(chunks), image_url
        except Exception as exc:
            log.info("Publisher image unavailable (%s): %s", image_url, exc)
    return None


def check_feeds():
    feeds = configured_feeds()
    result = {
        "feeds_configured": len(feeds), "feeds_checked": 0, "new_items": 0,
        "drafts_saved": 0, "published": 0, "already_seen": 0, "errors": [],
        "publisher_photos_used": 0, "ai_illustrations_used": 0, "fallback_graphics_used": 0,
        "google_news_results": 0, "image_reference_candidates": 0, "google_discovery_feeds": 18,
        "visual_references_checked": 0, "feed_details": [],
    }
    if not feeds:
        result["errors"].append("NEWS_FEEDS is empty or invalid. Add RSS/Atom feed URLs in Render.")
        return result

    for source_name, feed_url in feeds:
        feed_info = {
            "source": source_name, "entries_found": 0, "new_items": 0,
            "already_seen": 0, "stale_skipped": 0, "undated_skipped": 0,
            "sample_titles": [], "error": None,
        }
        try:
            parsed = fetch_feed(feed_url)
            result["feeds_checked"] += 1
            feed_info["entries_found"] = len(parsed.entries)
            feed_info["sample_titles"] = [
                clean_text(entry.get("title"), 140)
                for entry in parsed.entries[:3] if entry.get("title")
            ]
            new_items_from_feed = 0
            # Sort by publisher timestamp and reject stories older than 24 hours.
            # Undated entries are skipped rather than risking stale news.
            dated_entries = sorted(
                parsed.entries,
                key=lambda entry: _entry_timestamp(entry) or 0,
                reverse=True,
            )
            for item in dated_entries[:50]:
                title = clean_text(item.get("title"), 300)
                link = (item.get("link") or "").strip()
                description = clean_text(item.get("summary") or item.get("description") or "")
                if not title or not link:
                    continue

                fresh, freshness_reason = _is_fresh_entry(item, max_age_hours=24)
                if not fresh:
                    if freshness_reason == "missing publication date":
                        feed_info["undated_skipped"] += 1
                    else:
                        feed_info["stale_skipped"] += 1
                    continue

                item_id = hashlib.sha256((source_name + "|" + link).encode("utf-8")).hexdigest()
                if already_seen(item_id) or already_seen_title(title):
                    result["already_seen"] += 1
                    feed_info["already_seen"] += 1
                    retry_draft = get_draft_for_image_retry(item_id)
                    if retry_draft:
                        try:
                            retry_image = generate_news_image(
                                title, description, retry_draft["summary"]
                            )
                            save_draft(
                                item_id, title, source_name, link,
                                retry_draft["summary"], image_data=retry_image,
                                image_status="ai_illustration", image_error=None,
                            )
                            result.setdefault("images_retried", 0)
                            result["images_retried"] += 1
                            log.info("Recovered missing image for previously saved story: %s", title)
                        except Exception as retry_exc:
                            log.warning("Image retry provider unavailable for %s: %s", title, retry_exc)
                            try:
                                fallback_image = create_fallback_news_graphic(
                                    title, retry_draft["summary"]
                                )
                                save_draft(
                                    item_id, title, source_name, link,
                                    retry_draft["summary"], image_data=fallback_image,
                                    image_status="fallback_graphic",
                                    image_error=f"{type(retry_exc).__name__}: {str(retry_exc)[:300]}",
                                )
                                result.setdefault("fallback_graphics_used", 0)
                                result["fallback_graphics_used"] += 1
                                log.info("Recovered missing image with a labelled fallback graphic: %s", title)
                            except Exception as fallback_exc:
                                save_draft(
                                    item_id, title, source_name, link,
                                    retry_draft["summary"], image_data=None,
                                    image_status="failed",
                                    image_error=f"{type(fallback_exc).__name__}: {str(fallback_exc)[:350]}",
                                )
                                result.setdefault("image_retry_errors", [])
                                result["image_retry_errors"].append(
                                    f"{source_name}: {type(fallback_exc).__name__}: {str(fallback_exc)[:180]}"
                                )
                    continue

                summary = write_summary(title=title, description=description,
                                        source_name=source_name, article_url=link)
                if not summary:
                    raise ValueError("Groq returned an empty news post.")

                # Research the story before choosing or generating its image.
                # Google News results provide context; Google Images references
                # are visually checked when Google CSE credentials are configured.
                research = {}
                try:
                    research = research_story(title, description)
                    result["google_news_results"] += len(research.get("news_results", []))
                    result["image_reference_candidates"] += int(research.get("image_candidates", 0))
                    if research.get("visual_reference"):
                        result["visual_references_checked"] += 1
                except Exception as research_exc:
                    log.warning("Story research failed for %s: %s", source_name, research_exc)
                    result["errors"].append(
                        f"{source_name} research: {type(research_exc).__name__}: {str(research_exc)[:160]}"
                    )

                image_data = None
                image_status = "failed"
                image_error = None
                try:
                    publisher_photo = fetch_permitted_publisher_photo(item, link)
                    if publisher_photo:
                        photo_bytes, _photo_url = publisher_photo
                        image_data = create_publisher_news_card(photo_bytes, title, summary)
                        image_status = "publisher_photo"
                        result["publisher_photos_used"] += 1
                    else:
                        # Use research context and the visually checked reference
                        # only to improve scene/category selection. The reference
                        # image itself is never downloaded or republished.
                        research_context = research.get("news_context", "")
                        visual_reference = research.get("visual_reference", "")
                        visual_brief = " ".join(
                            part for part in (
                                "Research context: " + research_context if research_context else "",
                                "Visual reference inspection (not evidence): " + visual_reference if visual_reference else "",
                            ) if part
                        )
                        image_data = generate_news_image(
                            title, description, summary,
                            visual_brief=visual_brief[:900],
                        )
                        image_status = "ai_illustration"
                        result["ai_illustrations_used"] += 1
                except Exception as image_exc:
                    log.warning("Image provider unavailable for %s: %s", source_name, image_exc)
                    image_error = f"{type(image_exc).__name__}: {str(image_exc)[:350]}"
                    # Never attach an unrelated stock photo. A clearly labelled,
                    # topic-headline graphic keeps the draft readable without
                    # pretending to be a real scene or an AI photograph.
                    try:
                        image_data = create_fallback_news_graphic(title, summary)
                        image_status = "fallback_graphic"
                        result.setdefault("fallback_graphics_used", 0)
                        result["fallback_graphics_used"] += 1
                    except Exception as fallback_exc:
                        log.exception("Fallback graphic failed for %s", source_name)
                        result["errors"].append(
                            f"{source_name} fallback graphic: {type(fallback_exc).__name__}: {str(fallback_exc)[:160]}"
                        )
                    result["errors"].append(
                        f"{source_name} image provider unavailable: {type(image_exc).__name__}: {str(image_exc)[:180]}"
                    )

                # A draft is saved even when image creation fails; image-less drafts
                # are never auto-published.
                save_draft(item_id, title, source_name, link, summary,
                           image_data=image_data, image_status=image_status,
                           image_error=image_error)
                result["drafts_saved"] += 1

                # Publishing is a separate step from drafting. A Facebook API
                # failure must not cause the same story to be regenerated every
                # minute (which wastes AI/image credits and can create duplicate work).
                status = "draft"
                if image_data:
                    try:
                        publish_result = publish_image(summary, image_data)
                        if publish_result.get("published"):
                            result["published"] += 1
                            status = "published"
                    except Exception as publish_exc:
                        log.exception("Facebook publishing failed for %s", source_name)
                        result["errors"].append(
                            f"{source_name} Facebook publish: "
                            f"{type(publish_exc).__name__}: {str(publish_exc)[:180]}"
                        )

                # Always mark the item as seen after saving its draft. If publishing
                # fails, the saved draft remains available for manual review.
                set_draft_status(item_id, status)
                remember(item_id, title, source_name, link, status)
                result["new_items"] += 1
                feed_info["new_items"] += 1
                new_items_from_feed += 1
                if new_items_from_feed >= 1:
                    break
        except Exception as exc:
            log.exception("Feed processing failed for %s", source_name)
            feed_info["error"] = f"{type(exc).__name__}: {str(exc)[:180]}"
            result["errors"].append(f"{source_name}: {type(exc).__name__}: {str(exc)[:220]}")
        finally:
            result["feed_details"].append(feed_info)

    result["facebook_publishing_enabled"] = (
        os.getenv("ENABLE_FACEBOOK_PUBLISHING", "false").lower() == "true"
    )
    result["note"] = (
        " Eighteen Google News RSS discovery feeds cover Nepal English/Nepali, NEB Result & News, RONB, BBC Nepali, eKantipur, Onlinekhabar, Setopati, Ratopati, Nepal News, Nagarik, myRepublica, The Himalayan Times, Annapurna Post, cricket, world and football; direct publisher RSS feeds include NEB Result & News and The Kathmandu Post. Facebook page references are discovery leads only; stories are rewritten from original reporting and corroborated where possible. Only stories published within the last 24 hours with a valid feed timestamp are eligible; undated and older entries are skipped. Stories are researched with Google News RSS. Visual references are searched "
        "through Wikimedia Commons by default and checked with Groq vision; existing "
        "Google Custom Search credentials can optionally enable Google Images. Reference "
        "images are never republished. Publisher photos are used only when their article "
        "and image hosts are explicitly allowlisted; otherwise a labelled AI illustration "
        "is used."
    )
    return result
