import os
import threading
from datetime import datetime, timezone
from functools import wraps

from flask import Flask, jsonify, redirect, render_template_string, request, session, url_for, Response
from apscheduler.schedulers.background import BackgroundScheduler

from database import init_db, get_drafts, get_draft_image, replace_draft_image
from image_generator import create_fallback_news_graphic
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

# Keep feed checks out of HTTP requests and prevent scheduled/manual overlap.
_check_lock = threading.Lock()
_feed_execution_lock = threading.Lock()
_check_status = {"running": False, "finished": False, "result": None, "error": None, "started_at": None, "finished_at": None, "trigger": None}


def _execute_feed_check():
    global _check_status
    if not _feed_execution_lock.acquire(blocking=False):
        return
    try:
        result = check_feeds()
        with _check_lock:
            _check_status = {"running": False, "finished": True, "result": result, "error": None, "started_at": _check_status.get("started_at"), "finished_at": datetime.now(timezone.utc).isoformat(), "trigger": _check_status.get("trigger", "manual")}
    except Exception as exc:
        app.logger.exception("Feed check failed")
        with _check_lock:
            _check_status = {
                "running": False, "finished": True, "result": None,
                "error": f"{type(exc).__name__}: {str(exc)[:300]}",
                "started_at": _check_status.get("started_at"),
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "trigger": _check_status.get("trigger", "manual"),
            }
    finally:
        _feed_execution_lock.release()


def _run_feed_check_in_background():
    _execute_feed_check()


def _scheduled_feed_check():
    global _check_status
    if not _feed_execution_lock.acquire(blocking=False):
        app.logger.info("Scheduled feed check skipped: another check is already running.")
        return
    with _check_lock:
        _check_status = {"running": True, "finished": False, "result": None, "error": None, "started_at": datetime.now(timezone.utc).isoformat(), "finished_at": None, "trigger": "automatic"}
    try:
        result = check_feeds()
        with _check_lock:
            _check_status = {"running": False, "finished": True, "result": result, "error": None, "started_at": _check_status.get("started_at"), "finished_at": datetime.now(timezone.utc).isoformat(), "trigger": "automatic"}
    except Exception as exc:
        app.logger.exception("Scheduled feed check failed")
        with _check_lock:
            _check_status = {
                "running": False, "finished": True, "result": None,
                "error": f"{type(exc).__name__}: {str(exc)[:300]}",
                "started_at": _check_status.get("started_at"),
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "trigger": "automatic",
            }
    finally:
        _feed_execution_lock.release()


# On Render's free web service this runs only while the service is awake.
# A continuously running paid worker would be needed for guaranteed 24/7 checks.
scheduler = BackgroundScheduler(daemon=True)
if os.getenv("ENABLE_AUTO_MONITORING", "true").lower() == "true":
    scheduler.add_job(
        _scheduled_feed_check,
        trigger="interval",
        seconds=max(180, int(os.getenv("NEWS_CHECK_INTERVAL_SECONDS", "180"))),
        id="nayadrishti_news_monitor",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    scheduler.start()
    app.logger.info("Automatic news monitoring enabled (minimum interval: 180 seconds).")
else:
    app.logger.info("Automatic news monitoring is disabled.")


PAGE = """
<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>NayaDrishti AI</title>
<style>
*{box-sizing:border-box}body{margin:0;background:#f3f5f9;color:#172033;font-family:Arial,sans-serif}
.wrap{max-width:920px;margin:auto;padding:18px}header{background:#172554;color:#fff;padding:24px;border-radius:16px;margin:8px 0 18px}
h1{margin:0 0 6px;font-size:27px}header p{margin:4px 0;color:#dbeafe}
.panel,.draft{background:#fff;border:1px solid #e5e7eb;border-radius:14px;padding:18px;margin:14px 0}
button{background:#1d4ed8;color:white;border:0;border-radius:9px;padding:12px 16px;font-weight:700;font-size:15px}
input{width:100%;padding:13px;border:1px solid #cbd5e1;border-radius:9px;margin:8px 0 14px;font-size:16px}
.small{color:#64748b;font-size:13px}.status{white-space:pre-wrap;overflow-wrap:anywhere;background:#f8fafc;padding:12px;border-radius:9px;margin-top:12px}
.draft h3{margin:0 0 8px}.draft .post-headline{white-space:pre-line;line-height:1.65;font-size:21px}.draft p{white-space:pre-wrap;line-height:1.55}.top{display:flex;justify-content:space-between;gap:10px;align-items:center;flex-wrap:wrap}
a{color:#1d4ed8;overflow-wrap:anywhere}.danger{color:#b91c1c}.draft img{width:100%;max-height:420px;object-fit:contain;background:#0f172a;border-radius:10px;margin:10px 0}
</style></head><body><main class="wrap">
<header><h1>NayaDrishti AI</h1><p>नयाँ दृष्टि, नयाँ खबर।</p><p>A New Perspective on Every Story.</p></header>
{% if not logged_in %}
<section class="panel"><h2>Admin sign in</h2><p class="small">Sign in to check feeds and view generated drafts.</p>
<form method="post" action="/login"><label for="password">Admin password</label>
<input id="password" name="password" type="password" required autocomplete="current-password">
<button type="submit">Sign in</button></form>{% if error %}<p class="danger">{{ error }}</p>{% endif %}</section>
{% else %}
<section class="panel"><div class="top"><h2>Feed controls</h2><form method="post" action="/logout"><button>Log out</button></form></div>
<p>Configured feeds: <strong>{{ feed_count }}</strong></p>
<p>Automatic monitoring: <strong>{{ "ON" if monitoring_enabled else "OFF" }}</strong> · Check interval: <strong>{{ check_interval }} seconds</strong></p>
<p class="small">Automatic checks run only while this free Render web service is awake. GitHub keep-awake pings are best-effort; this is not guaranteed 24/7 monitoring.</p>
<div id="monitoring-status" class="status">Loading latest monitoring status...</div>
<p class="small">Hybrid image mode: permitted publisher photo first, free AI-generated editorial illustration second, and a clearly labelled NayaDrishti graphic if image generation is unavailable. AI images are illustrations, not documentary photographs. Facebook auto-publishing remains off for testing.</p>
<button id="check" onclick="runCheck()">Run a feed check</button>
<div id="result" class="status" aria-live="polite">Ready. Press the button to check your feeds.</div></section>
<section class="panel"><h2>Publishing status</h2>
<p>Facebook auto-publishing: <strong>{{ 'ON' if publishing else 'OFF' }}</strong></p>
<p class="small">Do not enable until you have reviewed generated text and images.</p></section>
<section><div class="top"><h2>Generated news drafts</h2><span class="small">{{ drafts|length }} shown</span></div>
{% for d in drafts %}<article class="draft"><h3 class="post-headline">{{ d.summary }}</h3>
{% if d.has_image %}<img src="/draft-image/{{ d.item_id }}" alt="NayaDrishti editorial news graphic">
{% if d.image_status == 'fallback_graphic' %}<button type="button" onclick="refreshGraphic('{{ d.item_id }}', this)">Refresh graphic design</button><span class="small">Rebuilds this saved graphic using the latest design without using AI credits.</span>{% endif %}
{% else %}<p class="danger">Image not ready — this draft will not be auto-published.</p>{% if d.image_error %}<p class="small danger">Image error: {{ d.image_error }}</p>{% else %}<p class="small">The system will retry image generation after its 30-minute retry cooldown.</p>{% endif %}{% endif %}
<div class="small">Image style: {{ 'Publisher photo' if d.image_status == 'publisher_photo' else ('AI illustration' if d.image_status == 'ai_illustration' else ('Branded fallback graphic (not a photograph)' if d.image_status == 'fallback_graphic' else (d.image_status or 'pending'))) }}</div>
<a href="{{ d.url }}" target="_blank" rel="noopener noreferrer">Open original source</a></article>
{% else %}<div class="panel">No drafts yet. Add valid RSS/Atom feed URLs in Render, then run a feed check.</div>{% endfor %}
</section>{% endif %}</main>
{% if logged_in %}<script>
async function refreshGraphic(itemId,button){
 button.disabled=true;button.textContent='Refreshing design...';
 try{
  const response=await fetch('/refresh-fallback/'+itemId,{method:'POST',headers:{'Accept':'application/json'}});
  const data=await response.json();
  if(!response.ok)throw new Error(data.error||('HTTP '+response.status));
  location.reload();
 }catch(error){button.disabled=false;button.textContent='Refresh graphic design';alert('Could not refresh graphic: '+error.message);}
}
async function runCheck(){const b=document.getElementById('check'),out=document.getElementById('result');
b.disabled=true;b.textContent='Checking...';out.textContent='Feed check started. News and images are being processed in the background.';
try{const r=await fetch('/run-check',{method:'POST',headers:{'Accept':'application/json'}});const data=await r.json();
if(!r.ok){out.textContent='Request failed (HTTP '+r.status+'): '+JSON.stringify(data,null,2);b.disabled=false;b.textContent='Run a feed check';return;}
const poll=async()=>{try{const s=await fetch('/run-check',{headers:{'Accept':'application/json'}});const state=await s.json();
if(state.running){out.textContent='Still checking feeds and generating images...';document.getElementById("monitoring-status").textContent="A news check is currently running ("+(state.trigger||"unknown")+" check).";setTimeout(poll,2500);return;}
if(state.finished){
const x=state.result||{};
document.getElementById("monitoring-status").textContent="Last check: "+(state.finished_at||"time unavailable")+" · Started: "+(state.started_at||"unknown")+" · Type: "+(state.trigger||"unknown");
if(state.error){out.textContent='Feed check failed: '+state.error;}
else if(x.errors&&x.errors.length){const details=(x.feed_details||[]).map(f=>f.source+': '+f.entries_found+' entries'+(f.error?' · ERROR '+f.error:'')).join('\\n');out.textContent='Feed check finished. Feeds checked: '+x.feeds_checked+'/'+x.feeds_configured+'\\nNew stories: '+x.new_items+'\\nDrafts saved: '+x.drafts_saved+'\\nAI illustrations: '+x.ai_illustrations_used+'\\nFallback graphics: '+(x.fallback_graphics_used||0)+'\\nAlready seen: '+x.already_seen+'\\nFeed diagnostics:\\n'+details+'\\nErrors:\\n- '+x.errors.join('\\n- ');}
else if(x.new_items===0){const details=(x.feed_details||[]).map(f=>f.source+': '+f.entries_found+' entries, '+f.already_seen+' already seen'+(f.error?' · ERROR '+f.error:'')+(f.sample_titles&&f.sample_titles.length?' · Latest: '+f.sample_titles[0]:'')).join('\\n');out.textContent='Feed check finished, but no new unseen stories were saved. Feeds checked: '+x.feeds_checked+'/'+x.feeds_configured+'\\nAlready seen: '+x.already_seen+'\\nFeed diagnostics:\\n'+(details||'No feed details returned.')+'\\nThis shows whether feeds are empty, returning old stories, or failing.';}
else{out.textContent='Feed check complete!\\nFeeds checked: '+x.feeds_checked+'/'+x.feeds_configured+'\\nNew stories: '+x.new_items+'\\nDrafts saved: '+x.drafts_saved+'\\nAI illustrations: '+x.ai_illustrations_used+'\\nFallback graphics: '+(x.fallback_graphics_used||0)+'\\nPublisher photos: '+x.publisher_photos_used+'\\nFacebook published: '+x.published;}
b.disabled=false;b.textContent='Run a feed check';setTimeout(()=>location.reload(),2500);return;}
out.textContent='Waiting for feed check status...';setTimeout(poll,2500);
}catch(e){out.textContent='Could not read check status: '+e.message;b.disabled=false;b.textContent='Run a feed check';}};poll();
}catch(e){out.textContent='Request failed: '+e.message;b.disabled=false;b.textContent='Run a feed check';}}
window.addEventListener("load",async()=>{try{const r=await fetch("/run-check",{headers:{"Accept":"application/json"}});if(!r.ok)return;const s=await r.json();const el=document.getElementById("monitoring-status");if(!el)return;if(s.running){el.textContent="A news check is currently running ("+(s.trigger||"unknown")+" check).";}else if(s.finished){el.textContent="Last check: "+(s.finished_at||"time unavailable")+" · Started: "+(s.started_at||"unknown")+" · Type: "+(s.trigger||"unknown");}else{el.textContent="No check has completed since this server started. Automatic monitoring is "+("{{ \"enabled\" if monitoring_enabled else \"disabled\" }}")+". The next scheduled check runs while Render is awake.";}}catch(e){}});
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
        PAGE, logged_in=logged_in, drafts=get_drafts() if logged_in else [],
        feed_count=len(configured_feeds()),
        monitoring_enabled=os.getenv("ENABLE_AUTO_MONITORING", "true").lower() == "true",
        check_interval=max(180, int(os.getenv("NEWS_CHECK_INTERVAL_SECONDS", "180"))),
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
    return jsonify(status="ok", service="NayaDrishti AI")


@app.post("/refresh-fallback/<item_id>")
@login_required
def refresh_fallback(item_id):
    """Rebuild an existing fallback graphic using the current design template."""
    draft = next((item for item in get_drafts(limit=100) if item["item_id"] == item_id), None)
    if not draft:
        return jsonify(error="Draft not found."), 404
    if draft.get("image_status") != "fallback_graphic":
        return jsonify(error="Only fallback graphics can be refreshed here."), 409
    try:
        graphic = create_fallback_news_graphic(draft["title"], draft.get("summary") or "")
        if not replace_draft_image(item_id, graphic, "fallback_graphic"):
            return jsonify(error="Could not save the refreshed graphic."), 500
        return jsonify(success=True, message="Fallback graphic refreshed.")
    except Exception as exc:
        app.logger.exception("Could not refresh fallback graphic for %s", item_id)
        return jsonify(error=f"Graphic refresh failed: {type(exc).__name__}: {str(exc)[:180]}"), 500


@app.get("/draft-image/<item_id>")
@login_required
def draft_image(item_id):
    data = get_draft_image(item_id)
    if not data:
        return "Image not found.", 404
    return Response(data, mimetype="image/jpeg", headers={"Cache-Control": "private, no-store, max-age=0"})


@app.route("/run-check", methods=["GET", "POST"])
@login_required
def run_check():
    global _check_status
    if request.method == "GET":
        with _check_lock:
            return jsonify(_check_status)

    with _check_lock:
        if _check_status["running"]:
            return jsonify(_check_status), 202
        _check_status = {"running": True, "finished": False, "result": None, "error": None, "started_at": datetime.now(timezone.utc).isoformat(), "finished_at": None, "trigger": "manual"}

    thread = threading.Thread(target=_run_feed_check_in_background, daemon=True)
    thread.start()
    return jsonify(running=True, message="Feed check started in the background."), 202


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")))
