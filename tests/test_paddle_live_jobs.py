from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
import json
import os
from threading import Event

import pytest

from app import paddle_live_jobs as jobs, paddle_live_backup as backup
from tests.test_paddle_live_store import store, bound, send, event, PRICE, SUB, TXN
from tests.test_paddle_live_monitor import Provider, CHECKED, codes


@pytest.fixture
def setup(store, monkeypatch, tmp_path):
    root = tmp_path / 'jobs'
    root.mkdir(mode=0o700)
    monkeypatch.setenv('TRADE_PAPER_PADDLE_LIVE_JOBS', '1')
    monkeypatch.setenv('TRADE_PAPER_PADDLE_LIVE_MONITOR', '1')
    class ClockMeta(type):
        def __instancecheck__(cls, value):
            return isinstance(value, datetime)
    class Clock(datetime, metaclass=ClockMeta):
        @classmethod
        def now(cls, tz=None):
            return CHECKED
    monkeypatch.setattr(backup, 'datetime', Clock)
    monkeypatch.setattr(jobs, 'datetime', Clock)
    bound(store)
    send(store, event(2))
    return store, root


def run(setup, **kwargs):
    store, root = setup
    options = dict(price_id=PRICE, client=Provider(), now=CHECKED)
    options.update(kwargs)
    return jobs.run(store.path, root, **options)


def check(setup, **kwargs):
    store, root = setup
    return jobs.check(store.path, root, price_id=PRICE, now=kwargs.pop('now', CHECKED), **kwargs)


def state(setup):
    return json.loads((setup[1] / 'state.json').read_text())


def test_cycle_creates_verified_backup_and_records_sanitized_health(setup):
    before = setup[0].path.read_bytes()
    result = run(setup)
    assert result['status'] == 'ok' and result['backup_attempt'] == 'created'
    assert check(setup) == result
    saved = state(setup)
    assert saved['phase'] == 'finished' and saved['schema'] == 1
    archive = setup[1] / saved['latest']['name']
    backup.verify_backup(archive, price_id=PRICE, expected_sha256=saved['latest']['sha256'])
    assert setup[0].path.read_bytes() == before
    assert all(p.stat().st_mode & 0o077 == 0 for p in setup[1].iterdir())
    for private in ('account-A', SUB, TXN, str(setup[0].path), 'synthetic-live'):
        assert private not in json.dumps(result)


def test_fresh_archive_is_reused_and_due_archive_is_retained(setup, monkeypatch):
    run(setup)
    first = state(setup)['latest']
    assert run(setup, now=CHECKED + timedelta(hours=1))['backup_attempt'] == 'reused'
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return CHECKED + timedelta(hours=6)
    monkeypatch.setattr(backup, 'datetime', Clock)
    assert run(setup, now=Clock.now())['backup_attempt'] == 'created'
    assert len(list(setup[1].glob('ledger-*.zip'))) == 2
    assert state(setup)['latest'] != first
    assert (setup[1] / first['name']).exists()


def test_failed_due_attempt_is_critical_despite_still_fresh_previous_archive(setup, monkeypatch):
    run(setup)
    first = state(setup)['latest']
    def fail(*args, **kwargs):
        raise OSError('private path or credential must not escape')
    monkeypatch.setattr(jobs, 'create_backup', fail)
    later = CHECKED + timedelta(hours=6)
    result = run(setup, now=later)
    assert result['backup']['status'] == 'valid'
    assert result['status'] == 'critical' and 'backup_attempt_failed' in codes(result)
    assert state(setup)['latest'] == first
    assert check(setup, now=later) == result
    assert 'credential' not in json.dumps(result)


@pytest.mark.parametrize('kind', ['missing', 'corrupt'])
def test_invalid_previous_archive_is_reported_even_after_replacement(setup, kind):
    run(setup)
    old = setup[1] / state(setup)['latest']['name']
    if kind == 'missing':
        old.unlink()
    else:
        old.write_bytes(b'corrupt synthetic backup')
    result = run(setup)
    assert result['backup_attempt'] == 'created'
    assert 'previous_backup_invalid' in codes(result) and result['status'] == 'critical'
    assert state(setup)['latest']['name'] != old.name


def test_local_only_and_provider_failure_never_report_green(setup):
    result = run(setup, client=None)
    assert result['status'] == 'warning' and 'provider_check_skipped' in codes(result)
    class Broken:
        def events(self, *args):
            raise OSError('synthetic outage')
    result = run(setup, client=Broken())
    assert result['status'] == 'critical' and 'provider_check_incomplete' in codes(result)


def test_missing_ledger_never_initializes_empty_database(setup):
    source = setup[0].path.with_name('nonexistent.sqlite3')
    result = jobs.run(source, setup[1], price_id=PRICE, now=CHECKED)
    assert {'backup_attempt_failed', 'ledger_unavailable_or_invalid'} <= codes(result)
    assert not source.exists() and state(setup)['latest'] is None


def test_diagnostic_exception_is_durable_and_does_not_hide_successful_backup(setup, monkeypatch):
    def fail(*args, **kwargs):
        raise ValueError('private diagnostics failure')
    monkeypatch.setattr(jobs, 'diagnose', fail)
    result = run(setup)
    assert codes(result) == {'diagnostic_attempt_failed'}
    assert result['backup_attempt'] == 'created' and check(setup) == result


def test_process_death_receipt_is_detected_then_next_cycle_reports_interruption(setup, monkeypatch):
    original = jobs.diagnose
    def crash(*args, **kwargs):
        raise SystemExit('synthetic abrupt exit')
    monkeypatch.setattr(jobs, 'diagnose', crash)
    with pytest.raises(SystemExit):
        run(setup)
    assert state(setup)['phase'] == 'running' and state(setup)['latest']
    assert codes(check(setup)) == {'job_interrupted'}
    monkeypatch.setattr(jobs, 'diagnose', original)
    assert 'previous_job_incomplete' in codes(run(setup))
    assert run(setup)['status'] == 'ok'


def test_overlap_skips_and_watchdog_distinguishes_running_from_stale(setup, monkeypatch):
    entered, release = Event(), Event()
    original = jobs.create_backup
    def wait(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return original(*args, **kwargs)
    monkeypatch.setattr(jobs, 'create_backup', wait)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(run, setup)
        try:
            assert entered.wait(5)
            assert codes(run(setup)) == {'job_already_running'}
            assert codes(check(setup)) == {'job_running'}
            assert codes(check(setup, now=CHECKED + timedelta(minutes=31))) == {'job_stale'}
        finally:
            release.set()
        assert first.result(timeout=5)['status'] == 'ok'
    assert len(list(setup[1].glob('ledger-*.zip'))) == 1


def test_watchdog_detects_never_ran_stale_and_clock_rollback(setup):
    assert codes(check(setup)) == {'job_never_completed'}
    run(setup)
    assert codes(check(setup, now=CHECKED + timedelta(minutes=31))) == {'job_stale'}
    assert codes(check(setup, now=CHECKED - timedelta(minutes=6))) == {'job_clock_ahead'}


@pytest.mark.parametrize('flag', ['JOBS', 'MONITOR'])
def test_opt_in_required_before_creating_files(setup, monkeypatch, flag):
    monkeypatch.delenv('TRADE_PAPER_PADDLE_LIVE_' + flag)
    with pytest.raises(ValueError):
        run(setup)
    assert list(setup[1].iterdir()) == []


@pytest.mark.parametrize('kind', ['directory', 'lock', 'state', 'different-ledger', 'inventory-path'])
def test_untrusted_or_mismatched_inventory_cannot_overwrite_existing_evidence(setup, kind):
    run(setup)
    root = setup[1]
    if kind == 'directory':
        root.chmod(0o755)
    elif kind == 'lock':
        (root / 'job.lock').unlink()
        (root / 'job.lock').symlink_to(setup[0].path)
    elif kind == 'state':
        (root / 'state.json').write_text('not json')
    elif kind == 'inventory-path':
        data = state(setup)
        data['latest']['name'] = '../private.zip'
        (root / 'state.json').write_text(json.dumps(data))
    source = setup[0].path if kind != 'different-ledger' else root / 'different.sqlite3'
    before = (root / 'state.json').read_bytes()
    with pytest.raises((OSError, ValueError)):
        jobs.run(source, root, price_id=PRICE, now=CHECKED)
    assert (root / 'state.json').read_bytes() == before
    assert len(list(root.glob('ledger-*.zip'))) == 1


def test_state_publication_failure_does_not_return_old_green(setup, monkeypatch):
    run(setup)
    original = os.replace
    attempts = []
    def fail_final(*args):
        attempts.append(1)
        if len(attempts) == 2:
            raise OSError('synthetic disk full')
        return original(*args)
    monkeypatch.setattr(jobs.os, 'replace', fail_final)
    with pytest.raises(OSError):
        run(setup)
    assert state(setup)['phase'] == 'running'
    assert codes(check(setup)) == {'job_interrupted'}
    assert list(setup[1].glob('.state-*')) == []


def test_cli_exit_codes_and_default_off_are_sanitized(setup, monkeypatch, capsys):
    args = ['--ledger', str(setup[0].path), '--directory', str(setup[1]), '--price-id', PRICE]
    monkeypatch.delenv('TRADE_PAPER_PADDLE_LIVE_JOBS')
    assert jobs.main(['run', *args]) == 2
    assert json.loads(capsys.readouterr().out)['status'] == 'critical'
    monkeypatch.setenv('TRADE_PAPER_PADDLE_LIVE_JOBS', '1')
    assert jobs.main(['run', *args]) == 1
    assert json.loads(capsys.readouterr().out)['provider']['status'] == 'skipped'
    assert jobs.main(['check', *args]) == 1
    assert json.loads(capsys.readouterr().out)['backup_attempt'] == 'created'
