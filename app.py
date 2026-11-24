import os
import threading
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from functools import wraps

from apscheduler.schedulers.background import BackgroundScheduler
from flask import Flask, jsonify, redirect, render_template_string, request, session, url_for

from database import get_drafts, init_db, set_draft_status
from groq_budget import get_groq_usage_status
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
    "new_drafts": 0, "last_draft": None,
}


def _record_new_draft(draft):
    """Publish per-story progress so the dashboard can refresh immediately."""
    with _check_lock:
        _check_status["new_drafts"] = int(_check_status.get("new_drafts", 0)) + 1
        _check_status["last_draft"] = {
            "title": str(draft.get("title", ""))[:180],
            "source": str(draft.get("source", ""))[:100],
        }


def _run_check(trigger, lock_acquired=False):
    global _check_status
    # Manual requests reserve the execution lock before starting this thread.
    # Scheduled checks acquire it here. This prevents a race that could leave
    # the dashboard stuck in the "checking" state.
    if not lock_acquired and not _feed_execution_lock.acquire(blocking=False):
        app.logger.info("Feed check skipped: another check is already running.")
        with _check_lock:
            if not _check_status["running"]:
                now = datetime.now(timezone.utc).isoformat()
                _check_status = {
                    "running": False, "finished": True, "result": None,
                    "error": "Another news check is already running. Please try again shortly.",
                    "started_at": now, "finished_at": now, "trigger": trigger,
                    "new_drafts": 0, "last_draft": None,
                }
        return
    with _check_lock:
        started = datetime.now(timezone.utc).isoformat()
        _check_status = {
            "running": True, "finished": False, "result": None, "error": None,
            "started_at": started, "finished_at": None, "trigger": trigger,
            "new_drafts": 0, "last_draft": None,
        }
    try:
        result = check_feeds(on_new_draft=_record_new_draft)
        with _check_lock:
            _check_status = {
                "running": False, "finished": True, "result": result, "error": None,
                "started_at": started, "finished_at": datetime.now(timezone.utc).isoformat(),
                "trigger": trigger,
                "new_drafts": int(_check_status.get("new_drafts", 0)),
                "last_draft": _check_status.get("last_draft"),
            }
    except Exception as exc:
        app.logger.exception("News feed check failed")
        with _check_lock:
            _check_status = {
                "running": False, "finished": True, "result": None,
                "error": f"{type(exc).__name__}: {str(exc)[:300]}",
                "started_at": started, "finished_at": datetime.now(timezone.utc).isoformat(),
                "trigger": trigger,
                "new_drafts": int(_check_status.get("new_drafts", 0)),
                "last_draft": _check_status.get("last_draft"),
            }
    finally:
        _feed_execution_lock.release()


def _scheduled_feed_check():
    _run_check("automatic")


scheduler = BackgroundScheduler(daemon=True)
if os.getenv("ENABLE_AUTO_MONITORING", "true").lower() == "true":
    # Run only during Nepal daytime hours. Render free instances can sleep;
    # the scheduler resumes only when the web process is awake.
    scheduler.add_job(
        _scheduled_feed_check,
        trigger="cron",
        minute="*/5",
        hour="5-22",
        timezone=ZoneInfo("Asia/Kathmandu"),
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
.draft h3{margin:0 0 8px;white-space:pre-line;line-height:1.7;font-size:19px}\n.actions{display:flex;gap:8px;flex-wrap:wrap;margin-top:14px}.actions button{font-size:13px;padding:10px 13px}.actions .secondary{background:#475569}
.top{display:flex;justify-content:space-between;gap:10px;align-items:center;flex-wrap:wrap}
.feed-row{padding:11px 0;border-top:1px solid #e5e7eb;display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap}.feed-name{font-weight:700}.filters{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin:12px 0}.filters select{padding:11px;border:1px solid #cbd5e1;border-radius:9px;background:#fff;min-width:170px}.tag{display:inline-block;padding:5px 9px;border-radius:999px;background:#e2e8f0;color:#334155;font-size:12px;font-weight:700;margin:0 5px 8px 0}.tag-breaking{background:#fee2e2;color:#991b1b}.feed-ok{color:#166534}.feed-error{color:#b91c1c}.feed-meta{color:#64748b;font-size:13px;margin-top:4px}.feed-error-text{color:#b91c1c;font-size:13px;margin-top:4px;overflow-wrap:anywhere}
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
<p>Automatic monitoring: <strong>{{ "ON" if monitoring_enabled else "OFF" }}</strong> · Schedule: <strong>5:00 AM–11:00 PM Nepal time</strong> · Every <strong>5 minutes</strong></p>
<p class="small">Automatic checks run from 5:00 AM through 10:55 PM Nepal time while the Render service is awake. Render's free service may sleep after inactivity, so checks can pause until it wakes.</p>
<p class="small">Text-only mode: no images or timestamps in posts. New drafts appear automatically as soon as each story is processed. Source details are kept separately in the dashboard.</p>
<div id="monitoring-status" class="status">Loading latest check status...</div>
<section class="panel"><h3>Groq usage protection</h3><div id="groq-usage" class="small">Loading daily usage...</div><p class="small">Safety budgets are tracked using Nepal time. AI writing pauses automatically when a budget is reached; RSS feed checks continue. This protects NayaDrishti, but cannot change Groq account limits.</p></section>
<button id="check" onclick="runCheck()">Check news now</button><p class="small">Automatic checks: every 5 minutes, 5:00 AM–10:55 PM Nepal time, while the service is awake. The dashboard refreshes when a new draft is saved.</p>
<div id="result" class="status" aria-live="polite">Ready. Press the button to collect news.</div></section>\n<details class="panel" id="feed-health"><summary><strong>News source health</strong> <span class="small">— details from the latest check</span></summary><div id="feed-health-list" class="small" style="padding-top:12px">Run a news check to see which sources are working.</div></details>
<section class="panel"><h2>Facebook publishing</h2>
<p>Auto-publishing: <strong>{{ 'ON' if publishing else 'OFF' }}</strong></p>
<p class="small">Keep publishing OFF while testing. Review several drafts and their original sources before turning it on.</p></section>
<section><div class="top"><h2>News drafts</h2><span class="small">{{ drafts|length }} waiting for review</span></div>
<p class="small">Review each Nepali post before using it. Coverage labels compare collected headlines only; they do not prove a claim. Open original reports and compare details. Approve marks it reviewed only; it does not publish to Facebook.</p>
<form class="filters" method="get" action="/">
<label for="category">Category</label><select id="category" name="category"><option value="">All categories ({{ total_drafts }})</option>{% for category in category_options %}<option value="{{ category }}" {% if selected_category == category %}selected{% endif %}>{{ category }} ({{ category_counts.get(category, 0) }})</option>{% endfor %}</select>
<label><input type="checkbox" name="breaking" value="1" {% if breaking_only %}checked{% endif %} style="width:auto;margin:0 5px 0 0"> Potential breaking only ({{ breaking_count }})</label>
<button type="submit">Apply filters</button><a href="/">Clear</a>
</form>
<p class="small">Breaking labels are automatic signals based on urgent wording, source priority and a recent publication time. They are not confirmation; check the original article.</p>
{% if notice %}<p class="status" role="status">{{ notice }}</p>{% endif %}
{% for d in drafts %}<article class="draft">{% if d.is_breaking %}<span class="tag tag-breaking">सम्भावित ब्रेकिङ</span><p class="small">{{ d.breaking_reason }}</p>{% endif %}<span class="tag">{{ d.category or "अन्य" }}</span><h3>{{ d.summary }}</h3>
<p class="small">Source: {{ d.source or "Original publisher" }}</p>
<p class="small"><strong>Coverage check:</strong>
{% if d.verification_status == "matching_coverage" %}Similar reports found from {{ d.verification_count }} publishers.
{% elif d.verification_status == "needs_comparison" %}Needs manual comparison — reports contain different numbers.
{% else %}Only one matching publisher report found.
{% endif %}
{{ d.verification_note }}</p>
{% if d.verification_sources %}<p class="small">Compared publishers: {{ d.verification_sources }}</p>{% endif %}
{% if d.url %}<a href="{{ d.url }}" target="_blank" rel="noopener noreferrer">Read original report</a>{% endif %}
<div class="actions"><form method="post" action="/draft/{{ d.item_id }}/status"><input type="hidden" name="status" value="approved"><button type="submit">Approve · reviewed</button></form><form method="post" action="/draft/{{ d.item_id }}/status"><input type="hidden" name="status" value="rejected"><button class="secondary" type="submit">Dismiss</button></form></div>
</article>
{% else %}<div class="panel">No drafts waiting. Run a news check or confirm that your RSS sources are configured.</div>{% endfor %}
</section>{% endif %}</main>
{% if logged_in %}<script>
async function loadGroqUsage(){
 const target=document.getElementById("groq-usage");if(!target)return;
 try{
  const response=await fetch("/groq-usage",{credentials:"same-origin",cache:"no-store"});
  if(!response.ok)throw new Error("Usage status unavailable");
  const u=await response.json();
  target.textContent="Today ("+u.date+"): "+u.posts_created+"/"+u.post_limit+" AI drafts · Groq "+u.requests+"/"+u.request_limit+" requests · Gemini backup "+(u.fallback_requests||0)+" requests / "+(u.fallback_posts_created||0)+" drafts · "+u.tokens.toLocaleString()+" / "+u.token_limit.toLocaleString()+" Groq tokens"+(u.paused?" · Groq PAUSED: "+(u.pause_reason||"daily safety limit"):" · Groq ACTIVE")+(u.fallback_configured?(u.fallback_paused?" · Gemini COOLING DOWN: "+(u.fallback_pause_reason||"temporary provider error"):" · Gemini READY"):" · Gemini NOT CONFIGURED");
 }catch(error){target.textContent="Could not load Groq usage status."}
}
function renderFeedHealth(data){
 const target=document.getElementById('feed-health-list');
 if(!target)return;
 target.replaceChildren();
 const feeds=Array.isArray(data&&data.feed_details)?data.feed_details:[];
 if(!feeds.length){target.textContent='No source check details are available yet.';return;}
 feeds.forEach(feed=>{
  const row=document.createElement('div');row.className='feed-row';
  const left=document.createElement('div');
  const name=document.createElement('div');name.className='feed-name';name.textContent=feed.source||'Unnamed source';
  const meta=document.createElement('div');meta.className='feed-meta';
  meta.textContent='Priority: '+(feed.priority||'C · थप खोज')+' · Entries: '+(feed.entries_found||0)+' · Fresh: '+(feed.fresh_candidates||0)+' · New stories: '+(feed.new_items||0)+' · Duplicates: '+(feed.already_seen||0)+' · Old: '+(feed.stale_skipped||0)+' · Undated: '+(feed.undated_skipped||0);
  left.append(name,meta);
  const status=document.createElement('strong');
  if(feed.error){status.className='feed-error';status.textContent='ERROR';}
  else if(Number(feed.entries_found||0)===0){status.className='feed-ok';status.textContent='OK · no entries';}
  else{status.className='feed-ok';status.textContent='OK';}
  row.append(left,status);
  if(feed.error){const error=document.createElement('div');error.className='feed-error-text';error.textContent=feed.error;row.append(error);}
  target.append(row);
 });
}
async function runCheck(){
 const button=document.getElementById('check'),out=document.getElementById('result');
 if(button.disabled)return;
 button.disabled=true;button.textContent='Collecting news...';
 out.textContent='Starting news collection...';
 const startedAt=Date.now();
 try{
  const start=await fetch('/run-check',{method:'POST',credentials:'same-origin',cache:'no-store',headers:{'Accept':'application/json'}});
  const startState=await start.json().catch(()=>({}));
  if(start.status===401)throw new Error('Your admin session expired. Please sign in again.');
  if(!start.ok && start.status!==202)throw new Error(startState.error||'Could not start check (HTTP '+start.status+')');
  if(startState.error)throw new Error(startState.error);
  out.textContent='Checking RSS sources and preparing Nepali news drafts...';
  const poll=async()=>{
   if(Date.now()-startedAt>180000)throw new Error('The check is taking longer than 3 minutes. It may still be running on the server; refresh the dashboard to see its latest status.');
   const response=await fetch('/run-check',{credentials:'same-origin',cache:'no-store',headers:{'Accept':'application/json'}});
   if(response.status===401)throw new Error('Your admin session expired. Please sign in again.');
   if(!response.ok)throw new Error('Could not read check status (HTTP '+response.status+')');
   const state=await response.json();
   if(state.running){out.textContent='Still checking sources and preparing Nepali posts...';await new Promise(resolve=>setTimeout(resolve,2500));return poll();}
   if(state.finished){
    const data=state.result||{};
    renderFeedHealth(data);loadGroqUsage();
    const status=document.getElementById('monitoring-status');
    status.textContent='Last check completed · '+(state.trigger||'unknown')+' run';
    if(state.error)out.textContent='Check failed: '+state.error;
    else{
     const lines=[
      'News check complete.',
      'Sources checked: '+(data.feeds_checked||0)+' / '+(data.feeds_configured||0),
      'Fresh stories found: '+(data.fresh_candidates||0),
      'New drafts saved: '+(data.drafts_saved||0),
      'Potential breaking candidates: '+(data.breaking_candidates||0),
      'Already seen: '+(data.already_seen||0),
      'Facebook posts published: '+(data.published||0)
     ];
     if(data.errors&&data.errors.length)lines.push('Issues:\\n- '+data.errors.join('\\n- '));
     out.textContent=lines.join('\\n');
    }
    button.disabled=false;button.textContent='Check news now';
    if(!state.error)setTimeout(()=>location.reload(),2500);
    return;
   }
   throw new Error('The server did not confirm that the news check started. Please try again.');
  };
  await poll();
 }catch(error){out.textContent='News check: '+error.message;button.disabled=false;button.textContent='Check news now';}
}
window.addEventListener('load',async()=>{
 loadGroqUsage();setInterval(loadGroqUsage,30000);
 const el=document.getElementById('monitoring-status');
 let seenDrafts=0;
 try{
  const response=await fetch('/run-check',{credentials:'same-origin',cache:'no-store',headers:{'Accept':'application/json'}});
  const state=await response.json(); seenDrafts=Number(state.new_drafts||0);
  if(state.finished&&state.result)renderFeedHealth(state.result);
  if(state.running)el.textContent='Automatic news check is running.';
  else if(state.finished)el.textContent='Latest check completed. Waiting for the next automatic check.';
  else el.textContent='Automatic monitoring is ready.';
 }catch(error){el.textContent='Could not load monitoring status.';}
 setInterval(async()=>{
  try{
   const response=await fetch('/run-check',{credentials:'same-origin',cache:'no-store',headers:{'Accept':'application/json'}});
   if(!response.ok)return;
   const state=await response.json();
   if(state.finished&&state.result)renderFeedHealth(state.result);
   if(state.running)el.textContent='Automatically checking news sources...';
   if(Number(state.new_drafts||0)>seenDrafts){
    el.textContent='New news draft found. Updating dashboard...';
    location.reload();
   }
  }catch(error){}
 },2500);
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
    all_drafts = get_drafts(limit=100) if logged_in else []
    selected_category = (request.args.get("category") or "").strip()
    breaking_only = request.args.get("breaking") == "1"
    category_options = ["राजनीति", "खेलकुद", "अर्थतन्त्र", "शिक्षा", "प्रविधि", "अन्तर्राष्ट्रिय", "अन्य"]
    category_counts = {category: sum(1 for draft in all_drafts if (draft.get("category") or "अन्य") == category) for category in category_options}
    breaking_count = sum(1 for draft in all_drafts if draft.get("is_breaking"))
    drafts = [draft for draft in all_drafts if (not selected_category or (draft.get("category") or "अन्य") == selected_category) and (not breaking_only or draft.get("is_breaking"))]
    return render_template_string(
        PAGE,
        logged_in=logged_in,
        drafts=drafts,
        total_drafts=len(all_drafts),
        category_options=category_options,
        category_counts=category_counts,
        selected_category=selected_category,
        breaking_only=breaking_only,
        breaking_count=breaking_count,
        feed_count=len(configured_feeds()),
        monitoring_enabled=os.getenv("ENABLE_AUTO_MONITORING", "true").lower() == "true",
        check_interval=max(60, int(os.getenv("NEWS_CHECK_INTERVAL_SECONDS", "300"))),
        publishing=os.getenv("ENABLE_FACEBOOK_PUBLISHING", "false").lower() == "true",
        error=request.args.get("error"),
        notice=request.args.get("notice"),
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


@app.post("/draft/<item_id>/status")
@login_required
def update_draft_status(item_id):
    """Mark a draft reviewed or dismiss it; never publishes to social media."""
    status = (request.form.get("status") or "").strip().lower()
    if status not in {"approved", "rejected"}:
        return redirect(url_for("home", notice="Invalid review action."))
    set_draft_status(item_id, status)
    message = "Post marked as reviewed. It was not published." if status == "approved" else "Draft dismissed."
    return redirect(url_for("home", notice=message))


@app.get("/groq-usage")
@login_required
def groq_usage():
    """Expose daily Groq budget counters only to signed-in administrators."""
    return jsonify(get_groq_usage_status())


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
    if not _feed_execution_lock.acquire(blocking=False):
        with _check_lock:
            # The scheduled worker may have just reserved the lock but not yet
            # updated its status. Report a running state instead of returning
            # an empty state that makes the browser poll forever.
            if not _check_status["running"]:
                now = datetime.now(timezone.utc).isoformat()
                _check_status = {
                    "running": True, "finished": False, "result": None, "error": None,
                    "started_at": now, "finished_at": None, "trigger": "automatic",
                    "new_drafts": 0, "last_draft": None,
                }
            return jsonify(_check_status), 202
    with _check_lock:
        started = datetime.now(timezone.utc).isoformat()
        _check_status = {
            "running": True, "finished": False, "result": None, "error": None,
            "started_at": started, "finished_at": None, "trigger": "manual",
        }
    thread = threading.Thread(
        target=_run_check, args=("manual", True), daemon=True,
    )
    thread.start()
    return jsonify(running=True, message="News check started."), 202


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")))
