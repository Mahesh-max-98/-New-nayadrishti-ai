"""Routes and mobile UI for the Roman Nepali funny-photo creator."""
import base64
import threading
import uuid
from functools import wraps

from flask import Blueprint, jsonify, render_template_string, request, session, send_file
import io

from funny_post_creator import create_funny_post

funny_bp = Blueprint("funny_posts", __name__)
_jobs = {}
_lock = threading.Lock()

def _login_required(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        if not session.get("admin"):
            return jsonify(error="Please sign in first."), 401
        return fn(*args, **kwargs)
    return wrapped

@funny_bp.get("/funny-studio")
@_login_required
def page():
    return render_template_string("""
<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Roman Nepali Funny Post Creator</title>
<style>
body{margin:0;background:#f3f5f9;color:#172033;font-family:Arial,sans-serif}.wrap{max-width:760px;margin:auto;padding:18px}
.panel{background:#fff;border:1px solid #e5e7eb;border-radius:16px;padding:20px;margin:14px 0}
h1{font-size:25px;margin:12px 0}p{line-height:1.5;color:#475569}
input,select{width:100%;padding:13px;border:1px solid #cbd5e1;border-radius:9px;margin:8px 0 14px;font-size:16px}
button{background:#1d4ed8;color:white;border:0;border-radius:9px;padding:13px 18px;font-weight:700;font-size:16px}
button:disabled{opacity:.55}img{width:100%;border-radius:12px;margin-top:14px}a{color:#1d4ed8}
.status{white-space:pre-wrap;background:#f8fafc;padding:12px;border-radius:9px;margin-top:12px}
</style></head><body><main class="wrap"><section class="panel"><a href="/">Back to dashboard</a>
<h1>Funny Roman Nepali Post Creator</h1>
<p>Create original jokes, funny observations, savage one-liners and entertaining captions in Roman Nepali, written directly on a natural-looking photo.</p>
<label>Content type</label><select id="kind">
<option value="mixed">Surprise me — mixed content</option><option value="joke">Funny joke</option>
<option value="observation">Funny observation</option><option value="savage">Savage one-liner</option>
<option value="caption">Entertaining caption</option></select>
<label>Optional topic</label><input id="topic" maxlength="180" placeholder="e.g. student life, friendship, daily life">
<button id="go" onclick="makePost()">Create funny photo post</button>
<div id="status" class="status">Ready. Your joke will be written in Roman Nepali and placed directly on the photo.</div>
<div id="result"></div></section></main>
<script>
async function makePost(){
 const button=document.getElementById('go'),status=document.getElementById('status'),result=document.getElementById('result');
 button.disabled=true;result.replaceChildren();status.textContent='Writing your joke and generating a photo...';
 try{
  const response=await fetch('/funny-studio/generate',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({kind:document.getElementById('kind').value,topic:document.getElementById('topic').value})});
  const data=await response.json();if(!response.ok)throw new Error(data.error||'Could not start generation.');
  async function poll(){
   const sr=await fetch('/funny-studio/status/'+data.job_id),job=await sr.json();
   if(!sr.ok)throw new Error(job.error||'Job status unavailable.');
   if(job.status==='running'||job.status==='queued'){status.textContent=job.message||'Working...';setTimeout(()=>poll().catch(fail),2500);return;}
   if(job.status==='error')throw new Error(job.error||'Generation failed.');
   status.textContent='Your photo post is ready!';
   const quote=document.createElement('p');quote.textContent=job.quote||'';result.appendChild(quote);
   const image=document.createElement('img');image.alt='Roman Nepali joke written on a natural photo';image.src='/funny-studio/image/'+data.job_id;result.appendChild(image);
   const link=document.createElement('a');link.href='/funny-studio/download/'+data.job_id;link.textContent='Download image';result.appendChild(link);
   button.disabled=false;
  }
  await poll();
 }catch(error){fail(error);}
 function fail(error){status.textContent='Could not create post: '+error.message;button.disabled=false;}
}
</script></body></html>
""")

@funny_bp.post("/funny-studio/generate")
@_login_required
def generate():
    payload=request.get_json(silent=True) or {}
    kind=str(payload.get("kind") or "mixed").strip().lower()
    if kind not in ("mixed","joke","observation","savage","caption"):
        kind="mixed"
    topic=str(payload.get("topic") or "").strip()[:180]
    with _lock:
        if any(j.get("status") in ("queued","running") for j in _jobs.values()):
            return jsonify(error="A post is already being created. Please wait for it to finish."),429
        job_id=uuid.uuid4().hex
        _jobs[job_id]={"status":"queued","message":"Your request is queued..."}
        for old_id in list(_jobs)[:-5]:
            _jobs.pop(old_id,None)
    def work():
        with _lock:
            _jobs[job_id]={"status":"running","message":"Writing your Roman Nepali joke..."}
        try:
            quote,image_bytes=create_funny_post(topic,kind)
            with _lock:
                _jobs[job_id]={"status":"done","message":"Post created.","quote":quote,
                               "image":base64.b64encode(image_bytes).decode("ascii")}
        except Exception as exc:
            with _lock:
                _jobs[job_id]={"status":"error","error":type(exc).__name__+": "+str(exc)[:240]}
    threading.Thread(target=work,daemon=True).start()
    return jsonify(job_id=job_id,status="queued"),202

@funny_bp.get("/funny-studio/status/<job_id>")
@_login_required
def status(job_id):
    with _lock:
        job=_jobs.get(job_id)
        if not job:return jsonify(error="This post job is no longer available."),404
        return jsonify({key:value for key,value in job.items() if key!="image"})

@funny_bp.get("/funny-studio/image/<job_id>")
@_login_required
def image(job_id):
    with _lock:
        job=_jobs.get(job_id)
        encoded=job.get("image") if job and job.get("status")=="done" else None
    if not encoded:return "Image not ready.",404
    return send_file(io.BytesIO(base64.b64decode(encoded)),mimetype="image/jpeg",as_attachment=False,max_age=0)

@funny_bp.get("/funny-studio/download/<job_id>")
@_login_required
def download(job_id):
    with _lock:
        job=_jobs.get(job_id)
        encoded=job.get("image") if job and job.get("status")=="done" else None
    if not encoded:return "Image not ready.",404
    return send_file(io.BytesIO(base64.b64decode(encoded)),mimetype="image/jpeg",as_attachment=True,
                     download_name="roman-nepali-funny-post.jpg",max_age=0)
