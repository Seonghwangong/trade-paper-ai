from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from app.storage import StorageValidationError, load_json_strict, locked_json_mutation


def _text(value):
    return str(value or "").strip()


def ensure_legacy_invoice_ownership(invoices_file, users_file):
    invoices_file = Path(invoices_file)
    invoices = load_json_strict(invoices_file, [], list)
    if not any(
        isinstance(record, dict) and not _text(record.get("account_id"))
        for record in invoices
    ):
        return invoices

    users = load_json_strict(users_file, [], list)
    account_ids = {
        _text(user.get("account_id"))
        for user in users
        if isinstance(user, dict) and _text(user.get("account_id"))
    }
    if len(account_ids) != 1:
        raise StorageValidationError(
            "Legacy Invoice ownership cannot be determined safely."
        )
    legacy_account_id = next(iter(account_ids))

    def assign_owner(records):
        for record in records:
            if isinstance(record, dict) and not _text(record.get("account_id")):
                record["account_id"] = legacy_account_id
        return records

    return locked_json_mutation(invoices_file, [], assign_owner, list)


def public_invoice(record):
    if not isinstance(record, dict):
        return {}
    return deepcopy({
        key: value
        for key, value in record.items()
        if key != "account_id"
    })
