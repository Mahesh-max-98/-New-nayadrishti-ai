# NayaDrishti AI

A clean rebuild of the NayaDrishti AI Nepali news workspace.

## Stage 1
- Responsive landing page
- Admin sign-in with session protection and CSRF validation
- Health endpoint at `/health`
- No news scanning, database, AI calls, scheduled jobs, or Facebook publishing yet

## Required environment variables
- `FLASK_SECRET_KEY`: long random secret
- `ADMIN_PASSWORD`: admin sign-in password

Optional:
- `COOKIE_SECURE`: defaults to `true`; set to `false` only for local HTTP testing

Keep all secrets in the hosting provider's environment settings. Never commit them to GitHub.

## Run locally
```bash
pip install -r requirements.txt
FLASK_SECRET_KEY="local-test-secret" ADMIN_PASSWORD="local-test-password" COOKIE_SECURE=false python app.py
```

Open `http://127.0.0.1:10000`. Check `http://127.0.0.1:10000/health`.

## Safe rollout
Keep automatic monitoring and Facebook publishing disabled. Test the landing page, health endpoint, and sign-in before adding the database or news features.
