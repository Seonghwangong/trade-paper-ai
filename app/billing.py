from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import re
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


def _currency(value):
    # Older billing records were displayed as USD; preserve that interpretation.
    code = str(value or "USD").strip().upper() or "USD"
    if not re.fullmatch(r"[A-Z]{3}", code):
        raise ValueError("Invalid billing currency")
    return code


def _amount(value):
    try:
        amount = Decimal(str(value if value not in (None, "") else 0))
    except InvalidOperation:
        raise ValueError("Invalid billing amount") from None
    if not amount.is_finite():
        raise ValueError("Invalid billing amount")
    return amount


def amount_label(record):
    """Display major currency units, never convert between currencies."""
    try:
        currency, amount = _currency(record.get("currency")), _amount(record.get("amount"))
    except ValueError:
        return "Amount unavailable"
    symbol = {"USD": "$", "KRW": "₩"}.get(currency, "")
    return f"{currency} {symbol}{amount:,.2f}"


def monthly_recorded_amounts(records, month):
    """Active billing entries by currency; not MRR or verified provider revenue."""
    totals = {}
    invalid = False
    for row in records:
        if (not isinstance(row, dict) or row.get("status") != "Active"
                or not str(row.get("created_at", "")).startswith(month)):
            continue
        try:
            currency, amount = _currency(row.get("currency")), _amount(row.get("amount"))
        except ValueError:
            invalid = True
            continue
        totals[currency] = totals.get(currency, Decimal(0)) + amount
    labels = [amount_label({"currency": code, "amount": value}) for code, value in sorted(totals.items())]
    if invalid:
        labels.append("Some amounts unavailable")
    return labels or [amount_label({"amount": 0})]


def record_billing_event(account_id: str, plan: str, status: str, event: str, *, amount=0, currency="USD", now=None, path: Path | None = None):
    entry = {
        "account_id": str(account_id or "").strip(),
        "created_at": (now or datetime.now(timezone.utc)).isoformat(),
        "plan": str(plan or ""), "status": str(status or ""),
        "amount": float(_amount(amount)), "currency": _currency(currency), "event": str(event or ""),
    }
    locked_json_mutation(path or BILLING_HISTORY_FILE, [], lambda rows: rows.append(entry), list)
    return entry
