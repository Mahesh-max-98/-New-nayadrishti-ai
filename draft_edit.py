"""Validation for manual editorial draft changes."""


def validate_draft_text(value):
    """Require non-empty plain text within a safe, practical size limit."""
    if not isinstance(value, str):
        raise ValueError("Draft text is required.")
    text = value.strip()
    if not text:
        raise ValueError("Draft text cannot be empty.")
    if len(text) > 10000:
        raise ValueError("Draft text is too long. Keep it within 10,000 characters.")
    return text
