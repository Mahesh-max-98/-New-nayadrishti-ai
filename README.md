# NayaDrishti AI

NayaDrishti AI is a text-only Nepali news collection and Facebook publishing dashboard. It discovers recent stories through RSS/Atom feeds, reads the original article where accessible, prepares concise Nepali posts with Groq, stores drafts in PostgreSQL, avoids duplicates, and can publish text to a Facebook Page.

## What it does

1. Reads direct public RSS/Atom feeds from Onlinekhabar, Setopati, and Ratopati, plus configured feeds and Google News discovery feeds.
2. Uses Google News discovery for RONB, Troll Cricket Nepal, BBC Nepali, OK Khabar, Laligurash Media Network, NEB, Thaha Khabar, Nepal Samaya, and other supported publishers.
3. Sorts entries by publication date and skips undated or stale items.
3. Opens the original article where accessible and prefers its text over a short RSS teaser.
4. Checks item links and normalized headlines against previously seen stories.
5. Uses Groq to write a clear 4–7 line Nepali Facebook post.
6. Adds a final source-credit line. Publication timestamps and clock times are not included in the post.
7. Saves the text draft, then publishes text only if Facebook publishing is explicitly enabled.

There is no Image Studio, image-generation route, image attachment, publisher-photo fetching, or image storage in this project.

## News discovery sources

Direct public RSS feeds are included for:
- Onlinekhabar — https://www.onlinekhabar.com/feed
- Setopati — https://www.setopati.com/feed
- Ratopati — https://www.ratopati.com/feed

Built-in Google News RSS discovery queries cover:
- Routine of Nepal Banda (RONB)
- Troll Cricket Nepal
- BBC Nepali
- Onlinekhabar, Setopati, and Ratopati
- Thaha Khabar and Nepal Samaya
- OK Khabar
- Laligurash Media Network
- National Examinations Board (NEB)

Google News RSS is a discovery index. It does not scrape every post from those publishers' Facebook pages and cannot guarantee every story is found. The original publisher name is extracted from the Google News result title when available. For stronger coverage, add official public RSS/Atom feed URLs or authorized publisher APIs through `NEWS_FEEDS`.

Example:

    NEWS_FEEDS=Publisher Name=https://publisher.example/rss.xml|Another Publisher=https://another.example/feed

Only use feed URLs you are authorized to access. The app does not bypass paywalls or access private Facebook content.

## Environment variables

Required:
- `ADMIN_PASSWORD`
- `FLASK_SECRET_KEY`
- `DATABASE_URL`
- `GROQ_API_KEY`

Optional:
- `GROQ_MODEL` (default: `openai/gpt-oss-120b`)
- `NEWS_FEEDS`
- `NEWS_MAX_AGE_HOURS` (default: 36; maximum accepted: 168)
- `MAX_STORIES_PER_FEED` (default: 2; maximum accepted: 5)
- `ENABLE_AUTO_MONITORING` (default: true)
- `NEWS_CHECK_INTERVAL_SECONDS` (default: 600; minimum: 600)
- `ENABLE_FACEBOOK_PUBLISHING` (default: false)
- `FB_PAGE_ID` / `FACEBOOK_PAGE_ID`
- `FB_PAGE_ACCESS_TOKEN` / `FACEBOOK_PAGE_ACCESS_TOKEN`
- `FB_GRAPH_VERSION` (default: v23.0)
- `COOKIE_SECURE`
- `LOG_LEVEL`

Keep API keys, passwords, and access tokens in Render environment variables. Never commit them to GitHub or share them in chat/screenshots.

## Safe rollout

1. Deploy this branch to the Render service.
2. Confirm the required environment variables are set in Render.
3. Keep `ENABLE_FACEBOOK_PUBLISHING=false`.
4. Sign in and run several manual news checks.
5. Open each source link and verify the Nepali summary, source credit, and freshness.
6. Only after the drafts are consistently accurate, set `ENABLE_FACEBOOK_PUBLISHING=true`.

Facebook publishing uses the Page `/{page-id}/feed` endpoint and sends a text message only. The code defaults to publishing OFF.

## Monitoring limits

The dashboard scheduler runs only while the Render web service is awake. Free web services can sleep, so this does not guarantee 24/7 monitoring. An always-on worker or external scheduler may be needed for dependable continuous collection; check the hosting plan and cost before enabling one.

## Local run

    pip install -r requirements.txt
    python app.py
