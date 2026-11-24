import os
import hmac
import threading
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from functools import wraps
import csv
import io

from apscheduler.schedulers.background import BackgroundScheduler
from flask import Flask, Response, jsonify, redirect, render_template_string, request, session, url_for

from database import get_drafts, init_db, set_draft_status, get_pending_queue_status, get_facebook_publication_status, get_facebook_publishing_control, set_facebook_publishing_blocked, get_news_source_health, get_news_analytics, get_database_recovery_health
from groq_budget import get_groq_usage_status
from news_monitor import check_feeds, configured_feeds
from facebook_publisher import facebook_configuration_status, check_facebook_connection
from legal_pages import register_legal_routes
from review_gate import review_checklist_complete

app = Flask(__name__)
register_legal_routes(app)
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
    "new_drafts": 0, "last_draft": None, "progress": None,
}


def _record_new_draft(draft):
    """Publish per-story progress so the dashboard can refresh immediately."""
    with _check_lock:
        _check_status["new_drafts"] = int(_check_status.get("new_drafts", 0)) + 1
        _check_status["last_draft"] = {
            "title": str(draft.get("title", ""))[:180],
            "source": str(draft.get("source", ""))[:100],
        }
        if isinstance(_check_status.get("progress"), dict):
            _check_status["progress"]["drafts_saved"] = _check_status["new_drafts"]


def _record_check_progress(progress):
    """Expose per-feed progress to the dashboard while a scan is running."""
    with _check_lock:
        _check_status["progress"] = dict(progress or {})


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
                    "progress": {"phase": "starting", "message": "Waiting for active scan..."},
                }
        return
    with _check_lock:
        started = datetime.now(timezone.utc).isoformat()
        _check_status = {
            "running": True, "finished": False, "result": None, "error": None,
            "started_at": started, "finished_at": None, "trigger": trigger,
            "new_drafts": 0, "last_draft": None,
            "progress": {"phase": "starting", "message": "Preparing source scan..."},
        }
    try:
        result = check_feeds(on_new_draft=_record_new_draft, on_progress=_record_check_progress)
        with _check_lock:
            _check_status = {
                "running": False, "finished": True, "result": result, "error": None,
                "started_at": started, "finished_at": datetime.now(timezone.utc).isoformat(),
                "trigger": trigger,
                "new_drafts": int(_check_status.get("new_drafts", 0)),
                "last_draft": _check_status.get("last_draft"),
                "progress": {"phase": "complete", "feeds_processed": result.get("feeds_processed", 0),
                             "feeds_total": result.get("feeds_configured", 0),
                             "drafts_saved": result.get("drafts_saved", 0)},
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
                "progress": {"phase": "failed", "message": "News scan failed."},
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
.feed-row{padding:11px 0;border-top:1px solid #e5e7eb;display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap}.feed-name{font-weight:700}.filters{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin:12px 0}.filters select{padding:11px;border:1px solid #cbd5e1;border-radius:9px;background:#fff;min-width:170px}.tag{display:inline-block;padding:5px 9px;border-radius:999px;background:#e2e8f0;color:#334155;font-size:12px;font-weight:700;margin:0 5px 8px 0}.tag-breaking{background:#fee2e2;color:#991b1b}.feed-ok{color:#166534}.feed-error{color:#b91c1c}.feed-meta{color:#64748b;font-size:13px;margin-top:4px}.feed-error-text{color:#b91c1c;font-size:13px;margin-top:4px;overflow-wrap:anywhere}.analytics-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:10px;margin:14px 0}.analytics-card{background:#f8fafc;border:1px solid #e2e8f0;border-radius:10px;padding:12px;display:flex;flex-direction:column;gap:6px}.analytics-card strong{font-size:25px;color:#172554}.analytics-row{display:flex;justify-content:space-between;padding:7px 0;border-bottom:1px solid #e5e7eb}
a{color:#1d4ed8;overflow-wrap:anywhere}.danger{color:#b91c1c}details>summary{cursor:pointer;list-style-position:inside}details>summary:focus-visible{outline:2px solid #1d4ed8;outline-offset:4px;border-radius:5px}
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
<details class="panel" id="database-recovery"><summary><strong>Database safety &amp; recovery</strong> <span class="small">— health check and recovery information</span></summary><div style="padding-top:12px">
<p class="small">Read-only checks for the database connection and essential news tables. This check does not create or verify a backup.</p>
<div id="database-health-status" class="status">Database health has not been checked yet.</div>
<div id="database-health-tables" class="small"></div>
<p class="small danger"><strong>Important — Free database:</strong> Render Free Postgres does not include built-in backups. Render currently lists this database to expire on 22 December 2026. Ask the authorized workspace owner to arrange a separate export or upgrade before that date. After expiry, Render provides only a 14-day grace period before deletion. This dashboard check does not create an export.</p>
<p class="small"><a href="https://dashboard.render.com/d/dpg-dc1bad6q1p3s73fo5dr0-a" target="_blank" rel="noopener noreferrer">Open NayaDrishti database in Render</a></p>
<button type="button" class="secondary" id="refresh-database-health" onclick="loadDatabaseHealth()">Run read-only database check</button>
</div></details>
<section class="panel" id="news-analytics">
<h3>News desk analytics</h3>
<p class="small">Lifetime totals and recent activity from this dashboard. Counts describe collected records, not verified facts.</p>
<div class="analytics-grid">
<div class="analytics-card"><span class="small">Discovered items</span><strong id="analytics-discovered">—</strong></div>
<div class="analytics-card"><span class="small">Waiting for review</span><strong id="analytics-waiting">—</strong></div>
<div class="analytics-card"><span class="small">Reviewed</span><strong id="analytics-reviewed">—</strong></div>
<div class="analytics-card"><span class="small">Dismissed</span><strong id="analytics-dismissed">—</strong></div>
<div class="analytics-card"><span class="small">Drafts · last 7 days</span><strong id="analytics-week">—</strong></div>
<div class="analytics-card"><span class="small">Facebook posts published</span><strong id="analytics-published">—</strong></div>
</div>
<h4>Pending drafts by category</h4><div id="analytics-categories" class="small">Loading category totals...</div>
<h4>News source status</h4><div id="analytics-sources" class="small">Loading source totals...</div>
<button type="button" class="secondary" id="refresh-analytics" onclick="loadAnalytics()">Refresh analytics</button>
</section>
<section class="panel"><h3>Groq usage protection</h3><div id="groq-usage" class="small">Loading daily usage...</div><p class="small">Safety budgets are tracked using Nepal time. If both AI providers are unavailable, eligible stories are saved in a persistent recovery queue and retried with longer intervals for up to 7 days. Stories older than 72 hours are not auto-published, to avoid posting stale news. RSS feed checks continue. This cannot override provider quotas.</p></section>
<button id="check" onclick="runCheck()">Check news now</button><p class="small">Automatic checks: every 5 minutes, 5:00 AM–10:55 PM Nepal time, while the service is awake. The dashboard refreshes when a new draft is saved.</p>
<div id="result" class="status" aria-live="polite">Ready. Press the button to collect news.</div></section>\n<details class="panel" id="feed-health"><summary><strong>News source health</strong> <span class="small">— details from the latest check</span></summary><div id="feed-health-list" class="small" style="padding-top:12px">Run a news check to see which sources are working.</div><h4>Saved source history</h4><div id="persistent-feed-health" class="small">Loading saved source history...</div></details>
<section class="panel" id="facebook-publishing-panel">
<h2>Facebook publishing</h2>
<p>Auto-publishing: <strong>{{ 'ON' if publishing else 'OFF' }}</strong></p>
<p class="small">Keep publishing OFF while testing. Review several drafts and their original sources before turning it on. Posts with uncertain results are not retried automatically; check your Page first.</p>

<div id="facebook-status" class="small" role="status" aria-live="polite">Checking Facebook configuration...</div>
<div id="facebook-history" class="small" aria-live="polite">Loading publication totals...</div>
<button type="button" class="secondary" id="refresh-facebook-status" onclick="loadFacebookStatus()">Refresh Facebook status</button>
<button type="button" class="secondary" id="check-facebook-connection" onclick="checkFacebookConnection()">Check connection · read-only, no post</button>
<div id="facebook-connection-result" class="status" role="status" aria-live="polite">Connection check has not been run.</div>
</section>
<section><div class="top"><h2>News drafts</h2><span class="small">{{ drafts|length }} waiting for review</span></div>
<p class="small">Review each Nepali post before using it. Coverage labels compare collected headlines only; they do not prove a claim. Open original reports and compare details. Approve marks it reviewed only; it does not publish to Facebook.</p>
<p class="small"><a href="/export-drafts.csv">Download pending drafts as CSV</a> · Includes draft text and source links for your private editorial records.</p>
<form class="filters" method="get" action="/">
<label for="draft-search">Search news</label><input id="draft-search" name="q" type="search" value="{{ search_query }}" placeholder="Search headline or news text..." style="width:min(100%,320px);margin:0">
<label for="category">Category</label><select id="category" name="category"><option value="">All categories ({{ total_drafts }})</option>{% for category in category_options %}<option value="{{ category }}" {% if selected_category == category %}selected{% endif %}>{{ category }} ({{ category_counts.get(category, 0) }})</option>{% endfor %}</select>
<label><input type="checkbox" name="breaking" value="1" {% if breaking_only %}checked{% endif %} style="width:auto;margin:0 5px 0 0"> Potential breaking only ({{ breaking_count }})</label>
<button type="submit">Search / filter</button><a href="/">Clear</a>
</form>
{% if search_query %}<p class="small">Search results for “{{ search_query }}”: {{ drafts|length }} matching drafts.</p>{% endif %}
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
<details class="source-preview"><summary><strong>Original source preview</strong> <span class="small">— compare with the Nepali draft</span></summary>
{% if d.article_text %}<p class="small">Extracted source text · preview only; check the publisher's original page for the complete article.</p><p style="white-space:pre-wrap;line-height:1.7">{{ d.article_text[:2500] }}{% if d.article_text|length > 2500 %}…{% endif %}</p>
{% else %}<p class="small">Original article text was not available during collection. Open the original report to review it.</p>{% endif %}
</details>
<div class="actions"><form method="post" action="/draft/{{ d.item_id }}/status"><input type="hidden" name="status" value="approved"><label class="small"><input type="checkbox" name="read_original" value="yes" required style="width:auto"> मैले मूल समाचार खोलेर पढेको छु</label><label class="small"><input type="checkbox" name="checked_key_facts" value="yes" required style="width:auto"> नाम, स्थान र मुख्य तथ्य जाँचेको छु</label><label class="small"><input type="checkbox" name="summary_matches_source" value="yes" required style="width:auto"> पोस्ट मूल समाचारसँग मेल खान्छ</label><button type="submit">Approve · reviewed</button></form><form method="post" action="/draft/{{ d.item_id }}/status"><input type="hidden" name="status" value="rejected"><button class="secondary" type="submit">Dismiss</button></form></div>
</article>
{% else %}<div class="panel">No drafts waiting. Run a news check or confirm that your RSS sources are configured.</div>{% endfor %}
</section>{% endif %}</main>
{% if logged_in %}<script>
async function loadDatabaseHealth(){
 const button=document.getElementById('refresh-database-health');
 const status=document.getElementById('database-health-status');
 const tableBox=document.getElementById('database-health-tables');
 if(button)button.disabled=true;
 if(status)status.textContent='Checking database connection and essential tables...';
 if(tableBox)tableBox.replaceChildren();
 try{
  const response=await fetch('/database-health',{credentials:'same-origin',cache:'no-store'});
  if(response.status===401)throw new Error('Sign in again to run this check.');
  if(!response.ok)throw new Error('Database check failed (HTTP '+response.status+').');
  const data=await response.json();
  const mb=(Number(data.database_size_bytes||0)/1048576).toFixed(2);
  status.textContent=(data.status==='healthy'?'Core database tables are present.':'Some required tables are missing.')
   +' Database: '+data.database_name+' · PostgreSQL '+data.postgres_version
   +' · Size: '+mb+' MB · Public tables: '+data.table_count+'.';
  (data.required_tables||[]).forEach(item=>{
   const row=document.createElement('div');row.className='analytics-row';
   const name=document.createElement('span');name.textContent=item.name;
   const count=document.createElement('strong');
   count.textContent=item.present?'Present · '+Number(item.row_count||0).toLocaleString()+' rows':'MISSING';
   row.append(name,count);tableBox.append(row);
  });
  const note=document.createElement('p');note.className='small';note.textContent=data.note||'Backup status not verified.';
  tableBox.append(note);
 }catch(error){status.textContent='Could not check database health. '+error.message;}
 finally{if(button)button.disabled=false;}
}
async function loadAnalytics(){
 const refresh=document.getElementById('refresh-analytics');
 if(refresh)refresh.disabled=true;
 try{
  const response=await fetch('/analytics',{credentials:'same-origin',cache:'no-store'});
  if(response.status===401)throw new Error('Sign in again to view analytics.');
  if(!response.ok)throw new Error('Analytics request failed (HTTP '+response.status+').');
  const data=await response.json(),drafts=data.drafts||{},sources=data.sources||{};
  const values={
   'analytics-discovered':data.discovered_items,
   'analytics-waiting':drafts.waiting_review,
   'analytics-reviewed':drafts.reviewed,
   'analytics-dismissed':drafts.dismissed,
   'analytics-week':drafts.created_last_7_days,
   'analytics-published':data.facebook_published
  };
  Object.entries(values).forEach(([id,value])=>{const el=document.getElementById(id);if(el)el.textContent=Number(value||0).toLocaleString();});
  const categories=document.getElementById('analytics-categories');
  categories.replaceChildren();
  if(!(data.categories||[]).length)categories.textContent='No pending drafts by category yet.';
  (data.categories||[]).forEach(item=>{
   const row=document.createElement('div');row.className='analytics-row';
   const name=document.createElement('span');name.textContent=item.category;
   const count=document.createElement('strong');count.textContent=Number(item.count||0).toLocaleString();
   row.append(name,count);categories.append(row);
  });
  const target=document.getElementById('analytics-sources');
  target.textContent='Healthy: '+Number(sources.healthy||0)+' · Warning: '+Number(sources.warning||0)+' · Error: '+Number(sources.error||0)+' · Tracked: '+Number(sources.total||0);
 }catch(error){
  const target=document.getElementById('analytics-categories');
  if(target)target.textContent='Could not load analytics. '+error.message;
 }finally{if(refresh)refresh.disabled=false;}
}
async function loadFacebookStatus(){
 const statusTarget=document.getElementById("facebook-status");
 const historyTarget=document.getElementById("facebook-history");
 const refreshButton=document.getElementById("refresh-facebook-status");
 if(!statusTarget||!historyTarget)return;
 if(refreshButton)refreshButton.disabled=true;
 statusTarget.textContent="Checking Facebook configuration...";
 historyTarget.textContent="Loading publication totals...";
 try{
  const response=await fetch("/facebook-status",{credentials:"same-origin",cache:"no-store",headers:{"Accept":"application/json"}});
  if(response.status===401||response.status===403)throw new Error("Your admin session has expired or lacks permission. Sign in again to view Facebook status.");
  if(!response.ok)throw new Error("Facebook status request failed (HTTP "+response.status+").");
  const data=await response.json();
  const config=data.config||{},history=data.history||{},counts=history.counts||{},control=data.publishing_control||{};
  const line=document.createElement("p");
  line.textContent="Configuration: "+(config.enabled?(config.credentials_present?"Configured · permissions not verified":"Incomplete · add missing Page credentials"):"Disabled")+
   " · Page ID: "+(config.page_id_configured?"configured":"missing")+
   " · Page access token: "+(config.page_token_configured?"configured":"missing")+
   (control.auth_blocked?" · AUTO-PUBLISH PAUSED: "+(control.reason||"connection check required"):"");
  statusTarget.replaceChildren(line);
  const totals=document.createElement("p");
  totals.textContent="Publication totals: "+(counts.published||0)+" published · "+(counts.failed||0)+" failed · "+(counts.unknown||0)+" uncertain · "+(counts.publishing||0)+" in progress.";
  historyTarget.replaceChildren(totals);
 }catch(error){
  statusTarget.textContent=error.message||"Could not load Facebook configuration status.";
  historyTarget.textContent="Publication history could not be loaded. Refresh after signing in again.";
 }finally{if(refreshButton)refreshButton.disabled=false;}
}
async function checkFacebookConnection(){
 const button=document.getElementById("check-facebook-connection");
 const target=document.getElementById("facebook-connection-result");
 if(!button||!target)return;
 button.disabled=true;target.textContent="Checking the Page connection with a read-only Meta request...";
 try{
  const response=await fetch("/facebook-connection-check",{method:"POST",credentials:"same-origin",cache:"no-store",headers:{"Accept":"application/json"}});
  const result=await response.json().catch(()=>({}));
  if(response.status===401)throw new Error("Your admin session expired. Sign in again.");
  if(!response.ok)throw new Error(result.message||"Connection check failed (HTTP "+response.status+").");
  target.textContent=result.message||"Connection check finished.";
  if(result.page_name)target.textContent+=" Page: "+result.page_name+".";
  if(result.error_code)target.textContent+=" Error code: "+result.error_code+(result.error_subcode?"/"+result.error_subcode:"")+".";
  await loadFacebookStatus();
 }catch(error){target.textContent=error.message||"Could not check the Facebook connection.";}
 finally{button.disabled=false;}
}
async function loadGroqUsage(){
 const target=document.getElementById("groq-usage");if(!target)return;
 try{
  const response=await fetch("/groq-usage",{credentials:"same-origin",cache:"no-store"});
  if(!response.ok)throw new Error("Usage status unavailable");
  const u=await response.json();
  const q=u.pending_queue||{};
  target.textContent="Today ("+u.date+"): "+u.posts_created+"/"+u.post_limit+" AI drafts · Groq "+u.requests+"/"+u.request_limit+" requests · Gemini backup "+(u.fallback_requests||0)+" requests / "+(u.fallback_posts_created||0)+" drafts · "+u.tokens.toLocaleString()+" / "+u.token_limit.toLocaleString()+" Groq tokens"+(u.paused?" · Groq PAUSED: "+(u.pause_reason||"daily safety limit"):" · Groq ACTIVE")+(u.fallback_configured?(u.fallback_paused?" · Gemini COOLING DOWN: "+(u.fallback_pause_reason||"temporary provider error"):" · Gemini READY"):" · Gemini NOT CONFIGURED")+" · Recovery queue: "+(q.pending||0)+" waiting, "+(q.due||0)+" ready to retry, "+(q.expired||0)+" expired.";
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
  meta.textContent='Priority: '+(feed.priority||'C · थप खोज')+' · Entries: '+(feed.entries_found||0)+' · Fresh: '+(feed.fresh_candidates||0)+' · New posts/drafts: '+(feed.new_items||0)+' · Duplicates: '+(feed.already_seen||0)+' · Old: '+(feed.stale_skipped||0)+' · Short headlines saved: '+(feed.short_source_drafts||0)+' · Undated review: '+(feed.undated_review_drafts||0);
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
async function loadPersistentFeedHealth(){
 const target=document.getElementById('persistent-feed-health');if(!target)return;
 try{
  const response=await fetch('/source-health',{credentials:'same-origin',cache:'no-store'});
  if(!response.ok)throw new Error('Source history unavailable');
  const data=await response.json();const sources=Array.isArray(data.sources)?data.sources:[];
  target.replaceChildren();
  if(!sources.length){target.textContent='No saved source history yet. Run a news check.';return;}
  sources.forEach(source=>{
   const row=document.createElement('div');row.className='feed-row';
   const left=document.createElement('div');
   const name=document.createElement('div');name.className='feed-name';name.textContent=source.source_name;
   const meta=document.createElement('div');meta.className='feed-meta';
   meta.textContent='Consecutive failed checks: '+source.consecutive_failures+' · Last HTTP: '+(source.last_http_status||'not recorded')+' · Entries last scan: '+source.last_entries_found+' · Last duration: '+source.last_duration_ms+' ms';
   left.append(name,meta);
   const status=document.createElement('strong');
   status.className=source.status==='error'?'feed-error':source.status==='warning'?'danger':'feed-ok';
   status.textContent=source.status==='error'?'ERROR':source.status==='warning'?'WARNING':'HEALTHY';
   row.append(left,status);target.append(row);
   if(source.last_error){const err=document.createElement('div');err.className='feed-error-text';err.textContent=source.last_error;target.append(err);}
  });
 }catch(error){target.textContent='Could not load saved source health.';}
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
   if(Date.now()-startedAt>600000)throw new Error('The scan is taking longer than 10 minutes. Refresh the dashboard to see whether it is still running or has completed.');
   const response=await fetch('/run-check',{credentials:'same-origin',cache:'no-store',headers:{'Accept':'application/json'}});
   if(response.status===401)throw new Error('Your admin session expired. Please sign in again.');
   if(!response.ok)throw new Error('Could not read check status (HTTP '+response.status+')');
   const state=await response.json();
   if(state.running){
    const p=state.progress||{};
    const where=p.source?' · '+p.source:'';
    const count=Number(p.feeds_processed||0)+'/'+Number(p.feeds_total||0);
    const story=p.story_index?' · Story '+p.story_index+'/'+(p.story_total||'?'):'';
    out.textContent='Checking news sources '+count+where+story+' · New drafts: '+Number(state.new_drafts||0);
    await new Promise(resolve=>setTimeout(resolve,2500));return poll();
   }
   if(state.finished){
    const data=state.result||{};
    renderFeedHealth(data);loadPersistentFeedHealth();loadGroqUsage();
    const status=document.getElementById('monitoring-status');
    status.textContent='Last check completed · '+(state.trigger||'unknown')+' run';
    if(state.error)out.textContent='Check failed: '+state.error;
    else{
     const lines=[
      'News check complete.',
      'Sources checked: '+(data.feeds_checked||0)+' / '+(data.feeds_configured||0),
      'RSS entries discovered across sources: '+(data.entries_found_total||0),
      'Fresh dated stories found: '+(data.fresh_candidates||0),
      'Short-source headline drafts saved: '+(data.short_source_drafts||0),
      'Undated drafts saved for manual review: '+(data.undated_review_drafts||0),
      'Sources with feed errors: '+(data.feed_errors||0),
      'Stories skipped because source text was too short: '+(data.short_source_skipped||0),
      'New drafts saved: '+(data.drafts_saved||0),
      'Potential breaking candidates: '+(data.breaking_candidates||0),
      'Already seen: '+(data.already_seen||0),
      'Old stories skipped: '+(data.stale_skipped||0),
      'Article pages fetched: '+(data.article_fetches||0),
      'Facebook posts published: '+(data.published||0)
     ];
     if(!(data.drafts_saved||0))lines.push('No new eligible drafts were saved in this scan. Check source health for errors, duplicates, or no recent entries.');
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
 loadDatabaseHealth();loadAnalytics();loadGroqUsage();loadFacebookStatus();loadPersistentFeedHealth();setInterval(loadGroqUsage,30000);setInterval(loadFacebookStatus,30000);
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
    search_query = (request.args.get("q") or "").strip()[:100]
    search_term = search_query.casefold()
    breaking_only = request.args.get("breaking") == "1"
    category_options = ["राजनीति", "खेलकुद", "अर्थतन्त्र", "शिक्षा", "प्रविधि", "अन्तर्राष्ट्रिय", "अन्य"]
    category_counts = {category: sum(1 for draft in all_drafts if (draft.get("category") or "अन्य") == category) for category in category_options}
    breaking_count = sum(1 for draft in all_drafts if draft.get("is_breaking"))
    drafts = [
        draft for draft in all_drafts
        if (not selected_category or (draft.get("category") or "अन्य") == selected_category)
        and (not breaking_only or draft.get("is_breaking"))
        and (
            not search_term
            or search_term in str(draft.get("summary") or "").casefold()
            or search_term in str(draft.get("source") or "").casefold()
            or search_term in str(draft.get("category") or "").casefold()
        )
    ]
    return render_template_string(
        PAGE,
        logged_in=logged_in,
        drafts=drafts,
        total_drafts=len(all_drafts),
        category_options=category_options,
        category_counts=category_counts,
        selected_category=selected_category,
        search_query=search_query,
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
    if status == "approved" and not review_checklist_complete(request.form):
        return redirect(url_for(
            "home",
            notice="Review checklist incomplete. Open the original report and confirm all three checks before marking this draft reviewed."
        ))
    set_draft_status(item_id, status)
    message = "Post marked as reviewed. It was not published." if status == "approved" else "Draft dismissed."
    return redirect(url_for("home", notice=message))


@app.get("/export-drafts.csv")
@login_required
def export_pending_drafts():
    """Export pending editorial drafts for the signed-in admin; never publishes them."""
    drafts = get_drafts(limit=5000)
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(["headline", "nepali_draft", "category", "publisher", "original_article_url", "created_at"])
    for draft in drafts:
        writer.writerow([
            draft.get("title") or "",
            draft.get("summary") or "",
            draft.get("category") or "",
            draft.get("source") or "",
            draft.get("url") or "",
            draft.get("created_at").isoformat() if hasattr(draft.get("created_at"), "isoformat") else (draft.get("created_at") or ""),
        ])
    response = Response("\ufeff" + output.getvalue(), mimetype="text/csv; charset=utf-8")
    response.headers["Content-Disposition"] = 'attachment; filename="nayadrishti-pending-drafts.csv"'
    response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/database-health")
@login_required
def database_health():
    """Return read-only core database integrity information to signed-in admins."""
    return jsonify(get_database_recovery_health())


@app.get("/analytics")
@login_required
def analytics():
    """Return aggregate news desk metrics to signed-in administrators."""
    return jsonify(get_news_analytics())


@app.get("/source-health")
@login_required
def source_health():
    """Return persistent health history for configured news feeds."""
    return jsonify(sources=get_news_source_health(limit=100))


@app.get("/groq-usage")
@login_required
def groq_usage():
    """Expose daily Groq budget counters only to signed-in administrators."""
    status = get_groq_usage_status()
    status["pending_queue"] = get_pending_queue_status()
    return jsonify(status)


@app.get("/facebook-status")
@login_required
def facebook_status():
    """Show credential presence and durable publication history, never secrets."""
    return jsonify(
        config=facebook_configuration_status(),
        history=get_facebook_publication_status(limit=20),
        publishing_control=get_facebook_publishing_control(),
    )


@app.post("/facebook-connection-check")
@login_required
def facebook_connection_check():
    """Validate the configured Page token with a read-only Graph API request."""
    result = check_facebook_connection()
    if result.get("ok"):
        set_facebook_publishing_blocked(False, "")
    elif str(result.get("error_code")) == "190":
        set_facebook_publishing_blocked(
            True, "Facebook publishing paused after Meta rejected the Page access token. Reconnect the Page before enabling publishing."
        )
    return jsonify(
        **result,
        publishing_control=get_facebook_publishing_control(),
    )


@app.get("/health")
def health():
    return jsonify(status="ok", service="NayaDrishti AI", mode="text_only")


@app.post("/scheduled-check")
def external_scheduled_check():
    """Allow the independent GitHub Actions scheduler to start a news check."""
    expected = os.getenv("SCHEDULER_SHARED_SECRET", "").strip()
    authorization = request.headers.get("Authorization", "")
    supplied = authorization[7:].strip() if authorization.startswith("Bearer ") else ""
    if not expected or not supplied or not hmac.compare_digest(supplied, expected):
        return jsonify(error="Unauthorized scheduler request."), 401

    # Keep the same Nepal daytime window as the in-process scheduler.
    local_now = datetime.now(ZoneInfo("Asia/Kathmandu"))
    if not (5 <= local_now.hour <= 22 and (local_now.hour < 22 or local_now.minute <= 55)):
        return jsonify(status="outside_schedule", timezone="Asia/Kathmandu"), 200

    if not _feed_execution_lock.acquire(blocking=False):
        with _check_lock:
            return jsonify(status="already_running", check=_check_status), 202

    with _check_lock:
        started = datetime.now(timezone.utc).isoformat()
        _check_status.update({
            "running": True, "finished": False, "result": None, "error": None,
            "started_at": started, "finished_at": None, "trigger": "github_actions",
            "new_drafts": 0, "last_draft": None,
        })

    thread = threading.Thread(
        target=_run_check, args=("github_actions", True), daemon=True,
    )
    thread.start()
    return jsonify(status="started", trigger="github_actions"), 202


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
            "new_drafts": 0, "last_draft": None,
            "progress": {"phase": "starting", "message": "Starting manual scan..."},
        }
    thread = threading.Thread(
        target=_run_check, args=("manual", True), daemon=True,
    )
    thread.start()
    return jsonify(running=True, message="News check started."), 202


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")))