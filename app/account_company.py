from __future__ import annotations

from pathlib import Path
from urllib.parse import urlsplit

from app.storage import load_json_strict, locked_json_mutation


COMPANY_FIELDS = ("name", "address", "email", "phone")


def _text(value):
    return str(value or "").strip()


def public_company(record):
    record = record if isinstance(record, dict) else {}
    return {
        field: _text(record.get(field, ""))
        for field in COMPANY_FIELDS
    }


def safe_local_path(value):
    candidate = _text(value)
    if not candidate.startswith("/") or candidate.startswith("//") or "\\" in candidate:
        return "/"
    parsed = urlsplit(candidate)
    if parsed.scheme or parsed.netloc:
        return "/"
    return parsed.path + (f"?{parsed.query}" if parsed.query else "")


def ensure_account_companies(users, accounts_file, legacy_company_file):
    accounts_file = Path(accounts_file)
    existing = load_json_strict(accounts_file, [], list)
    account_ids = {
        _text(record.get("account_id"))
        for record in users
        if isinstance(record, dict) and _text(record.get("account_id"))
    }
    existing_ids = {
        _text(record.get("account_id"))
        for record in existing
        if isinstance(record, dict) and _text(record.get("account_id"))
    }
    if account_ids.issubset(existing_ids):
        return existing

    legacy = load_json_strict(legacy_company_file, {}, dict)
    use_legacy = len(account_ids) == 1 and bool(_text(legacy.get("name")))

    def add_missing(records):
        present = {
            _text(record.get("account_id"))
            for record in records
            if isinstance(record, dict) and _text(record.get("account_id"))
        }
        for user in users:
            if not isinstance(user, dict):
                continue
            account_id = _text(user.get("account_id"))
            if not account_id or account_id in present:
                continue
            initial = public_company(legacy) if use_legacy else {
                "name": _text(user.get("company")),
                "address": "",
                "email": "",
                "phone": "",
            }
            records.append({
                "account_id": account_id,
                **initial,
                "setup_complete": use_legacy,
            })
            present.add(account_id)
        return records

    return locked_json_mutation(accounts_file, [], add_missing, list)


def load_account_company(account_id, accounts_file):
    normalized = _text(account_id)
    records = load_json_strict(accounts_file, [], list)
    record = next(
        (
            item for item in records
            if isinstance(item, dict) and _text(item.get("account_id")) == normalized
        ),
        None,
    )
    return public_company(record)


def company_setup_complete(account_id, accounts_file):
    normalized = _text(account_id)
    records = load_json_strict(accounts_file, [], list)
    record = next(
        (
            item for item in records
            if isinstance(item, dict) and _text(item.get("account_id")) == normalized
        ),
        None,
    )
    return bool(record and record.get("setup_complete"))


def save_account_company(account_id, company, accounts_file):
    normalized = _text(account_id)
    if not normalized:
        raise ValueError("Authenticated account is required.")
    values = public_company(company)

    def replace_company(records):
        for record in records:
            if isinstance(record, dict) and _text(record.get("account_id")) == normalized:
                record.update(values)
                record["setup_complete"] = True
                return
        records.append({
            "account_id": normalized,
            **values,
            "setup_complete": True,
        })

    locked_json_mutation(accounts_file, [], replace_company, list)
    return values
