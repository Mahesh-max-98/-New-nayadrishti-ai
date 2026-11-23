import os

import requests


def publish_text(message):
    """Publish a text-only post to the configured Facebook Page."""
    if os.getenv("ENABLE_FACEBOOK_PUBLISHING", "false").strip().lower() != "true":
        return {"published": False, "reason": "Facebook publishing is disabled."}
    page_id = os.getenv("FB_PAGE_ID") or os.getenv("FACEBOOK_PAGE_ID")
    token = os.getenv("FB_PAGE_ACCESS_TOKEN") or os.getenv("FACEBOOK_PAGE_ACCESS_TOKEN")
    if not page_id or not token:
        raise RuntimeError("Facebook Page ID and Page access token are required.")
    version = (os.getenv("FB_GRAPH_VERSION") or os.getenv("META_GRAPH_VERSION") or "v23.0").strip()
    response = requests.post(
        f"https://graph.facebook.com/{version}/{page_id}/feed",
        data={"message": message, "access_token": token},
        timeout=45,
    )
    if not response.ok:
        raise RuntimeError(
            f"Facebook Graph API error {response.status_code}: "
            f"{response.text[:450].replace(chr(10), ' ')}"
        )
    return {"published": True, "post_id": response.json().get("id")}
