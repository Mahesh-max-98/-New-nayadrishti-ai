
import os
import psycopg
from psycopg.rows import dict_row
from contextlib import contextmanager

DATABASE_URL = os.getenv("DATABASE_URL")


@contextmanager
def connection():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not configured.")

    conn = psycopg.connect(
        DATABASE_URL,
        row_factory=dict_row
    )

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


def already_seen(item_id):
    with connection() as db:
        row = db.execute(
            "SELECT 1 FROM seen_items WHERE item_id=%s",
            (item_id,)
        ).fetchone()

        return row is not None


def remember(item_id, title, source, url, status="seen"):
    with connection() as db:
        db.execute("""
            INSERT INTO seen_items
            (item_id, title, source, url, status)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT(item_id)
            DO UPDATE SET status=EXCLUDED.status
        """, (item_id, title, source, url, status))


def save_draft(item_id, title, source, url, summary, language="auto"):
    with connection() as db:
        db.execute("""
            INSERT INTO drafts
            (item_id, title, source, url, summary, language)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT(item_id) DO NOTHING
        """, (item_id, title, source, url, summary, language))


def get_drafts(limit=50):
    with connection() as db:
        rows = db.execute("""
            SELECT * FROM drafts
            WHERE status='draft'
            ORDER BY created_at DESC
            LIMIT %s
        """, (limit,)).fetchall()

        return [dict(row) for row in rows]
        



def set_draft_status(item_id, status):
    """Update a saved draft after its publication status is known."""
    with connection() as db:
        db.execute(
            "UPDATE drafts SET status=%s WHERE item_id=%s",
            (status, item_id),
        )
