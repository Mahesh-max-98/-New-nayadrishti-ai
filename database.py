import os
import psycopg
from psycopg.rows import dict_row
from contextlib import contextmanager

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
            language TEXT DEFAULT 'auto',
            status TEXT DEFAULT 'draft',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """)
        db.execute("ALTER TABLE drafts ADD COLUMN IF NOT EXISTS image_data BYTEA")
        db.execute("ALTER TABLE drafts ADD COLUMN IF NOT EXISTS image_status TEXT DEFAULT 'pending'")
        db.execute("ALTER TABLE drafts ADD COLUMN IF NOT EXISTS image_error TEXT")
        db.execute("ALTER TABLE drafts ADD COLUMN IF NOT EXISTS image_last_attempt TIMESTAMP")
        db.execute("""
        CREATE TABLE IF NOT EXISTS funny_post_jobs (
            job_id TEXT PRIMARY KEY,
            status TEXT NOT NULL,
            message TEXT,
            quote TEXT,
            image_error TEXT,
            image_data BYTEA,
            error TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """)
        db.execute("CREATE INDEX IF NOT EXISTS funny_post_jobs_created_idx ON funny_post_jobs(created_at)")


def already_seen(item_id):
    with connection() as db:
        return db.execute(
            "SELECT 1 FROM seen_items WHERE item_id=%s", (item_id,)
        ).fetchone() is not None


def already_seen_title(title):
    """Avoid duplicate headlines repeated across different feeds."""
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


def save_draft(item_id, title, source, url, summary, language="auto",
               image_data=None, image_status="pending", image_error=None):
    with connection() as db:
        db.execute("""
            INSERT INTO drafts
            (item_id, title, source, url, summary, language, image_data, image_status,
             image_error, image_last_attempt)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
            ON CONFLICT(item_id) DO UPDATE SET
                image_data=COALESCE(EXCLUDED.image_data, drafts.image_data),
                image_status=EXCLUDED.image_status,
                image_error=EXCLUDED.image_error,
                image_last_attempt=CURRENT_TIMESTAMP
        """, (item_id, title, source, url, summary, language, image_data, image_status, image_error))


def get_drafts(limit=50):
    with connection() as db:
        rows = db.execute("""
            SELECT item_id, title, source, url, summary, language, status,
                   image_status, image_error, created_at,
                   (image_data IS NOT NULL) AS has_image
            FROM drafts
            WHERE status='draft'
            ORDER BY created_at DESC
            LIMIT %s
        """, (limit,)).fetchall()
        return [dict(row) for row in rows]


def get_draft_for_image_retry(item_id):
    """Return an unpublished draft needing an image retry after its cooldown."""
    with connection() as db:
        row = db.execute("""
            SELECT item_id, title, source, url, summary, image_status,
                   image_error, image_last_attempt,
                   (image_data IS NOT NULL) AS has_image
            FROM drafts WHERE item_id=%s AND status='draft'
        """, (item_id,)).fetchone()
        if not row:
            return None
        # Automatically retry failed/missing images and branded fallback graphics.
        # Do not repeatedly regenerate a successful AI illustration or publisher photo.
        if row["image_status"] not in ("pending", "failed", "fallback_graphic"):
            return None
        if row["has_image"] and row["image_status"] != "fallback_graphic":
            return None
        last_attempt = row["image_last_attempt"]
        if last_attempt is not None:
            elapsed = db.execute(
                "SELECT EXTRACT(EPOCH FROM (CURRENT_TIMESTAMP - %s)) AS seconds",
                (last_attempt,)
            ).fetchone()["seconds"]
            if elapsed is not None and elapsed < 1800:
                return None
        return dict(row)



def get_drafts_needing_image_retry(limit=2):
    """Find unpublished drafts that need an image retry, even if their RSS item disappeared."""
    with connection() as db:
        rows = db.execute("""
            SELECT item_id, title, source, url, summary, image_status,
                   image_error, image_last_attempt,
                   (image_data IS NOT NULL) AS has_image
            FROM drafts
            WHERE status='draft'
              AND image_status IN ('pending', 'failed', 'fallback_graphic')
              AND (image_data IS NULL OR image_status='fallback_graphic')
              AND (image_last_attempt IS NULL
                   OR image_last_attempt < CURRENT_TIMESTAMP - INTERVAL '30 minutes')
            ORDER BY
              CASE WHEN image_data IS NULL THEN 0 ELSE 1 END,
              created_at ASC
            LIMIT %s
        """, (max(1, min(int(limit), 5)),)).fetchall()
        return [dict(row) for row in rows]


def replace_draft_image(item_id, image_data, image_status="fallback_graphic"):
    """Replace a saved draft image explicitly, including an older fallback graphic."""
    with connection() as db:
        result = db.execute(
            """UPDATE drafts
               SET image_data=%s, image_status=%s, image_error=NULL,
                   image_last_attempt=CURRENT_TIMESTAMP
               WHERE item_id=%s""",
            (image_data, image_status, item_id),
        )
        return result.rowcount > 0


def get_draft_image(item_id):
    with connection() as db:
        row = db.execute(
            "SELECT image_data FROM drafts WHERE item_id=%s", (item_id,)
        ).fetchone()
        return bytes(row["image_data"]) if row and row["image_data"] else None


def set_draft_status(item_id, status):
    with connection() as db:
        db.execute("UPDATE drafts SET status=%s WHERE item_id=%s", (status, item_id))
