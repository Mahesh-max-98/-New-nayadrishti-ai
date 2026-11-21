import os
from flask import Flask, jsonify
from database import init_db
from news_monitor import check_feeds

app = Flask(__name__)
init_db()

@app.get("/")
def home():
    return """
    <h1>NayaDrishti AI</h1>
    <p>News monitoring service is running.</p>
    <p><a href='/health'>Health check</a> · <a href='/run-check'>Run a feed check</a></p>
    """

@app.get("/health")
def health():
    return jsonify(status="ok", service="NayaDrishti AI")

@app.get("/run-check")
def run_check():
    # Protect this endpoint with a secret before deploying publicly.
    expected = os.getenv("ADMIN_SECRET")
    supplied = os.getenv("FLASK_REQUEST_SECRET")
    if expected and supplied != expected:
        return jsonify(error="Set FLASK_REQUEST_SECRET to ADMIN_SECRET before using this endpoint."), 403
    result = check_feeds()
    return jsonify(result)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")))
