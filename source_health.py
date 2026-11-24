"""Small, deterministic helpers for classifying RSS feed health."""


def classify_feed_health(parsed=None, error=None):
    """Separate reachable/valid feeds from parse warnings and fetch failures."""
    if error is not None or parsed is None:
        return "error"
    if bool(getattr(parsed, "bozo", False)):
        return "warning"
    return "healthy"
