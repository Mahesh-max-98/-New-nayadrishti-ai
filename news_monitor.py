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
from facebook_publisher import publish_text

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
    allowed = (
        "neb", "routine of nepal banda", "ronb", "troll cricket",
        "bbc nepali", "onlinekhabar", "ok khabar", "okkhabar",
        "laligurash", "laligurash media",
    )
    for part in raw.split("|"):
        if "=" not in part:
            continue
        name, url = part.split("=", 1)
        name, url = name.strip(), url.strip()
        if name and url.startswith(("https://", "http://")) and any(term in name.lower() for term in allowed):
            feeds.append((name, url))

    # Discover publishers through Google News RSS when they do not expose a
    # stable public RSS feed. This is an index, not direct access to their pages.
    google_queries = [
        ("Google News - Routine of Nepal Banda", '"Routine of Nepal Banda" OR RONB Nepal when:1d'),
        ("Google News - Troll Cricket Nepal", '"Troll Cricket Nepal" when:1d'),
        ("Google News - BBC Nepali", 'site:bbc.com/nepali OR site:bbc.com/news "Nepal" when:1d'),
        ("Google News - Onlinekhabar", 'site:onlinekhabar.com when:1d'),
        ("Google News - OK Khabar", 'site:okkhabar.com when:1d'),
        ("Google News - Laligurash Media Network", 'site:laligurash.com when:1d'),
        ("Google News - NEB", 'site:neb.gov.np OR "National Examinations Board Nepal" when:1d'),
    ]
    for source_name, query in google_queries:
        params = urlencode({"q": query, "hl": "ne", "gl": "NP", "ceid": "NP:ne"})
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
        "stale_skipped": 0, "undated_skipped": 0, "fresh_candidates": 0,
        "feed_details": [],
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
                # Google News is a discovery index; recover the publisher suffix
                # from its headline so the post credits the original outlet.
                if source_name.startswith("Google News - "):
                    publisher_match = re.search(r"\\s[-–—]\\s([^|]+)$", title)
                    if publisher_match:
                        source_name_for_post = publisher_match.group(1).strip()
                        title = title[:publisher_match.start()].strip()
                    else:
                        source_name_for_post = source_name.replace("Google News - ", "").strip()
                else:
                    source_name_for_post = source_name
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
                                        source_name=source_name_for_post, article_url=link)
                if not summary:
                    raise ValueError("Groq returned an empty news post.")

                # Text-only workflow: never fetch, generate, attach, or publish an image.
                save_draft(
                    item_id, title, source_name, link, summary,
                    image_data=None, image_status="text_only", image_error=None,
                )
                result["drafts_saved"] += 1

                status = "draft"
                if os.getenv("ENABLE_FACEBOOK_PUBLISHING", "false").strip().lower() == "true":
                    try:
                        publish_result = publish_text(summary)
                        if publish_result.get("published"):
                            result["published"] += 1
                            status = "published"
                    except Exception as publish_exc:
                        log.exception("Facebook text publishing failed for %s", source_name)
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
        "Text-only Nepali news workflow. No article images are fetched, generated, attached, or published. "
        "Only stories published within the last 24 hours with a valid timestamp are eligible; "
        "undated and older entries are skipped. Google News RSS is discovery only, and the original "
        "publisher is credited in the generated Nepali post."
    )
    total_stale = sum(item.get("stale_skipped", 0) for item in result["feed_details"])
    total_undated = sum(item.get("undated_skipped", 0) for item in result["feed_details"])
    log.info(
        "Text-only feed check: checked=%s/%s, new=%s, drafts=%s, duplicates=%s, stale_skipped=%s, undated_skipped=%s, errors=%s",
        result["feeds_checked"], result["feeds_configured"], result["new_items"],
        result["drafts_saved"], result["already_seen"], total_stale, total_undated,
        len(result["errors"]),
    )
    return result
