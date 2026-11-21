import hashlib
import html
import logging
import os
import re
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

import feedparser
import requests

from ai_writer import write_summary
from database import already_seen, remember, save_draft, set_draft_status
from facebook_publisher import publish_image
from image_generator import create_publisher_news_card, generate_news_image

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
        "publisher_photos_used": 0, "ai_illustrations_used": 0,
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
                    publisher_photo = fetch_permitted_publisher_photo(item, link)
                    if publisher_photo:
                        photo_bytes, _photo_url = publisher_photo
                        image_data = create_publisher_news_card(photo_bytes, title, summary)
                        image_status = "publisher_photo"
                        result["publisher_photos_used"] += 1
                    else:
                        image_data = generate_news_image(title, description, summary)
                        image_status = "ai_illustration"
                        result["ai_illustrations_used"] += 1
                except Exception as image_exc:
                    log.exception("Image creation failed for %s", source_name)
                    result["errors"].append(
                        f"{source_name} image: {type(image_exc).__name__}: {str(image_exc)[:180]}"
                    )

                # A draft is saved even when image creation fails; image-less drafts
                # are never auto-published.
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
        "Publisher photos are used only when their article and image hosts are explicitly "
        "allowlisted in PUBLISHER_IMAGE_ALLOWED_DOMAINS. Otherwise a story-specific AI "
        "illustration is used. Review reuse rights before allowlisting any publisher domain."
    )
    return result
