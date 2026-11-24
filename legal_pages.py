"""Public legal and privacy pages for NayaDrishti AI."""
import html
import os

from flask import render_template_string


_LAYOUT = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="index,follow">
<title>{{ title }} — NayaDrishti AI</title>
<style>
*{box-sizing:border-box}body{margin:0;background:#f3f5f9;color:#172033;font-family:Arial,sans-serif;line-height:1.65}
main{max-width:850px;margin:28px auto;padding:24px;background:#fff;border:1px solid #e5e7eb;border-radius:16px}
h1{line-height:1.25;color:#172554}h2{margin-top:28px;font-size:20px}
a{color:#1d4ed8;overflow-wrap:anywhere}.nav{display:flex;gap:16px;flex-wrap:wrap;margin-bottom:22px}
.small{color:#64748b;font-size:13px}li{margin:7px 0}
</style>
</head>
<body><main>
<nav class="nav"><a href="/">NayaDrishti AI home</a><a href="/privacy">Privacy</a><a href="/terms">Terms</a><a href="/data-deletion">Data deletion</a></nav>
<h1>{{ title }}</h1>
{{ body|safe }}
<p class="small">Last updated: 24 November 2026</p>
</main></body></html>"""


def _page(title, body):
    contact = os.getenv("PRIVACY_CONTACT_EMAIL", "").strip()
    contact_html = (
        '<a href="mailto:' + html.escape(contact, quote=True) + '">' + html.escape(contact) + '</a>'
        if contact else
        "Please contact the NayaDrishti AI operator through the official NayaDrishti AI Facebook Page."
    )
    body = body.replace("{{CONTACT}}", contact_html)
    return render_template_string(_LAYOUT, title=title, body=body)


def register_legal_routes(app):
    """Register public policy URLs required for transparency and Meta review."""

    @app.get("/privacy")
    def privacy_policy():
        return _page("Privacy Policy", """
<p>NayaDrishti AI is a Nepali news collection and editorial-assistance service. This policy explains what information is handled when the service is operated and when an administrator connects a Facebook Page.</p>
<h2>Information handled</h2>
<ul>
<li><strong>News material:</strong> public RSS/Atom feed entries, article links, publisher names, headlines and article text that is accessible to the service. These are used to prepare and store news drafts.</li>
<li><strong>Draft and review records:</strong> news summaries, source details, review status and technical timestamps are stored in the service database.</li>
<li><strong>Administrator security:</strong> when an administrator signs in, the service uses a secure session cookie. The administrator password and Flask session secret are configuration secrets and are not displayed in the dashboard.</li>
<li><strong>Facebook connection:</strong> if an administrator connects a Facebook Page, the Page ID and Page access token are stored as private hosting environment variables. Tokens are used to check the Page connection and, only if publishing is deliberately enabled, to publish approved content.</li>
</ul>
<h2>How information is used</h2>
<p>News text may be sent to configured AI service providers (such as Groq or Google Gemini) to generate Nepali summaries. Those providers process requests under their own terms and privacy policies. The service does not sell personal information or use Facebook access tokens for advertising.</p>
<p>The service is designed to collect public news feeds. It does not intentionally request private Facebook messages, friends lists or personal profile content.</p>
<h2>Storage and security</h2>
<p>Drafts and related news records are stored in the configured PostgreSQL database. Hosting configuration secrets are kept in the hosting provider's environment-variable settings and must not be shared in public issues, screenshots or chat. Access to the administration dashboard is password-protected. No internet service can guarantee absolute security.</p>
<h2>Retention and choices</h2>
<p>News and draft records may be retained for editorial history and duplicate prevention. An administrator can dismiss drafts through the dashboard. A person who believes the service holds personal information about them can request review or deletion using the contact route below.</p>
<h2>Third-party services</h2>
<p>The service may rely on Render for hosting, PostgreSQL for storage, GitHub for source-code hosting and configured AI providers for summarization. Facebook/Meta is used only when a Page administrator authorizes the app. Each provider has its own privacy terms.</p>
<h2>Contact</h2>
<p>Privacy and data requests: {{CONTACT}}</p>
""")

    @app.get("/terms")
    def terms_of_service():
        return _page("Terms of Service", """
<p>By using NayaDrishti AI, you agree to these terms. If you do not agree, do not use the service.</p>
<h2>Purpose and editorial review</h2>
<p>NayaDrishti AI collects publicly available news-feed material and assists with drafting Nepali summaries. AI-generated text can be incomplete, outdated or incorrect. Similar headlines from multiple sources are not proof that a claim is true. An authorized editor should check the original reporting and correct errors before sharing.</p>
<h2>No guarantee of availability or accuracy</h2>
<p>News feeds, AI providers, Facebook, hosting and database services may be unavailable or change their interfaces. The service does not guarantee uninterrupted operation, complete news coverage, factual accuracy or publication at a particular time.</p>
<h2>Permitted use</h2>
<ul>
<li>Use only news sources and accounts you are authorized to access.</li>
<li>Respect publishers' copyrights, website terms and applicable law.</li>
<li>Do not use the service to impersonate others, distribute unlawful material or bypass access controls.</li>
<li>Keep administrator passwords, API keys and access tokens confidential.</li>
</ul>
<h2>Facebook publishing</h2>
<p>Facebook publishing is disabled by default and may remain paused for safety. Only an authorized Page administrator should grant permissions or enable publishing. The operator is responsible for reviewing content and ensuring that each post complies with Meta policies and applicable law. A connection check is read-only and does not publish a post.</p>
<h2>Changes and contact</h2>
<p>These terms may be updated as the service changes. Questions: {{CONTACT}}</p>
""")

    @app.get("/data-deletion")
    def data_deletion_instructions():
        return _page("Data Deletion Instructions", """
<p>You can ask the NayaDrishti AI operator to review and delete personal information that the service may hold about you.</p>
<h2>How to request deletion</h2>
<ol>
<li>Send a request to the contact route below. Include enough information to identify the relevant record, but do not send passwords, access tokens or other secrets.</li>
<li>State that you are requesting deletion of personal information associated with NayaDrishti AI and include any relevant Facebook Page/app interaction details.</li>
<li>The operator will review the request and, where appropriate, delete or de-identify the relevant information, subject to legitimate security, legal and record-keeping requirements.</li>
</ol>
<p>Contact for deletion requests: {{CONTACT}}</p>
<h2>Disconnect Facebook</h2>
<p>To remove the app's Facebook access, open Facebook Settings → Apps and websites, select NayaDrishti AI, and remove it. This revokes the app's connection but does not by itself submit a request to delete records already stored by NayaDrishti AI. Use the contact route above for that request.</p>
<h2>What to expect</h2>
<p>The operator may ask for reasonable information to verify a request. Do not include passwords or access tokens. The operator will respond through the contact channel used for the request.</p>
""")
