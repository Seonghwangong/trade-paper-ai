"""Read-only host diagnostics. No event replay, payment mutations or notifications.

Local silence cannot prove a missing webhook. Optional authenticated provider GETs
compare a bounded event window to local receipts; skipped/incomplete checks never
report full health. This command must be scheduled/connected to alerts separately.
"""
import argparse
from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
import os
import sqlite3
import zipfile

from app.paddle_live_actions import LiveClient, ProviderUnavailable
from app.paddle_live_adjustments import needs_review
from app.paddle_live_backup import inspect_ledger, verify_backup, _readonly
from app.paddle_live_store import SUBSCRIPTION_EVENTS, _id
from app.paddle_subscription_policy import _instant

KINDS = SUBSCRIPTION_EVENTS | {'transaction.completed', 'adjustment.created', 'adjustment.updated'}
INVALID = (OSError, sqlite3.Error, ValueError, TypeError, KeyError, AttributeError, zipfile.BadZipFile, RuntimeError)


def _issue(report, code, severity='warning', count=None, event_ids=None):
    item = {'code': code, 'severity': severity}
    if count is not None:
        item['count'] = count
    if event_ids:
        item['event_ids'] = sorted(event_ids)[:20]
    report['issues'].append(item)


def _finish(report):
    severities = {item['severity'] for item in report['issues']}
    report['status'] = 'critical' if 'critical' in severities else 'warning' if severities else 'ok'
    return report


def _scope(event, checkouts, bindings, price_id):
    data, kind = event['data'], event['event_type']
    prefix = 'txn' if kind == 'transaction.completed' else 'sub' if kind in SUBSCRIPTION_EVENTS else 'adj'
    entity = _id(data['id'], prefix)
    if kind == 'transaction.completed':
        if entity in checkouts:
            return 'tracked'
        if data.get('subscription_id') in bindings:
            return 'renewal' if data.get('origin') == 'subscription_recurring' else 'unsupported'
    elif kind in SUBSCRIPTION_EVENTS:
        if entity in bindings:
            return 'tracked'
    elif data.get('subscription_id') in bindings or data.get('transaction_id') in checkouts:
        return 'tracked'
    items = data.get('items')
    if isinstance(items, list) and items:
        prices = [item.get('price', {}).get('id') for item in items if isinstance(item, dict)]
        if price_id in prices:
            return 'unscoped'
        if len(prices) == len(items) and all(prices):
            return 'unrelated'
    # Unknown adjustment ownership cannot be classified by price. Do not hide it.
    return 'unscoped'


def _provider_report(report, db, events, since, until, price_id):
    if not isinstance(events, list) or len(events) > 1000:
        raise ValueError('Incomplete provider window')
    checkouts = {r[0] for r in db.execute('SELECT transaction_id FROM checkouts')}
    bindings = {r[0] for r in db.execute('SELECT subscription_id FROM bindings')}
    counts = {'tracked': 0, 'renewal': 0, 'unsupported': 0, 'unscoped': 0, 'unrelated': 0}
    missing, conflicts, seen = [], [], set()
    for event in events:
        identifier = _id(event['event_id'], 'evt')
        kind = event['event_type']
        occurred = _instant(event['occurred_at'])
        if (identifier in seen or kind not in KINDS or not since <= occurred <= until
                or not isinstance(event['data'], dict)):
            raise ValueError('Invalid provider event')
        seen.add(identifier)
        scope = _scope(event, checkouts, bindings, price_id)
        counts[scope] += 1
        if scope not in ('tracked', 'renewal'):
            continue
        row = db.execute('SELECT occurred_at, result FROM events WHERE event_id=?', (identifier,)).fetchone()
        if not row:
            missing.append(identifier)
            continue
        allowed = ({'renewal_recorded', 'renewal_existing'} if scope == 'renewal' else
                   {'bound'} if kind == 'transaction.completed' else
                   {'applied', 'stale', 'equivalent'} if kind in SUBSCRIPTION_EVENTS else
                   {'adjustment_review', 'adjustment_recorded'})
        mismatch = _instant(row[0]) != occurred or row[1] not in allowed
        if scope == 'renewal':
            receipt = db.execute('SELECT transaction_id FROM live_renewal_receipts WHERE event_id=?', (identifier,)).fetchone()
            mismatch = mismatch or receipt != (event['data']['id'],)
        if mismatch:
            conflicts.append(identifier)
    report['provider'] = {'status': 'complete', 'from': since.isoformat(), 'to': until.isoformat(),
                          'events': len(events), **counts}
    if missing:
        _issue(report, 'provider_events_missing_locally', 'critical', len(missing), missing)
    if conflicts:
        _issue(report, 'provider_receipt_conflict', 'critical', len(conflicts), conflicts)
    if counts['unsupported']:
        _issue(report, 'unsupported_subscription_completion', count=counts['unsupported'])
    if counts['unscoped']:
        _issue(report, 'provider_events_without_local_ownership', count=counts['unscoped'])


def _local_report(report, db, now, grace, operation_age):
    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    bound = db.execute('SELECT subscription_id FROM bindings').fetchall()
    no_snapshot, overdue, holds = 0, 0, 0
    for sub, in bound:
        holds += int(needs_review(db, sub))
        row = db.execute('SELECT snapshot FROM snapshots WHERE subscription_id=?', (sub,)).fetchone()
        if not row:
            no_snapshot += 1
            continue
        snapshot = json.loads(row[0])
        if snapshot['status'] == 'active':
            due = _instant(snapshot['current_billing_period']['ends_at'])
            change = snapshot['scheduled_change']
            if change and change['action'] in ('cancel', 'pause'):
                due = min(due, _instant(change['effective_at']))
            overdue += int(due < now - timedelta(seconds=grace))
    if no_snapshot:
        _issue(report, 'bound_subscriptions_without_snapshot', count=no_snapshot)
    if overdue:
        _issue(report, 'subscription_confirmation_overdue', count=overdue)
    if holds:
        _issue(report, 'billing_reviews_pending', count=holds)
    pending, waiting, future = 0, 0, 0
    if 'live_operations' in tables:
        for account, kind, started, target, result in db.execute('SELECT * FROM live_operations'):
            age = now.timestamp() - started
            future += int(age < -grace)
            if age < operation_age:
                continue
            if result is None:
                pending += 1
            elif kind == 'cancel':
                row = db.execute('SELECT snapshot FROM snapshots WHERE subscription_id=?', (target,)).fetchone()
                data = json.loads(row[0]) if row else None
                confirmed = data and (data['status'] == 'canceled' or
                    (result == 'scheduled' and data.get('scheduled_change') and
                     data['scheduled_change']['action'] == 'cancel'))
                waiting += int(not confirmed)
    if pending:
        _issue(report, 'ambiguous_operations_overdue', 'critical', pending)
    if waiting:
        _issue(report, 'cancellation_confirmation_overdue', count=waiting)
    latest = None
    for when, in db.execute('SELECT occurred_at FROM events'):
        stamp = _instant(when)
        latest = max(latest, stamp) if latest else stamp
        future += int(stamp > now + timedelta(seconds=grace))
    if future:
        _issue(report, 'ledger_clock_ahead', 'critical', future)
    report['ledger'].update({'bound_subscriptions': len(bound), 'pending_reviews': holds,
                             'latest_event_occurred_at': latest.isoformat() if latest else None})


def diagnose(path, *, price_id, archive=None, archive_sha256=None, client=None, now=None,
             lookback_hours=24, grace_seconds=300, operation_age_seconds=900, backup_age_hours=24):
    now = datetime.now(timezone.utc) if now is None else now
    _id(price_id, 'pri')
    if (not isinstance(now, datetime) or now.tzinfo is None
            or type(lookback_hours) is not int or not 1 <= lookback_hours <= 2160
            or type(grace_seconds) is not int or not 1 <= grace_seconds <= 3600
            or type(operation_age_seconds) is not int or not 60 <= operation_age_seconds <= 86400
            or type(backup_age_hours) is not int or not 1 <= backup_age_hours <= 720):
        raise ValueError('Invalid monitoring thresholds')
    now = now.astimezone(timezone.utc)
    report = {'checked_at': now.isoformat(), 'issues': [], 'ledger': {'status': 'unchecked'},
              'provider': {'status': 'skipped'}, 'backup': {'status': 'unchecked'}}
    try:
        summary = inspect_ledger(path, price_id)
        report['ledger'] = {'status': 'valid', 'tables': summary['tables']}
    except INVALID:
        report['ledger']['status'] = 'unavailable_or_invalid'
        _issue(report, 'ledger_unavailable_or_invalid', 'critical')
        return _finish(report)
    since = now - timedelta(hours=lookback_hours)
    until = now - timedelta(seconds=grace_seconds)
    if since >= until:
        raise ValueError('Grace must be shorter than lookback')
    events = None
    if client is None:
        _issue(report, 'provider_check_skipped')
    else:
        try:
            events = client.events(since.isoformat(), until.isoformat())
            if not isinstance(events, list):
                events = None
                raise ValueError('Incomplete provider window')
        except (ProviderUnavailable, *INVALID):
            report['provider']['status'] = 'unavailable'
            _issue(report, 'provider_check_incomplete', 'critical')
    try:
        with closing(_readonly(path)) as db:
            db.execute('PRAGMA query_only=ON')
            db.execute('PRAGMA trusted_schema=OFF')
            db.execute('BEGIN')
            _local_report(report, db, now, grace_seconds, operation_age_seconds)
            if events is not None:
                try:
                    _provider_report(report, db, events, since, until, price_id)
                except INVALID:
                    report['provider'] = {'status': 'invalid'}
                    _issue(report, 'provider_check_incomplete', 'critical')
    except INVALID:
        report['ledger']['status'] = 'unavailable_or_invalid'
        _issue(report, 'ledger_unavailable_or_invalid', 'critical')
    if archive is None or archive_sha256 is None:
        report['backup']['status'] = 'unconfigured'
        _issue(report, 'verified_backup_not_configured')
    else:
        try:
            verified = verify_backup(archive, price_id=price_id, expected_sha256=archive_sha256)
            age = (now - _instant(verified['created_at'])).total_seconds()
            report['backup'] = {'status': 'valid', 'created_at': verified['created_at'],
                                'age_seconds': int(age), 'archive_sha256': verified['archive_sha256']}
            if age < -grace_seconds:
                report['backup']['status'] = 'future_dated'
                _issue(report, 'backup_clock_ahead', 'critical')
            elif age > backup_age_hours * 3600:
                report['backup']['status'] = 'stale'
                _issue(report, 'backup_too_old', 'critical')
        except INVALID:
            report['backup']['status'] = 'unavailable_or_invalid'
            _issue(report, 'backup_unavailable_or_invalid', 'critical')
    return _finish(report)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ledger', required=True)
    parser.add_argument('--price-id', required=True)
    parser.add_argument('--backup')
    parser.add_argument('--backup-sha256')
    parser.add_argument('--with-provider', action='store_true')
    parser.add_argument('--lookback-hours', type=int, default=24)
    parser.add_argument('--grace-seconds', type=int, default=300)
    parser.add_argument('--operation-age-seconds', type=int, default=900)
    parser.add_argument('--backup-age-hours', type=int, default=24)
    args = parser.parse_args(argv)
    if args.with_provider and os.environ.get('TRADE_PAPER_PADDLE_LIVE_MONITOR') != '1':
        parser.exit(2, 'Provider monitoring is disabled.\n')
    try:
        client = LiveClient(os.environ.get('TRADE_PAPER_PADDLE_LIVE_API_KEY', '')) if args.with_provider else None
        result = diagnose(args.ledger, price_id=args.price_id, archive=args.backup,
            archive_sha256=args.backup_sha256, client=client, lookback_hours=args.lookback_hours,
            grace_seconds=args.grace_seconds, operation_age_seconds=args.operation_age_seconds,
            backup_age_hours=args.backup_age_hours)
    except INVALID:
        parser.exit(2, 'Monitoring configuration is invalid.\n')
    print(json.dumps(result, sort_keys=True))
    return {'ok': 0, 'warning': 1, 'critical': 2}[result['status']]


if __name__ == '__main__':
    raise SystemExit(main())
