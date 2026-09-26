"""Host-operator review release. Provider reads only; no public HTTP endpoint.

Preview and apply each fetch fresh canonical state twice. A release covers exact
signed event IDs, never a subscription-wide exemption or a timestamp watermark.
Signed snapshots, matching completed periods and review holds determine access.
"""
from dataclasses import asdict
from datetime import datetime, timezone
import argparse
import hashlib
import json
import os
import re
import sqlite3
import time

from fastapi import HTTPException

from app.paddle_live_actions import LiveClient, ProviderUnavailable
from app.paddle_live_adjustments import ACTIONS
from app.paddle_live_offer import validate_completed_transaction
from app.paddle_live_store import BillingConflict, _account, _id, _json
from app.paddle_subscription_policy import evaluate_snapshot


class ReviewBlocked(BillingConflict):
    """Evidence is incomplete, unsafe, or changed; keep the hold."""


def _digest(value):
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _reference(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,79}', value):
        raise ValueError('Use a short operator/case reference without personal details')
    return value


def _local(db, account):
    binding = db.execute('SELECT subscription_id, customer_id, transaction_id FROM bindings '
                         'WHERE account_id=?', (account,)).fetchone()
    if not binding:
        raise ReviewBlocked('Bound account required')
    snapshot = db.execute('SELECT occurred_at, snapshot FROM snapshots WHERE subscription_id=?',
                          (binding[0],)).fetchone()
    if not snapshot:
        raise ReviewBlocked('Signed subscription snapshot required')
    events = db.execute('SELECT * FROM live_adjustment_events WHERE subscription_id=? ORDER BY event_id',
                        (binding[0],)).fetchall()
    coverage = []
    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='live_review_coverage'").fetchone():
        coverage = db.execute('SELECT c.event_id, c.review_id FROM live_review_coverage c '
                              'JOIN live_review_releases r ON r.review_id=c.review_id '
                              'WHERE r.subscription_id=? ORDER BY c.event_id', (binding[0],)).fetchall()
    return {'binding': binding, 'snapshot': snapshot, 'events': events, 'coverage': coverage}


def _projection(data):
    result = {key: data[key] for key in ('id', 'customer_id', 'status', 'collection_mode')}
    for key, fields in (('billing_cycle', ('interval', 'frequency')),
                        ('current_billing_period', ('starts_at', 'ends_at')),
                        ('scheduled_change', ('action', 'effective_at'))):
        result[key] = None if data[key] is None else {field: data[key][field] for field in fields}
    result['items'] = [{'price': {'id': item['price']['id']}, 'quantity': item['quantity']}
                       for item in data['items']]
    return result


def _canonical(client, local, offer, now):
    sub, customer, initial_txn = local['binding']
    data = client.subscription(sub)
    decision = evaluate_snapshot(data, subscription_id=sub, customer_id=customer,
                                 price_id=offer.price_id, now=now)
    projection = _projection(data)
    if not decision.starter_access or projection != json.loads(local['snapshot'][1]):
        raise ReviewBlocked('Current active subscription must match signed local state')
    adjustments = client.adjustments(sub)
    if not isinstance(adjustments, list) or not 1 <= len(adjustments) <= 1000:
        raise ReviewBlocked('Complete adjustment history required')
    normalized = {}
    transactions = {initial_txn}
    for item in adjustments:
        adj, txn = _id(item['id'], 'adj'), _id(item['transaction_id'], 'txn')
        if (adj in normalized or item['subscription_id'] != sub or item['customer_id'] != customer
                or item['currency_code'] != offer.currency or item['action'] not in ACTIONS
                or item['type'] not in ('full', 'partial', None)):
            raise ReviewBlocked('Adjustment ownership or terms mismatch')
        action, status = item['action'], item['status']
        reverse = action.endswith('_reverse')
        if status not in ('rejected', 'reversed') and not (reverse and status == 'approved'):
            raise ReviewBlocked('Unresolved refund, credit or dispute remains')
        total = item['totals']['total']
        if (item['totals']['currency_code'] != offer.currency or not isinstance(total, str)
                or not re.fullmatch(r'-?(0|[1-9][0-9]{0,19})', total)):
            raise ReviewBlocked('Invalid adjustment amount')
        normalized[adj] = {'transaction_id': txn, 'action': action, 'status': status,
                           'type': item['type'], 'currency': item['currency_code'], 'total': total}
        transactions.add(txn)
    for value in normalized.values():
        if value['action'].endswith('_reverse') and value['status'] == 'approved':
            original = value['action'].removesuffix('_reverse')
            if not any(v['transaction_id'] == value['transaction_id'] and v['action'] == original
                       and v['status'] == 'reversed' for v in normalized.values()):
                raise ReviewBlocked('Reversal lacks a reversed original adjustment')
    for row in local['events']:
        # Compare identity even for pending/rejected evidence. Missing provider
        # history is not evidence that a refund/dispute was resolved.
        item = normalized.get(row[1])
        if (item is None or (item['transaction_id'], item['action'], item['currency'])
                != (row[4], row[5], row[8])):
            raise ReviewBlocked('Signed adjustment history does not match provider')
    if len(transactions) > 50:
        raise ReviewBlocked('History exceeds manual review limit')
    verified = {}
    for txn in sorted(transactions):
        payment = client.transaction(txn)
        if (payment['id'] != txn or payment['subscription_id'] != sub
                or payment['customer_id'] != customer):
            raise ReviewBlocked('Transaction ownership mismatch')
        # Only fully restored, captured, unadjusted Starter payments qualify.
        # Approved partial/tax refunds are intentionally not auto-accepted.
        validate_completed_transaction(payment, offer)
        verified[txn] = {'status': payment['status'], 'currency': payment['currency_code'],
                         'total': payment['details']['totals']['grand_total'],
                         'adjusted_total': payment['details']['adjusted_totals']['grand_total'],
                         'evidence_digest': _digest({'details': payment['details'], 'items': payment['items'],
                                                     'payments': payment['payments']})}
    return {'policy': 'restored-payment-v1', 'subscription': projection,
            'adjustments': normalized, 'transactions': verified}


def reconcile(store, client, offer, account, *, operator, case, expected=None, now=None):
    """Preview by default; apply re-fetches evidence and requires its exact digest.

    Trusted host operators only. Do not expose this function as a customer route.
    Operator/case references are audit labels, not an authentication mechanism.
    """
    _account(account)
    _reference(operator)
    _reference(case)
    if offer.price_id != store.price_id:
        raise ValueError('Offer/store mismatch')
    if expected is not None and (store.read_only or not re.fullmatch(r'[a-f0-9]{64}', expected)):
        raise ValueError('Writable ledger and exact preview digest required')
    now = datetime.now(timezone.utc) if now is None else now
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise ValueError('Timezone-aware review time required')
    started = time.monotonic()
    with store.connect() as db:
        db.execute('BEGIN')
        local = _local(db, account)
    covered = {row[0] for row in local['coverage']}
    targets = [row[0] for row in local['events'] if row[11] and row[0] not in covered]
    if not targets:
        raise ReviewBlocked('No unresolved signed review events')
    try:
        canonical = _canonical(client, local, offer, now)
        if canonical != _canonical(client, local, offer, now):
            raise ReviewBlocked('Provider state changed during review')
    except (KeyError, TypeError, IndexError, ValueError, AttributeError):
        raise ReviewBlocked('Incomplete or inconsistent provider evidence') from None
    if time.monotonic() - started > 90:
        raise ReviewBlocked('Provider review exceeded freshness limit')
    digest = _digest({'account': account, 'operator': operator, 'case': case, 'offer': asdict(offer),
                      'local': local, 'provider': canonical})
    if expected is not None and digest != expected:
        raise ReviewBlocked('Preview changed; inspect a new preview before applying')
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE' if expected else 'BEGIN')
        if _local(db, account) != local:
            raise ReviewBlocked('Local evidence changed during provider review')
        # Never extend a period that expired while provider requests ran.
        elapsed = time.monotonic() - started
        if elapsed > 90:
            raise ReviewBlocked('Provider review exceeded freshness limit')
        finished = now.timestamp() + elapsed
        decision = evaluate_snapshot(canonical['subscription'], subscription_id=local['binding'][0],
                    customer_id=local['binding'][1], price_id=offer.price_id,
                    now=datetime.fromtimestamp(finished, timezone.utc))
        from app.paddle_live_access import gate
        decision = gate(db, canonical['subscription'], decision, offer.price_id)
        if not decision.starter_access:
            raise ReviewBlocked('Paid period missing or access expired during review')
        if expected:
            db.execute('INSERT INTO live_review_releases VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                       (digest, account, local['binding'][0], operator, case,
                        datetime.fromtimestamp(finished, timezone.utc).isoformat(),
                        _digest(canonical), _json(canonical)))
            db.executemany('INSERT INTO live_review_coverage VALUES (?, ?)',
                           [(event_id, digest) for event_id in targets])
    return {'result': 'released' if expected else 'preview', 'digest': digest,
            'review_events': len(targets), 'case': case, 'account': account,
            'subscription_id': local['binding'][0], 'policy': canonical['policy'],
            'adjustments': canonical['adjustments'], 'transactions': canonical['transactions'],
            'access_until': decision.access_until.isoformat()}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--account', required=True)
    parser.add_argument('--operator', required=True, help='Non-personal operator reference')
    parser.add_argument('--case', required=True, help='Non-personal support case reference')
    parser.add_argument('--apply', metavar='PREVIEW_DIGEST')
    args = parser.parse_args(argv)
    if os.environ.get('TRADE_PAPER_PADDLE_LIVE_RECONCILE') != '1':
        parser.exit(2, 'Live review tool is disabled.\n')
    try:
        from app import paddle_live_runtime as runtime
        # Validate an existing ledger first; never create one for a CLI typo.
        store = runtime.store(read_only=True)
        offer = runtime.offer()
        client = LiveClient(os.environ.get('TRADE_PAPER_PADDLE_LIVE_API_KEY', ''))
        if args.apply:
            store = runtime.store()
        result = reconcile(store, client, offer, args.account, operator=args.operator,
                           case=args.case, expected=args.apply)
    except (BillingConflict, ProviderUnavailable, ValueError, OSError, sqlite3.Error, HTTPException):
        # Errors may contain paths or provider content. Do not echo them.
        parser.exit(2, 'Review not applied; verify configuration and current billing evidence.\n')
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    main()
