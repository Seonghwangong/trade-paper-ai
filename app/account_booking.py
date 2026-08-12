from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from app.storage import StorageValidationError, load_json_strict, locked_json_mutation


def _text(value):
    return str(value or "").strip()


def ensure_legacy_booking_ownership(booking_file, users_file):
    booking_file = Path(booking_file)
    records = load_json_strict(booking_file, [], list)
    if not any(
        isinstance(record, dict) and not _text(record.get("account_id"))
        for record in records
    ):
        return records

    users = load_json_strict(users_file, [], list)
    account_ids = {
        _text(user.get("account_id"))
        for user in users
        if isinstance(user, dict) and _text(user.get("account_id"))
    }
    if len(account_ids) != 1:
        raise StorageValidationError(
            "Legacy Booking Confirmation ownership cannot be determined safely."
        )
    legacy_account_id = next(iter(account_ids))

    def assign_owner(existing):
        for record in existing:
            if isinstance(record, dict) and not _text(record.get("account_id")):
                record["account_id"] = legacy_account_id
        return existing

    return locked_json_mutation(booking_file, [], assign_owner, list)


def public_booking(record):
    if not isinstance(record, dict):
        return {}
    return deepcopy({
        key: value for key, value in record.items()
        if key != "account_id"
    })
