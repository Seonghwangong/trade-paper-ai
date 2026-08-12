from __future__ import annotations

from pathlib import Path

from app.storage import StorageValidationError, load_json_strict, locked_json_mutation


def _text(value):
    return str(value or "").strip()


def ensure_legacy_customer_ownership(customers_file, users_file):
    customers_file = Path(customers_file)
    customers = load_json_strict(customers_file, [], list)
    if not any(
        isinstance(record, dict) and not _text(record.get("account_id"))
        for record in customers
    ):
        return customers

    users = load_json_strict(users_file, [], list)
    account_ids = {
        _text(user.get("account_id"))
        for user in users
        if isinstance(user, dict) and _text(user.get("account_id"))
    }
    if len(account_ids) != 1:
        raise StorageValidationError(
            "Legacy Customer ownership cannot be determined safely."
        )
    legacy_account_id = next(iter(account_ids))

    def assign_owner(records):
        for record in records:
            if isinstance(record, dict) and not _text(record.get("account_id")):
                record["account_id"] = legacy_account_id
        return records

    return locked_json_mutation(customers_file, [], assign_owner, list)


def public_customer(record):
    record = record if isinstance(record, dict) else {}
    return {
        "company": _text(record.get("company")),
        "country": _text(record.get("country")),
        "address": _text(record.get("address")),
        "email": _text(record.get("email")),
        "phone": _text(record.get("phone")),
        "pic": _text(record.get("pic")),
    }
