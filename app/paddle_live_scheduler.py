"""Opt-in, same-instance scheduling of the read-only billing backup job."""
import asyncio
from dataclasses import dataclass
import fcntl
import logging
import os
from pathlib import Path
import sys

from app.paddle_live_actions import LiveClient
from app.paddle_live_jobs import _private
from app.paddle_live_store import PaddleLiveStore
from app import paddle_live_runtime as runtime
from app.storage import data_path

logger = logging.getLogger(__name__)
PREFIX = 'TRADE_PAPER_PADDLE_LIVE_'
PROJECT_ROOT = Path(__file__).resolve().parent.parent
TERMINATE_GRACE_SECONDS = 5


@dataclass(frozen=True)
class Settings:
    ledger: Path
    directory: Path
    price_id: str
    interval: int = 900
    timeout: int = 120
    backup_hours: int = 6
    with_provider: bool = False
    alerts: bool = False

    def command(self):
        args = [sys.executable, '-m', 'app.paddle_live_jobs', 'run',
                '--ledger', str(self.ledger), '--directory', str(self.directory),
                '--price-id', self.price_id, '--backup-hours', str(self.backup_hours)]
        return args + (['--with-provider'] if self.with_provider else [])


def _settings():
    # No storage access, subprocess, credential validation or task when disabled.
    if os.environ.get(PREFIX + 'SCHEDULER') != '1':
        return None
    if os.environ.get(PREFIX + 'JOBS') != '1':
        raise ValueError('Jobs opt-in required')
    directory = Path(os.environ[PREFIX + 'JOBS_DIRECTORY'])
    if not directory.is_absolute():
        raise ValueError('Absolute directory required')
    _private(directory.lstat(), directory=True)
    price = runtime.price_id()
    ledger = data_path('paddle_live.sqlite3').absolute()
    # Validate only an existing ledger; never initialize/migrate one at startup.
    PaddleLiveStore(ledger, price_id=price, environment='live', read_only=True)
    interval = int(os.environ.get(PREFIX + 'SCHEDULER_INTERVAL_SECONDS', '900'))
    timeout = int(os.environ.get(PREFIX + 'SCHEDULER_TIMEOUT_SECONDS', '120'))
    hours = int(os.environ.get(PREFIX + 'BACKUP_HOURS', '6'))
    if not (60 <= interval <= 3600 and 10 <= timeout <= 300
            and timeout < interval and 1 <= hours <= 24):
        raise ValueError('Invalid schedule bounds')
    provider = os.environ.get(PREFIX + 'MONITOR') == '1'
    if provider:
        LiveClient(os.environ.get(PREFIX + 'API_KEY', ''))  # Format only, no request.
    from app.paddle_live_alerts import configuration
    alerts = configuration() is not None
    from app.paddle_live_offsite import configuration as offsite_configuration
    offsite_configuration()  # Offline validation only; no request or key issuance.
    return Settings(ledger, directory, price, interval, timeout, hours, provider, alerts)


class Scheduler:
    def __init__(self, settings, lock_fd):
        self.settings = settings
        self.lock_fd = lock_fd
        self.stopped = asyncio.Event()
        self.task = None

    async def stop(self):
        self.stopped.set()
        if self.task is not None:
            await self.task

    async def _reap(self, process):
        if process.returncode is None:
            try:
                process.terminate()
            except ProcessLookupError:
                pass
        try:
            await asyncio.wait_for(process.wait(), timeout=TERMINATE_GRACE_SECONDS)
        except asyncio.TimeoutError:
            try:
                process.kill()
            except ProcessLookupError:
                pass
            await process.wait()

    async def _cycle(self, *, command=None, timeout=None, label='paddle_job'):
        # Fixed command, no shell or credentials in arguments. Job diagnostics are
        # saved privately; even an unexpected child traceback cannot enter logs.
        spawning = asyncio.create_task(asyncio.create_subprocess_exec(
            *(self.settings.command() if command is None else command), cwd=PROJECT_ROOT,
            stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL))
        try:
            process = await asyncio.shield(spawning)
        except asyncio.CancelledError:
            # Cancellation while the OS is creating a child must still reap it.
            process = await spawning
            await self._reap(process)
            raise
        waiter = asyncio.create_task(process.wait())
        stopping = asyncio.create_task(self.stopped.wait())
        try:
            done, _ = await asyncio.wait(
                (waiter, stopping), timeout=self.settings.timeout if timeout is None else timeout,
                return_when=asyncio.FIRST_COMPLETED)
            if waiter in done:
                code = waiter.result()
                status = {0: 'ok', 1: 'warning', 2: 'critical'}.get(code, 'critical')
                logger.log(logging.INFO if code == 0 else logging.WARNING if code == 1
                           else logging.ERROR, '%s status=%s exit=%d', label, status, code)
                return status, 'job_exit'
            elif self.stopped.is_set():
                logger.warning('%s status=warning reason=shutdown_interrupted', label)
                return None
            else:
                logger.error('%s status=critical reason=timeout', label)
                return 'critical', 'timeout'
        finally:
            await self._reap(process)
            stopping.cancel()
            await asyncio.gather(waiter, stopping, return_exceptions=True)

    async def _alert(self, outcome):
        if not self.settings.alerts or outcome is None or self.stopped.is_set():
            return
        status, reason = outcome
        try:
            await self._cycle(command=[sys.executable, '-m', 'app.paddle_live_alerts',
                                      '--status', status, '--reason', reason],
                              timeout=20, label='paddle_alert')
        except Exception:
            logger.error('paddle_alert status=critical reason=process_failure')

    async def _loop(self):
        try:
            while not self.stopped.is_set():
                started = asyncio.get_running_loop().time()
                try:
                    outcome = await self._cycle()
                except OSError:
                    logger.error('paddle_job status=critical reason=launch_or_process_failure')
                    outcome = ('critical', 'launch_failure')
                await self._alert(outcome)
                # Monotonic start-to-start cadence; never run concurrent cycles or
                # catch up a burst of missed runs after downtime/slow execution.
                remaining = max(0, self.settings.interval -
                                (asyncio.get_running_loop().time() - started))
                try:
                    await asyncio.wait_for(self.stopped.wait(), timeout=remaining)
                except asyncio.TimeoutError:
                    pass
        except Exception:
            logger.error('paddle_scheduler status=critical reason=loop_failed')
            await self._alert(('critical', 'loop_failed'))
        finally:
            os.close(self.lock_fd)
            self.lock_fd = None


async def start_from_environment():
    fd = None
    try:
        settings = _settings()
        if settings is None:
            return None
        fd = os.open(settings.directory / 'scheduler.lock',
                     os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        _private(os.fstat(fd))
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        scheduler = Scheduler(settings, fd)
        scheduler.task = asyncio.create_task(scheduler._loop(), name='paddle-live-jobs')
        logger.info('paddle_scheduler status=started')
        return scheduler
    except Exception:
        if fd is not None:
            os.close(fd)
        # Invalid opt-in must fail startup, not silently claim to be scheduled.
        raise RuntimeError('Paddle scheduler configuration/storage/lock invalid') from None
