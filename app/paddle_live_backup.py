"""Consistent Live SQLite backup, verification and isolated restore staging.

No provider calls, scheduler, web route or activation. A staged old ledger must
never replace a running ledger: stop writers and reconcile post-backup activity
before enabling payments or access. See docs/paddle-live-backup.md.
"""
import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import stat
import tempfile
import time
import zipfile

from app.paddle_live_store import _account, _id, _json
from app.paddle_subscription_policy import _instant, evaluate_snapshot

MAX_DB = 512 * 1024 * 1024
MAX_MANIFEST = 64 * 1024
FORMAT = 'trade-paper-paddle-live-v1'
CORE = {'paddle_live_meta', 'checkouts', 'bindings', 'events', 'snapshots'}
COLUMNS = {
    'paddle_live_meta': 'key value',
    'checkouts': 'transaction_id account_id price_id',
    'bindings': 'subscription_id customer_id account_id transaction_id',
    'events': 'event_id digest occurred_at result',
    'snapshots': 'subscription_id occurred_at snapshot',
    'live_operations': 'account_id kind started target_id result',
    'live_adjustment_events': 'event_id adjustment_id subscription_id customer_id transaction_id action status adjustment_type currency total occurred_at requires_review',
    'live_review_releases': 'review_id account_id subscription_id operator_ref case_ref checked_at evidence_digest evidence',
    'live_review_coverage': 'event_id review_id',
    'live_renewals': 'transaction_id subscription_id customer_id account_id terms terms_digest',
    'live_renewal_receipts': 'event_id transaction_id',
}
PRIMARY_KEYS = {'paddle_live_meta': ['key'], 'checkouts': ['transaction_id'],
                'bindings': ['subscription_id'], 'events': ['event_id'],
                'snapshots': ['subscription_id'], 'live_operations': ['account_id', 'kind'],
                'live_adjustment_events': ['event_id'], 'live_review_releases': ['review_id'],
                'live_review_coverage': ['event_id'], 'live_renewals': ['transaction_id'],
                'live_renewal_receipts': ['event_id']}


class BackupError(ValueError):
    """Invalid, incomplete or unsafe backup; never activate it."""


def _hash(stream):
    digest = hashlib.sha256()
    for chunk in iter(lambda: stream.read(1024 * 1024), b''):
        digest.update(chunk)
    return digest.hexdigest()


def _sha(path):
    with open(path, 'rb') as stream:
        return _hash(stream)


def _readonly(path):
    return sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True, timeout=2)


def _file(path):
    path = Path(path).absolute()
    if not stat.S_ISREG(path.lstat().st_mode):
        raise BackupError('Regular file required; symlinks are not supported')
    return path


def _private_file(path):
    return os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'wb')


def _sync_dir(path):
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def inspect_ledger(path, price_id):
    """Read-only physical/schema/relational checks; never migrate a backup."""
    _id(price_id, 'pri')
    path = _file(path)
    if not 0 < path.stat().st_size <= MAX_DB:
        raise BackupError('Ledger exceeds supported size')
    deadline = time.monotonic() + 60
    with closing(_readonly(path)) as db:
        db.execute('PRAGMA trusted_schema=OFF')
        db.execute('PRAGMA query_only=ON')
        db.set_progress_handler(lambda: int(time.monotonic() > deadline), 10000)
        db.execute('BEGIN')
        if db.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
            raise BackupError('SQLite integrity check failed')
        objects = db.execute("SELECT type, name, sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'").fetchall()
        if any(kind not in ('table', 'index') or (kind == 'table' and 'VIRTUAL' in sql.upper())
               for kind, name, sql in objects):
            raise BackupError('Unexpected schema objects')
        tables = {name for kind, name, sql in objects if kind == 'table'}
        if not CORE <= tables or not tables <= COLUMNS.keys():
            raise BackupError('Unsupported ledger schema')
        for table in tables:
            columns = db.execute('PRAGMA table_info(' + table + ')').fetchall()
            if ([r[1] for r in columns] != COLUMNS[table].split()
                    or [r[1] for r in sorted(columns, key=lambda r: r[5]) if r[5]] != PRIMARY_KEYS[table]):
                raise BackupError('Ledger columns do not match this application')
            if table in ('checkouts', 'bindings'):
                unique = []
                for index in db.execute('PRAGMA index_list(' + table + ')'):
                    if index[2] and not index[4]:
                        # Quote identifier from schema; never treat it as SQL code.
                        name = '"' + index[1].replace('"', '""') + '"'
                        unique.append([r[2] for r in db.execute('PRAGMA index_info(' + name + ')')])
                if ['account_id'] not in unique or (table == 'bindings' and ['transaction_id'] not in unique):
                    raise BackupError('Missing ownership uniqueness constraints')
        meta = dict(db.execute('SELECT key, value FROM paddle_live_meta'))
        if meta != {'schema': '1', 'environment': 'live', 'price_id': price_id}:
            raise BackupError('Live environment or price mismatch')
        release_tables = {'live_review_releases', 'live_review_coverage'} & tables
        if release_tables and (len(release_tables) != 2 or 'live_adjustment_events' not in tables):
            raise BackupError('Incomplete review schema')
        counts = {table: db.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0] for table in sorted(tables)}
        # The ledger has no SQL foreign keys. Check its cross-table invariants.
        if db.execute('SELECT 1 FROM bindings b LEFT JOIN checkouts c ON c.transaction_id=b.transaction_id '
                      'WHERE c.transaction_id IS NULL OR c.account_id<>b.account_id LIMIT 1').fetchone():
            raise BackupError('Orphaned or conflicting binding')
        for txn, account, price in db.execute('SELECT * FROM checkouts'):
            _id(txn, 'txn'); _account(account)
            if price != price_id:
                raise BackupError('Checkout price mismatch')
        for sub, customer, account, txn in db.execute('SELECT * FROM bindings'):
            _id(sub, 'sub'); _id(customer, 'ctm'); _account(account); _id(txn, 'txn')
        for event, digest, occurred, result in db.execute('SELECT * FROM events'):
            _id(event, 'evt'); _instant(occurred)
            if (not re.fullmatch(r'[a-f0-9]{64}', digest) or result not in
                    {'bound', 'applied', 'stale', 'equivalent', 'adjustment_review', 'adjustment_recorded',
                     'renewal_recorded', 'renewal_existing'}):
                raise BackupError('Invalid event receipt')
        renewals = {'live_renewals', 'live_renewal_receipts'} & tables
        if renewals and len(renewals) != 2:
            raise BackupError('Incomplete renewal schema')
        if not renewals and db.execute("SELECT 1 FROM events WHERE result IN ('renewal_recorded','renewal_existing') LIMIT 1").fetchone():
            raise BackupError('Missing renewal evidence')
        if renewals:
            from app.paddle_live_renewals import validate_terms
            for txn, sub, customer, account, raw, digest in db.execute('SELECT * FROM live_renewals'):
                _id(txn, 'txn')
                terms = json.loads(raw)
                validate_terms(terms, price_id)
                if (hashlib.sha256(_json(terms).encode()).hexdigest() != digest
                        or db.execute('SELECT customer_id, account_id FROM bindings WHERE subscription_id=?', (sub,)).fetchone() != (customer, account)
                        or db.execute('SELECT 1 FROM checkouts WHERE transaction_id=?', (txn,)).fetchone()
                        or db.execute("SELECT COUNT(*) FROM live_renewal_receipts r JOIN events e ON e.event_id=r.event_id "
                                      "WHERE r.transaction_id=? AND e.result='renewal_recorded'", (txn,)).fetchone()[0] != 1):
                    raise BackupError('Renewal ownership or evidence conflict')
            if db.execute("SELECT 1 FROM live_renewal_receipts r LEFT JOIN live_renewals t ON t.transaction_id=r.transaction_id "
                          "LEFT JOIN events e ON e.event_id=r.event_id WHERE t.transaction_id IS NULL OR e.event_id IS NULL "
                          "OR e.result NOT IN ('renewal_recorded','renewal_existing') LIMIT 1").fetchone():
                raise BackupError('Orphaned renewal receipt')
            if db.execute("SELECT 1 FROM events e LEFT JOIN live_renewal_receipts r ON r.event_id=e.event_id "
                          "WHERE e.result IN ('renewal_recorded','renewal_existing') AND r.event_id IS NULL LIMIT 1").fetchone():
                raise BackupError('Missing renewal receipt')
        for sub, occurred, raw in db.execute('SELECT * FROM snapshots'):
            owner = db.execute('SELECT customer_id FROM bindings WHERE subscription_id=?', (sub,)).fetchone()
            if not owner:
                raise BackupError('Orphaned subscription snapshot')
            evaluate_snapshot(json.loads(raw), subscription_id=sub, customer_id=owner[0],
                              price_id=price_id, now=_instant(occurred))
        if 'live_operations' in tables:
            for account, kind, started, target, result in db.execute('SELECT * FROM live_operations'):
                _account(account)
                if not isinstance(started, (int, float)) or not math.isfinite(started) or started < 0:
                    raise BackupError('Invalid operation time')
                if kind == 'checkout':
                    if (target is None and result is None):
                        continue  # Important: preserve ambiguous mutation reservations.
                    if result != 'ready' or db.execute('SELECT account_id FROM checkouts WHERE transaction_id=?',
                                                       (target,)).fetchone() != (account,):
                        raise BackupError('Conflicting checkout operation')
                elif kind == 'cancel':
                    if (result not in (None, 'scheduled', 'canceled') or
                            db.execute('SELECT account_id FROM bindings WHERE subscription_id=?',
                                       (target,)).fetchone() != (account,)):
                        raise BackupError('Conflicting cancellation operation')
                else:
                    raise BackupError('Unknown operation kind')
        if 'live_adjustment_events' in tables:
            from app.paddle_live_adjustments import ACTIONS
            for row in db.execute('SELECT * FROM live_adjustment_events'):
                event, adj, sub, customer, txn, action, status, kind, currency, total, when, review = row
                _id(event, 'evt'); _id(adj, 'adj'); _id(txn, 'txn'); _instant(when)
                owner = db.execute('SELECT account_id FROM bindings WHERE subscription_id=? AND customer_id=?',
                                   (sub, customer)).fetchone()
                receipt = db.execute('SELECT occurred_at, result FROM events WHERE event_id=?', (event,)).fetchone()
                expected_review = status in ('approved', 'reversed')
                if (not owner or action not in ACTIONS or status not in {'pending_approval', 'approved', 'rejected', 'reversed'}
                        or kind not in ('full', 'partial', None) or review != int(expected_review)
                        or not re.fullmatch(r'[A-Z]{3}', currency)
                        or not re.fullmatch(r'-?(0|[1-9][0-9]{0,19})', total)
                        or receipt != (when, 'adjustment_review' if review else 'adjustment_recorded')):
                    raise BackupError('Adjustment evidence conflict')
                reservation = db.execute('SELECT account_id FROM checkouts WHERE transaction_id=?', (txn,)).fetchone()
                if reservation and reservation != owner:
                    raise BackupError('Adjustment transaction ownership conflict')
                if renewals:
                    renewal = db.execute('SELECT subscription_id, customer_id FROM live_renewals WHERE transaction_id=?', (txn,)).fetchone()
                    if renewal and renewal != (sub, customer):
                        raise BackupError('Adjustment renewal ownership conflict')
            if db.execute("SELECT 1 FROM events e LEFT JOIN live_adjustment_events a ON a.event_id=e.event_id "
                          "WHERE e.result IN ('adjustment_review','adjustment_recorded') AND a.event_id IS NULL LIMIT 1").fetchone():
                raise BackupError('Missing adjustment evidence')
        elif db.execute("SELECT 1 FROM events WHERE result IN ('adjustment_review','adjustment_recorded') LIMIT 1").fetchone():
            raise BackupError('Missing adjustment table')
        if release_tables:
            if db.execute('SELECT 1 FROM live_review_coverage c '
                          'LEFT JOIN live_review_releases r ON r.review_id=c.review_id '
                          'LEFT JOIN live_adjustment_events e ON e.event_id=c.event_id '
                          'LEFT JOIN bindings b ON b.subscription_id=r.subscription_id '
                          'WHERE r.review_id IS NULL OR e.event_id IS NULL OR b.subscription_id IS NULL '
                          'OR e.requires_review<>1 OR e.subscription_id<>r.subscription_id '
                          'OR b.account_id<>r.account_id LIMIT 1').fetchone():
                raise BackupError('Review coverage ownership conflict')
            for review_id, account, sub, actor, case, checked, digest, evidence in db.execute('SELECT * FROM live_review_releases'):
                _instant(checked)
                value = json.loads(evidence)
                if (not re.fullmatch(r'[a-f0-9]{64}', review_id)
                        or hashlib.sha256(_json(value).encode()).hexdigest() != digest
                        or value['subscription']['id'] != sub
                        or value['policy'] != 'restored-payment-v1'
                        or not db.execute('SELECT 1 FROM live_review_coverage WHERE review_id=?', (review_id,)).fetchone()):
                    raise BackupError('Review audit evidence conflict')
        if db.execute('PRAGMA foreign_key_check').fetchall():
            raise BackupError('Foreign-key check failed')
        return {'metadata': meta, 'tables': counts}


def create_backup(source, destination, *, price_id):
    """Publish a verified archive exclusively; a failure never replaces a backup."""
    source, destination = _file(source), Path(destination).absolute()
    _id(price_id, 'pri')
    if os.path.lexists(destination):
        raise BackupError('Backup destination already exists')
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix='.paddle-backup-', dir=destination.parent) as work:
        snapshot = Path(work) / 'ledger.sqlite3'
        with _private_file(snapshot):
            pass
        with closing(_readonly(source)) as src, closing(sqlite3.connect(snapshot)) as dst:
            # No copy of the active .db alone: the backup API includes committed WAL pages.
            page_size = src.execute('PRAGMA page_size').fetchone()[0]
            def progress(status, remaining, total):
                if time.monotonic() - started > 60 or total * page_size > MAX_DB:
                    raise BackupError('Backup exceeded time or size limit')
            src.backup(dst, pages=256, progress=progress, sleep=0.05)
            dst.execute('PRAGMA journal_mode=DELETE')
        summary = inspect_ledger(snapshot, price_id)
        manifest = {'format': FORMAT, 'created_at': datetime.now(timezone.utc).isoformat(),
                    'database': {'name': 'ledger.sqlite3', 'size': snapshot.stat().st_size, 'sha256': _sha(snapshot)},
                    **summary}
        archive = Path(work) / 'backup.zip'
        with _private_file(archive) as stream:
            with zipfile.ZipFile(stream, 'w', zipfile.ZIP_DEFLATED) as z:
                z.write(snapshot, 'ledger.sqlite3')
                z.writestr('manifest.json', _json(manifest))
            stream.flush(); os.fsync(stream.fileno())
        # Independent extraction/check verifies the actual deliverable, not just its source.
        verified = verify_backup(archive, price_id=price_id, expected_sha256=_sha(archive))
        os.link(archive, destination)  # Atomic, exclusive publication; never overwrite.
        _sync_dir(destination.parent)
        return {**verified, 'archive': str(destination)}


def _unpack(archive, directory, price_id, expected_sha256):
    archive = _file(archive)
    if archive.stat().st_size > MAX_DB + 1024 * 1024:
        raise BackupError('Archive exceeds supported size')
    with open(archive, 'rb') as stream:
        digest = _hash(stream)
        if expected_sha256 is not None and (not re.fullmatch(r'[a-f0-9]{64}', expected_sha256)
                                            or digest != expected_sha256):
            raise BackupError('Archive checksum mismatch')
        stream.seek(0)
        with zipfile.ZipFile(stream) as z:
            entries = z.infolist()
            if len(entries) != 2 or {e.filename for e in entries} != {'ledger.sqlite3', 'manifest.json'}:
                raise BackupError('Unexpected archive members')
            if any(e.flag_bits & 1 or e.is_dir() or e.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)
                   for e in entries):
                raise BackupError('Unsupported archive member')
            if z.getinfo('manifest.json').file_size > MAX_MANIFEST or not 0 < z.getinfo('ledger.sqlite3').file_size <= MAX_DB:
                raise BackupError('Archive member exceeds supported size')
            manifest = json.loads(z.read('manifest.json'))
            _instant(manifest['created_at'])
            if manifest['format'] != FORMAT or manifest['database']['name'] != 'ledger.sqlite3':
                raise BackupError('Unknown backup format')
            snapshot = Path(directory) / 'ledger.sqlite3'
            with z.open('ledger.sqlite3') as source, _private_file(snapshot) as target:
                copied = 0
                for chunk in iter(lambda: source.read(1024 * 1024), b''):
                    copied += len(chunk)
                    if copied > MAX_DB:
                        raise BackupError('Expanded database exceeds supported size')
                    target.write(chunk)
                target.flush(); os.fsync(target.fileno())
            if (snapshot.stat().st_size != manifest['database']['size']
                    or _sha(snapshot) != manifest['database']['sha256']):
                raise BackupError('Database checksum mismatch')
        # Pin the same open archive against an in-place change during extraction.
        stream.seek(0)
        if _hash(stream) != digest:
            raise BackupError('Archive changed during verification')
    summary = inspect_ledger(snapshot, price_id)
    if summary != {key: manifest[key] for key in ('metadata', 'tables')}:
        raise BackupError('Manifest ledger summary mismatch')
    return {'format': FORMAT, 'archive_sha256': digest, 'created_at': manifest['created_at'],
            'database_sha256': manifest['database']['sha256'], **summary}


def verify_backup(archive, *, price_id, expected_sha256=None):
    with tempfile.TemporaryDirectory(prefix='paddle-verify-') as directory:
        return _unpack(archive, directory, price_id, expected_sha256)


def stage_restore(archive, destination, *, price_id, expected_sha256):
    """Restore only into a newly created private directory; never activate it."""
    if not isinstance(expected_sha256, str) or not re.fullmatch(r'[a-f0-9]{64}', expected_sha256):
        raise BackupError('Independently recorded archive checksum required for restore')
    destination = Path(destination).absolute()
    if os.path.lexists(destination):
        raise BackupError('Restore directory must not exist')
    with tempfile.TemporaryDirectory(prefix='.paddle-restore-', dir=destination.parent) as work:
        summary = _unpack(archive, work, price_id, expected_sha256)
        receipt = {**summary, 'staged_at': datetime.now(timezone.utc).isoformat(),
                   'activated': False, 'requires_reconciliation': True}
        marker = Path(work) / 'RESTORE_NOT_ACTIVATED.json'
        with _private_file(marker) as stream:
            stream.write(_json(receipt).encode()); stream.flush(); os.fsync(stream.fileno())
        # Reserving a NEW directory prevents replacement, even if another process
        # creates the requested directory between validation and publication.
        destination.mkdir(mode=0o700)
        published = []
        try:
            for source, name in ((Path(work) / 'ledger.sqlite3', 'paddle_live.sqlite3'),
                                 (marker, 'RESTORE_NOT_ACTIVATED.json')):
                target = destination / name
                os.link(source, target)
                published.append(target)
            _sync_dir(destination)
            _sync_dir(destination.parent)
        except BaseException:
            for target in published:
                target.unlink()
            destination.rmdir()
            raise
        return {**receipt, 'directory': str(destination)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--price-id', required=True)
    commands = parser.add_subparsers(dest='command', required=True)
    backup = commands.add_parser('backup')
    backup.add_argument('--source', required=True)
    backup.add_argument('--output', required=True)
    for name in ('verify', 'restore'):
        command = commands.add_parser(name)
        command.add_argument('--archive', required=True)
        command.add_argument('--sha256', required=name == 'restore')
        if name == 'restore':
            command.add_argument('--output-dir', required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == 'backup':
            result = create_backup(args.source, args.output, price_id=args.price_id)
        elif args.command == 'verify':
            result = verify_backup(args.archive, price_id=args.price_id, expected_sha256=args.sha256)
        else:
            result = stage_restore(args.archive, args.output_dir, price_id=args.price_id, expected_sha256=args.sha256)
    except (OSError, sqlite3.Error, ValueError, TypeError, KeyError, zipfile.BadZipFile, RuntimeError):
        parser.exit(2, 'Backup operation failed; check paths, checksum, Live price and ledger integrity.\n')
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    main()
