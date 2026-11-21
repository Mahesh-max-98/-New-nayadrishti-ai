import hashlib
import html
import logging
import os
import re

import feedparser
import requests

from ai_writer import write_summary
from database import already_seen, remember, save_draft, set_draft_status
from facebook_publisher import publish_image
from image_generator import generate_news_image

log = logging.getLogger("nayadrishti")
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))


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
        "feeds_configured": len(feeds), "feeds_checked": 0, "new_items": 0,
        "drafts_saved": 0, "published": 0, "already_seen": 0, "errors": [],
    }
    if not feeds:
        result["errors"].append("NEWS_FEEDS is empty or invalid. Add RSS/Atom feed URLs in Render.")
        return result

    for source_name, feed_url in feeds:
        try:
            parsed = fetch_feed(feed_url)
            result["feeds_checked"] += 1
            for item in parsed.entries[:10]:
                title = clean_text(item.get("title"), 300)
                link = (item.get("link") or "").strip()
                description = clean_text(item.get("summary") or item.get("description") or "")
                if not title or not link:
                    continue
                item_id = hashlib.sha256((source_name + "|" + link).encode("utf-8")).hexdigest()
                if already_seen(item_id):
                    result["already_seen"] += 1
                    continue

                summary = write_summary(title=title, description=description,
                                        source_name=source_name, article_url=link)
                if not summary:
                    raise ValueError("Groq returned an empty news post.")

                image_data = None
                image_status = "failed"
                try:
                    image_data = generate_news_image(title, description)
                    image_status = "ready"
                except Exception as image_exc:
                    log.exception("Image generation failed for %s", source_name)
                    result["errors"].append(
                        f"{source_name} image: {type(image_exc).__name__}: {str(image_exc)[:180]}"
                    )

                # Always preserve the text draft. Never publish without its image.
                save_draft(item_id, title, source_name, link, summary,
                           image_data=image_data, image_status=image_status)
                result["drafts_saved"] += 1

                if image_data:
                    publish_result = publish_image(summary, image_data)
                    if publish_result.get("published"):
                        result["published"] += 1
                        status = "published"
                    else:
                        status = "draft"
                else:
                    status = "draft"

                set_draft_status(item_id, status)
                remember(item_id, title, source_name, link, status)
                result["new_items"] += 1
                break
        except Exception as exc:
            log.exception("Feed processing failed for %s", source_name)
            result["errors"].append(f"{source_name}: {type(exc).__name__}: {str(exc)[:220]}")

    result["facebook_publishing_enabled"] = (
        os.getenv("ENABLE_FACEBOOK_PUBLISHING", "false").lower() == "true"
    )
    result["note"] = (
        "Each new draft gets a Cloudflare-generated branded image. "
        "Text-only drafts are saved but never auto-published. "
        "At most one new item per feed is processed during each check."
    )
    return result
