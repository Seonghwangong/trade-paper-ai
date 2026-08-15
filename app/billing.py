from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from app.storage import data_path, load_json_strict, locked_json_mutation


BILLING_HISTORY_FILE = data_path("billing_history.json")


class StripeAdapter(Protocol):
    """Future payment boundary. The MVP never calls an external provider."""

    def create_checkout_session(self, *, account_id: str, plan: str, return_url: str) -> str:
        ...

    def create_customer_portal_session(self, *, account_id: str, return_url: str) -> str:
        ...


def account_billing_history(account_id: str, path: Path | None = None):
    owner = str(account_id or "").strip()
    rows = [
        item for item in load_json_strict(path or BILLING_HISTORY_FILE, [], list)
        if isinstance(item, dict) and str(item.get("account_id", "") or "").strip() == owner
    ]
    return sorted(rows, key=lambda item: str(item.get("created_at", "") or ""), reverse=True)


def account_invoice_history(account_id: str, path: Path | None = None):
    return [item for item in account_billing_history(account_id, path) if item.get("event") == "Invoice"]


def record_billing_event(account_id: str, plan: str, status: str, event: str, *, amount=0, now=None, path: Path | None = None):
    entry = {
        "account_id": str(account_id or "").strip(),
        "created_at": (now or datetime.now(timezone.utc)).isoformat(),
        "plan": str(plan or ""), "status": str(status or ""),
        "amount": float(amount or 0), "event": str(event or ""),
    }
    locked_json_mutation(path or BILLING_HISTORY_FILE, [], lambda rows: rows.append(entry), list)
    return entry
