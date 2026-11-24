"""Validation for explicit bulk actions on pending news drafts."""

MAX_BULK_DISMISS = 50


def validate_bulk_dismiss_ids(values):
    if values is None:
        raise ValueError("Select at least one draft first.")
    ids = []
    for value in values:
        if not isinstance(value, str):
            raise ValueError("Invalid draft selection.")
        item_id = value.strip()
        if not item_id or len(item_id) > 500:
            raise ValueError("Invalid draft selection.")
        if item_id not in ids:
            ids.append(item_id)
    if len(ids) > MAX_BULK_DISMISS:
        raise ValueError("Select no more than 50 drafts at a time.")
    return ids
