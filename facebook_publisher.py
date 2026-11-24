import os

import requests


class FacebookPublishError(RuntimeError):
    """A definitive Graph API rejection with a safe, non-secret error code."""

    def __init__(self, status_code, error_code=None, message="Facebook rejected the request."):
        super().__init__(message)
        self.status_code = status_code
        self.error_code = str(error_code)[:80] if error_code is not None else None


def facebook_configuration_status():
    """Expose configuration presence only; never return credentials."""
    page_id = os.getenv("FB_PAGE_ID") or os.getenv("FACEBOOK_PAGE_ID")
    token = os.getenv("FB_PAGE_ACCESS_TOKEN") or os.getenv("FACEBOOK_PAGE_ACCESS_TOKEN")
    enabled = os.getenv("ENABLE_FACEBOOK_PUBLISHING", "false").strip().lower() == "true"
    return {
        "enabled": enabled,
        "page_id_configured": bool(page_id),
        "page_token_configured": bool(token),
        "ready": bool(enabled and page_id and token),
    }


def publish_text(message):
    """Publish one text-only post. Credentials are sent only to Meta over HTTPS."""
    status = facebook_configuration_status()
    if not status["enabled"]:
        return {"published": False, "reason": "Facebook publishing is disabled."}
    page_id = os.getenv("FB_PAGE_ID") or os.getenv("FACEBOOK_PAGE_ID")
    token = os.getenv("FB_PAGE_ACCESS_TOKEN") or os.getenv("FACEBOOK_PAGE_ACCESS_TOKEN")
    if not page_id or not token:
        raise FacebookPublishError(0, "missing_credentials", "Facebook Page credentials are not configured.")
    version = (os.getenv("FB_GRAPH_VERSION") or os.getenv("META_GRAPH_VERSION") or "v23.0").strip()
    response = requests.post(
        f"https://graph.facebook.com/{version}/{page_id}/feed",
        data={"message": message, "access_token": token},
        timeout=45,
    )
    if not response.ok:
        code = None
        try:
            payload = response.json()
            code = (payload.get("error") or {}).get("code")
        except (ValueError, AttributeError, TypeError):
            pass
        # Never include raw response text: it may contain sensitive request details.
        raise FacebookPublishError(response.status_code, code)
    try:
        post_id = response.json().get("id")
    except (ValueError, AttributeError, TypeError):
        post_id = None
    if not post_id:
        raise FacebookPublishError(response.status_code, "missing_post_id", "Facebook response did not include a post ID.")
    return {"published": True, "post_id": str(post_id)}
