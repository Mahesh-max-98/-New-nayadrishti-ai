# NayaDrishti AI

NayaDrishti AI is a mobile-friendly Flask dashboard for reading configured RSS/Atom feeds, creating concise Nepali-first/English-second news drafts with Groq, preventing duplicate processing, and optionally publishing text posts to the owner's Facebook Page.

## Current features

- Groq-powered bilingual short news drafts.
- RSS/Atom feed reader with request timeouts.
- Duplicate detection using PostgreSQL.
- Android-friendly admin dashboard with a visible **Run a feed check** button.
- Admin password login.
- Facebook text publishing integration, OFF unless explicitly enabled.
- Health endpoint at `/health`.

## Important source and copyright rules

Only add official RSS/Atom feeds or feeds you are authorized to access. A Facebook Page URL is not an RSS feed, and this app does not scrape Facebook pages. Do not copy complete articles or reuse photos without permission. Each generated draft includes source attribution and a link to the original source. AI summaries must be checked against the original reporting before publication.

The requested sources (NEB NEWS AND RESULT, TROLL CRICKET NEPAL, and Routine of Nepal Banda) must be connected using valid RSS/Atom feed URLs or authorized APIs. Do not put their ordinary Facebook page URLs into `NEWS_FEEDS`.

## Render environment variables

Required:
- `ADMIN_PASSWORD`: dashboard login password.
- `FLASK_SECRET_KEY`: long random secret used to sign the login session.
- `DATABASE_URL`: PostgreSQL connection URL.
- `GROQ_API_KEY`: Groq API key.
- `GROQ_MODEL`: model name enabled for your Groq account.
- `NEWS_FEEDS`: pipe-separated source/feed pairs.

Optional:
- `ENABLE_FACEBOOK_PUBLISHING`: keep `false` while testing; set to `true` only after drafts and Page permissions are verified.
- `FB_PAGE_ID`, `FB_PAGE_ACCESS_TOKEN`, `FB_GRAPH_VERSION`: Facebook Page publishing settings.
- `COOKIE_SECURE`: use `true` on HTTPS hosting.
- `LOG_LEVEL`: defaults to INFO.

Example feed format (replace with actual authorized feed URLs):
```
NEWS_FEEDS=Example Source=https://example.com/rss.xml|Another Source=https://example.org/feed
```

Never put API keys, passwords, or access tokens in GitHub. Store them only in Render environment variables. Do not send them in chat or screenshots.

## Deploy

The included Render configuration deploys the web dashboard. Render should redeploy after a commit to the connected GitHub branch.

Open the service URL, sign in with `ADMIN_PASSWORD`, and press **Run a feed check**. The response shows configured feeds, checked feeds, drafts, duplicates, publication count, and errors.

## One-minute monitoring

`worker.py` can run the feed checker on a 60-second schedule, but it must be deployed as a separate always-on background worker. Free web services may sleep and do not guarantee one-minute background execution. Check the hosting plan and costs before enabling a worker.

## Facebook publishing

Publishing is disabled by default. First verify feed parsing and generated drafts. Then verify that the Page access token has the required Meta permissions. Only enable direct publishing after you have reviewed multiple drafts and confirmed they are accurate. This application currently publishes text/link posts; image-card generation is not included in this rebuild yet.

## Local run

```bash
pip install -r requirements.txt
python app.py
```

The background worker can be started separately with `python worker.py` when a suitable always-on environment is available.
