"""Host-only audited recovery of ambiguous operations, using provider GETs only."""
import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import os
import re
import sqlite3
import time

from fastapi import HTTPException

from app.paddle_live_actions import LiveClient, ProviderUnavailable, _transaction
from app.paddle_live_offer import validate_transaction_offer, validate_completed_transaction
from app.paddle_live_store import BillingConflict, _account, _id, _json
from app.paddle_subscription_policy import _instant, evaluate_snapshot


def initialize_correlations(db):
    db.execute('''CREATE TABLE IF NOT EXISTS live_checkout_correlations (
        account_id TEXT PRIMARY KEY, token TEXT NOT NULL UNIQUE, created_at REAL NOT NULL)''')


def initialize_audit(db):
    db.execute('''CREATE TABLE IF NOT EXISTS live_operation_recoveries (
        recovery_id TEXT PRIMARY KEY, account_id TEXT NOT NULL, kind TEXT NOT NULL,
        target_id TEXT NOT NULL, result TEXT NOT NULL, checked_at TEXT NOT NULL,
        operator_ref TEXT NOT NULL, case_ref TEXT NOT NULL,
        evidence_digest TEXT NOT NULL, evidence TEXT NOT NULL)''')


def _reference(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,79}', value):
        raise ValueError('Non-personal operator/case reference required')


def _enabled():
    if (os.environ.get('TRADE_PAPER_PADDLE_LIVE_OPERATION_RECOVERY') != '1'
            or os.environ.get('TRADE_PAPER_PADDLE_LIVE_ACCESS') == '1'
            or os.environ.get('TRADE_PAPER_PADDLE_LIVE_CHECKOUT') == '1'):
        raise BillingConflict('Recovery requires opt-in and access/sales maintenance')


def _digest(value):
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _local(db, account, kind):
    operation = db.execute('SELECT started, target_id, result FROM live_operations WHERE account_id=? AND kind=?',
                           (account, kind)).fetchone()
    if not operation:
        raise BillingConflict('Existing operation required')
    reservation = db.execute('SELECT transaction_id, price_id FROM checkouts WHERE account_id=?', (account,)).fetchone()
    binding = db.execute('SELECT subscription_id, customer_id, transaction_id FROM bindings WHERE account_id=?',
                         (account,)).fetchone()
    correlation = None
    if db.execute("SELECT 1 FROM sqlite_master WHERE name='live_checkout_correlations' AND type='table'").fetchone():
        correlation = db.execute('SELECT token, created_at FROM live_checkout_correlations WHERE account_id=?', (account,)).fetchone()
    return {'operation': operation, 'reservation': reservation, 'binding': binding, 'correlation': correlation}


def _canonical(client, local, kind, target, offer, now):
    if kind == 'checkout':
        if local['binding']:
            raise BillingConflict('Already bound checkout is not recoverable here')
        correlation = local['correlation']
        registered = local['reservation'] == (target, offer.price_id) and local['operation'][1] == target
        if not registered and not correlation:
            raise BillingConflict('No trusted transaction correlation; preserve the unknown request')
        data = client.transaction(target)
        if data['id'] != target or data['origin'] != 'api':
            raise BillingConflict('Expected original server-created checkout')
        if not registered:
            if (not re.fullmatch(r'[a-f0-9]{64}', correlation[0])
                    or correlation[1] != local['operation'][0]
                    or data.get('custom_data', {}).get('trade_paper_intent') != correlation[0]
                    or not -5 <= _instant(data['created_at']).timestamp() - correlation[1] <= 60
                    or _instant(data['created_at']) > now):
                raise BillingConflict('Candidate is not linked to this server intent')
        if data['status'] == 'completed':
            validate_completed_transaction(data, offer)
            result = 'awaiting_completion'
        else:
            _transaction(data, offer.price_id, target)
            validate_transaction_offer(data, offer)
            # An unpaid draft with any payment attempt needs a separate review.
            if data.get('payments') != []:
                raise BillingConflict('Payment attempts require separate reconciliation')
            result = 'ready'
        safe = {'id': target, 'status': data['status'], 'origin': data['origin'],
                'provider_digest': _digest(data)}
    else:
        binding = local['binding']
        if not binding or target != binding[0] or local['operation'][1] != target:
            raise BillingConflict('Bound cancellation target required')
        data = client.subscription(target)
        decision = evaluate_snapshot(data, subscription_id=binding[0], customer_id=binding[1], price_id=offer.price_id, now=now)
        if decision.provider_status == 'canceled':
            result = 'canceled'
        elif (decision.provider_status == 'active' and decision.cancellation_pending
              and _instant(data['scheduled_change']['effective_at']) == _instant(data['current_billing_period']['ends_at'])
              and _instant(data['scheduled_change']['effective_at']) > now):
            result = 'scheduled'
        else:
            raise BillingConflict('Cancellation not confirmed; never repeat its POST')
        safe = {'id': target, 'status': data['status'], 'scheduled_change': data['scheduled_change'],
                'provider_digest': _digest(data)}
    return {'result': result, 'provider': safe}


def recover(store, client, offer, account, kind, *, target=None, operator, case, expected=None, now=None):
    _enabled(); _account(account); _reference(operator); _reference(case)
    if kind not in ('checkout', 'cancel') or offer.price_id != store.price_id:
        raise ValueError('Operation/offer mismatch')
    if expected is not None and (store.read_only or not re.fullmatch(r'[a-f0-9]{64}', expected)):
        raise ValueError('Writable ledger and preview digest required')
    now = datetime.now(timezone.utc) if now is None else now
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise ValueError('Timezone-aware time required')
    started = time.monotonic()
    with store.connect() as db:
        db.execute('BEGIN')
        local = _local(db, account, kind)
    target = target or local['operation'][1]
    if local['operation'][0] > now.timestamp():
        raise BillingConflict('Operation timestamp is in the future')
    _id(target, 'txn' if kind == 'checkout' else 'sub')
    if local['operation'][2] is not None:
        if local['operation'][1] != target:
            raise BillingConflict('Operation already resolved to another target')
        return {'result': 'already_confirmed', 'operation': kind}
    if local['operation'][1] not in (None, target) or (kind == 'checkout' and local['reservation'] not in (None, (target, offer.price_id))):
        raise BillingConflict('Operation target conflict')
    try:
        canonical = _canonical(client, local, kind, target, offer, now)
        if canonical != _canonical(client, local, kind, target, offer, now):
            raise BillingConflict('Provider evidence changed during review')
    except (KeyError, TypeError, AttributeError):
        raise ValueError('Incomplete provider evidence') from None
    # Store only a hash of the opaque correlation; its original stays in its table.
    safe_local = {**local, 'correlation': _digest(local['correlation']) if local['correlation'] else None}
    evidence = {'policy': 'operation-recovery-v1', 'account': account, 'kind': kind, 'target': target,
                'local': safe_local, 'canonical': canonical, 'offer': asdict(offer), 'operator': operator, 'case': case}
    digest = _digest(evidence)
    if expected is not None and expected != digest:
        raise BillingConflict('Preview changed; inspect a new preview')
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE' if expected else 'BEGIN')
        _enabled()
        if _local(db, account, kind) != local:
            raise BillingConflict('Local operation changed during review')
        elapsed = time.monotonic() - started
        if elapsed > 60:
            raise BillingConflict('Recovery exceeded freshness limit')
        checked = datetime.fromtimestamp(now.timestamp() + elapsed, timezone.utc)
        if canonical['result'] == 'scheduled' and _instant(canonical['provider']['scheduled_change']['effective_at']) <= checked:
            raise BillingConflict('Cancellation boundary passed during review')
        if expected:
            if kind == 'checkout':
                other = db.execute('SELECT account_id FROM checkouts WHERE transaction_id=?', (target,)).fetchone()
                if ((other and other != (account,))
                        or db.execute('SELECT 1 FROM live_renewals WHERE transaction_id=?', (target,)).fetchone()
                        or db.execute('SELECT 1 FROM live_adjustment_events a JOIN bindings b ON b.subscription_id=a.subscription_id '
                                      'WHERE a.transaction_id=? AND b.account_id<>?', (target, account)).fetchone()):
                    raise BillingConflict('Transaction already belongs to another billing flow')
                db.execute('INSERT OR IGNORE INTO checkouts VALUES (?, ?, ?)', (target, account, offer.price_id))
            initialize_audit(db)
            db.execute('INSERT INTO live_operation_recoveries VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                       (digest, account, kind, target, canonical['result'], checked.isoformat(), operator, case, digest, _json(evidence)))
            # Fresh reuse window for this exact unpaid transaction, never a new POST.
            stamp = checked.timestamp() if kind == 'checkout' else local['operation'][0]
            db.execute('UPDATE live_operations SET started=?, target_id=?, result=? WHERE account_id=? AND kind=?',
                       (stamp, target, canonical['result'], account, kind))
    return {'result': 'recovered' if expected else 'preview', 'digest': digest,
            'operation': kind, 'provider_result': canonical['result'], 'target_id': target,
            'signed_confirmation_required': True}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--account', required=True)
    parser.add_argument('--kind', required=True, choices=['checkout', 'cancel'])
    parser.add_argument('--transaction', help='Trusted provider transaction ID for an unknown checkout response')
    parser.add_argument('--operator', required=True)
    parser.add_argument('--case', required=True)
    parser.add_argument('--apply', metavar='PREVIEW_DIGEST')
    args = parser.parse_args(argv)
    try:
        _enabled()
        if args.kind == 'cancel' and args.transaction:
            raise ValueError('Cancellation target comes only from the existing operation')
        from app import paddle_live_runtime as runtime
        store = runtime.store(read_only=True)
        if args.apply:
            store = runtime.store()
        result = recover(store, LiveClient(os.environ.get('TRADE_PAPER_PADDLE_LIVE_API_KEY', '')),
                         runtime.offer(), args.account, args.kind, target=args.transaction,
                         operator=args.operator, case=args.case, expected=args.apply)
    except (BillingConflict, ProviderUnavailable, OSError, sqlite3.Error, ValueError, HTTPException):
        parser.exit(2, 'Operation recovery not confirmed. Preserve the request and inspect provider evidence; do not submit another payment or cancellation.\n')
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    main()
