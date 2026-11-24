"""Optional Gemini fallback with a small persistent daily safety budget."""
import os
import re
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import psycopg
import requests
from psycopg.rows import dict_row

_NPT = ZoneInfo("Asia/Kathmandu")
_GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


def _connect():
    url = os.getenv("DATABASE_URL", "").strip()
    if not url:
        raise RuntimeError("DATABASE_URL is not configured for fallback AI usage tracking.")
    return psycopg.connect(url, row_factory=dict_row)


def reserve_gemini_request():
    """Reserve one fallback attempt and enforce the shared daily post ceiling."""
    day = datetime.now(_NPT).date()
    try:
        fallback_limit = max(1, min(int(os.getenv("MAX_FALLBACK_AI_REQUESTS_PER_DAY", "50")), 200))
        post_limit = max(1, min(int(os.getenv("MAX_NEWS_POSTS_PER_DAY", "100")), 1000))
    except (TypeError, ValueError):
        fallback_limit, post_limit = 50, 100
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
        db.execute("""
            CREATE TABLE IF NOT EXISTS gemini_daily_usage (
                usage_date DATE PRIMARY KEY,
                requests_started INTEGER NOT NULL DEFAULT 0,
                posts_created INTEGER NOT NULL DEFAULT 0,
                last_error TEXT NOT NULL DEFAULT '',
                paused_until TIMESTAMPTZ,
                pause_reason TEXT NOT NULL DEFAULT '',
                updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        db.execute("ALTER TABLE gemini_daily_usage ADD COLUMN IF NOT EXISTS paused_until TIMESTAMPTZ")
        db.execute("ALTER TABLE gemini_daily_usage ADD COLUMN IF NOT EXISTS pause_reason TEXT NOT NULL DEFAULT ''")
        db.execute("INSERT INTO groq_daily_usage (usage_date) VALUES (%s) ON CONFLICT DO NOTHING", (day,))
        db.execute("INSERT INTO gemini_daily_usage (usage_date) VALUES (%s) ON CONFLICT DO NOTHING", (day,))
        groq = db.execute("SELECT posts_created FROM groq_daily_usage WHERE usage_date=%s FOR UPDATE", (day,)).fetchone()
        gemini = db.execute("SELECT * FROM gemini_daily_usage WHERE usage_date=%s FOR UPDATE", (day,)).fetchone()
        now = datetime.now(timezone.utc)
        if gemini.get("paused_until") and gemini["paused_until"] > now:
            seconds = max(1, int((gemini["paused_until"] - now).total_seconds()))
            raise RuntimeError(f"GEMINI_PROVIDER_PAUSED: Gemini is cooling down; retry in about {seconds} seconds.")
        if int(gemini["requests_started"]) >= fallback_limit:
            raise RuntimeError("GEMINI_BUDGET_PAUSED: NayaDrishti daily backup-AI request limit reached.")
        if int(groq["posts_created"]) + int(gemini["posts_created"]) >= post_limit:
            raise RuntimeError("GEMINI_BUDGET_PAUSED: NayaDrishti daily news-post limit reached.")
        db.execute("""
            UPDATE gemini_daily_usage
            SET requests_started=requests_started+1, updated_at=CURRENT_TIMESTAMP
            WHERE usage_date=%s
        """, (day,))
    return {"usage_date": day}


def _gemini_pause_seconds(error):
    """Choose a conservative shared cooldown after provider-wide errors."""
    match = re.search(r"Gemini API error (\d{3})", error or "")
    if not match:
        return 0
    status = int(match.group(1))
    detail = (error or "").casefold()
    if status == 429:
        if any(word in detail for word in ("quota", "per day", "daily limit", "resource_exhausted")):
            now = datetime.now(_NPT)
            tomorrow = now.date() + timedelta(days=1)
            reset = datetime.combine(tomorrow, datetime.min.time(), tzinfo=_NPT)
            return max(60, int((reset - now).total_seconds()))
        return 300
    if status == 503:
        return 300
    if status in (500, 502, 504):
        return 120
    return 0


def finish_gemini_request(reservation, post_created=False, error=""):
    if not reservation:
        return
    pause_seconds = _gemini_pause_seconds(error)
    paused_until = (
        datetime.now(timezone.utc) + timedelta(seconds=pause_seconds)
        if pause_seconds else None
    )
    pause_reason = (error or "")[:300] if pause_seconds else ""
    with _connect() as db:
        db.execute("""
            UPDATE gemini_daily_usage
            SET posts_created=posts_created+%s,
                last_error=%s,
                paused_until=COALESCE(%s::timestamptz, paused_until),
                pause_reason=CASE WHEN %s='' THEN pause_reason ELSE %s END,
                updated_at=CURRENT_TIMESTAMP
            WHERE usage_date=%s
        """, (
            1 if post_created else 0, (error or "")[:300],
            paused_until, pause_reason, pause_reason, reservation["usage_date"],
        ))


def generate_with_gemini(prompt):
    """Call Gemini using a separately configured key; never logs or returns the key."""
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("Gemini fallback is not configured. Add GEMINI_API_KEY in Render.")
    model = os.getenv("GEMINI_MODEL", "gemini-3.8-flash").strip()
    payload = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.15, "maxOutputTokens": 320},
    }
    response = None
    # Retry one time only for temporary server-capacity errors. Never retry
    # 429 blindly; the provider's quota/reset response must be respected.
    for attempt in range(2):
        response = requests.post(
            _GEMINI_URL.format(model=model),
            headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
            json=payload,
            timeout=45,
        )
        if response.ok or response.status_code not in (500, 502, 503, 504) or attempt == 1:
            break
        retry_after = response.headers.get("Retry-After")
        try:
            delay = float(retry_after) if retry_after else 2.0
        except (TypeError, ValueError):
            delay = 2.0
        time.sleep(max(1.0, min(delay, 5.0)))
    if not response.ok:
        detail = response.text[:350].replace("\n", " ").replace("\r", " ")
        raise RuntimeError(f"Gemini API error {response.status_code}: {detail}")
    data = response.json()
    candidates = data.get("candidates") or []
    if not candidates:
        raise RuntimeError("Gemini returned no generated response.")
    parts = (candidates[0].get("content") or {}).get("parts") or []
    generated = "\n".join(part.get("text", "") for part in parts if isinstance(part, dict))
    if not generated.strip():
        raise RuntimeError("Gemini returned an empty response.")
    return generated
