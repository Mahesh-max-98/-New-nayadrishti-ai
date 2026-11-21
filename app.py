
import os
from flask import Flask, jsonify, render_template_string
from database import init_db, get_drafts
from news_monitor import check_feeds

app = Flask(__name__)
init_db()

DASHBOARD_HTML = """
<!DOCTYPE html>
<html>
<head>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>NayaDrishti AI Dashboard</title>
    <style>
        body {
            font-family: Arial, sans-serif;
            background: #f3f4f6;
            color: #222;
            margin: 0;
            padding: 20px;
        }
        .container {
            max-width: 800px;
            margin: auto;
        }
        header {
            background: #172554;
            color: white;
            padding: 22px;
            border-radius: 12px;
            margin-bottom: 20px;
        }
        .draft {
            background: white;
            padding: 18px;
            border-radius: 10px;
            margin-bottom: 15px;
            box-shadow: 0 2px 8px #00000010;
        }
        .draft h2 {
            font-size: 19px;
            margin-top: 0;
        }
        .meta {
            color: #666;
            font-size: 13px;
        }
        a {
            color: #1d4ed8;
            overflow-wrap: anywhere;
        }
        .empty {
            background: white;
            padding: 20px;
            border-radius: 10px;
        }
        button {
            background: #1d4ed8;
            color: white;
            border: 0;
            border-radius: 8px;
            padding: 12px 18px;
            font-size: 15px;
            cursor: pointer;
        }
        button:disabled { opacity: 0.6; cursor: wait; }
        #check-result {
            background: white;
            padding: 14px;
            border-radius: 10px;
            margin: 14px 0;
            white-space: pre-wrap;
            overflow-wrap: anywhere;
        }
    </style>
</head>
<body>
<div class="container">
    <header>
        <h1>NayaDrishti AI</h1>
        <p>नयाँ दृष्टि, नयाँ खबर।</p>
        <p>News Draft Dashboard</p>
    </header>

    <h2>Feed Controls</h2>
    <button id="run-check-button" type="button" onclick="runFeedCheck()">Run a feed check</button>
    <div id="check-result" aria-live="polite">Press the button to check your configured news feeds.</div>

    <h2>Generated News Drafts</h2>
    <p>Total drafts: {{ drafts|length }}</p>

    {% if drafts %}
        {% for draft in drafts %}
        <div class="draft">
            <h2>{{ draft.title }}</h2>
            <p class="meta">
                Source: {{ draft.source or "Unknown" }}
            </p>
            <p>{{ draft.summary }}</p>
            <p class="meta">Created: {{ draft.created_at }}</p>
            <p><a href="{{ draft.url }}" target="_blank"
                  rel="noopener noreferrer">Read original article</a></p>
        </div>
        {% endfor %}
    {% else %}
        <div class="empty">
            No drafts yet. New generated news summaries will appear here.
        </div>
    {% endif %}
</div>
<script>
async function runFeedCheck() {
    const button = document.getElementById("run-check-button");
    const result = document.getElementById("check-result");
    button.disabled = true;
    button.textContent = "Checking feeds...";
    result.textContent = "Please wait while NayaDrishti checks the configured feeds.";
    try {
        const response = await fetch("/run-check", { method: "GET" });
        const data = await response.json();
        result.textContent = JSON.stringify(data, null, 2);
        if (!response.ok) {
            result.textContent = "Feed check failed (" + response.status + "):\\n" + JSON.stringify(data, null, 2);
        }
    } catch (error) {
        result.textContent = "Could not complete the feed check: " + error.message;
    } finally {
        button.disabled = false;
        button.textContent = "Run a feed check";
    }
}
</script>
</body>
</html>
"""


@app.get("/")
def home():
    drafts = get_drafts()
    return render_template_string(DASHBOARD_HTML, drafts=drafts)


@app.get("/health")
def health():
    return jsonify(status="ok", service="NayaDrishti AI")


@app.get("/run-check")
def run_check():
    expected = os.getenv("ADMIN_SECRET")
    supplied = os.getenv("FLASK_REQUEST_SECRET")

    if expected and supplied != expected:
        return jsonify(
            error="Set FLASK_REQUEST_SECRET to ADMIN_SECRET before using this endpoint."
        ), 403

    result = check_feeds()
    return jsonify(result)


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.getenv("PORT", "10000"))
    )
    
