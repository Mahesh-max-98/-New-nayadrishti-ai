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
from database import already_seen, already_seen_title, get_draft_for_image_retry, get_drafts_needing_image_retry, remember, save_draft, set_draft_status
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
    """Use the three requested priority sources; Google News is discovery only."""
    raw = os.getenv("NEWS_FEEDS", "")
    feeds = []
    allowed = ("neb", "routine of nepal banda", "ronb", "troll cricket")
    for part in raw.split("|"):
        if "=" not in part:
            continue
        name, url = part.split("=", 1)
        name, url = name.strip(), url.strip()
        if (
            name and url.startswith(("https://", "http://"))
            and any(term in name.lower() for term in allowed)
        ):
            feeds.append((name, url))

    # Always include the official NEB feed; avoid adding it twice.
    neb_url = "https://nebnews.net/feed/"
    if not any(url.rstrip("/") == neb_url.rstrip("/") for _, url in feeds):
        feeds.append(("NEB News and Result", neb_url))

    # Google News RSS is used only to discover recent posts from the two
    # requested social/news sources. It is not treated as the original publisher.
    google_queries = [
        ("Routine of Nepal Banda", '"Routine of Nepal Banda" OR RONB Nepal when:1d', "en", "NP", "NP:en"),
        ("Troll Cricket Nepal", '"Troll Cricket Nepal" when:1d', "en", "NP", "NP:en"),
    ]
    for source_name, query, language, country, edition in google_queries:
        params = urlencode({"q": query, "hl": language, "gl": country, "ceid": edition})
        feeds.append((source_name, "https://news.google.com/rss/search?" + params))
    # De-duplicate configured URLs while preserving order.
    unique = []
    seen_urls = set()
    for name, url in feeds:
        if url not in seen_urls:
            unique.append((name, url))
            seen_urls.add(url)
    return unique


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
    """Fetch an RSS feed with bounded retries for temporary rate limits/server errors."""
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry

    session = requests.Session()
    retry = Retry(
        total=2,
        connect=2,
        read=1,
        status=2,
        backoff_factor=1.0,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET"]),
        respect_retry_after_header=True,
        raise_on_status=False,
    )
    session.mount("https://", HTTPAdapter(max_retries=retry))
    session.mount("http://", HTTPAdapter(max_retries=retry))
    response = session.get(
        url,
        headers={"User-Agent": "Mozilla/5.0 (compatible; NayaDrishtiAI/1.0; +RSS reader)"},
        timeout=(8, 18),
    )
    response.raise_for_status()
    parsed = feedparser.parse(response.content)
    if parsed.bozo and not parsed.entries:
        raise ValueError("The URL did not return a readable RSS/Atom feed.")
    if not parsed.entries:
        log.info("Feed returned no entries: %s", url)
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
    domains = {
        value.strip().lower().lstrip(".").rstrip(".")
        for value in os.getenv("PUBLISHER_IMAGE_ALLOWED_DOMAINS", "").split(",")
        if value.strip()
    }
    # The user has confirmed permission from Laligurash Media Network to reuse
    # its still news photographs on the NayaDrishti Facebook Page.
    domains.add("laligurash.com")
    return domains


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
    if not allowed:
        return None

    # Google News RSS URLs redirect to the original publisher article.
    try:
        article_response = requests.get(
            article_url,
            headers={"User-Agent": "Mozilla/5.0 (compatible; NayaDrishtiAI/1.0)"},
            timeout=(8, 15),
            allow_redirects=True,
            stream=True,
        )
        final_article_url = article_response.url or article_url
        article_response.close()
    except Exception as exc:
        log.info("Could not resolve publisher article URL %s: %s", article_url, exc)
        final_article_url = article_url

    if not _allowed_host(final_article_url, allowed):
        return None

    candidates = _candidate_feed_images(item, final_article_url)
    if not candidates:
        try:
            meta_image = _article_meta_image(final_article_url)
            if meta_image:
                candidates.append(meta_image)
        except Exception:
            log.info("Could not read article image metadata for %s", final_article_url)

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



class _ArticleTextParser(HTMLParser):
    """Extract readable article paragraphs without scripts, navigation or markup."""
    SKIP_TAGS = {"script", "style", "noscript", "svg", "header", "footer", "nav", "aside"}

    def __init__(self):
        super().__init__()
        self.skip_depth = 0
        self.in_paragraph = 0
        self.parts = []
        self.current = []

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in self.SKIP_TAGS:
            self.skip_depth += 1
        elif not self.skip_depth and tag == "p":
            self.in_paragraph += 1
            self.current = []

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in self.SKIP_TAGS and self.skip_depth:
            self.skip_depth -= 1
        elif not self.skip_depth and tag == "p" and self.in_paragraph:
            paragraph = re.sub(r"\s+", " ", " ".join(self.current)).strip()
            if len(paragraph) >= 45:
                self.parts.append(paragraph)
            self.in_paragraph -= 1
            self.current = []

    def handle_data(self, data):
        if not self.skip_depth and self.in_paragraph:
            value = data.strip()
            if value:
                self.current.append(value)


def fetch_article_text(article_url, fallback_description=""):
    """Fetch a bounded amount of article text to avoid summaries based on RSS teasers alone."""
    if not article_url.startswith(("https://", "http://")):
        return clean_text(fallback_description, 5000)
    try:
        response = requests.get(
            article_url,
            headers={"User-Agent": "Mozilla/5.0 (compatible; NayaDrishtiAI/1.0; +news-summary)"},
            timeout=(7, 12),
            stream=True,
        )
        response.raise_for_status()
        content_type = response.headers.get("Content-Type", "").lower()
        if "text/html" not in content_type:
            return clean_text(fallback_description, 5000)
        chunks, total = [], 0
        for chunk in response.iter_content(32768):
            if not chunk:
                continue
            chunks.append(chunk)
            total += len(chunk)
            if total >= 900_000:
                break
        encoding = response.encoding or "utf-8"
        parser = _ArticleTextParser()
        parser.feed(b"".join(chunks).decode(encoding, errors="ignore"))
        article_text = " ".join(parser.parts)
        if len(article_text) >= 180:
            return clean_text(article_text, 5000)
    except Exception as exc:
        log.info("Article text extraction unavailable for %s: %s", article_url, exc)
    return clean_text(fallback_description, 5000)


def check_feeds():
    feeds = configured_feeds()
    result = {
        "feeds_configured": len(feeds), "feeds_checked": 0, "new_items": 0,
        "drafts_saved": 0, "published": 0, "already_seen": 0, "errors": [],
        "publisher_photos_used": 0, "ai_illustrations_used": 0, "fallback_graphics_used": 0,
        "google_news_results": 0, "image_reference_candidates": 0, "google_discovery_feeds": 2,
        "stale_skipped": 0, "undated_skipped": 0, "fresh_candidates": 0,
        "visual_references_checked": 0, "feed_details": [],
    }
    if not feeds:
        result["errors"].append("NEWS_FEEDS is empty or invalid. Add RSS/Atom feed URLs in Render.")
        return result

    for source_name, feed_url in feeds:
        feed_info = {
            "source": source_name, "entries_found": 0, "new_items": 0,
            "already_seen": 0, "stale_skipped": 0, "undated_skipped": 0,
            "fresh_candidates": 0, "latest_published": None,
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
            newest_timestamp = max(
                (_entry_timestamp(entry) or 0 for entry in parsed.entries),
                default=0,
            )
            if newest_timestamp:
                feed_info["latest_published"] = datetime.fromtimestamp(
                    newest_timestamp, timezone.utc
                ).isoformat()
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

                # Still-image news only: never process direct video links or enclosures.
                video_hosts = ("youtube.com", "youtu.be", "vimeo.com", "dailymotion.com")
                link_host = (urlparse(link).hostname or "").lower()
                video_enclosure = any(
                    isinstance(enc, dict)
                    and (
                        (enc.get("type") or "").lower().startswith("video/")
                        or urlparse(enc.get("href") or enc.get("url") or "").path.lower().endswith(
                            (".mp4", ".mov", ".m4v", ".webm", ".avi")
                        )
                    )
                    for enc in (item.get("enclosures") or [])
                )
                if (
                    any(link_host == host or link_host.endswith("." + host) for host in video_hosts)
                    or video_enclosure
                ):
                    feed_info.setdefault("videos_skipped", 0)
                    feed_info["videos_skipped"] += 1
                    continue

                fresh, freshness_reason = _is_fresh_entry(item, max_age_hours=24)
                if not fresh:
                    if freshness_reason == "missing publication date":
                        feed_info["undated_skipped"] += 1
                        result["undated_skipped"] += 1
                    else:
                        feed_info["stale_skipped"] += 1
                        result["stale_skipped"] += 1
                    continue
                feed_info["fresh_candidates"] += 1
                result["fresh_candidates"] += 1

                item_id = hashlib.sha256((source_name + "|" + link).encode("utf-8")).hexdigest()
                if already_seen(item_id) or already_seen_title(title):
                    result["already_seen"] += 1
                    feed_info["already_seen"] += 1
                    continue

                # Prefer the article body over short RSS teaser text so the summary
                # describes the actual development rather than announcing the publisher.
                article_text = fetch_article_text(link, description)
                summary = write_summary(title=title, description=article_text,
                                        source_name=source_name, article_url=link)
                if not summary:
                    raise ValueError("Groq returned an empty news post.")

                # Research the story before choosing or generating its image.
                # Google News results provide context; Google Images references
                # are visually checked when Google CSE credentials are configured.
                research = {}
                try:
                    research = research_story(title, article_text)
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
                            title, article_text, summary,
                            visual_brief=visual_brief[:900],
                        )
                        image_status = "ai_illustration"
                        result["ai_illustrations_used"] += 1
                except Exception as image_exc:
                    image_message = str(image_exc).lower()
                    if "daily free" in image_message or "temporarily paused" in image_message:
                        log.info("AI image quota is paused; using the free branded fallback for source: %s", source_name)
                    else:
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
                        # Keep AI disclosure in the post caption, not stamped across
                        # the photograph, so readers are not misled about generated imagery.
                        publish_caption = summary
                        if image_status == "ai_illustration":
                            publish_caption = summary.rstrip() + "\nचित्र: AI द्वारा तयार गरिएको सम्पादकीय चित्र।"
                        publish_result = publish_image(publish_caption, image_data)
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


    # Image retries are deliberately excluded from feed discovery. They must not
    # delay fresh-news checks; regenerate images only when explicitly requested.

    result["facebook_publishing_enabled"] = (
        os.getenv("ENABLE_FACEBOOK_PUBLISHING", "false").lower() == "true"
    )
    result["note"] = (
        " Three priority sources are monitored: NEB News and Result, Routine of Nepal Banda, and Troll Cricket Nepal. Google News RSS is used only as a discovery index for the latter two, not as the original publisher. Stories are rewritten from available source reporting and only items published within the last 24 hours with a valid timestamp are eligible; undated and older entries are skipped. Stories are researched with Google News RSS. Visual references are searched "
        "through Google Images only when Google Custom Search credentials are configured; "
        "otherwise the story headline and reporting context guide the illustration. "
        "Visual-reference images are never republished. Publisher photos are used only when their article "
        "and image hosts are explicitly allowlisted; otherwise a labelled AI illustration "
        "is used."
    )
    total_stale = sum(item.get("stale_skipped", 0) for item in result["feed_details"])
    total_undated = sum(item.get("undated_skipped", 0) for item in result["feed_details"])
    log.info(
        "Feed check summary: checked=%s/%s, new=%s, drafts=%s, duplicates=%s, stale_skipped=%s, undated_skipped=%s, errors=%s, publisher_photos=%s, ai_illustrations=%s, fallback_graphics=%s",
        result["feeds_checked"], result["feeds_configured"], result["new_items"],
        result["drafts_saved"], result["already_seen"], total_stale, total_undated,
        len(result["errors"]), result["publisher_photos_used"],
        result["ai_illustrations_used"], result["fallback_graphics_used"],
    )
    return result
