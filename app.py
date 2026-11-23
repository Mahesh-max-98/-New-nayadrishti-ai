import os
import threading
from datetime import datetime, timezone
from functools import wraps

from apscheduler.schedulers.background import BackgroundScheduler
from flask import Flask, jsonify, redirect, render_template_string, request, session, url_for

from database import get_drafts, init_db
from news_monitor import check_feeds, configured_feeds

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "").strip()
if not app.secret_key:
    raise RuntimeError("FLASK_SECRET_KEY is required. Set a long, random secret in Render environment variables.")
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.getenv("COOKIE_SECURE", "true").lower() == "true",
)
init_db()

_check_lock = threading.Lock()
_feed_execution_lock = threading.Lock()
_check_status = {
    "running": False, "finished": False, "result": None, "error": None,
    "started_at": None, "finished_at": None, "trigger": None,
}


def _run_check(trigger):
    global _check_status
    if not _feed_execution_lock.acquire(blocking=False):
        app.logger.info("Feed check skipped: another check is already running.")
        return
    with _check_lock:
        started = datetime.now(timezone.utc).isoformat()
        _check_status = {
            "running": True, "finished": False, "result": None, "error": None,
            "started_at": started, "finished_at": None, "trigger": trigger,
        }
    try:
        result = check_feeds()
        with _check_lock:
            _check_status = {
                "running": False, "finished": True, "result": result, "error": None,
                "started_at": started, "finished_at": datetime.now(timezone.utc).isoformat(),
                "trigger": trigger,
            }
    except Exception as exc:
        app.logger.exception("News feed check failed")
        with _check_lock:
            _check_status = {
                "running": False, "finished": True, "result": None,
                "error": f"{type(exc).__name__}: {str(exc)[:300]}",
                "started_at": started, "finished_at": datetime.now(timezone.utc).isoformat(),
                "trigger": trigger,
            }
    finally:
        _feed_execution_lock.release()


def _scheduled_feed_check():
    _run_check("automatic")


scheduler = BackgroundScheduler(daemon=True)
if os.getenv("ENABLE_AUTO_MONITORING", "true").lower() == "true":
    scheduler.add_job(
        _scheduled_feed_check,
        trigger="interval",
        seconds=max(600, int(os.getenv("NEWS_CHECK_INTERVAL_SECONDS", "600"))),
        id="nayadrishti_news_monitor",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    scheduler.start()
    app.logger.info("Automatic text-only news monitoring enabled.")
else:
    app.logger.info("Automatic news monitoring is disabled.")


PAGE = """
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>NayaDrishti AI — News Desk</title>
<style>
*{box-sizing:border-box}body{margin:0;background:#f3f5f9;color:#172033;font-family:Arial,sans-serif}
.wrap{max-width:920px;margin:auto;padding:18px}
header{background:#172554;color:#fff;padding:24px;border-radius:16px;margin:8px 0 18px}
h1{margin:0 0 6px;font-size:27px}header p{margin:4px 0;color:#dbeafe}
.panel,.draft{background:#fff;border:1px solid #e5e7eb;border-radius:14px;padding:18px;margin:14px 0}
button{background:#1d4ed8;color:white;border:0;border-radius:9px;padding:12px 16px;font-weight:700;font-size:15px;cursor:pointer}
button:disabled{opacity:.6;cursor:wait}
input{width:100%;padding:13px;border:1px solid #cbd5e1;border-radius:9px;margin:8px 0 14px;font-size:16px}
.small{color:#64748b;font-size:13px}.status{white-space:pre-wrap;overflow-wrap:anywhere;background:#f8fafc;padding:12px;border-radius:9px;margin-top:12px}
.draft h3{margin:0 0 8px;white-space:pre-line;line-height:1.7;font-size:19px}
.top{display:flex;justify-content:space-between;gap:10px;align-items:center;flex-wrap:wrap}
a{color:#1d4ed8;overflow-wrap:anywhere}.danger{color:#b91c1c}
</style></head><body><main class="wrap">
<header><h1>NayaDrishti AI</h1><p>नयाँ दृष्टि, नयाँ खबर।</p><p>A New Perspective on Every Story.</p></header>
{% if not logged_in %}
<section class="panel"><h2>Admin sign in</h2><p class="small">Sign in to monitor news sources and review posts.</p>
<form method="post" action="/login"><label for="password">Admin password</label>
<input id="password" name="password" type="password" required autocomplete="current-password">
<button type="submit">Sign in</button></form>{% if error %}<p class="danger">{{ error }}</p>{% endif %}</section>
{% else %}
<section class="panel"><div class="top"><h2>News monitoring</h2><form method="post" action="/logout"><button>Log out</button></form></div>
<p>Configured news sources: <strong>{{ feed_count }}</strong></p>
<p>Automatic monitoring: <strong>{{ "ON" if monitoring_enabled else "OFF" }}</strong> · Interval: <strong>{{ check_interval }} seconds</strong></p>
<p class="small">Checks run while the Render service is awake. A free web service may sleep, so continuous 24/7 monitoring is not guaranteed.</p>
<p class="small">Text-only mode: no image generation, image fetching, photo attachments, or image studio. News posts contain a Nepali headline, factual summary, and source credit. The post text does not include publication timestamps.</p>
<div id="monitoring-status" class="status">Loading latest check status...</div>
<button id="check" onclick="runCheck()">Check news now</button>
<div id="result" class="status" aria-live="polite">Ready. Press the button to collect news.</div></section>
<section class="panel"><h2>Facebook publishing</h2>
<p>Auto-publishing: <strong>{{ 'ON' if publishing else 'OFF' }}</strong></p>
<p class="small">Keep publishing OFF while testing. Review several drafts and their original sources before turning it on.</p></section>
<section><div class="top"><h2>News drafts</h2><span class="small">{{ drafts|length }} waiting for review</span></div>
{% for d in drafts %}<article class="draft"><h3>{{ d.summary }}</h3>
<p class="small">Source: {{ d.source or "Original publisher" }}</p>
{% if d.url %}<a href="{{ d.url }}" target="_blank" rel="noopener noreferrer">Read original report</a>{% endif %}
</article>
{% else %}<div class="panel">No drafts waiting. Run a news check or confirm that your RSS sources are configured.</div>{% endfor %}
</section>{% endif %}</main>
{% if logged_in %}<script>
async function runCheck(){
 const button=document.getElementById('check'),out=document.getElementById('result');
 button.disabled=true;button.textContent='Collecting news...';
 out.textContent='Checking RSS sources, filtering duplicates, reading article text, and writing Nepali summaries...';
 try{
  const start=await fetch('/run-check',{method:'POST',headers:{'Accept':'application/json'}});
  if(!start.ok && start.status!==202)throw new Error('Could not start check (HTTP '+start.status+')');
  const poll=async()=>{
   const response=await fetch('/run-check',{headers:{'Accept':'application/json'}});
   const state=await response.json();
   if(state.running){out.textContent='Still checking sources and preparing Nepali posts...';setTimeout(poll,2500);return;}
   if(state.finished){
    const data=state.result||{};
    const status=document.getElementById('monitoring-status');
    status.textContent='Last check completed · '+(state.trigger||'unknown')+' run';
    if(state.error)out.textContent='Check failed: '+state.error;
    else{
     const lines=[
      'News check complete.',
      'Sources checked: '+(data.feeds_checked||0)+' / '+(data.feeds_configured||0),
      'Fresh stories found: '+(data.fresh_candidates||0),
      'New drafts saved: '+(data.drafts_saved||0),
      'Already seen: '+(data.already_seen||0),
      'Facebook posts published: '+(data.published||0)
     ];
     if(data.errors&&data.errors.length)lines.push('Issues:\n- '+data.errors.join('\n- '));
     out.textContent=lines.join('\n');
    }
    button.disabled=false;button.textContent='Check news now';
    setTimeout(()=>location.reload(),2200);return;
   }
   setTimeout(poll,2500);
  };
  poll();
 }catch(error){out.textContent='Could not check news: '+error.message;button.disabled=false;button.textContent='Check news now';}
}
window.addEventListener('load',async()=>{
 try{
  const response=await fetch('/run-check',{headers:{'Accept':'application/json'}});
  const state=await response.json(),el=document.getElementById('monitoring-status');
  if(state.running)el.textContent='A news check is currently running.';
  else if(state.finished)el.textContent='The latest news check has finished.';
  else el.textContent='No check has completed since this server started.';
 }catch(error){}
});
</script>{% endif %}</body></html>
"""


def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not session.get("admin"):
            return jsonify(error="Please sign in first."), 401
        return fn(*args, **kwargs)
    return wrapper


@app.get("/")
def home():
    logged_in = bool(session.get("admin"))
    return render_template_string(
        PAGE,
        logged_in=logged_in,
        drafts=get_drafts() if logged_in else [],
        feed_count=len(configured_feeds()),
        monitoring_enabled=os.getenv("ENABLE_AUTO_MONITORING", "true").lower() == "true",
        check_interval=max(600, int(os.getenv("NEWS_CHECK_INTERVAL_SECONDS", "600"))),
        publishing=os.getenv("ENABLE_FACEBOOK_PUBLISHING", "false").lower() == "true",
        error=request.args.get("error"),
    )


@app.post("/login")
def login():
    expected = os.getenv("ADMIN_PASSWORD")
    if not expected:
        return "ADMIN_PASSWORD is not configured in the hosting dashboard.", 503
    if request.form.get("password", "") != expected:
        return redirect(url_for("home", error="Incorrect password."))
    session.clear()
    session["admin"] = True
    return redirect(url_for("home"))


@app.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("home"))


@app.get("/health")
def health():
    return jsonify(status="ok", service="NayaDrishti AI", mode="text_only")


@app.route("/run-check", methods=["GET", "POST"])
@login_required
def run_check():
    global _check_status
    if request.method == "GET":
        with _check_lock:
            return jsonify(_check_status)
    if _feed_execution_lock.locked():
        with _check_lock:
            return jsonify(_check_status), 202
    with _check_lock:
        if _check_status["running"]:
            return jsonify(_check_status), 202
        _check_status = {
            "running": True, "finished": False, "result": None, "error": None,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "finished_at": None, "trigger": "manual",
        }
    thread = threading.Thread(target=_run_check, args=("manual",), daemon=True)
    thread.start()
    return jsonify(running=True, message="News check started."), 202


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")))
