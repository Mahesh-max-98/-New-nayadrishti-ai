import os
from contextlib import contextmanager

import psycopg
from psycopg.rows import dict_row

from story_quality import normalize_story_title as _normalize_story_title, similar_story_titles

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
        # Preserve legacy columns and records; schema updates below are additive only.
        db.execute("ALTER TABLE drafts ADD COLUMN IF NOT EXISTS verification_status TEXT DEFAULT 'single_source'")
        db.execute("ALTER TABLE drafts ADD COLUMN IF NOT EXISTS verification_count INTEGER DEFAULT 1")
        db.execute("ALTER TABLE drafts ADD COLUMN IF NOT EXISTS verification_sources TEXT DEFAULT ''")
        db.execute("ALTER TABLE drafts ADD COLUMN IF NOT EXISTS verification_note TEXT DEFAULT 'Only one matching publisher report was found.'")
        db.execute("ALTER TABLE drafts ADD COLUMN IF NOT EXISTS category TEXT DEFAULT 'अन्य'")
        db.execute("ALTER TABLE drafts ADD COLUMN IF NOT EXISTS is_breaking BOOLEAN DEFAULT FALSE")
        db.execute("ALTER TABLE drafts ADD COLUMN IF NOT EXISTS breaking_reason TEXT DEFAULT ''")
        db.execute("ALTER TABLE drafts ADD COLUMN IF NOT EXISTS article_text TEXT DEFAULT ''")
        db.execute("""
            CREATE TABLE IF NOT EXISTS pending_stories (
                item_id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT '',
                url TEXT NOT NULL,
                article_text TEXT NOT NULL DEFAULT '',
                published_at TIMESTAMPTZ,
                status TEXT NOT NULL DEFAULT 'pending',
                attempts INTEGER NOT NULL DEFAULT 0,
                next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                expires_at TIMESTAMPTZ NOT NULL,
                last_error TEXT NOT NULL DEFAULT '',
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        db.execute("CREATE INDEX IF NOT EXISTS pending_stories_due_idx ON pending_stories(status, next_attempt_at, expires_at)")
        db.execute("""
            CREATE TABLE IF NOT EXISTS facebook_publications (
                item_id TEXT PRIMARY KEY,
                status TEXT NOT NULL CHECK (status IN ('publishing','published','failed','unknown')),
                post_id TEXT,
                error_code TEXT,
                error_message TEXT NOT NULL DEFAULT '',
                attempts INTEGER NOT NULL DEFAULT 1,
                started_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                finished_at TIMESTAMPTZ,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        db.execute("CREATE INDEX IF NOT EXISTS facebook_publications_status_idx ON facebook_publications(status, updated_at DESC)")
        db.execute("""
            CREATE TABLE IF NOT EXISTS facebook_publishing_control (
                id SMALLINT PRIMARY KEY CHECK (id = 1),
                auth_blocked BOOLEAN NOT NULL DEFAULT FALSE,
                reason TEXT NOT NULL DEFAULT '',
                updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS news_source_health (
                source_name TEXT PRIMARY KEY,
                status TEXT NOT NULL DEFAULT 'unknown',
                consecutive_failures INTEGER NOT NULL DEFAULT 0,
                last_checked_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                last_success_at TIMESTAMPTZ,
                last_http_status INTEGER,
                last_entries_found INTEGER NOT NULL DEFAULT 0,
                last_duration_ms INTEGER NOT NULL DEFAULT 0,
                last_error TEXT NOT NULL DEFAULT ''
            )
        """)
        # Preserve the previous authentication failure as a persistent safety stop.
        db.execute("""
            INSERT INTO facebook_publishing_control (id, auth_blocked, reason)
            SELECT 1, TRUE, 'Facebook publishing paused after a previous Meta authentication error. Run the connection check.'
            WHERE EXISTS (
                SELECT 1 FROM facebook_publications
                WHERE status='failed' AND error_code LIKE '190%'
            )
            ON CONFLICT (id) DO NOTHING
        """)


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


def save_draft(item_id, title, source, url, summary, language="ne", verification=None, category="अन्य", is_breaking=False, breaking_reason="", article_text=""):
    verification = verification or {}
    with connection() as db:
        db.execute("""
            INSERT INTO drafts (
                item_id, title, source, url, summary, language, status, article_text,
                verification_status, verification_count, verification_sources, verification_note,
                category, is_breaking, breaking_reason
            )
            VALUES (%s, %s, %s, %s, %s, %s, 'draft', %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT(item_id) DO NOTHING
        """, (
            item_id, title, source, url, summary, language, (article_text or "")[:7000],
            verification.get("status", "single_source"),
            verification.get("count", 1),
            verification.get("sources", source),
            verification.get("note", "Only one matching publisher report was found."),
            category, bool(is_breaking), breaking_reason,
        ))


def get_drafts(limit=50):
    with connection() as db:
        rows = db.execute("""
            SELECT item_id, title, source, url, summary, article_text, language, status, created_at, verification_status, verification_count, verification_sources, verification_note, category, is_breaking, breaking_reason
            FROM drafts
            WHERE status='draft'
            ORDER BY is_breaking DESC, created_at DESC
            LIMIT %s        """, (limit,)).fetchall()
        return [dict(row) for row in rows]


def update_pending_draft_summary(item_id, summary):
    """Update only the editable post text of a draft that is still awaiting review."""
    with connection() as db:
        row = db.execute("""
            UPDATE drafts
            SET summary=%s
            WHERE item_id=%s AND status='draft'
            RETURNING item_id
        """, (summary, item_id)).fetchone()
    return row is not None


def set_draft_status(item_id, status):
    with connection() as db:
        db.execute("UPDATE drafts SET status=%s WHERE item_id=%s AND status=%s", (status, item_id, "draft"))



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


def already_seen_story(title, url, similarity_threshold=0.88):
    """Catch duplicate stories across seen, saved-draft and pending records."""
    normalized_url = _normalize_story_url(url)
    normalized_title = _normalize_story_title(title)
    if not normalized_title:
        return False
    with connection() as db:
        rows = db.execute("""
            SELECT title, url, created_at FROM seen_items
            UNION ALL
            SELECT title, url, created_at FROM drafts
            UNION ALL
            SELECT title, url, created_at FROM pending_stories
            WHERE status='pending'
            ORDER BY created_at DESC
            LIMIT 1000
        """).fetchall()

    for row in rows:
        previous_url = _normalize_story_url(row.get("url"))
        if normalized_url and previous_url and normalized_url == previous_url:
            return True
        if similar_story_titles(title, row.get("title"), similarity_threshold):
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
            "note": "No sufficiently similar report from another publisher was found in the recent collected headlines. This is not proof that the story is false or verified."}


def enqueue_pending_story(item_id, title, source, url, article_text, published_at=None):
    """Persist an unprocessed story so provider outages never lose it."""
    from datetime import timedelta

    try:
        retention_hours = int(os.getenv("PENDING_NEWS_RETENTION_HOURS", "168"))
    except (TypeError, ValueError):
        retention_hours = 168
    retention_hours = max(24, min(retention_hours, 336))
    with connection() as db:
        db.execute("""
            INSERT INTO pending_stories (
                item_id, title, source, url, article_text, published_at,
                status, attempts, next_attempt_at, expires_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, 'pending', 0, CURRENT_TIMESTAMP,
                    CURRENT_TIMESTAMP + (%s * INTERVAL '1 hour'))
            ON CONFLICT(item_id) DO UPDATE SET
                article_text=CASE WHEN length(EXCLUDED.article_text) > length(pending_stories.article_text)
                                  THEN EXCLUDED.article_text ELSE pending_stories.article_text END,
                updated_at=CURRENT_TIMESTAMP
            WHERE pending_stories.status='pending'
        """, (
            item_id, title[:300], source[:120], url[:2000],
            (article_text or "")[:7000], published_at, retention_hours,
        ))


def already_pending_story(title, url, similarity_threshold=0.90):
    """Avoid queuing the same report again from another RSS discovery feed."""
    from difflib import SequenceMatcher

    normalized_title = _normalize_story_title(title)
    normalized_url = _normalize_story_url(url)
    if not normalized_title:
        return False
    with connection() as db:
        rows = db.execute("""
            SELECT title, url FROM pending_stories
            WHERE status='pending'
            ORDER BY created_at DESC LIMIT 500
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


def get_due_pending_stories(limit=5):
    """Return queued stories whose next retry time has arrived."""
    with connection() as db:
        db.execute("""
            UPDATE pending_stories
            SET status='expired', article_text='', updated_at=CURRENT_TIMESTAMP
            WHERE status='pending' AND expires_at <= CURRENT_TIMESTAMP
        """)
        db.execute("""
            DELETE FROM pending_stories
            WHERE status IN ('completed', 'expired')
              AND updated_at < CURRENT_TIMESTAMP - INTERVAL '30 days'
        """)
        rows = db.execute("""
            SELECT item_id, title, source, url, article_text, published_at,
                   attempts, created_at, expires_at
            FROM pending_stories
            WHERE status='pending'
              AND next_attempt_at <= CURRENT_TIMESTAMP
              AND expires_at > CURRENT_TIMESTAMP
            ORDER BY created_at ASC
            LIMIT %s
        """, (max(1, min(int(limit), 25)),)).fetchall()
        return [dict(row) for row in rows]


def pending_retry_delay_seconds(attempt_number):
    """Return an exponential retry delay, capped at one day."""
    delays = (900, 1800, 3600, 7200, 14400, 28800, 43200, 86400)
    try:
        attempt = max(1, int(attempt_number))
    except (TypeError, ValueError):
        attempt = 1
    return delays[min(attempt - 1, len(delays) - 1)]


def retry_pending_story(item_id, error=""):
    """Schedule a progressively slower retry, up to once every 24 hours."""
    with connection() as db:
        row = db.execute("""
            SELECT attempts, expires_at
            FROM pending_stories
            WHERE item_id=%s AND status='pending'
            FOR UPDATE
        """, (item_id,)).fetchone()
        if not row:
            return
        attempts = int(row["attempts"]) + 1
        delay = pending_retry_delay_seconds(attempts)
        db.execute("""
            UPDATE pending_stories
            SET attempts=%s,
                next_attempt_at=CURRENT_TIMESTAMP + (%s * INTERVAL '1 second'),
                status=CASE
                    WHEN expires_at <= CURRENT_TIMESTAMP + (%s * INTERVAL '1 second')
                    THEN 'expired' ELSE 'pending' END,
                article_text=CASE
                    WHEN expires_at <= CURRENT_TIMESTAMP + (%s * INTERVAL '1 second')
                    THEN '' ELSE article_text END,
                last_error=%s,
                updated_at=CURRENT_TIMESTAMP
            WHERE item_id=%s
        """, (attempts, delay, delay, delay, (error or "")[:500], item_id))


def complete_pending_story(item_id):
    """Mark a story completed and erase its temporary source-text copy."""
    with connection() as db:
        db.execute("""
            UPDATE pending_stories
            SET status='completed', article_text='', last_error='',
                updated_at=CURRENT_TIMESTAMP
            WHERE item_id=%s AND status='pending'
        """, (item_id,))


def get_pending_queue_status():
    """Return safe counts for the admin dashboard."""
    with connection() as db:
        row = db.execute("""
            SELECT
                COUNT(*) FILTER (WHERE status='pending') AS pending,
                COUNT(*) FILTER (WHERE status='pending' AND next_attempt_at <= CURRENT_TIMESTAMP) AS due,
                COUNT(*) FILTER (WHERE status='expired') AS expired
            FROM pending_stories
        """).fetchone()
    return {key: int(value or 0) for key, value in row.items()}


def expire_pending_story(item_id, reason="Story became too old to publish safely."):
    """Retain the audit status but remove temporary source text."""
    with connection() as db:
        db.execute("""
            UPDATE pending_stories
            SET status='expired', article_text='', last_error=%s,
                updated_at=CURRENT_TIMESTAMP
            WHERE item_id=%s AND status='pending'
        """, ((reason or "")[:500], item_id))



def count_non_ai_headline_posts_today():
    """Count fallback headline drafts created since midnight in Nepal time."""
    with connection() as db:
        row = db.execute("""
            SELECT COUNT(*) AS total
            FROM drafts
            WHERE category='स्रोत शीर्षक'
              AND created_at >= (date_trunc('day', now() AT TIME ZONE 'Asia/Kathmandu') - INTERVAL '5 hours 45 minutes')
        """).fetchone()
    return int(row["total"] or 0)


def begin_facebook_publication(item_id):
    """Durably reserve one Facebook attempt; never automatically repeat an uncertain attempt."""
    with connection() as db:
        inserted = db.execute("""
            INSERT INTO facebook_publications (item_id, status)
            VALUES (%s, 'publishing')
            ON CONFLICT(item_id) DO NOTHING
            RETURNING item_id
        """, (item_id,)).fetchone()
        row = db.execute("""
            SELECT item_id, status, post_id, error_code, error_message, attempts,
                   started_at, finished_at, updated_at
            FROM facebook_publications WHERE item_id=%s FOR UPDATE
        """, (item_id,)).fetchone()
        if not row:
            return None
        result = dict(row)
        result["reserved"] = bool(inserted)
        return result


def finish_facebook_publication(item_id, status, post_id=None, error_code=None, error_message=""):
    if status not in {"published", "failed", "unknown"}:
        raise ValueError("Invalid Facebook publication status.")
    with connection() as db:
        db.execute("""
            UPDATE facebook_publications
            SET status=%s, post_id=%s, error_code=%s, error_message=%s,
                finished_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP
            WHERE item_id=%s AND status='publishing'
        """, (status, post_id, (str(error_code)[:80] if error_code else None),
              (error_message or "")[:300], item_id))


def get_facebook_publishing_control():
    """Return the persistent authentication circuit-breaker state."""
    with connection() as db:
        row = db.execute("""
            SELECT auth_blocked, reason, updated_at
            FROM facebook_publishing_control WHERE id=1
        """).fetchone()
    return dict(row) if row else {"auth_blocked": False, "reason": ""}


def set_facebook_publishing_blocked(blocked, reason=""):
    """Pause publishing after auth errors; clear only after a successful connection check."""
    with connection() as db:
        db.execute("""
            INSERT INTO facebook_publishing_control (id, auth_blocked, reason)
            VALUES (1, %s, %s)
            ON CONFLICT (id) DO UPDATE SET
                auth_blocked=EXCLUDED.auth_blocked,
                reason=EXCLUDED.reason,
                updated_at=CURRENT_TIMESTAMP
        """, (bool(blocked), (reason or "")[:300]))


def get_facebook_publication_status(limit=30):
    """Return safe publication counts and recent records for the admin dashboard."""
    with connection() as db:
        db.execute("""
            UPDATE facebook_publications
            SET status='unknown',
                error_message='The app restarted before the publishing result was recorded. Check the Page before retrying.',
                finished_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP
            WHERE status='publishing'
              AND started_at < CURRENT_TIMESTAMP - INTERVAL '10 minutes'
        """)
        counts = db.execute("""
            SELECT
                COUNT(*) FILTER (WHERE status='published') AS published,
                COUNT(*) FILTER (WHERE status='failed') AS failed,
                COUNT(*) FILTER (WHERE status='unknown') AS unknown,
                COUNT(*) FILTER (WHERE status='publishing') AS publishing
            FROM facebook_publications
        """).fetchone()
        rows = db.execute("""
            SELECT item_id, status, post_id, error_code, error_message,
                   started_at, finished_at, updated_at
            FROM facebook_publications
            ORDER BY updated_at DESC LIMIT %s
        """, (max(1, min(int(limit), 100)),)).fetchall()
    return {
        "counts": {key: int(value or 0) for key, value in counts.items()},
        "recent": [dict(row) for row in rows],
    }



def record_news_source_health(source_name, status, http_status=None, entries_found=0, duration_ms=0, error=""):
    """Persist the latest health result for one configured news feed."""
    source_name = str(source_name or "Unknown source").strip()[:160]
    status = status if status in {"healthy", "warning", "error"} else "error"
    safe_error = str(error or "")[:300]
    with connection() as db:
        db.execute("""
            INSERT INTO news_source_health (
                source_name, status, consecutive_failures, last_checked_at,
                last_success_at, last_http_status, last_entries_found,
                last_duration_ms, last_error
            ) VALUES (
                %s, %s, %s, CURRENT_TIMESTAMP,
                CASE WHEN %s = 'healthy' THEN CURRENT_TIMESTAMP ELSE NULL END,
                %s, %s, %s, %s
            )
            ON CONFLICT (source_name) DO UPDATE SET
                status=EXCLUDED.status,
                consecutive_failures=CASE
                    WHEN EXCLUDED.status = 'healthy' THEN 0
                    ELSE news_source_health.consecutive_failures + 1
                END,
                last_checked_at=CURRENT_TIMESTAMP,
                last_success_at=CASE
                    WHEN EXCLUDED.status = 'healthy' THEN CURRENT_TIMESTAMP
                    ELSE news_source_health.last_success_at
                END,
                last_http_status=EXCLUDED.last_http_status,
                last_entries_found=EXCLUDED.last_entries_found,
                last_duration_ms=EXCLUDED.last_duration_ms,
                last_error=EXCLUDED.last_error
        """, (
            source_name, status, 0 if status == "healthy" else 1, status,
            http_status, max(0, int(entries_found or 0)),
            max(0, int(duration_ms or 0)), safe_error,
        ))


def get_news_source_health(limit=100):
    """Return persistent source health without exposing configured feed URLs."""
    with connection() as db:
        rows = db.execute("""
            SELECT source_name, status, consecutive_failures, last_checked_at,
                   last_success_at, last_http_status, last_entries_found,
                   last_duration_ms, last_error
            FROM news_source_health
            ORDER BY CASE status WHEN 'error' THEN 0 WHEN 'warning' THEN 1 ELSE 2 END,
                     consecutive_failures DESC, source_name ASC
            LIMIT %s
        """, (max(1, min(int(limit), 200)),)).fetchall()
        return [dict(row) for row in rows]


def get_database_recovery_health():
    """Read-only integrity overview for core news tables; does not claim backup availability."""
    required_tables = (
        "seen_items",
        "drafts",
        "pending_stories",
        "facebook_publications",
        "facebook_publishing_control",
        "news_source_health",
    )
    with connection() as db:
        metadata = db.execute("""
            SELECT current_database() AS database_name,
                   current_setting('server_version') AS postgres_version,
                   pg_database_size(current_database())::BIGINT AS database_size_bytes,
                   (SELECT COUNT(*)::INTEGER
                    FROM information_schema.tables
                    WHERE table_schema='public' AND table_type='BASE TABLE') AS table_count
        """).fetchone()
        existing = {
            row["table_name"] for row in db.execute("""
                SELECT table_name FROM information_schema.tables
                WHERE table_schema='public' AND table_type='BASE TABLE'
            """).fetchall()
        }
        table_status = []
        for table_name in required_tables:
            row_count = None
            if table_name in existing:
                # Identifiers come only from the fixed allowlist above.
                row = db.execute(f"SELECT COUNT(*)::BIGINT AS row_count FROM {table_name}").fetchone()
                row_count = int(row["row_count"])
            table_status.append({
                "name": table_name,
                "present": table_name in existing,
                "row_count": row_count,
            })

    missing = [item["name"] for item in table_status if not item["present"]]
    return {
        "status": "warning" if missing else "healthy",
        "database_name": metadata["database_name"],
        "postgres_version": metadata["postgres_version"],
        "database_size_bytes": int(metadata["database_size_bytes"]),
        "table_count": int(metadata["table_count"]),
        "required_tables": table_status,
        "missing_tables": missing,
        "backup_verified": False,
        "note": "This is a read-only database integrity check. It does not verify that a restorable backup exists.",
    }


def get_news_analytics():
    """Return privacy-safe aggregate dashboard metrics without exposing story content."""
    with connection() as db:
        totals = db.execute("""
            SELECT
                COUNT(*)::INTEGER AS total_drafts,
                COUNT(*) FILTER (WHERE status='draft')::INTEGER AS waiting_review,
                COUNT(*) FILTER (WHERE status='approved')::INTEGER AS reviewed,
                COUNT(*) FILTER (WHERE status='rejected')::INTEGER AS dismissed,
                COUNT(*) FILTER (WHERE created_at >= CURRENT_TIMESTAMP - INTERVAL '7 days')::INTEGER AS created_last_7_days
            FROM drafts
        """).fetchone()
        seen = db.execute("SELECT COUNT(*)::INTEGER AS total FROM seen_items").fetchone()
        published = db.execute(
            "SELECT COUNT(*)::INTEGER AS total FROM facebook_publications WHERE status='published'"
        ).fetchone()
        sources = db.execute("""
            SELECT
                COUNT(*)::INTEGER AS total,
                COUNT(*) FILTER (WHERE status='healthy')::INTEGER AS healthy,
                COUNT(*) FILTER (WHERE status='warning')::INTEGER AS warning,
                COUNT(*) FILTER (WHERE status='error')::INTEGER AS error
            FROM news_source_health
        """).fetchone()
        categories = db.execute("""
            SELECT COALESCE(NULLIF(category, ''), 'अन्य') AS category,
                   COUNT(*)::INTEGER AS count
            FROM drafts
            WHERE status='draft'
            GROUP BY COALESCE(NULLIF(category, ''), 'अन्य')
            ORDER BY count DESC, category ASC
            LIMIT 8
        """).fetchall()

    return {
        "drafts": dict(totals or {}),
        "discovered_items": int((seen or {}).get("total", 0)),
        "facebook_published": int((published or {}).get("total", 0)),
        "sources": dict(sources or {}),
        "categories": [dict(row) for row in categories],
    }
