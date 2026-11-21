import os
import requests


def publish_text(message, link=None):
    """Publish to the configured Facebook Page; disabled unless explicitly enabled."""
    enabled = os.getenv("ENABLE_FACEBOOK_PUBLISHING", "false").strip().lower()
    if enabled != "true":
        return {
            "published": False,
            "reason": "Facebook publishing is disabled.",
        }

    # Prefer the variable names currently configured in the Render dashboard.
    page_id = os.getenv("FB_PAGE_ID") or os.getenv("FACEBOOK_PAGE_ID")
    token = os.getenv("FB_PAGE_ACCESS_TOKEN") or os.getenv("FACEBOOK_PAGE_ACCESS_TOKEN")

    if not page_id or not token:
        raise RuntimeError(
            "FB_PAGE_ID and FB_PAGE_ACCESS_TOKEN are required when Facebook publishing is enabled."
        )

    payload = {"message": message, "access_token": token}
    if link:
        payload["link"] = link

    version = (
        os.getenv("FB_GRAPH_VERSION")
        or os.getenv("META_GRAPH_VERSION")
        or "v23.0"
    ).strip()

    url = f"https://graph.facebook.com/{version}/{page_id}/feed"
    response = requests.post(url, data=payload, timeout=30)

    if not response.ok:
        detail = response.text[:500].replace("\n", " ")
        raise RuntimeError(
            f"Facebook Graph API error {response.status_code}: {detail}"
        )

    return {
        "published": True,
        "post_id": response.json().get("id"),
    }
