"""Side-effect-free policy for authenticated, server-bound Paddle snapshots.

The opt-in Live runtime derives access without production entitlement writes. Its caller
must verify signatures/API provenance, ownership and event ordering BEFORE using
it. Browser checkout events/custom_data must never be passed as trusted state.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import re


@dataclass(frozen=True)
class AccessDecision:
    starter_access: bool
    provider_status: str
    access_until: datetime | None
    cancellation_pending: bool


def _instant(value):
    if not isinstance(value, str):
        raise ValueError("Timestamp required")
    try:
        instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("Invalid timestamp") from None
    if instant.tzinfo is None:
        raise ValueError("Timezone required")
    return instant.astimezone(timezone.utc)


def evaluate_snapshot(data, *, subscription_id, customer_id, price_id, now):
    """One monthly Starter item, without a paid-plan trial or past-due grace.

    IDs must come from a trusted server checkout binding, not request parameters.
    Expired snapshots cannot extend access; the integration must reconcile
    against Paddle before renewing the local access period.
    """
    for prefix, value in (("sub", subscription_id), ("ctm", customer_id), ("pri", price_id)):
        if not isinstance(value, str) or not re.fullmatch(prefix + r"_[a-z0-9]{26}", value):
            raise ValueError("Trusted provider IDs required")
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise ValueError("Timezone-aware current time required")
    try:
        if data["id"] != subscription_id or data["customer_id"] != customer_id:
            raise ValueError("Subscription ownership mismatch")
        items = data["items"]
        if (not isinstance(items, list) or len(items) != 1
                or items[0]["price"]["id"] != price_id
                or type(items[0]["quantity"]) is not int or items[0]["quantity"] != 1
                or data["collection_mode"] != "automatic"
                or data["billing_cycle"]["interval"] != "month"
                or type(data["billing_cycle"]["frequency"]) is not int
                or data["billing_cycle"]["frequency"] != 1):
            raise ValueError("Unexpected Starter subscription")
        status = data["status"]
        if status not in {"active", "trialing", "past_due", "paused", "canceled"}:
            raise ValueError("Unknown subscription status")
        change = data["scheduled_change"]
        action, effective = None, None
        if change is not None:
            action = change["action"]
            if action not in {"cancel", "pause", "resume"}:
                raise ValueError("Unknown scheduled change")
            effective = _instant(change["effective_at"])
        if status != "active":
            return AccessDecision(False, status, None, action == "cancel")
        period = data["current_billing_period"]
        start, end = _instant(period["starts_at"]), _instant(period["ends_at"])
        if start >= end:
            raise ValueError("Invalid billing period")
        if action in {"cancel", "pause"}:
            end = min(end, effective)
        allowed = start <= now < end
        return AccessDecision(allowed, status, end, action == "cancel")
    except (KeyError, TypeError, IndexError, AttributeError):
        raise ValueError("Incomplete subscription snapshot") from None


def is_provider_managed(record):
    """Conservatively reserve provider-linked records for provider workflows."""
    return bool(record.get("billing_provider") or record.get("paddle_subscription_id"))


def require_local_subscription(record):
    if is_provider_managed(record):
        from fastapi import HTTPException
        raise HTTPException(409, "This subscription is managed by a payment provider. Contact support to change or cancel it.")
