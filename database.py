import os
import sqlite3
from contextlib import contextmanager

DB_PATH = os.getenv("DATABASE_PATH", "nayadrishti.db")

@contextmanager
def connection():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
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
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """)

def already_seen(item_id):
    with connection() as db:
        return db.execute("SELECT 1 FROM seen_items WHERE item_id=?", (item_id,)).fetchone() is not None

def remember(item_id, title, source, url, status="seen"):
    with connection() as db:
        db.execute(
            "INSERT OR IGNORE INTO seen_items(item_id,title,source,url,status) VALUES(?,?,?,?,?)",
            (item_id, title, source, url, status)
        )
