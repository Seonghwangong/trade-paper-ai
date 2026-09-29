import asyncio
from dataclasses import replace
import ssl
import sys

import pytest

from app import paddle_live_watchdog as watchdog, paddle_live_scheduler as scheduler
from tests.test_paddle_live_scheduler import configured
from tests.test_paddle_live_store import store

UUID = '12345678-1234-4123-8123-123456789abc'
URL = 'https://hc-ping.com/' + UUID


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setenv(watchdog.PREFIX + 'WATCHDOG', '1')
    monkeypatch.setenv(watchdog.PREFIX + 'WATCHDOG_URL', URL)


def test_disabled_does_not_touch_url_or_network(monkeypatch):
    monkeypatch.delenv(watchdog.PREFIX + 'WATCHDOG', raising=False)
    monkeypatch.setenv(watchdog.PREFIX + 'WATCHDOG_URL', 'invalid')
    monkeypatch.setattr(watchdog.http.client, 'HTTPSConnection', lambda *a, **k: pytest.fail('Network called'))
    assert watchdog.notify('ok') == 'disabled'


@pytest.mark.parametrize('url', ['', 'http://hc-ping.com/'+UUID,
    'https://hc-ping.com.evil.test/'+UUID, 'https://127.0.0.1/'+UUID,
    'https://user@hc-ping.com/'+UUID, 'https://hc-ping.com:443/'+UUID,
    URL+'?create=1', URL+'/start', URL+'#x', URL+'\n'])
def test_unsupported_destination_rejected_before_network(enabled, monkeypatch, url):
    monkeypatch.setenv(watchdog.PREFIX + 'WATCHDOG_URL', url)
    monkeypatch.setattr(watchdog.http.client, 'HTTPSConnection', lambda *a, **k: pytest.fail('Network called'))
    with pytest.raises(ValueError):
        watchdog.notify('ok')


@pytest.mark.parametrize('outcome,suffix', [('ok',''),('warning','/fail'),('critical','/fail')])
def test_bodyless_signal_and_verified_tls(enabled, monkeypatch, outcome, suffix):
    calls = []
    class Connection:
        def __init__(self, host, timeout, context):
            assert host == 'hc-ping.com' and timeout == 5
            assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname
        def request(self, *args, **kwargs):
            calls.append((args, kwargs))
        def getresponse(self):
            return type('Response', (), {'status':200,'read':lambda self,n:b'OK'})()
        def close(self):
            calls.append('closed')
    monkeypatch.setattr(watchdog.http.client, 'HTTPSConnection', Connection)
    assert watchdog.notify(outcome) == 'sent'
    assert calls == [(('POST','/'+UUID+suffix),{'body':b'','headers':{'Content-Length':'0'}}),'closed']
    assert UUID not in repr(watchdog.configuration())


@pytest.mark.parametrize('status,body', [(200,b'OK (not found)'),(200,b'OK (rate limited)'),
    (200,b'x'*129),(301,b'OK'),(503,b'OK')])
def test_unknown_limited_redirect_and_bad_response_are_not_success(enabled, monkeypatch, capsys, status, body):
    requests = []
    closed = []
    class Connection:
        def __init__(self,*a,**k): pass
        def request(self,*a,**k): requests.append(a)
        def getresponse(self):
            return type('Response', (), {'status':status,'read':lambda self,n:body})()
        def close(self): closed.append(True)
    monkeypatch.setattr(watchdog.http.client, 'HTTPSConnection', Connection)
    assert watchdog.main(['--status','ok']) == 2
    output = capsys.readouterr().out
    assert output == '{"watchdog": "failed"}\n' and UUID not in output
    assert len(requests) == 1 and closed == [True]


def test_transport_exception_is_closed_and_sanitized(enabled, monkeypatch, capsys):
    closed = []
    class Connection:
        def __init__(self,*a,**k): pass
        def request(self,*a,**k): raise OSError(URL)
        def close(self): closed.append(True)
    monkeypatch.setattr(watchdog.http.client, 'HTTPSConnection', Connection)
    assert watchdog.main(['--status','critical']) == 2
    assert URL not in capsys.readouterr().out and closed == [True]


def test_scheduler_validates_url_without_contacting_provider(configured, enabled, monkeypatch):
    monkeypatch.setattr(watchdog.http.client, 'HTTPSConnection', lambda *a,**k:pytest.fail('Network called'))
    assert scheduler._settings().watchdog
    monkeypatch.setenv(watchdog.PREFIX+'WATCHDOG_URL', 'invalid')
    with pytest.raises(RuntimeError):
        asyncio.run(scheduler.start_from_environment())


def test_scheduler_signal_is_bounded_and_skips_shutdown(configured, enabled, monkeypatch):
    settings = scheduler._settings()
    calls = []
    async def cycle(self, **kwargs): calls.append(kwargs)
    monkeypatch.setattr(scheduler.Scheduler, '_cycle', cycle)
    async def run():
        runner = scheduler.Scheduler(settings, None)
        await runner._watchdog(('critical','timeout'))
        assert calls == [{'command':[sys.executable,'-m','app.paddle_live_watchdog','--status','critical'],
                          'timeout':10,'label':'paddle_watchdog'}]
        await runner._watchdog(None)
        runner.stopped.set()
        await runner._watchdog(('ok','job_exit'))
        await scheduler.Scheduler(replace(settings,watchdog=False),None)._watchdog(('ok','job_exit'))
        assert len(calls) == 1
    asyncio.run(run())


def test_loop_signals_before_mail_and_preserves_failure(configured, enabled, monkeypatch, caplog):
    calls = []
    async def cycle(self, **kwargs):
        if kwargs:
            calls.append(('ping',kwargs['command'][-1]))
            raise OSError(URL)
        return 'critical','timeout'
    async def alert(self,outcome):
        calls.append(('alert',outcome))
        self.stopped.set()
    monkeypatch.setattr(scheduler.Scheduler,'_cycle',cycle)
    monkeypatch.setattr(scheduler.Scheduler,'_alert',alert)
    async def run():
        runner = await scheduler.start_from_environment()
        await runner.task
        assert runner.lock_fd is None
    asyncio.run(run())
    assert calls == [('ping','critical'),('alert',('critical','timeout'))]
    assert URL not in caplog.text and 'paddle_watchdog status=critical' in caplog.text
