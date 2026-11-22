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

## AI Image Studio (development branch)

The separate Image Studio uses a free-first provider router:
- Primary image provider: Cloudflare Workers AI with Black Forest Labs FLUX.2 Klein 9B (model ID @cf/black-forest-labs/flux-2-klein-9b), when configured and available.
- Free text-to-image fallback: AI Horde community workers, using its public asynchronous REST API. Anonymous requests use the public anonymous key and have the lowest queue priority; the community may be busy and generation is not guaranteed to finish quickly.
- Privacy guard: reference-image requests are never forwarded to AI Horde. Editing and variations that include uploaded/source images require Cloudflare. AI Horde is a public volunteer network; anonymous prompts and generated results may be shared with its workers/community. Do not send sensitive or private prompts.
- Prompt understanding: Groq, used only to improve text prompts; Groq is not the image generator.
- Persistent history: PostgreSQL stores conversations, messages, image versions and reference uploads. Render's local filesystem is not used for image history.
- Editing: reference-image-based editing and variations use Cloudflare only. The source image is preserved as a separate version.

Cloudflare supports up to four reference images in this adapter. AI Horde fallback is text-to-image only and uses one image per request. The initial implementation supports one active generation job at a time.

### Image Studio environment variables

- CLOUDFLARE_ACCOUNT_ID: Cloudflare account ID.
- CLOUDFLARE_API_TOKEN: server-side token with Workers AI permission.
- IMAGE_MODEL: optional; defaults to @cf/black-forest-labs/flux-2-klein-9b.
- AI_HORDE_ENABLED: optional; defaults to true. Set false to disable AI Horde fallback.
- AI_HORDE_API_KEY: optional registered AI Horde key; if absent, the public anonymous key is used. A registered key may improve queue priority.
- AI_HORDE_MAX_WAIT_SECONDS: optional queue wait limit; defaults to 180 seconds and is capped at 240.
- GROQ_API_KEY: required for prompt enhancement and existing news writing.
- GROQ_MODEL: text model used by Groq.

Keep all credentials in Render environment variables. Never commit real keys to GitHub.

### Cost and privacy limits

Cloudflare documents a free Workers AI allocation of 10,000 neurons per day. It is not unlimited. AI Horde is a community-funded volunteer service and may be used for free, but anonymous requests have low priority and may be delayed or restricted under high load. Neither provider promises unlimited, uninterrupted generation. No paid provider or paid fallback is configured.

The Image Studio uses PostgreSQL BYTEA storage for the current version. This is durable only while the configured PostgreSQL service and its retention plan remain active. A separate private object-storage service has not yet been configured.

The current app has one admin-password account. Image history is private to that admin session; multi-user accounts are not implemented. Editing uses model reference conditioning, not precise brush masks or guaranteed pixel-level inpainting. Background removal, object removal, selective masks, upscaling and multi-image batch generation are not implemented yet.

### Image Studio API routes

- POST /api/images/generate
- POST /api/images/edit
- POST /api/images/variations
- POST /api/images/enhance-prompt
- POST /api/images/upload
- GET /api/images/history
- GET /api/images/<image_id>
- GET /api/images/<image_id>/content
- DELETE /api/images/<image_id>
- GET /api/images/conversations/<conversation_id>
- DELETE /api/images/conversations/<conversation_id>

The legacy news-image generation and Facebook publishing code remains separate. Facebook auto-publishing should remain disabled during image testing.
