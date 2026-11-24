import os

import requests


class FacebookPublishError(RuntimeError):
    """A definitive Graph API rejection with safe, non-secret diagnostics."""

    def __init__(self, status_code, error_code=None, message="Facebook rejected the request.", error_subcode=None):
        super().__init__(message)
        self.status_code = status_code
        self.error_code = str(error_code)[:40] if error_code is not None else None
        self.error_subcode = str(error_subcode)[:40] if error_subcode is not None else None


def _safe_meta_error(error):
    """Translate Meta errors into safe guidance; never expose raw response text."""
    error = error if isinstance(error, dict) else {}
    code = error.get("code")
    subcode = error.get("error_subcode")
    raw_message = str(error.get("message") or "").lower()
    try:
        numeric_code = int(code)
    except (TypeError, ValueError):
        numeric_code = None
    try:
        numeric_subcode = int(subcode)
    except (TypeError, ValueError):
        numeric_subcode = None

    if numeric_code == 190:
        if "application" in raw_message and "deleted" in raw_message:
            message = "Meta reports that the app linked to this Page token is deleted or unavailable. An active Meta app and a newly issued Page token are required."
        elif numeric_subcode == 463:
            message = "The Page access token has expired or been revoked. Reconnect the Page and replace the token."
        elif numeric_subcode == 458:
            message = "This Page token is not authorized for the connected Meta app. Reauthorize the Page with the active app."
        elif numeric_subcode == 460:
            message = "Meta invalidated this token after an account security or password event. Reauthorize the Page."
        elif numeric_subcode == 467:
            message = "Meta rejected this invalid Page access token. Reauthorize the Page and replace the token."
        elif numeric_subcode == 492:
            message = "The account associated with this Page token may no longer have the required Page role."
        else:
            message = "Meta rejected the Page access token (OAuth error 190). Run the read-only connection check and review the active Meta app."
    elif numeric_code == 10 or (numeric_code is not None and 200 <= numeric_code <= 299):
        message = "The Meta app or Page token is missing a required Page permission or role."
    else:
        message = "Meta rejected the request. Check the Page connection, app status, and required permissions."
    return {
        "error_code": str(code)[:40] if code is not None else "unknown",
        "error_subcode": str(subcode)[:40] if subcode is not None else None,
        "message": message,
    }


def check_facebook_connection():
    """Perform a read-only Page lookup. This never creates or publishes a post."""
    status = facebook_configuration_status()
    if not status["page_id_configured"] or not status["page_token_configured"]:
        return {"ok": False, "error_code": "missing_credentials",
                "message": "The Page ID or Page access token is missing from the hosting configuration."}
    page_id = os.getenv("FB_PAGE_ID") or os.getenv("FACEBOOK_PAGE_ID")
    token = os.getenv("FB_PAGE_ACCESS_TOKEN") or os.getenv("FACEBOOK_PAGE_ACCESS_TOKEN")
    version = (os.getenv("FB_GRAPH_VERSION") or os.getenv("META_GRAPH_VERSION") or "v23.0").strip()
    try:
        response = requests.get(
            f"https://graph.facebook.com/{version}/{page_id}",
            params={"fields": "id,name", "access_token": token},
            timeout=20,
        )
    except requests.RequestException:
        return {"ok": False, "error_code": "network_error",
                "message": "The connection check could not reach Meta. Try again later."}
    try:
        payload = response.json()
    except (ValueError, AttributeError, TypeError):
        payload = {}
    if not response.ok or not isinstance(payload, dict) or not payload.get("id"):
        details = _safe_meta_error(payload.get("error") if isinstance(payload, dict) else {})
        return {"ok": False, **details}
    if str(payload.get("id")) != str(page_id):
        return {"ok": False, "error_code": "page_mismatch",
                "message": "Meta returned a different Page ID. Check the Page ID configured in Render."}
    return {
        "ok": True,
        "page_name": str(payload.get("name") or "")[:120],
        "message": "Read-only Page connection verified. No post was created.",
    }


def facebook_configuration_status():
    """Expose configuration presence only; never return credentials."""
    page_id = os.getenv("FB_PAGE_ID") or os.getenv("FACEBOOK_PAGE_ID")
    token = os.getenv("FB_PAGE_ACCESS_TOKEN") or os.getenv("FACEBOOK_PAGE_ACCESS_TOKEN")
    enabled = os.getenv("ENABLE_FACEBOOK_PUBLISHING", "false").strip().lower() == "true"
    return {
        "enabled": enabled,
        "page_id_configured": bool(page_id),
        "page_token_configured": bool(token),
        "credentials_present": bool(page_id and token),
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
        try:
            payload = response.json()
        except (ValueError, AttributeError, TypeError):
            payload = {}
        details = _safe_meta_error(payload.get("error") if isinstance(payload, dict) else {})
        # Never include raw response text: it may contain sensitive request details.
        raise FacebookPublishError(
            response.status_code, details["error_code"], details["message"],
            error_subcode=details.get("error_subcode"),
        )
    try:
        post_id = response.json().get("id")
    except (ValueError, AttributeError, TypeError):
        post_id = None
    if not post_id:
        raise FacebookPublishError(response.status_code, "missing_post_id", "Facebook response did not include a post ID.")
    return {"published": True, "post_id": str(post_id)}
