import os
import hashlib
import logging
import feedparser
from database import already_seen, remember
from ai_writer import write_summary
from facebook_publisher import publish_text

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("nayadrishti")

def configured_feeds():
    # Format: Source Name=https://example.com/rss.xml|Another Source=https://example.org/feed
    raw = os.getenv("NEWS_FEEDS", "")
    feeds = []
    for entry in raw.split("|"):
        if "=" not in entry:
            continue
        name, url = entry.split("=", 1)
        if name.strip() and url.strip():
            feeds.append((name.strip(), url.strip()))
    return feeds

def check_feeds():
    checked = 0
    new_items = 0
    published = 0
    errors = []
    for source_name, feed_url in configured_feeds():
        try:
            feed = feedparser.parse(feed_url)
            if feed.bozo and not feed.entries:
                raise ValueError(str(feed.bozo_exception))
            for item in feed.entries[:20]:
                title = (item.get("title") or "").strip()
                link = (item.get("link") or "").strip()
                summary = (item.get("summary") or item.get("description") or "").strip()
                if not title or not link:
                    continue
                item_id = hashlib.sha256((source_name + "|" + link).encode()).hexdigest()
                if already_seen(item_id):
                    continue
                new_items += 1
                # Mark first to prevent repeated attempts if an API call fails.
                remember(item_id, title, source_name, link, "processing")
                try:
                    post = write_summary(title, summary, source_name, link)
                    result = publish_text(post, link)
                    status = "published" if result.get("published") else "draft"
                    remember(item_id + ":result", title, source_name, link, status)
                    if result.get("published"):
                        published += 1
                except Exception as exc:
                    log.exception("Could not process item: %s", link)
                    errors.append(f"{source_name}: {type(exc).__name__}")
            checked += 1
        except Exception as exc:
            log.exception("Feed failed: %s", feed_url)
            errors.append(f"{source_name}: {type(exc).__name__}")
    return {
        "feeds_checked": checked,
        "new_items": new_items,
        "published": published,
        "errors": errors,
        "note": "If publishing is disabled, generated items are marked as drafts; review/logging workflow is not yet implemented."
    }
