from __future__ import annotations

from pathlib import Path

from app.storage import StorageValidationError, load_json_strict, locked_json_mutation


def _text(value):
    return str(value or "").strip()


def ensure_legacy_buyer_ownership(buyers_file, users_file):
    buyers_file = Path(buyers_file)
    buyers = load_json_strict(buyers_file, [], list)
    if not any(
        isinstance(record, dict) and not _text(record.get("account_id"))
        for record in buyers
    ):
        return buyers

    users = load_json_strict(users_file, [], list)
    account_ids = {
        _text(user.get("account_id"))
        for user in users
        if isinstance(user, dict) and _text(user.get("account_id"))
    }
    if len(account_ids) != 1:
        raise StorageValidationError(
            "Legacy Buyer ownership cannot be determined safely."
        )
    legacy_account_id = next(iter(account_ids))

    def assign_owner(records):
        for record in records:
            if isinstance(record, dict) and not _text(record.get("account_id")):
                record["account_id"] = legacy_account_id
        return records

    return locked_json_mutation(buyers_file, [], assign_owner, list)


def public_buyer(record):
    record = record if isinstance(record, dict) else {}
    return {
        "name": _text(record.get("name")),
        "address": _text(record.get("address")),
        "email": _text(record.get("email")),
        "country": _text(record.get("country")),
        "default_currency": _text(record.get("default_currency")),
        "default_trade_term": _text(record.get("default_trade_term")),
        "default_payment_term": _text(record.get("default_payment_term")),
        "preferred_carrier": _text(record.get("preferred_carrier")),
        "preferred_loading_port": _text(record.get("preferred_loading_port")),
        "preferred_destination_port": _text(record.get("preferred_destination_port")),
        "default_remarks": _text(record.get("default_remarks")),
    }
