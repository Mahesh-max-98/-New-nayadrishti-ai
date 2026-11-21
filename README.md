# NayaDrishti AI

A starter Flask service for monitoring permitted RSS/Atom news feeds, creating short Nepali/English summaries with Groq, preventing duplicate processing, and optionally publishing to a Facebook Page through Meta's Graph API.

## Important limitations

- This version reads RSS/Atom feeds only. It does not scrape Facebook pages.
- Reading public posts from other Facebook Pages through Meta APIs may require Meta approval and appropriate permissions.
- Add only feeds and content you are authorized to use. Summaries should attribute sources and link to original reporting.
- Images are not yet implemented. Do not reuse source images without permission or a suitable licence.
- Facebook publishing is OFF by default. Test drafts and API permissions before enabling it.
- SQLite on an ephemeral host may lose data after redeploy/restart. Use a persistent disk or managed database before production.
- A 60-second worker requires a paid always-on worker plan on many hosting providers. Free hosting may sleep or restrict background workers.

## Local run

1. Install Python 3.11 or newer.
2. `pip install -r requirements.txt`
3. Copy `.env.example` to `.env` and set variables.
4. `python app.py` starts the web service.
5. `python worker.py` starts the background monitor.

## Feed configuration

Set `NEWS_FEEDS` as a pipe-separated list:

`NEWS_FEEDS=Example News=https://example.com/rss.xml|Another Feed=https://example.org/feed`

Use actual RSS/Atom feed URLs. A Facebook Page URL is not automatically an RSS feed.

## Render

Deploy the repository using the included `render.yaml`. The web service and worker are separate. Configure environment variables in Render's dashboard. The worker plan may incur charges.

## Facebook setup

Create a Meta app and obtain a Page access token with the required Page publishing permissions for your own Page. Store the token only in Render environment variables. Never commit tokens or API keys to GitHub. Keep `ENABLE_FACEBOOK_PUBLISHING=false` until you have tested with a safe test Page.
