"""Trusted-host notification replay; never ingest unsigned API event payloads.

Preview uses provider GETs. Apply reserves one attempt before requesting Paddle
to redeliver through the existing signature-verified webhook. No financial POSTs,
automatic retries, access activation or historical evidence backfill.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
import re
import sqlite3
import time

from fastapi import HTTPException

from app.paddle_live_actions import LiveClient, ProviderUnavailable
from app.paddle_live_store import BillingConflict, SUBSCRIPTION_EVENTS, _account, _id, _json
from app.paddle_subscription_policy import _instant, evaluate_snapshot
from app.paddle_live_offer import validate_completed_transaction

DESTINATION = 'https://www.tradepaper.ai/webhooks/paddle-live'
KINDS = SUBSCRIPTION_EVENTS | {'transaction.completed', 'adjustment.created', 'adjustment.updated'}


def initialize(db):
    db.execute('''CREATE TABLE IF NOT EXISTS live_replay_requests (
        event_id TEXT PRIMARY KEY, notification_id TEXT NOT NULL,
        setting_id TEXT NOT NULL, account_id TEXT NOT NULL,
        event_type TEXT NOT NULL, occurred_at TEXT NOT NULL,
        operator_ref TEXT NOT NULL, case_ref TEXT NOT NULL,
        requested_at TEXT NOT NULL, evidence_digest TEXT NOT NULL,
        replay_id TEXT)''')


def _reference(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,79}', value):
        raise ValueError('Non-personal operator and case references required')
    return value


def _maintenance():
    if (os.environ.get('TRADE_PAPER_PADDLE_LIVE_REPLAY') != '1'
            or os.environ.get('TRADE_PAPER_PADDLE_LIVE_WEBHOOK') != '1'
            or os.environ.get('TRADE_PAPER_PADDLE_LIVE_ACCESS') == '1'
            or os.environ.get('TRADE_PAPER_PADDLE_LIVE_CHECKOUT') == '1'):
        raise BillingConflict('Replay requires explicit opt-in, webhook reception and access/sales maintenance')


def accepted_results(kind):
    return ({'bound', 'renewal_recorded', 'renewal_existing'} if kind == 'transaction.completed' else
            {'applied', 'stale', 'equivalent'} if kind in SUBSCRIPTION_EVENTS else
            {'adjustment_review', 'adjustment_recorded'})


def _receipt(db, event_id, kind, occurred):
    row = db.execute('SELECT occurred_at, result FROM events WHERE event_id=?', (event_id,)).fetchone()
    if row and (_instant(row[0]) != _instant(occurred) or row[1] not in accepted_results(kind)):
        raise BillingConflict('Local receipt conflicts with selected provider event')
    return bool(row)


def _request_row(db, event_id):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='live_replay_requests'").fetchone():
        return None
    return db.execute('SELECT * FROM live_replay_requests WHERE event_id=?', (event_id,)).fetchone()


def status(store, event_id):
    """Local receipt, not provider HTTP acceptance, is the recovery confirmation."""
    _id(event_id, 'evt')
    with store.connect() as db:
        db.execute('BEGIN')
        row = _request_row(db, event_id)
        if not row:
            raise ValueError('No recorded replay attempt')
        received = _receipt(db, row[0], row[4], row[5])
    return {'event_id': event_id, 'result': 'received' if received else
            'awaiting_signed_delivery' if row[10] else 'outcome_unknown',
            'replay_id': row[10]}


def _ownership(db, payload, account, offer):
    data, kind = payload['data'], payload['event_type']
    sub, customer = (_id(data['id'], 'sub') if kind in SUBSCRIPTION_EVENTS else
                      _id(data['subscription_id'], 'sub')), _id(data['customer_id'], 'ctm')
    binding = db.execute('SELECT customer_id, account_id, transaction_id FROM bindings WHERE subscription_id=?',
                         (sub,)).fetchone()
    if kind == 'transaction.completed':
        validate_completed_transaction(data, offer)
        txn = _id(data['id'], 'txn')
        checkout = db.execute('SELECT account_id, price_id FROM checkouts WHERE transaction_id=?', (txn,)).fetchone()
        if data.get('origin') == 'subscription_recurring':
            if checkout or not binding or binding[:2] != (customer, account):
                raise BillingConflict('Bound renewal ownership required')
            from app.paddle_live_access import period
            period(data['billing_period'])
        elif checkout != (account, offer.price_id) or (binding and binding != (customer, account, txn)):
            raise BillingConflict('Existing exact checkout reservation required')
    elif not binding or binding[:2] != (customer, account):
        raise BillingConflict('Existing subscription ownership required')
    if kind in SUBSCRIPTION_EVENTS:
        evaluate_snapshot(data, subscription_id=sub, customer_id=customer, price_id=offer.price_id,
                          now=_instant(payload['occurred_at']))
    return {'subscription': sub, 'customer': customer, 'binding': binding}


def _provider(client, notification_id, setting_id, secret, now):
    setting = client.notification_setting(setting_id)
    # Never store or echo setting endpoint_secret_key. Only compare in memory.
    if (setting['id'] != setting_id or setting['type'] != 'url'
            or setting['destination'] != DESTINATION or setting['active'] is not True
            or type(setting['api_version']) is not int or setting['api_version'] != 1
            or setting['traffic_source'] != 'platform'
            or not hmac.compare_digest(setting['endpoint_secret_key'], secret)):
        raise BillingConflict('Live destination or signing configuration mismatch')
    notice = client.notification(notification_id)
    payload = notice['payload']
    event_id = _id(payload['event_id'], 'evt')
    occurred = _instant(payload['occurred_at'])
    kind = payload['event_type']
    if (notice['id'] != notification_id or notice['notification_setting_id'] != setting_id
            or notice['origin'] != 'event' or notice['status'] not in ('failed', 'delivered')
            or notice['type'] != kind or kind not in KINDS
            or payload.get('notification_id') != notification_id
            or not isinstance(payload['data'], dict)
            or not 0 <= (now - occurred).total_seconds() <= 90 * 86400
            or kind not in {r['name'] for r in setting['subscribed_events']}):
        raise BillingConflict('Original retained billing notification required')
    return payload, {'event_id': event_id, 'notification_id': notification_id, 'setting_id': setting_id,
                     'event_type': kind, 'occurred_at': occurred.isoformat(timespec='microseconds'),
                     'status': notice['status'], 'destination': DESTINATION,
                     'payload_digest': hashlib.sha256(_json(payload).encode()).hexdigest()}


def replay(store, client, offer, account, notification_id, setting_id, secret, *, operator, case,
           expected=None, now=None):
    _maintenance()
    _account(account); _id(notification_id, 'ntf'); _id(setting_id, 'ntfset')
    _reference(operator); _reference(case)
    if not isinstance(secret, str) or not secret or secret == os.environ.get('TRADE_PAPER_PADDLE_SANDBOX_WEBHOOK_SECRET'):
        raise ValueError('Separate Live signing secret required')
    if offer.price_id != store.price_id:
        raise ValueError('Ledger offer mismatch')
    if expected is not None and (store.read_only or not re.fullmatch(r'[a-f0-9]{64}', expected)):
        raise ValueError('Writable store and exact preview digest required')
    now = datetime.now(timezone.utc) if now is None else now
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise ValueError('Timezone-aware time required')
    started = time.monotonic()
    try:
        payload, evidence = _provider(client, notification_id, setting_id, secret, now)
        with store.connect() as db:
            db.execute('BEGIN IMMEDIATE' if expected else 'BEGIN')
            ownership = _ownership(db, payload, account, offer)
            if _receipt(db, evidence['event_id'], evidence['event_type'], evidence['occurred_at']):
                return {'event_id': evidence['event_id'], 'result': 'already_received'}
            previous = _request_row(db, evidence['event_id'])
            if previous:
                if previous[3] != account:
                    raise BillingConflict('Replay reservation ownership mismatch')
                return {'event_id': evidence['event_id'], 'result': 'already_requested', 'replay_id': previous[10]}
            digest = hashlib.sha256(_json({'provider': evidence, 'ownership': ownership, 'account': account,
                                          'operator': operator, 'case': case}).encode()).hexdigest()
            if expected and expected != digest:
                raise BillingConflict('Preview changed; inspect a new preview')
            _maintenance()
            if time.monotonic() - started > 60:
                raise BillingConflict('Replay preflight exceeded freshness limit')
            if not expected:
                return {'result': 'preview', 'digest': digest, 'event_id': evidence['event_id'],
                        'event_type': evidence['event_type'], 'occurred_at': evidence['occurred_at'],
                        'notification_id': notification_id, 'destination': DESTINATION}
            initialize(db)
            db.execute('INSERT INTO live_replay_requests VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)',
                       (evidence['event_id'], notification_id, setting_id, account, evidence['event_type'],
                        evidence['occurred_at'], operator, case, now.isoformat(), digest))
    except (KeyError, TypeError, AttributeError):
        raise ValueError('Incomplete notification evidence') from None
    # Persist intent before network mutation. Never retry after any failure here.
    _maintenance()
    replay_id = _id(client.replay_notification(notification_id), 'ntf')
    if replay_id == notification_id:
        raise ProviderUnavailable('Notification replay outcome is unconfirmed')
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        db.execute('UPDATE live_replay_requests SET replay_id=? WHERE event_id=? AND replay_id IS NULL',
                   (replay_id, evidence['event_id']))
    return status(store, evidence['event_id'])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--status', metavar='EVENT_ID')
    parser.add_argument('--account')
    parser.add_argument('--notification')
    parser.add_argument('--operator')
    parser.add_argument('--case')
    parser.add_argument('--apply', metavar='PREVIEW_DIGEST')
    args = parser.parse_args(argv)
    if os.environ.get('TRADE_PAPER_PADDLE_LIVE_REPLAY') != '1':
        parser.exit(2, 'Notification replay tool is disabled.\n')
    try:
        from app import paddle_live_runtime as runtime
        store = runtime.store(read_only=True)  # Never create a ledger for a typo.
        if args.status:
            if any((args.account, args.notification, args.operator, args.case, args.apply)):
                raise ValueError('Status is a separate read-only operation')
            result = status(store, args.status)
        else:
            if args.apply:
                _maintenance()
                store = runtime.store()
            result = replay(store, LiveClient(os.environ.get('TRADE_PAPER_PADDLE_LIVE_API_KEY', '')),
                            runtime.offer(), args.account, args.notification,
                            os.environ.get('TRADE_PAPER_PADDLE_LIVE_NOTIFICATION_SETTING_ID', ''),
                            os.environ.get('TRADE_PAPER_PADDLE_LIVE_WEBHOOK_SECRET', ''),
                            operator=args.operator, case=args.case, expected=args.apply)
    except (BillingConflict, ProviderUnavailable, OSError, sqlite3.Error, ValueError, HTTPException):
        parser.exit(2, 'Replay not confirmed; inspect local status and provider delivery logs. Do not retry blindly.\n')
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    main()
