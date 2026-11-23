import os
from contextlib import contextmanager

import psycopg
from psycopg.rows import dict_row

DATABASE_URL = os.getenv("DATABASE_URL")


@contextmanager
def connection():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not configured.")
    conn = psycopg.connect(DATABASE_URL, row_factory=dict_row)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    with connection() as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS seen_items (
                item_id TEXT PRIMARY KEY,
                title TEXT,
                source TEXT,
                url TEXT,
                status TEXT DEFAULT 'seen',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS drafts (
                id BIGSERIAL PRIMARY KEY,
                item_id TEXT UNIQUE,
                title TEXT NOT NULL,
                source TEXT,
                url TEXT,
                summary TEXT NOT NULL,
                language TEXT DEFAULT 'ne',
                status TEXT DEFAULT 'draft',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # Remove legacy image storage so the application is fully text-only.
        db.execute("ALTER TABLE drafts DROP COLUMN IF EXISTS image_data")
        db.execute("ALTER TABLE drafts DROP COLUMN IF EXISTS image_status")
        db.execute("ALTER TABLE drafts DROP COLUMN IF EXISTS image_error")
        db.execute("ALTER TABLE drafts DROP COLUMN IF EXISTS image_last_attempt")
        db.execute("ALTER TABLE drafts ADD COLUMN IF NOT EXISTS verification_status TEXT DEFAULT 'single_source'")
        db.execute("ALTER TABLE drafts ADD COLUMN IF NOT EXISTS verification_count INTEGER DEFAULT 1")
        db.execute("ALTER TABLE drafts ADD COLUMN IF NOT EXISTS verification_sources TEXT DEFAULT ''")
        db.execute("ALTER TABLE drafts ADD COLUMN IF NOT EXISTS verification_note TEXT DEFAULT 'Only one matching publisher report was found.'")


def already_seen(item_id):
    with connection() as db:
        return db.execute(
            "SELECT 1 FROM seen_items WHERE item_id=%s", (item_id,)
        ).fetchone() is not None


def already_seen_title(title):
    normalized = (title or "").strip()
    if not normalized:
        return False
    with connection() as db:
        return db.execute(
            "SELECT 1 FROM seen_items WHERE lower(trim(title))=lower(trim(%s)) LIMIT 1",
            (normalized,),
        ).fetchone() is not None


def remember(item_id, title, source, url, status="seen"):
    with connection() as db:
        db.execute("""
            INSERT INTO seen_items (item_id, title, source, url, status)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT(item_id) DO UPDATE SET status=EXCLUDED.status
        """, (item_id, title, source, url, status))


def save_draft(item_id, title, source, url, summary, language="ne", verification=None):
    verification = verification or {}
    with connection() as db:
        db.execute("""
            INSERT INTO drafts (
                item_id, title, source, url, summary, language, status,
                verification_status, verification_count, verification_sources, verification_note
            )
            VALUES (%s, %s, %s, %s, %s, %s, 'draft', %s, %s, %s, %s)
            ON CONFLICT(item_id) DO NOTHING
        """, (
            item_id, title, source, url, summary, language,
            verification.get("status", "single_source"),
            verification.get("count", 1),
            verification.get("sources", source),
            verification.get("note", "Only one matching publisher report was found."),
        ))


def get_drafts(limit=50):
    with connection() as db:
        rows = db.execute("""
            SELECT item_id, title, source, url, summary, language, status, created_at, verification_status, verification_count, verification_sources, verification_note
            FROM drafts
            WHERE status='draft'
            ORDER BY created_at DESC
            LIMIT %s
        """, (limit,)).fetchall()
        return [dict(row) for row in rows]


def set_draft_status(item_id, status):
    with connection() as db:
        db.execute("UPDATE drafts SET status=%s WHERE item_id=%s AND status=%s", (status, item_id, "draft"))



def _normalize_story_title(title):
    """Normalize punctuation and spacing while preserving Nepali and English words."""
    import re
    value = (title or "").casefold()
    value = re.sub(r"[^\w]+", " ", value, flags=re.UNICODE)
    return " ".join(value.split())


def _normalize_story_url(url):
    """Ignore fragments and common tracking parameters when comparing article URLs."""
    from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
    try:
        parts = urlsplit((url or "").strip())
        if not parts.scheme or not parts.netloc:
            return (url or "").strip().rstrip("/")
        query = [
            (key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True)
            if not key.lower().startswith("utm_")
            and key.lower() not in {"fbclid", "gclid", "mc_cid", "mc_eid", "ref", "source"}
        ]
        host = parts.netloc.lower().removeprefix("www.")
        path = parts.path.rstrip("/") or "/"
        return urlunsplit((parts.scheme.lower(), host, path, urlencode(query), ""))
    except Exception:
        return (url or "").strip().rstrip("/")


def already_seen_story(title, url, similarity_threshold=0.90):
    """Catch duplicate stories shared across feeds, including slightly edited headlines."""
    from difflib import SequenceMatcher

    normalized_title = _normalize_story_title(title)
    normalized_url = _normalize_story_url(url)
    if not normalized_title:
        return False
    with connection() as db:
        rows = db.execute("""
            SELECT title, url FROM seen_items
            ORDER BY created_at DESC
            LIMIT 500
        """).fetchall()

    for row in rows:
        previous_url = _normalize_story_url(row.get("url"))
        if normalized_url and previous_url and normalized_url == previous_url:
            return True
        previous_title = _normalize_story_title(row.get("title"))
        if len(normalized_title) >= 18 and len(previous_title) >= 18:
            if SequenceMatcher(None, normalized_title, previous_title).ratio() >= similarity_threshold:
                return True
    return False


def assess_cross_source_coverage(title, source, limit=500):
    """Find similar recent headlines from distinct publishers; this is not fact-checking."""
    import re
    from difflib import SequenceMatcher

    def tokens(value):
        return {token for token in re.findall(r"[\w]+", (value or "").casefold(), flags=re.UNICODE) if len(token) >= 3}

    current = tokens(title)
    if not current:
        return {"status": "single_source", "count": 1, "sources": source,
                "note": "No comparable headline terms were available; only this report is counted."}

    with connection() as db:
        rows = db.execute("""
            SELECT title, source FROM seen_items
            WHERE lower(coalesce(source, '')) <> lower(%s)
            ORDER BY created_at DESC LIMIT %s
        """, (source or "", limit)).fetchall()

    matches = {}
    numeric_pattern = re.compile(r"\d+(?:[.,]\d+)?")
    current_numbers = set(numeric_pattern.findall(title or ""))
    possible_conflict = False
    for row in rows:
        other_title = row.get("title") or ""
        other_source = (row.get("source") or "").strip()
        if not other_source or other_source.casefold() == (source or "").casefold():
            continue
        other = tokens(other_title)
        if not other:
            continue
        overlap = len(current & other) / max(1, len(current | other))
        similarity = SequenceMatcher(None, " ".join(sorted(current)), " ".join(sorted(other))).ratio()
        if overlap >= 0.35 or similarity >= 0.68:
            other_numbers = set(numeric_pattern.findall(other_title))
            if current_numbers and other_numbers and current_numbers != other_numbers:
                possible_conflict = True
            matches[other_source] = other_title

    sources = sorted(matches)
    count = 1 + len(sources)
    if possible_conflict:
        return {"status": "needs_comparison", "count": count, "sources": ", ".join([source] + sources),
                "note": "Similar headlines contain different numeric details. Compare original reports manually; no conclusion has been drawn."}
    if sources:
        return {"status": "matching_coverage", "count": count, "sources": ", ".join([source] + sources),
                "note": "Similar headlines found from other publishers. This is corroborating coverage, not proof that every claim is correct."}
    return {"status": "single_source", "count": 1, "sources": source,
            "note": "No sufficiently similar report from another publisher was found in the recent collected headlines."}
