"""Persistent, Nepal-time daily safety budgets for Groq API usage."""
import os
import threading
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import psycopg
from psycopg.rows import dict_row

_NPT = ZoneInfo("Asia/Kathmandu")
_schema_lock = threading.Lock()
_schema_ready = False


def _limits():
    def read_int(name, default, minimum, maximum):
        try:
            value = int(os.getenv(name, str(default)))
        except (TypeError, ValueError):
            value = default
        return max(minimum, min(value, maximum))

    return {
        "request_limit": read_int("GROQ_DAILY_REQUEST_BUDGET", 180, 1, 1000),
        "token_limit": read_int("GROQ_DAILY_TOKEN_BUDGET", 160000, 1000, 180000),
        "post_limit": read_int("MAX_NEWS_POSTS_PER_DAY", 100, 1, 1000),
    }


def estimate_request_tokens(prompt):
    """Conservative character-based reservation; actual provider usage replaces it."""
    return max(900, min(5000, (len(prompt or "") + 1) // 2 + 400))


def _connect():
    url = os.getenv("DATABASE_URL", "").strip()
    if not url:
        raise RuntimeError("DATABASE_URL is not configured.")
    return psycopg.connect(url, row_factory=dict_row)


def _ensure_table():
    global _schema_ready
    if _schema_ready:
        return
    with _schema_lock:
        if _schema_ready:
            return
        with _connect() as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS groq_daily_usage (
                    usage_date DATE PRIMARY KEY,
                    requests_started INTEGER NOT NULL DEFAULT 0,
                    posts_created INTEGER NOT NULL DEFAULT 0,
                    tokens_used BIGINT NOT NULL DEFAULT 0,
                    reserved_tokens BIGINT NOT NULL DEFAULT 0,
                    paused_until TIMESTAMPTZ,
                    pause_reason TEXT NOT NULL DEFAULT '',
                    last_error TEXT NOT NULL DEFAULT '',
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)
        _schema_ready = True


def _today():
    return datetime.now(_NPT).date()


def _ensure_day(db, day):
    db.execute(
        "INSERT INTO groq_daily_usage (usage_date) VALUES (%s) ON CONFLICT (usage_date) DO NOTHING",
        (day,),
    )


def get_groq_usage_status():
    _ensure_table()
    day = _today()
    limits = _limits()
    with _connect() as db:
        _ensure_day(db, day)
        row = db.execute(
            "SELECT * FROM groq_daily_usage WHERE usage_date=%s", (day,)
        ).fetchone()
    now = datetime.now(_NPT)
    paused_until = row["paused_until"]
    paused = bool(paused_until and paused_until > now)
    return {
        "date": day.isoformat(),
        "requests": int(row["requests_started"]),
        "request_limit": limits["request_limit"],
        "tokens": int(row["tokens_used"]),
        "reserved_tokens": int(row["reserved_tokens"]),
        "token_limit": limits["token_limit"],
        "posts_created": int(row["posts_created"]),
        "post_limit": limits["post_limit"],
        "paused": paused,
        "paused_until": paused_until.isoformat() if paused_until else None,
        "pause_reason": row["pause_reason"] if paused else "",
        "last_error": row["last_error"],
        "requests_remaining": max(0, limits["request_limit"] - int(row["requests_started"])),
        "tokens_remaining": max(0, limits["token_limit"] - int(row["tokens_used"]) - int(row["reserved_tokens"])),
        "posts_remaining": max(0, limits["post_limit"] - int(row["posts_created"])),
    }


def reserve_groq_request(estimated_tokens):
    """Atomically reserve request and token budget before contacting Groq."""
    _ensure_table()
    day = _today()
    limits = _limits()
    estimate = max(1, min(int(estimated_tokens), 5000))
    now = datetime.now(_NPT)
    with _connect() as db:
        _ensure_day(db, day)
        row = db.execute(
            "SELECT * FROM groq_daily_usage WHERE usage_date=%s FOR UPDATE", (day,)
        ).fetchone()
        if row["paused_until"] and row["paused_until"] > now:
            return {"allowed": False, "reason": row["pause_reason"] or "Groq is temporarily paused."}
        if int(row["requests_started"]) >= limits["request_limit"]:
            return {"allowed": False, "reason": "NayaDrishti daily Groq request safety budget reached."}
        if int(row["posts_created"]) >= limits["post_limit"]:
            return {"allowed": False, "reason": "NayaDrishti daily news-post target reached."}
        if int(row["tokens_used"]) + int(row["reserved_tokens"]) + estimate > limits["token_limit"]:
            return {"allowed": False, "reason": "NayaDrishti daily token safety budget reached."}
        db.execute("""
            UPDATE groq_daily_usage
            SET requests_started=requests_started+1,
                reserved_tokens=reserved_tokens+%s,
                updated_at=CURRENT_TIMESTAMP
            WHERE usage_date=%s
        """, (estimate, day))
    return {"allowed": True, "usage_date": day, "reserved_tokens": estimate}


def finish_groq_request(reservation, actual_tokens=0, post_created=False, error="", pause_seconds=0):
    """Release the reservation and record measured (or conservatively estimated) usage."""
    if not reservation or not reservation.get("allowed"):
        return
    _ensure_table()
    day = reservation["usage_date"]
    reserved = int(reservation["reserved_tokens"])
    measured = max(0, int(actual_tokens or 0))
    charged = min(measured, 100000) if measured else reserved
    now = datetime.now(_NPT)
    paused_until = None
    if pause_seconds == -1:
        tomorrow = now.date() + timedelta(days=1)
        paused_until = datetime.combine(tomorrow, datetime.min.time(), tzinfo=_NPT)
    elif pause_seconds > 0:
        paused_until = now + timedelta(seconds=min(int(pause_seconds), 86400))

    with _connect() as db:
        _ensure_day(db, day)
        db.execute("""
            UPDATE groq_daily_usage
            SET reserved_tokens=GREATEST(0, reserved_tokens-%s),
                tokens_used=tokens_used+%s,
                posts_created=posts_created+%s,
                paused_until=CASE WHEN %s IS NULL THEN paused_until ELSE %s END,
                pause_reason=CASE WHEN %s='' THEN pause_reason ELSE %s END,
                last_error=%s,
                updated_at=CURRENT_TIMESTAMP
            WHERE usage_date=%s
        """, (
            reserved, charged, 1 if post_created else 0,
            paused_until, paused_until,
            error[:300], error[:300], error[:300], day,
        ))
