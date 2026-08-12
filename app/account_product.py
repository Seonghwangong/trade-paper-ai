from __future__ import annotations

from pathlib import Path

from app.storage import StorageValidationError, load_json_strict, locked_json_mutation


def _text(value):
    return str(value or "").strip()


def ensure_legacy_product_ownership(products_file, users_file):
    products_file = Path(products_file)
    products = load_json_strict(products_file, [], list)
    if not any(
        isinstance(record, dict) and not _text(record.get("account_id"))
        for record in products
    ):
        return products

    users = load_json_strict(users_file, [], list)
    account_ids = {
        _text(user.get("account_id"))
        for user in users
        if isinstance(user, dict) and _text(user.get("account_id"))
    }
    if len(account_ids) != 1:
        raise StorageValidationError(
            "Legacy Product ownership cannot be determined safely."
        )
    legacy_account_id = next(iter(account_ids))

    def assign_owner(records):
        for record in records:
            if isinstance(record, dict) and not _text(record.get("account_id")):
                record["account_id"] = legacy_account_id
        return records

    return locked_json_mutation(products_file, [], assign_owner, list)


def public_product(record):
    record = record if isinstance(record, dict) else {}
    return {
        "name": _text(record.get("name")),
        "hs_code": _text(record.get("hs_code")),
        "unit_price": _text(record.get("unit_price")),
        "origin": _text(record.get("origin")),
    }
