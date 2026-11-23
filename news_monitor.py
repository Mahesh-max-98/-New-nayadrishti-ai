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
from database import already_seen, already_seen_title, already_seen_story, assess_cross_source_coverage, remember, save_draft, set_draft_status
from groq_budget import get_groq_usage_status
from facebook_publisher import publish_text

log = logging.getLogger("nayadrishti")
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))

DIRECT_NEWS_FEEDS = [
    # Public RSS endpoints listed for these publishers. Google News discovery
    # remains enabled as a fallback and for broader topic coverage.
    ("Onlinekhabar", "https://www.onlinekhabar.com/feed"),
    ("Setopati", "https://www.setopati.com/feed"),
    ("Ratopati", "https://www.ratopati.com/feed"),
    ("Nagarik News", "https://nagariknews.nagariknetwork.com/feed"),
]

GOOGLE_NEWS_QUERIES = [
    ("Google News - Routine of Nepal Banda", '"Routine of Nepal Banda" OR RONB Nepal when:1d'),
    ("Google News - Troll Cricket Nepal", '"Troll Cricket Nepal" when:1d'),
    ("Google News - BBC Nepali", 'site:bbc.com/nepali when:1d'),
    ("Google News - Onlinekhabar", 'site:onlinekhabar.com when:1d'),
    ("Google News - Kantipur/eKantipur", 'site:ekantipur.com when:1d'),
    ("Google News - Annapurna Post", 'site:annapurnapost.com when:1d'),
    ("Google News - Gorkhapatra", 'site:gorkhapatraonline.com when:1d'),
    ("Google News - Ujyaalo Online", 'site:ujyaaloonline.com when:1d'),
    ("Google News - Setopati", 'site:setopati.com when:1d'),
    ("Google News - Ratopati", 'site:ratopati.com when:1d'),
    ("Google News - Thaha Khabar", 'site:thahakhabar.com when:1d'),
    ("Google News - Nepal Samaya", 'site:nepalsamaya.com when:1d'),
    ("Google News - OK Khabar", 'site:okkhabar.com when:1d'),
    ("Google News - Laligurash Media Network", 'site:laligurash.com when:1d'),
    ("Google News - NEB", 'site:neb.gov.np OR "National Examinations Board Nepal" when:1d'),
]


# Editorial source priority is a discovery-ordering aid, not a guarantee that a
# publisher is always correct. Original links remain available for human review.
SOURCE_PRIORITY = {
    "bbc nepali": 100, "gorkhapatra": 100, "kantipur": 100, "ekantipur": 100,
    "annapurna": 100, "nagarik": 100, "onlinekhabar": 100, "setopati": 100,
    "ratopati": 100, "ujyaalo": 100, "ujyalo": 100,
    "thaha khabar": 85, "nepal samaya": 85, "ok khabar": 85,
    "okkhabar": 85, "neb": 85,
    "routine of nepal banda": 70, "ronb": 70,
    "troll cricket": 70, "laligurash": 65,
}

TOPIC_KEYWORDS = {
    "राजनीति": ("सरकार", "प्रधानमन्त्री", "मन्त्री", "संसद", "निर्वाचन", "चुनाव", "राजनीति", "राष्ट्रपति", "politics", "election"),
    "खेलकुद": ("खेल", "क्रिकेट", "फुटबल", "खेलाडी", "गोल", "प्रतियोगिता", "sports", "cricket", "football"),
    "अर्थतन्त्र": ("अर्थतन्त्र", "बजेट", "बैंक", "बजार", "महँगी", "मूल्य", "व्यवसाय", "आर्थिक", "economy", "business"),
    "शिक्षा": ("शिक्षा", "विद्यालय", "परीक्षा", "विद्यार्थी", "विश्वविद्यालय", "शिक्षक", "education", "exam", "school"),
    "प्रविधि": ("प्रविधि", "विज्ञान", "कृत्रिम बुद्धिमत्ता", "एआई", "technology", "science", "artificial intelligence", "AI"),
    "अन्तर्राष्ट्रिय": ("अन्तर्राष्ट्रिय", "विश्व", "अमेरिका", "भारत", "चीन", "international", "world"),
}


BREAKING_KEYWORDS = (
    "भूकम्प", "बाढी", "पहिरो", "आगलागी", "दुर्घटना", "विस्फोट",
    "आपत्कालीन", "आपतकाल", "आकस्मिक", "तत्काल", "बेपत्ता", "उद्धार",
    "राजीनामा", "युद्ध", "आक्रमण", "earthquake", "flood", "landslide",
    "fire", "accident", "explosion", "emergency", "breaking", "resigns",
    "missing", "rescue", "attack",
)


def detect_breaking_news(title, description="", source_name="", published_timestamp=None, now=None):
    """Conservative heuristic: fresh urgent wording from a higher-priority source."""
    combined = (str(title or "") + " " + str(description or "")).casefold()
    matched = [word for word in BREAKING_KEYWORDS if word.casefold() in combined]
    if not matched or source_priority(source_name) < 85 or published_timestamp is None:
        return False, ""
    current_time = float(now if now is not None else datetime.now(timezone.utc).timestamp())
    age_seconds = current_time - float(published_timestamp)
    if age_seconds < 0 or age_seconds > 2 * 3600:
        return False, ""
    return True, "Urgent keyword and publication within the last 2 hours; verify the original report."


def source_priority(source_name):
    """Return the highest configured editorial priority matching a publisher/feed."""
    normalized = clean_text(source_name, 200).casefold()
    matches = [score for name, score in SOURCE_PRIORITY.items() if name in normalized]
    return max(matches, default=40)


def source_priority_label(source_name):
    score = source_priority(source_name)
    return "A · प्राथमिक" if score >= 100 else "B · नियमित" if score >= 80 else "C · थप खोज"


def classify_story(title, description=""):
    """Assign broad topic labels using transparent keyword matching."""
    text = (str(title or "") + " " + str(description or "")).casefold()
    return [
        category for category, keywords in TOPIC_KEYWORDS.items()
        if any(keyword.casefold() in text for keyword in keywords)
    ] or ["अन्य"]


def _story_priority(item, feed_name):
    """Rank a candidate using publisher priority, topical signals and freshness."""
    raw_title = clean_text(item.get("title"), 300)
    publisher, headline = _publisher_from_google_title(raw_title, feed_name)
    timestamp = _entry_timestamp(item)
    age_hours = max(0.0, (datetime.now(timezone.utc).timestamp() - timestamp) / 3600) if timestamp else 168.0
    freshness_score = max(0, 24 - int(age_hours))
    topic_score = min(20, 5 * len(classify_story(headline, item.get("summary") or item.get("description") or "")))
    return (source_priority(publisher), topic_score, freshness_score)


def configured_feeds():
    """Return custom approved RSS feeds plus Google News discovery feeds."""
    feeds = list(DIRECT_NEWS_FEEDS)
    raw = os.getenv("NEWS_FEEDS", "")
    allowed = (
        "neb", "routine of nepal banda", "ronb", "troll cricket",
        "bbc nepali", "onlinekhabar", "setopati", "ratopati",
        "kantipur", "ekantipur", "annapurna", "nagarik", "gorkhapatra",
        "ujyaalo", "ujyalo",
        "thaha khabar", "nepal samaya", "ok khabar", "okkhabar",
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


def check_feeds(on_new_draft=None):
    feeds = configured_feeds()
    result = {
        "feeds_configured": len(feeds), "feeds_checked": 0, "new_items": 0,
        "drafts_saved": 0, "published": 0, "already_seen": 0, "breaking_candidates": 0, "errors": [],
        "ai_requests": 0, "ai_writes": 0, "ai_limit_reached": False,
        "stale_skipped": 0, "undated_skipped": 0, "fresh_candidates": 0,
        "feed_details": [],
    }
    if not feeds:
        result["errors"].append("No RSS feeds configured.")
        return result

    per_feed_limit = max(1, min(int(os.getenv("MAX_STORIES_PER_FEED", "2")), 5))
    freshness_hours = max(1, min(int(os.getenv("NEWS_MAX_AGE_HOURS", "36")), 168))
    ai_request_limit = max(1, min(int(os.getenv("MAX_AI_REQUESTS_PER_SCAN", "5")), 5))
    usage_at_start = get_groq_usage_status()
    ai_paused_for_scan = bool(usage_at_start["paused"] or not usage_at_start["requests_remaining"] or not usage_at_start["tokens_remaining"] or not usage_at_start["posts_remaining"])
    if ai_paused_for_scan:
        result["ai_budget_paused"] = True
        result["ai_budget_reason"] = usage_at_start.get("pause_reason") or "Daily NayaDrishti safety budget reached."

    # Check higher-priority publisher feeds first; retain every configured feed.
    feeds.sort(key=lambda feed: source_priority(feed[0]), reverse=True)
    for feed_name, feed_url in feeds:
        if result["ai_requests"] >= ai_request_limit:
            result["ai_limit_reached"] = True
        detail = {
            "source": feed_name, "entries_found": 0, "new_items": 0,
            "already_seen": 0, "stale_skipped": 0, "undated_skipped": 0,
            "fresh_candidates": 0, "error": None, "story_errors": 0,
            "priority": source_priority_label(feed_name),
        }
        try:
            parsed = fetch_feed(feed_url)
            result["feeds_checked"] += 1
            entries = sorted(
                parsed.entries,
                key=lambda entry: _story_priority(entry, feed_name),
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
                if already_seen(item_id) or already_seen_title(title) or already_seen_story(title, article_url):
                    detail["already_seen"] += 1
                    result["already_seen"] += 1
                    continue

                if result["ai_requests"] >= ai_request_limit or ai_paused_for_scan:
                    if result["ai_requests"] >= ai_request_limit:
                        result["ai_limit_reached"] = True
                    continue

                article_text = fetch_article_text(article_url, rss_description)
                if len(article_text) < 80:
                    log.info("Skipping story with too little source text: %s", title)
                    continue

                result["ai_requests"] += 1
                try:
                    post_text = write_summary(
                        title=title,
                        description=article_text,
                        source_name=publisher,
                        article_url=article_url,
                    )
                    result["ai_writes"] += 1
                except Exception as exc:
                    detail["story_errors"] += 1
                    result["errors"].append(
                        f"{publisher} summary failed: {type(exc).__name__}: {str(exc)[:140]}"
                    )
                    log.warning("AI summary failed for %s (%s): %s", publisher, title[:100], exc)
                    if "GROQ_BUDGET_PAUSED" in str(exc):
                        result["ai_requests"] = max(0, result["ai_requests"] - 1)
                        ai_paused_for_scan = True
                        result["ai_budget_paused"] = True
                        result["ai_budget_reason"] = str(exc)[:220]
                    elif "Groq API error 429" in str(exc) or "Groq API error 413" in str(exc):
                        ai_paused_for_scan = True
                        result["ai_provider_paused"] = True
                    continue

                verification = assess_cross_source_coverage(title, publisher)
                category = classify_story(title, article_text)[0]
                is_breaking, breaking_reason = detect_breaking_news(
                    title, article_text, publisher, _entry_timestamp(entry)
                )
                if is_breaking:
                    result["breaking_candidates"] += 1
                save_draft(
                    item_id, title, publisher, article_url, post_text,
                    language="ne", verification=verification, category=category,
                    is_breaking=is_breaking, breaking_reason=breaking_reason,
                )
                result["drafts_saved"] += 1
                if on_new_draft:
                    try:
                        on_new_draft({"title": title, "source": publisher})
                    except Exception:
                        log.exception("Could not publish live dashboard progress")

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

    result["groq_usage"] = get_groq_usage_status()
    result["facebook_publishing_enabled"] = (
        os.getenv("ENABLE_FACEBOOK_PUBLISHING", "false").strip().lower() == "true"
    )
    result["note"] = (
        "Text-only Nepali news. No images are fetched, generated, stored, or published. "
        "The article text is preferred over RSS teasers, duplicate headlines are skipped, "
        "publisher feeds are checked by editorial priority, and story candidates are ranked by publisher priority, topic signals and freshness. "
        "Priority is a discovery aid, not a guarantee of accuracy. Cross-source labels identify similar headlines from different publishers, not verified facts; differing numbers are flagged for manual comparison."
    )
    log.info(
        "News scan complete: checked=%s/%s new=%s drafts=%s published=%s duplicates=%s errors=%s",
        result["feeds_checked"], result["feeds_configured"], result["new_items"],
        result["drafts_saved"], result["published"], result["already_seen"],
        len(result["errors"]),
    )
    return result
