"""Host-only backup/diagnostic cycle and independent freshness check.

No installed scheduler, outbound alerts, retention deletion, payment mutations or
automatic restore. A dedicated private directory belongs to one ledger/path/price.
"""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import uuid

from app.paddle_live_actions import LiveClient
from app.paddle_live_backup import create_backup, verify_backup, _sync_dir
from app.paddle_live_monitor import diagnose, INVALID
from app.paddle_live_store import _id
from app.paddle_subscription_policy import _instant


def _private(info, directory=False):
    if (not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))
            or info.st_uid != os.getuid() or info.st_mode & 0o077):
        raise ValueError('Private owned directory/files required')


def _config(ledger, directory, price_id, now):
    _id(price_id, 'pri')
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise ValueError('Timezone-aware clock required')
    root = Path(directory).absolute()
    _private(root.lstat(), directory=True)
    identity = hashlib.sha256((str(Path(ledger).absolute()) + '\n' + price_id).encode()).hexdigest()
    return root, identity


@contextmanager
def _lock(root, *, shared=False):
    fd = os.open(root / 'job.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        _private(os.fstat(fd))
        acquired = False
        try:
            fcntl.flock(fd, (fcntl.LOCK_SH if shared else fcntl.LOCK_EX) | fcntl.LOCK_NB)
            acquired = True
        except BlockingIOError:
            pass
        yield acquired
    finally:
        os.close(fd)  # Kernel releases the lock on normal exit or process death.


def _read(root, identity):
    try:
        fd = os.open(root / 'state.json', os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return None
    with os.fdopen(fd, 'r') as stream:
        _private(os.fstat(stream.fileno()))
        raw = stream.read(1024 * 1024 + 1)
    if len(raw) > 1024 * 1024:
        raise ValueError('Oversized state')
    value = json.loads(raw)
    if (value['schema'] != 1 or value['identity'] != identity
            or value['phase'] not in ('running', 'finished')):
        raise ValueError('State does not match this job')
    _instant(value['started_at'])
    latest = value['latest']
    if latest is not None:
        if (not re.fullmatch(r'ledger-[a-f0-9]{32}\.zip', latest['name'])
                or not re.fullmatch(r'[a-f0-9]{64}', latest['sha256'])):
            raise ValueError('Invalid archive inventory')
        _instant(latest['created_at'])
    if value['phase'] == 'finished':
        _instant(value['finished_at'])
        if value['report']['status'] not in ('ok', 'warning', 'critical'):
            raise ValueError('Invalid result')
    return value


def _write(root, value):
    fd, temporary = tempfile.mkstemp(prefix='.state-', dir=root)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, root / 'state.json')
        _sync_dir(root)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _result(code, severity='critical'):
    return {'status': severity, 'issues': [{'code': code, 'severity': severity}]}


def run(ledger, directory, *, price_id, client=None, now=None, backup_hours=6):
    if os.environ.get('TRADE_PAPER_PADDLE_LIVE_JOBS') != '1':
        raise ValueError('Billing jobs disabled')
    if client is not None and os.environ.get('TRADE_PAPER_PADDLE_LIVE_MONITOR') != '1':
        raise ValueError('Provider monitoring disabled')
    if type(backup_hours) is not int or not 1 <= backup_hours <= 24:
        raise ValueError('Backup interval must be 1-24 hours')
    now = datetime.now(timezone.utc) if now is None else now
    root, identity = _config(ledger, directory, price_id, now)
    with _lock(root) as acquired:
        if not acquired:
            return _result('job_already_running', 'warning')
        previous = _read(root, identity)
        latest = previous['latest'] if previous else None
        state = {'schema': 1, 'identity': identity, 'phase': 'running',
                 'started_at': now.isoformat(), 'latest': latest}
        # Durable start receipt prevents a killed job from retaining an old green result.
        _write(root, state)
        issues = []
        if previous and previous['phase'] == 'running':
            issues.append({'code': 'previous_job_incomplete', 'severity': 'warning'})
        due = latest is None
        if latest:
            age = (now - _instant(latest['created_at'])).total_seconds()
            due = age >= backup_hours * 3600
            if age < -300:
                issues.append({'code': 'backup_clock_ahead', 'severity': 'critical'})
            # Verify the prior artifact even when it is not due. A missing/corrupt
            # artifact must be visible; a successful replacement cannot hide it.
            try:
                verify_backup(root / latest['name'], price_id=price_id, expected_sha256=latest['sha256'])
            except INVALID:
                due = True
                issues.append({'code': 'previous_backup_invalid', 'severity': 'critical'})
        backup_result = 'reused'
        if due:
            try:
                name = 'ledger-' + uuid.uuid4().hex + '.zip'
                saved = create_backup(ledger, root / name, price_id=price_id)
                latest = {'name': name, 'sha256': saved['archive_sha256'], 'created_at': saved['created_at']}
                state['latest'] = latest
                _write(root, state)
                backup_result = 'created'
            except INVALID:
                backup_result = 'failed'
                issues.append({'code': 'backup_attempt_failed', 'severity': 'critical'})
        try:
            report = diagnose(ledger, price_id=price_id, client=client, now=now,
                              archive=root / latest['name'] if latest else None,
                              archive_sha256=latest['sha256'] if latest else None)
        except INVALID:
            report = _result('diagnostic_attempt_failed')
        report['issues'].extend(issues)
        severity = {item['severity'] for item in report['issues']}
        report['status'] = 'critical' if 'critical' in severity else 'warning' if severity else 'ok'
        report['backup_attempt'] = backup_result
        state.update(phase='finished', finished_at=datetime.now(timezone.utc).isoformat(), report=report)
        _write(root, state)
        return report


def check(ledger, directory, *, price_id, now=None, max_age_minutes=30):
    """Check the saved job receipt, not provider state or complete release readiness."""
    if type(max_age_minutes) is not int or not 1 <= max_age_minutes <= 1440:
        raise ValueError('Invalid freshness limit')
    now = datetime.now(timezone.utc) if now is None else now
    root, identity = _config(ledger, directory, price_id, now)
    with _lock(root, shared=True) as idle:
        value = _read(root, identity)
        if value is None:
            return _result('job_never_completed')
        age = (now - _instant(value['started_at'])).total_seconds()
        if age < -300:
            return _result('job_clock_ahead')
        if age > max_age_minutes * 60:
            return _result('job_stale')
        if not idle:
            return _result('job_running', 'warning')
        if value['phase'] != 'finished':
            return _result('job_interrupted')
        # Includes the failed latest attempt even if the previous backup is fresh.
        return value['report']


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('run', 'check'))
    parser.add_argument('--ledger', required=True)
    parser.add_argument('--directory', required=True)
    parser.add_argument('--price-id', required=True)
    parser.add_argument('--with-provider', action='store_true')
    parser.add_argument('--backup-hours', type=int, default=6)
    parser.add_argument('--max-age-minutes', type=int, default=30)
    args = parser.parse_args(argv)
    try:
        if args.command == 'check':
            if args.with_provider:
                raise ValueError('Check never contacts provider')
            result = check(args.ledger, args.directory, price_id=args.price_id, max_age_minutes=args.max_age_minutes)
        else:
            client = LiveClient(os.environ.get('TRADE_PAPER_PADDLE_LIVE_API_KEY', '')) if args.with_provider else None
            result = run(args.ledger, args.directory, price_id=args.price_id, client=client, backup_hours=args.backup_hours)
    except INVALID:
        result = _result('job_configuration_or_storage_failure')
    print(json.dumps(result, sort_keys=True))
    return {'ok': 0, 'warning': 1, 'critical': 2}[result['status']]


if __name__ == '__main__':
    raise SystemExit(main())
