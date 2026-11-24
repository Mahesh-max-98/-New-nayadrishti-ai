"""Server-side editorial approval checklist for NayaDrishti drafts."""

REQUIRED_REVIEW_FIELDS = (
    "read_original",
    "checked_key_facts",
    "summary_matches_source",
)


def review_checklist_complete(form):
    """Return True only when every required human-review confirmation is present."""
    return all(str(form.get(field, "")).strip() == "yes" for field in REQUIRED_REVIEW_FIELDS)
