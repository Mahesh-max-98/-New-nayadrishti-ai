# NayaDrishti AI

NayaDrishti AI is a mobile-friendly Flask dashboard that monitors news feeds, writes short Nepali-only news posts with Groq, stores drafts in PostgreSQL, prevents duplicates, and can publish text-only posts to a Facebook Page.

## News post format
- 3–8 lines in Nepali: headline, factual summary, and original publisher credit.
- No generated images, publisher photos, image attachments, or photo posts in the automatic news workflow.
- Stories older than 24 hours or without a publication timestamp are skipped.
- Duplicate stories are not processed again.
- The separate, manually opened Image Studio remains optional; automatic news monitoring does not call it.

## Sources
The built-in Google News RSS discovery queries look for NEB notices/results, Routine of Nepal Banda (RONB), BBC Nepali, Onlinekhabar, OK Khabar, Laligurash Media Network, and Troll Cricket Nepal.

Google News RSS is a discovery index, not direct access to or endorsement by those publishers. The original publisher is identified from the result headline when available and credited in the generated post. Facebook pages are not scraped. To monitor a publisher directly, configure an official public RSS/Atom feed or an authorized API.

Additional feeds can be configured with NEWS_FEEDS using pipe-separated source/feed pairs, for example:

    NEWS_FEEDS=Publisher Name=https://publisher.example/rss.xml|Another Publisher=https://another.example/feed

## Render environment variables
Required: ADMIN_PASSWORD, FLASK_SECRET_KEY, DATABASE_URL, GROQ_API_KEY.

Optional: GROQ_MODEL (defaults to openai/gpt-oss-120b), NEWS_FEEDS, ENABLE_FACEBOOK_PUBLISHING, FB_PAGE_ID, FB_PAGE_ACCESS_TOKEN, FB_GRAPH_VERSION, COOKIE_SECURE, LOG_LEVEL.

Never put API keys, passwords, or access tokens in GitHub. Store them only in Render environment variables. Do not send them in chat or screenshots.

## Run and review
1. Deploy this branch to the Render service.
2. Set the required environment variables in Render.
3. Keep ENABLE_FACEBOOK_PUBLISHING=false initially.
4. Sign in to the dashboard and press Run a feed check.
5. Review generated Nepali drafts against their original source links.
6. After several accurate tests, configure the Facebook Page ID/token and enable text publishing.

Facebook publishing uses the Page feed endpoint and publishes text only. Publishing is disabled by default.

## Monitoring
The web dashboard's background checks run only while the Render web service is awake. A free Render web service may sleep and does not guarantee continuous monitoring. For dependable scheduled monitoring, use an appropriately configured always-on worker or scheduler and check the hosting plan/cost first.

## Local run
    pip install -r requirements.txt
    python app.py

The background worker can be started separately with python worker.py when a suitable always-on environment is available.