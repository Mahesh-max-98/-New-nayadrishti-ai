import hashlib
import html
import logging
import os
import re

import feedparser
import requests

from ai_writer import write_summary
from database import already_seen, remember, save_draft
from facebook_publisher import publish_text

log = logging.getLogger("nayadrishti")
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))


def configured_feeds():
    """Read feeds as Source Name=https://feed-url|Another Source=https://..."""
    raw = os.getenv("NEWS_FEEDS", "")
    feeds = []
    for part in raw.split("|"):
        if "=" not in part:
            continue
        name, url = part.split("=", 1)
        name, url = name.strip(), url.strip()
        if name and url.startswith(("https://", "http://")):
            feeds.append((name, url))
    return feeds


def clean_text(value, max_length=5000):
    value = re.sub(r"<[^>]+>", " ", value or "")
    value = html.unescape(value)
    return re.sub(r"\s+", " ", value).strip()[:max_length]


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


def check_feeds():
    feeds = configured_feeds()
    result = {
        "feeds_configured": len(feeds),
        "feeds_checked": 0,
        "new_items": 0,
        "drafts_saved": 0,
        "published": 0,
        "already_seen": 0,
        "errors": [],
    }

    if not feeds:
        result["errors"].append(
            "NEWS_FEEDS is empty or invalid. Add RSS/Atom feed URLs in Render."
        )
        return result

    for source_name, feed_url in feeds:
        try:
            parsed = fetch_feed(feed_url)
            result["feeds_checked"] += 1

            # Feed order is normally newest first. Try up to 10 recent entries
            # and process at most one unseen entry from each source per check.
            for item in parsed.entries[:10]:
                title = clean_text(item.get("title"), 300)
                link = (item.get("link") or "").strip()
                description = clean_text(
                    item.get("summary") or item.get("description") or ""
                )

                if not title or not link:
                    continue

                item_id = hashlib.sha256(
                    (source_name + "|" + link).encode("utf-8")
                ).hexdigest()

                if already_seen(item_id):
                    result["already_seen"] += 1
                    continue

                summary = write_summary(
                    title=title,
                    description=description,
                    source_name=source_name,
                    article_url=link,
                )
                if not summary:
                    raise ValueError("Groq returned an empty news post.")

                # Save draft before attempting publication so content is not lost.
                save_draft(item_id, title, source_name, link, summary)
                result["drafts_saved"] += 1

                publish_result = publish_text(summary, link)
                if publish_result.get("published"):
                    result["published"] += 1
                    status = "published"
                else:
                    status = "draft"

                remember(item_id, title, source_name, link, status)
                result["new_items"] += 1
                break

        except Exception as exc:
            log.exception("Feed processing failed for %s", source_name)
            result["errors"].append(
                f"{source_name}: {type(exc).__name__}: {str(exc)[:240]}"
            )

    result["facebook_publishing_enabled"] = (
        os.getenv("ENABLE_FACEBOOK_PUBLISHING", "false").lower() == "true"
    )
    result["note"] = (
        "At most one new item per feed is processed during each manual check. "
        "Only RSS/Atom feeds are supported; a Facebook Page URL is not a feed."
    )
    return result
