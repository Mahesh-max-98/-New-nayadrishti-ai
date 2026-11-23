import calendar
import hashlib
import html
import logging
import os
import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import urlencode

import feedparser
import requests

from ai_writer import write_summary
from database import already_seen, already_seen_title, remember, save_draft, set_draft_status
from facebook_publisher import publish_text

log = logging.getLogger("nayadrishti")
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))

GOOGLE_NEWS_QUERIES = [
    ("Google News - Routine of Nepal Banda", '"Routine of Nepal Banda" OR RONB Nepal when:1d'),
    ("Google News - Troll Cricket Nepal", '"Troll Cricket Nepal" when:1d'),
    ("Google News - BBC Nepali", 'site:bbc.com/nepali OR site:bbc.com/news "Nepal" when:1d'),
    ("Google News - Onlinekhabar", 'site:onlinekhabar.com when:1d'),
    ("Google News - OK Khabar", 'site:okkhabar.com when:1d'),
    ("Google News - Laligurash Media Network", 'site:laligurash.com when:1d'),
    ("Google News - NEB", 'site:neb.gov.np OR "National Examinations Board Nepal" when:1d'),
]


def configured_feeds():
    """Return custom approved RSS feeds plus Google News discovery feeds."""
    feeds = []
    raw = os.getenv("NEWS_FEEDS", "")
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

    for source_name, query in GOOGLE_NEWS_QUERIES:
        params = urlencode({"q": query, "hl": "ne", "gl": "NP", "ceid": "NP:ne"})
        feeds.append((source_name, "https://news.google.com/rss/search?" + params))

    unique, seen_urls = [], set()
    for name, url in feeds:
        if url not in seen_urls:
            unique.append((name, url))
            seen_urls.add(url)
    return unique


def clean_text(value, max_length=7000):
    value = html.unescape(re.sub(r"<[^>]+>", " ", value or ""))
    return re.sub(r"\s+", " ", value).strip()[:max_length]


def _entry_timestamp(item):
    parsed = item.get("published_parsed") or item.get("updated_parsed")
    if not parsed:
        return None
    try:
        return float(calendar.timegm(parsed))
    except (TypeError, ValueError, OverflowError):
        return None


def _is_fresh_entry(item, max_age_hours=36):
    """Accept recent dated stories; reject undated, stale, or far-future items."""
    timestamp = _entry_timestamp(item)
    if timestamp is None:
        return False, "missing publication date"
    age = datetime.now(timezone.utc).timestamp() - timestamp
    if age < -7200:
        return False, "future publication date"
    if age > max_age_hours * 3600:
        return False, "older than freshness window"
    return True, ""


def fetch_feed(url):
    """Fetch an RSS/Atom feed with bounded retries for temporary provider errors."""
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry

    client = requests.Session()
    retry = Retry(
        total=2, connect=2, read=1, status=2, backoff_factor=1,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET"]),
        respect_retry_after_header=True, raise_on_status=False,
    )
    client.mount("https://", HTTPAdapter(max_retries=retry))
    client.mount("http://", HTTPAdapter(max_retries=retry))
    response = client.get(
        url,
        headers={"User-Agent": "Mozilla/5.0 (compatible; NayaDrishtiAI/1.0; RSS reader)"},
        timeout=(8, 18),
    )
    response.raise_for_status()
    parsed = feedparser.parse(response.content)
    if parsed.bozo and not parsed.entries:
        raise ValueError("The URL did not return a readable RSS/Atom feed.")
    return parsed


class _ArticleTextParser(HTMLParser):
    """Extract article paragraphs while ignoring navigation, scripts and page chrome."""
    SKIP_TAGS = {"script", "style", "noscript", "svg", "header", "footer", "nav", "aside"}

    def __init__(self):
        super().__init__()
        self.skip_depth = 0
        self.in_paragraph = False
        self.current = []
        self.parts = []

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in self.SKIP_TAGS:
            self.skip_depth += 1
        elif not self.skip_depth and tag == "p":
            self.in_paragraph = True
            self.current = []

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in self.SKIP_TAGS and self.skip_depth:
            self.skip_depth -= 1
        elif tag == "p" and self.in_paragraph and not self.skip_depth:
            paragraph = re.sub(r"\s+", " ", " ".join(self.current)).strip()
            if len(paragraph) >= 35:
                self.parts.append(paragraph)
            self.in_paragraph = False
            self.current = []

    def handle_data(self, data):
        if self.in_paragraph and not self.skip_depth:
            value = data.strip()
            if value:
                self.current.append(value)


def fetch_article_text(article_url, fallback_description=""):
    """Prefer readable original article text; fall back to the RSS summary."""
    fallback = clean_text(fallback_description, 7000)
    if not article_url.startswith(("https://", "http://")):
        return fallback
    try:
        with requests.get(
            article_url,
            headers={"User-Agent": "Mozilla/5.0 (compatible; NayaDrishtiAI/1.0; news reader)"},
            timeout=(8, 15),
            stream=True,
            allow_redirects=True,
        ) as response:
            response.raise_for_status()
            if "text/html" not in response.headers.get("Content-Type", "").lower():
                return fallback
            chunks, total = [], 0
            for chunk in response.iter_content(32768):
                if not chunk:
                    continue
                chunks.append(chunk)
                total += len(chunk)
                if total >= 900_000:
                    break
            parser = _ArticleTextParser()
            parser.feed(b"".join(chunks).decode(response.encoding or "utf-8", errors="ignore"))
            body = clean_text(" ".join(parser.parts), 7000)
            return body if len(body) >= 180 else fallback
    except Exception as exc:
        log.info("Article extraction fell back to RSS description for %s: %s", article_url, exc)
        return fallback


def _publisher_from_google_title(title, fallback):
    match = re.search(r"\s[-–—]\s([^|]+)$", title or "")
    if not match:
        return fallback, title
    publisher = clean_text(match.group(1), 100)
    headline = (title[:match.start()] or "").strip()
    return (publisher or fallback), headline


def check_feeds():
    feeds = configured_feeds()
    result = {
        "feeds_configured": len(feeds), "feeds_checked": 0, "new_items": 0,
        "drafts_saved": 0, "published": 0, "already_seen": 0, "errors": [],
        "stale_skipped": 0, "undated_skipped": 0, "fresh_candidates": 0,
        "feed_details": [],
    }
    if not feeds:
        result["errors"].append("No RSS feeds configured.")
        return result

    per_feed_limit = max(1, min(int(os.getenv("MAX_STORIES_PER_FEED", "2")), 5))
    freshness_hours = max(1, min(int(os.getenv("NEWS_MAX_AGE_HOURS", "36")), 168))

    for feed_name, feed_url in feeds:
        detail = {
            "source": feed_name, "entries_found": 0, "new_items": 0,
            "already_seen": 0, "stale_skipped": 0, "undated_skipped": 0,
            "fresh_candidates": 0, "error": None,
        }
        try:
            parsed = fetch_feed(feed_url)
            result["feeds_checked"] += 1
            entries = sorted(
                parsed.entries,
                key=lambda entry: _entry_timestamp(entry) or 0,
                reverse=True,
            )
            detail["entries_found"] = len(entries)

            for entry in entries[:60]:
                raw_title = clean_text(entry.get("title"), 300)
                article_url = (entry.get("link") or "").strip()
                rss_description = clean_text(entry.get("summary") or entry.get("description") or "", 7000)
                if not raw_title or not article_url:
                    continue

                fresh, reason = _is_fresh_entry(entry, freshness_hours)
                if not fresh:
                    if reason == "missing publication date":
                        detail["undated_skipped"] += 1
                        result["undated_skipped"] += 1
                    else:
                        detail["stale_skipped"] += 1
                        result["stale_skipped"] += 1
                    continue
                detail["fresh_candidates"] += 1
                result["fresh_candidates"] += 1

                if feed_name.startswith("Google News - "):
                    publisher, title = _publisher_from_google_title(
                        raw_title, feed_name.replace("Google News - ", "").strip()
                    )
                else:
                    publisher, title = feed_name, raw_title
                if not title:
                    continue

                item_id = hashlib.sha256((publisher.lower() + "|" + article_url).encode("utf-8")).hexdigest()
                if already_seen(item_id) or already_seen_title(title):
                    detail["already_seen"] += 1
                    result["already_seen"] += 1
                    continue

                article_text = fetch_article_text(article_url, rss_description)
                if len(article_text) < 80:
                    log.info("Skipping story with too little source text: %s", title)
                    continue

                post_text = write_summary(
                    title=title,
                    description=article_text,
                    source_name=publisher,
                    article_url=article_url,
                )
                save_draft(item_id, title, publisher, article_url, post_text, language="ne")
                result["drafts_saved"] += 1

                status = "draft"
                if os.getenv("ENABLE_FACEBOOK_PUBLISHING", "false").strip().lower() == "true":
                    try:
                        published = publish_text(post_text)
                        if published.get("published"):
                            status = "published"
                            result["published"] += 1
                    except Exception as exc:
                        log.exception("Facebook text publishing failed for %s", publisher)
                        result["errors"].append(
                            f"{publisher} publish failed: {type(exc).__name__}: {str(exc)[:160]}"
                        )

                set_draft_status(item_id, status)
                remember(item_id, title, publisher, article_url, status)
                result["new_items"] += 1
                detail["new_items"] += 1
                if detail["new_items"] >= per_feed_limit:
                    break
        except Exception as exc:
            log.exception("Feed processing failed for %s", feed_name)
            detail["error"] = f"{type(exc).__name__}: {str(exc)[:180]}"
            result["errors"].append(f"{feed_name}: {detail['error']}")
        finally:
            result["feed_details"].append(detail)

    result["facebook_publishing_enabled"] = (
        os.getenv("ENABLE_FACEBOOK_PUBLISHING", "false").strip().lower() == "true"
    )
    result["note"] = (
        "Text-only Nepali news. No images are fetched, generated, stored, or published. "
        "The article text is preferred over RSS teasers, duplicate headlines are skipped, "
        "and posts contain a source credit without a publication timestamp."
    )
    log.info(
        "News scan complete: checked=%s/%s new=%s drafts=%s published=%s duplicates=%s errors=%s",
        result["feeds_checked"], result["feeds_configured"], result["new_items"],
        result["drafts_saved"], result["published"], result["already_seen"],
        len(result["errors"]),
    )
    return result
