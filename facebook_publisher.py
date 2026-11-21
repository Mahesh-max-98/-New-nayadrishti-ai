import os
import requests

def publish_text(message, link=None):
    """Publish to the configured Facebook Page. Disabled unless explicitly enabled."""
    if os.getenv("ENABLE_FACEBOOK_PUBLISHING", "false").lower() != "true":
        return {"published": False, "reason": "Facebook publishing is disabled."}

    page_id = os.getenv("FACEBOOK_PAGE_ID")
    token = os.getenv("FACEBOOK_PAGE_ACCESS_TOKEN")
    if not page_id or not token:
        raise RuntimeError("FACEBOOK_PAGE_ID and FACEBOOK_PAGE_ACCESS_TOKEN are required.")

    payload = {"message": message, "access_token": token}
    if link:
        payload["link"] = link

    version = os.getenv("META_GRAPH_VERSION", "v23.0")
    url = f"https://graph.facebook.com/{version}/{page_id}/feed"
    response = requests.post(url, data=payload, timeout=30)
    response.raise_for_status()
    return {"published": True, "post_id": response.json().get("id")}
