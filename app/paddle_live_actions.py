"""Server-only Live checkout and cancellation services.

The default-off management adapter authorizes cancellation and enforces CSRF.
Other callers must authenticate/authorize the account and enforce CSRF before use.
Persistent reservations prohibit repeating ambiguous provider mutations. API
responses never grant/revoke access: signed events remain authoritative.
"""
from __future__ import annotations

from datetime import datetime, timezone
from http.client import HTTPException as HTTPTransportError
import json
import math
import os
import re
import time
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

from fastapi import HTTPException

from app import paddle_live_runtime as runtime
from app.paddle_live_store import BillingConflict, SUBSCRIPTION_EVENTS, _account, _id
from app.paddle_subscription_policy import _instant, evaluate_snapshot
from app.paddle_live_offer import expected_offer, validate_price, validate_transaction_offer

API = 'https://api.paddle.com'
TTL = 900
MAX_RESPONSE = 262144


class ProviderUnavailable(Exception):
    """No provider response/credentials may be exposed; reconcile before retry."""


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class LiveClient:
    def __init__(self, key):
        # Environment-specific modern keys only; never accept legacy/sandbox keys.
        if not isinstance(key, str) or not re.fullmatch(r'pdl_live_apikey_[A-Za-z0-9_]+', key):
            raise ValueError('Live API key required')
        self._key = key

    def _response(self, method, path, body=None):
        req = Request(API + path, method=method,
                      data=None if body is None else json.dumps(body).encode(),
                      headers={'Authorization': 'Bearer ' + self._key,
                               'Content-Type': 'application/json', 'Paddle-Version': '1'})
        try:
            with build_opener(NoRedirect()).open(req, timeout=15) as response:
                raw = response.read(MAX_RESPONSE + 1)
                if response.status not in (200, 201) or len(raw) > MAX_RESPONSE:
                    raise ValueError()
                data = json.loads(raw)
                if not isinstance(data, dict):
                    raise ValueError()
                return data
        except (URLError, OSError, HTTPTransportError, ValueError, KeyError, TypeError):
            raise ProviderUnavailable('Billing provider unavailable; operator review may be required') from None

    def _request(self, method, path, body=None):
        envelope = self._response(method, path, body)
        data = envelope.get('data')
        if not isinstance(data, dict):
            raise ProviderUnavailable('Billing provider unavailable; operator review may be required')
        return data

    def adjustments(self, subscription_id):
        """Bounded complete scan; never follow a provider-supplied next URL."""
        sub = _id(subscription_id, 'sub')
        rows, seen, after = [], set(), ''
        try:
            for _ in range(20):
                path = '/adjustments?subscription_id=' + sub + '&per_page=50&order_by=id%5BASC%5D'
                if after:
                    path += '&after=' + after
                result = self._response('GET', path)
                page, more = result['data'], result['meta']['pagination']['has_more']
                if not isinstance(page, list) or len(page) > 50 or type(more) is not bool:
                    raise ValueError()
                for row in page:
                    identifier = _id(row['id'], 'adj')
                    if identifier in seen or (after and identifier <= after):
                        raise ValueError()
                    seen.add(identifier)
                    after = identifier
                    rows.append(row)
                if not more:
                    return rows
                if not page:
                    raise ValueError()
        except (KeyError, TypeError, ValueError):
            pass
        raise ProviderUnavailable('Complete adjustment history unavailable')

    def events(self, since, until):
        """Complete bounded billing-event window for read-only monitoring."""
        start, end = _instant(since), _instant(until)
        if not 0 < (end - start).total_seconds() <= 90 * 86400:
            raise ValueError('Event window must be positive and at most 90 days')
        kinds = SUBSCRIPTION_EVENTS | {'transaction.completed', 'adjustment.created', 'adjustment.updated'}
        query = {'from': start.isoformat(), 'to': end.isoformat(), 'per_page': 20,
                 'order_by': 'id[ASC]', 'event_type': ','.join(sorted(kinds))}
        rows, seen, after = [], set(), ''
        deadline = time.monotonic() + 60
        try:
            for _ in range(50):
                if time.monotonic() > deadline:
                    break
                result = self._response('GET', '/events?' + urlencode({**query, **({'after': after} if after else {})}))
                page, more = result['data'], result['meta']['pagination']['has_more']
                if not isinstance(page, list) or len(page) > 20 or type(more) is not bool:
                    raise ValueError()
                for row in page:
                    identifier = _id(row['event_id'], 'evt')
                    if (identifier in seen or (after and identifier <= after)
                            or row['event_type'] not in kinds
                            or not start <= _instant(row['occurred_at']) <= end
                            or not isinstance(row['data'], dict)):
                        raise ValueError()
                    seen.add(identifier)
                    after = identifier
                    rows.append(row)
                if not more and time.monotonic() <= deadline:
                    return rows
                if not page:
                    raise ValueError()
        except (KeyError, TypeError, ValueError):
            pass
        raise ProviderUnavailable('Complete event window unavailable')

    def create_checkout(self, price):
        _id(price, 'pri')
        return self._request('POST', '/transactions', {
            'items': [{'price_id': price, 'quantity': 1}], 'collection_mode': 'automatic', 'currency_code': 'KRW'})

    def price(self, price_id):
        return self._request('GET', '/prices/' + _id(price_id, 'pri') + '?include=product')

    def transaction(self, transaction_id):
        return self._request('GET', '/transactions/' + _id(transaction_id, 'txn'))

    def subscription(self, subscription_id):
        return self._request('GET', '/subscriptions/' + _id(subscription_id, 'sub'))

    def cancel_at_period_end(self, subscription_id):
        return self._request('POST', '/subscriptions/' + _id(subscription_id, 'sub') + '/cancel',
                             {'effective_from': 'next_billing_period'})


def configured_service(operation):
    """Default-off factory; importing this module never calls Paddle or opens DB."""
    if operation not in ('checkout', 'cancel'):
        raise ValueError('Unknown billing operation')
    if os.environ.get('TRADE_PAPER_PADDLE_LIVE_' + operation.upper()) != '1':
        raise HTTPException(404, 'Not found')
    secret = os.environ.get('TRADE_PAPER_PADDLE_LIVE_WEBHOOK_SECRET')
    if operation == 'checkout' and (os.environ.get('TRADE_PAPER_PADDLE_LIVE_WEBHOOK') != '1'
            or os.environ.get('TRADE_PAPER_PADDLE_LIVE_ACCESS') != '1'
            or not secret or secret == os.environ.get('TRADE_PAPER_PADDLE_SANDBOX_WEBHOOK_SECRET')):
        raise runtime.LiveBillingUnavailable()
    try:
        client = LiveClient(os.environ.get('TRADE_PAPER_PADDLE_LIVE_API_KEY', ''))
    except ValueError:
        raise runtime.LiveBillingUnavailable() from None
    return LiveActions(runtime.store(), client)


def _transaction(data, price, expected=None):
    try:
        txn = _id(data['id'], 'txn')
        items = data['items']
        if (expected is not None and txn != expected or data['status'] not in ('draft', 'ready')
                or data['collection_mode'] != 'automatic'
                or not isinstance(items, list) or len(items) != 1
                or items[0]['price']['id'] != price
                or type(items[0]['quantity']) is not int or items[0]['quantity'] != 1):
            raise ValueError()
        return txn
    except (ValueError, KeyError, TypeError, IndexError):
        raise ProviderUnavailable('Checkout needs operator review') from None


class LiveActions:
    def __init__(self, store, client):
        if store.read_only:
            raise ValueError('Writable Live ledger required')
        self.store, self.client = store, client

    def checkout(self, account_id, *, now=None):
        _account(account_id)
        now = time.time() if now is None else now
        if not isinstance(now, (int, float)) or not math.isfinite(now) or now < 0:
            raise ValueError('Valid timestamp required')
        offer = expected_offer(self.store.price_id)
        # Read-only preflight before reserving a mutation; invalid catalog never
        # creates a transaction or strands an otherwise unused account.
        validate_price(self.client.price(self.store.price_id), offer)
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT 1 FROM bindings WHERE account_id=?', (account_id,)).fetchone():
                raise BillingConflict('Subscription already linked')
            previous = db.execute("SELECT started, target_id, result FROM live_operations "
                                  "WHERE account_id=? AND kind='checkout'", (account_id,)).fetchone()
            registered = db.execute('SELECT transaction_id, price_id FROM checkouts WHERE account_id=?',
                                    (account_id,)).fetchone()
            if previous:
                if (previous[2] != 'ready' or not 0 <= now - previous[0] <= TTL
                        or registered != (previous[1], self.store.price_id)):
                    raise BillingConflict('Previous checkout needs operator review')
            elif registered:
                raise BillingConflict('Existing checkout needs operator review')
            else:
                # Commit intent BEFORE network. A crash/timeout never frees it.
                db.execute("INSERT INTO live_operations VALUES (?, 'checkout', ?, NULL, NULL)",
                           (account_id, now))
        if previous:
            data = self.client.transaction(previous[1])
            txn = _transaction(data, self.store.price_id, previous[1])
            validate_transaction_offer(data, offer)
            return txn
        data = self.client.create_checkout(self.store.price_id)
        txn = _transaction(data, self.store.price_id)
        validate_transaction_offer(data, offer)
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            # Registration and response journal commit together, before exposing ID.
            if db.execute('SELECT 1 FROM live_renewals WHERE transaction_id=?', (txn,)).fetchone():
                raise BillingConflict('Checkout response conflicts with a recorded renewal')
            db.execute('INSERT INTO checkouts VALUES (?, ?, ?)', (txn, account_id, self.store.price_id))
            db.execute("UPDATE live_operations SET target_id=?, result='ready' "
                       "WHERE account_id=? AND kind='checkout'", (txn, account_id))
        return txn

    def cancel(self, account_id, *, now=None):
        _account(account_id)
        now = datetime.now(timezone.utc) if now is None else now
        if not isinstance(now, datetime) or now.tzinfo is None:
            raise ValueError('Timezone-aware timestamp required')
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            binding = db.execute('SELECT subscription_id, customer_id FROM bindings WHERE account_id=?',
                                 (account_id,)).fetchone()
            if not binding:
                raise BillingConflict('Trusted subscription binding required')
            previous = db.execute("SELECT target_id FROM live_operations WHERE account_id=? AND kind='cancel'",
                                  (account_id,)).fetchone()
            if previous and previous[0] != binding[0]:
                raise BillingConflict('Cancellation ownership conflict')
            if not previous:
                db.execute("INSERT INTO live_operations VALUES (?, 'cancel', ?, ?, NULL)",
                           (account_id, now.timestamp(), binding[0]))
        # Read canonical state even on retry. Read-only reconciliation may confirm
        # a timed-out success, but absence of a change NEVER authorizes a second POST.
        data = self.client.subscription(binding[0])
        decision = self._decision(data, binding, now)
        if decision.provider_status == 'canceled':
            return self._confirmed_cancel(account_id, 'canceled')
        if decision.cancellation_pending:
            return self._confirmed_cancel(account_id, 'scheduled')
        if previous:
            raise BillingConflict('Previous cancellation needs operator review')
        if not decision.starter_access or data['scheduled_change'] is not None:
            raise BillingConflict('Subscription needs operator review before cancellation')
        response = self.client.cancel_at_period_end(binding[0])
        confirmed = self._decision(response, binding, now)
        if not confirmed.cancellation_pending or confirmed.provider_status != 'active':
            raise ProviderUnavailable('Cancellation needs operator review')
        if (_instant(response['scheduled_change']['effective_at'])
                != _instant(data['current_billing_period']['ends_at'])):
            raise ProviderUnavailable('Cancellation date needs operator review')
        return self._confirmed_cancel(account_id, 'scheduled')

    def _decision(self, data, binding, now):
        try:
            return evaluate_snapshot(data, subscription_id=binding[0], customer_id=binding[1],
                                     price_id=self.store.price_id, now=now)
        except ValueError:
            raise ProviderUnavailable('Subscription needs operator review') from None

    def _confirmed_cancel(self, account_id, state):
        with self.store.connect() as db:
            db.execute("UPDATE live_operations SET result=? WHERE account_id=? AND kind='cancel'",
                       (state, account_id))
        # This is an operation acknowledgement, NEVER an entitlement update.
        return state
