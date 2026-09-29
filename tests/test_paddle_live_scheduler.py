import asyncio
from dataclasses import replace
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from app import paddle_live_jobs as jobs, paddle_live_scheduler as scheduler
from app import storage
from tests.test_paddle_live_store import store, PRICE


@pytest.fixture
def configured(store, monkeypatch, tmp_path):
    # Deliberately existing synthetic ledger, never the app's local/host ledger.
    ledger = store.path.with_name('paddle_live.sqlite3')
    store.path.rename(ledger)
    root = tmp_path / 'jobs'
    root.mkdir(mode=0o700)
    monkeypatch.setattr(storage, 'DATA_DIR', tmp_path)
    monkeypatch.setenv('TRADE_PAPER_DATA_DIR', str(tmp_path))
    for name, value in {'SCHEDULER': '1', 'JOBS': '1', 'MONITOR': '0',
                        'JOBS_DIRECTORY': str(root), 'PRICE_ID': PRICE}.items():
        monkeypatch.setenv(scheduler.PREFIX + name, value)
    for name in ('SCHEDULER_INTERVAL_SECONDS', 'SCHEDULER_TIMEOUT_SECONDS', 'BACKUP_HOURS'):
        monkeypatch.delenv(scheduler.PREFIX + name, raising=False)
    monkeypatch.setenv('TRADE_PAPER_PADDLE_SANDBOX_PRICE_ID', 'pri_' + 'z' * 26)
    return ledger, root


async def eventually(predicate):
    async def waiting():
        while not predicate():
            await asyncio.sleep(.01)
    await asyncio.wait_for(waiting(), 10)


def receipt(root):
    path = root / 'state.json'
    return json.loads(path.read_text()) if path.exists() else {}


def test_disabled_fastapi_hooks_do_not_touch_storage_or_spawn(monkeypatch):
    from app import main
    monkeypatch.delenv(scheduler.PREFIX + 'SCHEDULER', raising=False)
    def forbidden(*args, **kwargs):
        pytest.fail('Disabled scheduler touched storage or spawned a task')
    monkeypatch.setattr(scheduler, 'data_path', forbidden)
    monkeypatch.setattr(scheduler.os, 'open', forbidden)
    monkeypatch.setattr(scheduler.asyncio, 'create_task', forbidden)
    async def lifecycle():
        await main.start_billing_scheduler()
        assert main.app.state.billing_scheduler is None
        await main.stop_billing_scheduler()
    asyncio.run(lifecycle())
    assert main.start_billing_scheduler in main.app.router.on_startup
    assert main.stop_billing_scheduler in main.app.router.on_shutdown


@pytest.mark.parametrize('kind', ['jobs_off', 'missing_ledger', 'wrong_price',
    'public_directory', 'symlink_directory', 'relative_directory', 'interval',
    'timeout', 'overlap_bounds', 'backup_hours', 'provider_no_key', 'symlink_lock', 'offsite_missing_key'])
def test_bad_enabled_configuration_fails_before_starting(configured, monkeypatch, kind):
    ledger, root = configured
    if kind == 'jobs_off':
        monkeypatch.setenv(scheduler.PREFIX + 'JOBS', '0')
    elif kind == 'missing_ledger':
        ledger.unlink()
    elif kind == 'wrong_price':
        monkeypatch.setenv(scheduler.PREFIX + 'PRICE_ID', 'pri_' + 'x' * 26)
    elif kind == 'public_directory':
        root.chmod(0o755)
    elif kind == 'symlink_directory':
        link = root.with_name('linked')
        link.symlink_to(root, target_is_directory=True)
        monkeypatch.setenv(scheduler.PREFIX + 'JOBS_DIRECTORY', str(link))
    elif kind == 'relative_directory':
        monkeypatch.setenv(scheduler.PREFIX + 'JOBS_DIRECTORY', 'jobs')
    elif kind == 'symlink_lock':
        (root / 'scheduler.lock').symlink_to(ledger)
    elif kind == 'provider_no_key':
        monkeypatch.setenv(scheduler.PREFIX + 'MONITOR', '1')
        monkeypatch.setenv(scheduler.PREFIX + 'API_KEY', '')
    elif kind == 'offsite_missing_key':
        monkeypatch.setenv(scheduler.PREFIX + 'OFFSITE', '1')
        monkeypatch.delenv(scheduler.PREFIX + 'OFFSITE_B2_KEY_ID', raising=False)
    else:
        name, value = {'interval': ('SCHEDULER_INTERVAL_SECONDS', 'nan'),
                       'timeout': ('SCHEDULER_TIMEOUT_SECONDS', '301'),
                       'overlap_bounds': ('SCHEDULER_INTERVAL_SECONDS', '60'),
                       'backup_hours': ('BACKUP_HOURS', '0')}[kind]
        monkeypatch.setenv(scheduler.PREFIX + name, value)
    before = ledger.read_bytes() if ledger.exists() else None
    with pytest.raises(RuntimeError, match='configuration/storage/lock invalid'):
        asyncio.run(scheduler.start_from_environment())
    assert (ledger.read_bytes() if ledger.exists() else None) == before
    assert not (root / 'state.json').exists()


def test_real_job_backup_shutdown_and_restart_reuse(configured, caplog):
    ledger, root = configured
    before = ledger.read_bytes()
    caplog.set_level(logging.INFO, logger=scheduler.__name__)
    async def run():
        first = await scheduler.start_from_environment()
        try:
            with pytest.raises(RuntimeError):
                await scheduler.start_from_environment()  # Same-host duplicate.
            await eventually(lambda: receipt(root).get('phase') == 'finished')
        finally:
            await first.stop()
        initial = receipt(root)
        assert initial['report']['backup_attempt'] == 'created'
        restarted = await scheduler.start_from_environment()
        try:
            await eventually(lambda: receipt(root).get('report', {}).get('backup_attempt') == 'reused')
        finally:
            await restarted.stop()
        assert receipt(root)['latest'] == initial['latest']
        assert first.task.done() and restarted.task.done()
    asyncio.run(run())
    assert ledger.read_bytes() == before
    assert len(list(root.glob('ledger-*.zip'))) == 1
    report = jobs.check(ledger, root, price_id=PRICE)
    assert report['status'] == 'warning'  # Provider was deliberately not contacted.
    assert 'provider_check_skipped' in {item['code'] for item in report['issues']}
    assert str(root) not in caplog.text and PRICE not in caplog.text
    assert all(p.stat().st_mode & 0o077 == 0 for p in root.iterdir())


@pytest.mark.parametrize('reason', ['timeout', 'shutdown', 'cancel_during_spawn', 'cancel_running'])
def test_hung_child_is_reaped_even_if_it_ignores_terminate(configured, monkeypatch, caplog, reason):
    _, root = configured
    pidfile = root / 'synthetic.pid'
    code = ('import os,signal,time,pathlib; signal.signal(signal.SIGTERM,signal.SIG_IGN); '
            f'pathlib.Path({str(pidfile)!r}).write_text(str(os.getpid())); time.sleep(60)')
    monkeypatch.setattr(scheduler.Settings, 'command', lambda self: [sys.executable, '-c', code])
    monkeypatch.setattr(scheduler, 'TERMINATE_GRACE_SECONDS', .05)
    settings = replace(scheduler._settings(), timeout=.15)
    monkeypatch.setattr(scheduler, '_settings', lambda: settings)
    caplog.set_level(logging.INFO, logger=scheduler.__name__)
    original_spawn = asyncio.create_subprocess_exec
    async def delayed_spawn(*args, **kwargs):
        process = await original_spawn(*args, **kwargs)
        await asyncio.sleep(.15)
        return process
    if reason == 'cancel_during_spawn':
        monkeypatch.setattr(scheduler.asyncio, 'create_subprocess_exec', delayed_spawn)
    async def run():
        runner = await scheduler.start_from_environment()
        await eventually(pidfile.exists)
        pid = int(pidfile.read_text())
        if reason == 'timeout':
            await eventually(lambda: 'reason=timeout' in caplog.text)
        elif reason in ('cancel_during_spawn', 'cancel_running'):
            runner.task.cancel()
        try:
            await runner.stop()
        except asyncio.CancelledError:
            assert reason in ('cancel_during_spawn', 'cancel_running')
        assert runner.task.done() and runner.lock_fd is None
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)
    asyncio.run(run())


def test_launch_failure_retries_without_leaking_exception(configured, monkeypatch, caplog):
    _, root = configured
    original_spawn = asyncio.create_subprocess_exec
    settings = replace(scheduler._settings(), interval=.05)
    monkeypatch.setattr(scheduler, '_settings', lambda: settings)
    calls = []
    async def flaky(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise OSError('PRIVATE-PATH-AND-SECRET')
        return await original_spawn(*args, **kwargs)
    monkeypatch.setattr(scheduler.asyncio, 'create_subprocess_exec', flaky)
    async def run():
        runner = await scheduler.start_from_environment()
        try:
            await eventually(lambda: receipt(root).get('phase') == 'finished')
        finally:
            await runner.stop()
    asyncio.run(run())
    assert len(calls) >= 2
    assert 'launch_or_process_failure' in caplog.text
    assert 'PRIVATE-PATH-AND-SECRET' not in caplog.text


def test_slow_cycles_are_serial_and_shutdown_interrupts_sleep(configured, monkeypatch):
    _, root = configured
    settings = replace(scheduler._settings(), interval=.01)
    monkeypatch.setattr(scheduler, '_settings', lambda: settings)
    marker = root / 'sequence.txt'
    code = (f'import pathlib,time; p=pathlib.Path({str(marker)!r}); '
            'f=p.open("a"); f.write("start\\n"); f.flush(); time.sleep(.05); '
            'f.write("end\\n"); f.close()')
    monkeypatch.setattr(scheduler.Settings, 'command', lambda self: [sys.executable, '-c', code])
    async def run():
        runner = await scheduler.start_from_environment()
        try:
            await eventually(lambda: marker.exists() and marker.read_text().count('end') >= 3)
        finally:
            await asyncio.wait_for(runner.stop(), 1)
    asyncio.run(run())
    lines = marker.read_text().splitlines()
    assert all(value == ('start' if index % 2 == 0 else 'end')
               for index, value in enumerate(lines))


def test_restart_reports_an_interrupted_previous_cycle(configured):
    ledger, root = configured
    jobs.run(ledger, root, price_id=PRICE)
    saved = receipt(root)
    saved['phase'] = 'running'
    jobs._write(root, saved)
    async def run():
        runner = await scheduler.start_from_environment()
        try:
            await eventually(lambda: receipt(root).get('phase') == 'finished')
        finally:
            await runner.stop()
    asyncio.run(run())
    assert 'previous_job_incomplete' in {
        item['code'] for item in receipt(root)['report']['issues']}
    assert receipt(root)['latest'] == saved['latest']


def test_abrupt_host_process_death_releases_scheduler_lock_and_preserves_backup(configured):
    ledger, root = configured
    code = ('import asyncio; from app.paddle_live_scheduler import start_from_environment; '
            'exec("async def main():\\n await start_from_environment()\\n await asyncio.sleep(60)"); '
            'asyncio.run(main())')
    process = subprocess.Popen([sys.executable, '-c', code], cwd=scheduler.PROJECT_ROOT,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        deadline = time.monotonic() + 10
        while receipt(root).get('phase') != 'finished':
            assert process.poll() is None and time.monotonic() < deadline
            time.sleep(.02)
        saved = receipt(root)
    finally:
        process.kill()
        process.wait(timeout=5)
    async def restart():
        runner = await scheduler.start_from_environment()
        try:
            await eventually(lambda: receipt(root).get('report', {}).get('backup_attempt') == 'reused')
        finally:
            await runner.stop()
    asyncio.run(restart())
    assert receipt(root)['latest'] == saved['latest']
    assert jobs.check(ledger, root, price_id=PRICE)['status'] == 'warning'


def test_notifications_receive_job_outcome_and_are_skipped_when_disabled_or_stopping(configured, monkeypatch):
    from app import paddle_live_alerts
    from tests.test_email_delivery import _environment
    for key, value in _environment('api').items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv(scheduler.PREFIX + 'ALERTS', '1')
    monkeypatch.setenv(scheduler.PREFIX + 'ALERT_RECIPIENT', 'operator@example.com')
    settings = scheduler._settings()
    assert settings.alerts
    commands = []
    async def cycle(self, **kwargs):
        commands.append(kwargs)
    monkeypatch.setattr(scheduler.Scheduler, '_cycle', cycle)
    async def run():
        runner = scheduler.Scheduler(settings, None)
        await runner._alert(('critical', 'timeout'))
        assert commands[0]['command'] == [sys.executable, '-m', 'app.paddle_live_alerts',
                                         '--status', 'critical', '--reason', 'timeout']
        assert commands[0]['timeout'] == 20 and commands[0]['label'] == 'paddle_alert'
        runner.stopped.set()
        await runner._alert(('critical', 'timeout'))
        disabled = scheduler.Scheduler(replace(settings, alerts=False), None)
        await disabled._alert(('critical', 'timeout'))
        assert len(commands) == 1
    asyncio.run(run())
    monkeypatch.setenv(scheduler.PREFIX + 'ALERT_RECIPIENT', '')
    with pytest.raises(RuntimeError):
        asyncio.run(scheduler.start_from_environment())


def test_loop_notifies_launch_failure_and_keeps_job_failure_visible(configured, monkeypatch, caplog):
    settings = replace(scheduler._settings(), alerts=True)
    monkeypatch.setattr(scheduler, '_settings', lambda: settings)
    outcomes = []
    async def cycle(self):
        raise OSError('private launch path')
    async def alert(self, outcome):
        outcomes.append(outcome)
        self.stopped.set()
    monkeypatch.setattr(scheduler.Scheduler, '_cycle', cycle)
    monkeypatch.setattr(scheduler.Scheduler, '_alert', alert)
    async def run():
        runner = await scheduler.start_from_environment()
        await runner.task
        assert runner.lock_fd is None
    asyncio.run(run())
    assert outcomes == [('critical', 'launch_failure')]
    assert 'launch_or_process_failure' in caplog.text and 'private launch path' not in caplog.text
