
import os
import hashlib
import logging
import feedparser

from database import already_seen, remember, save_draft
from ai_writer import write_summary
from facebook_publisher import publish_text

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("nayadrishti")


def configured_feeds():
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
    drafts_saved = 0
    errors = []

    for source_name, feed_url in configured_feeds():
        try:
            feed = feedparser.parse(feed_url)

            if feed.bozo and not feed.entries:
                raise ValueError(str(feed.bozo_exception))

            checked += 1

            # Process only ONE article per feed check.
            for item in feed.entries[:1]:
                title = (item.get("title") or "").strip()
                link = (item.get("link") or "").strip()

                description = (
                    item.get("summary")
                    or item.get("description")
                    or ""
                ).strip()

                if not title or not link:
                    continue

                item_id = hashlib.sha256(
                    (source_name + "|" + link).encode("utf-8")
                ).hexdigest()

                if already_seen(item_id):
                    continue

                try:
                    log.info("Generating Gemini summary: %s", title)

                    post = write_summary(
                        title,
                        description,
                        source_name,
                        link
                    )

                    if not post:
                        raise ValueError("AI returned an empty summary")

                    save_draft(
                        item_id,
                        title,
                        source_name,
                        link,
                        post
                    )

                    result = publish_text(post, link)

                    if result.get("published"):
                        published += 1
                        status = "published"
                    else:
                        drafts_saved += 1
                        status = "draft"

                    remember(
                        item_id,
                        title,
                        source_name,
                        link,
                        status
                    )

                    new_items += 1

                except Exception as exc:
                    log.exception("Article processing failed: %s", link)
                    errors.append(
                        f"{source_name}: {type(exc).__name__}: {str(exc)[:200]}"
                    )

        except Exception as exc:
            log.exception("Feed failed: %s", feed_url)
            errors.append(
                f"{source_name}: {type(exc).__name__}: {str(exc)[:200]}"
            )

    return {
        "feeds_checked": checked,
        "new_items": new_items,
        "drafts_saved": drafts_saved,
        "published": published,
        "errors": errors,
        "note": "Only one article per feed is processed per check. Facebook publishing remains controlled by its environment setting."
    }
    
