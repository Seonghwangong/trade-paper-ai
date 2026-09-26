"""Opt-in Live ledger configuration and authoritative read projection."""
import os
import re
import sqlite3

from fastapi import HTTPException

from app.paddle_live_store import PaddleLiveStore
from app.paddle_live_offer import expected_offer
from app.storage import data_path


class LiveBillingUnavailable(HTTPException):
    def __init__(self):
        super().__init__(503, 'Billing status is temporarily unavailable. Please try again later.')


def price_id():
    value = os.environ.get('TRADE_PAPER_PADDLE_LIVE_PRICE_ID', '')
    if (not re.fullmatch(r'pri_[a-z0-9]{26}', value)
            or value == os.environ.get('TRADE_PAPER_PADDLE_SANDBOX_PRICE_ID')):
        raise LiveBillingUnavailable()
    return value


def store(*, read_only=False):
    try:
        return PaddleLiveStore(data_path('paddle_live.sqlite3'), price_id=price_id(),
                               environment='live', read_only=read_only)
    except (OSError, sqlite3.Error, ValueError):
        raise LiveBillingUnavailable() from None


def offer():
    try:
        return expected_offer(price_id())
    except ValueError:
        raise LiveBillingUnavailable() from None


def account_state(account_id, *, now=None):
    # Existing provider reservations remain protected even when access is disabled.
    path = data_path('paddle_live.sqlite3')
    if not path.exists():
        if os.environ.get('TRADE_PAPER_PADDLE_LIVE_ACCESS') == '1':
            raise LiveBillingUnavailable()
        return False, None
    try:
        return store(read_only=True).account_state(account_id, now=now)
    except (OSError, sqlite3.Error, ValueError):
        raise LiveBillingUnavailable() from None


def subscription_override(account_id, record, *, now=None):
    managed, decision = account_state(account_id, now=now)
    marked = record.get('billing_provider') == 'paddle' or bool(record.get('paddle_subscription_id'))
    if not managed and not marked:
        return None
    if (os.environ.get('TRADE_PAPER_PADDLE_LIVE_ACCESS') == '1'
            and decision is not None and decision.starter_access):
        return {'plan': 'Starter', 'status': 'Active'}
    # Free allowance remains available; stale users.json never grants paid access.
    return {'plan': 'Free', 'status': 'Active'}


def is_managed(account_id):
    return account_state(account_id)[0]
