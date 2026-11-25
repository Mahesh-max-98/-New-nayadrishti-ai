import hmac
import os
import secrets
from functools import wraps

from flask import Flask, abort, jsonify, redirect, render_template_string, request, session, url_for

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "").strip()
if not app.secret_key:
    raise RuntimeError("FLASK_SECRET_KEY is required.")

app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.getenv("COOKIE_SECURE", "true").lower() == "true",
)

PAGE = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="theme-color" content="#101b35">
  <title>NayaDrishti AI</title>
  <style>
    * { box-sizing: border-box; }
    body { margin: 0; min-height: 100vh; color: #172033; background: #f4f7fc;
      font-family: Arial, Helvetica, sans-serif; }
    .shell { width: min(920px, calc(100% - 32px)); margin: 36px auto; }
    .hero { padding: 30px; color: white; border-radius: 22px;
      background: linear-gradient(135deg, #101b35, #174b83); }
    .brand { font-size: 13px; letter-spacing: .16em; text-transform: uppercase;
      color: #b9d9ff; font-weight: 700; }
    h1 { margin: 12px 0 8px; font-size: clamp(28px, 6vw, 42px); }
    .hero p { margin: 0; line-height: 1.6; color: #dceaff; }
    .panel { margin-top: 20px; padding: 24px; background: white;
      border: 1px solid #e2e8f0; border-radius: 18px;
      box-shadow: 0 12px 35px rgba(20, 40, 80, .06); }
    h2 { margin: 0 0 8px; }
    .muted { color: #64748b; line-height: 1.6; }
    label { display: block; margin: 18px 0 8px; font-weight: 700; }
    input { width: 100%; padding: 14px; border: 1px solid #cbd5e1;
      border-radius: 10px; font-size: 16px; }
    button { margin-top: 14px; padding: 13px 18px; border: 0; border-radius: 10px;
      color: white; background: #1d4ed8; font-size: 15px; font-weight: 700; }
    .notice { padding: 12px 14px; border-radius: 10px; background: #eff6ff;
      color: #1e40af; margin: 14px 0; }
    .error { background: #fff1f2; color: #9f1239; }
    .footer { padding: 18px 4px; color: #64748b; font-size: 13px; text-align: center; }
    @media (max-width: 520px) { .shell { margin: 16px auto; } .hero, .panel { padding: 20px; } }
  </style>
</head>
<body>
  <main class="shell">
    <header class="hero">
      <div class="brand">NayaDrishti AI · News Desk</div>
      <h1>Clear news. Clear perspective.</h1>
      <p>Nepali news collection and editorial review workspace.</p>
    </header>
    <section class="panel">
      {% if logged_in %}
        <h2>Welcome to NayaDrishti AI</h2>
        <p class="muted">The foundation is running. News collection and review tools will be added and tested in later stages.</p>
        <div class="notice">Stage 1: website and secure sign-in are active.</div>
        <form method="post" action="{{ url_for('logout') }}">
          <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
          <button type="submit">Sign out</button>
        </form>
      {% else %}
        <h2>Admin sign in</h2>
        <p class="muted">Sign in to open the editorial workspace.</p>
        {% if error %}<div class="notice error">{{ error }}</div>{% endif %}
        {% if not password_ready %}<div class="notice error">Admin password is not configured on the server yet.</div>{% endif %}
        <form method="post" action="{{ url_for('login') }}">
          <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
          <label for="password">Admin password</label>
          <input id="password" name="password" type="password" autocomplete="current-password" required>
          <button type="submit">Sign in</button>
        </form>
      {% endif %}
    </section>
    <div class="footer">NayaDrishti AI · Stage 1 foundation</div>
  </main>
</body>
</html>
"""

def csrf_token():
    token = session.get("_csrf")
    if not token:
        token = secrets.token_urlsafe(32)
        session["_csrf"] = token
    return token

def valid_csrf():
    supplied = request.form.get("csrf_token", "")
    expected = session.get("_csrf", "")
    return bool(supplied and expected and hmac.compare_digest(supplied, expected))

@app.after_request
def security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Cache-Control"] = "no-store"
    return response

@app.get("/health")
def health():
    return jsonify({"service": "NayaDrishti AI", "status": "ok", "stage": 1}), 200

@app.get("/")
def home():
    return render_template_string(
        PAGE,
        logged_in=session.get("admin") is True,
        error=request.args.get("error", ""),
        password_ready=bool(os.getenv("ADMIN_PASSWORD", "").strip()),
        csrf_token=csrf_token(),
    )

@app.post("/login")
def login():
    if not valid_csrf():
        abort(400, "Invalid form session. Reload the page and try again.")
    expected = os.getenv("ADMIN_PASSWORD", "").strip()
    supplied = request.form.get("password", "")
    if not expected:
        return "Admin password is not configured.", 503
    if not hmac.compare_digest(supplied, expected):
        return redirect(url_for("home", error="The password is incorrect. Please try again."))
    session.clear()
    session["admin"] = True
    csrf_token()
    return redirect(url_for("home"))

@app.post("/logout")
def logout():
    if not valid_csrf():
        abort(400, "Invalid form session. Reload the page and try again.")
    session.clear()
    return redirect(url_for("home"))

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")))
