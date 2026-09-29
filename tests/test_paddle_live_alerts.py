from datetime import datetime, timedelta, timezone
import json
import os
import subprocess
import sys

import pytest

from app import paddle_live_alerts as alerts
from tests.test_email_delivery import _environment

NOW = datetime(2026, 9, 29, 5, tzinfo=timezone.utc)


@pytest.fixture
def configured(tmp_path, monkeypatch):
    root = tmp_path / 'alerts'
    root.mkdir(mode=0o700)
    for key, value in _environment('api').items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv(alerts.PREFIX + 'ALERTS', '1')
    monkeypatch.setenv(alerts.PREFIX + 'ALERT_RECIPIENT', 'operator@example.com')
    monkeypatch.setenv(alerts.PREFIX + 'JOBS_DIRECTORY', str(root))
    return root


def notify(status='critical', reason='job_exit', **kwargs):
    kwargs.setdefault('now', NOW)
    kwargs.setdefault('sender', lambda message: True)
    return alerts.notify(status, reason, **kwargs)


def state(root):
    return json.loads((root / 'alert-state.json').read_text())


def test_disabled_does_not_read_storage_validate_email_or_send(monkeypatch):
    monkeypatch.delenv(alerts.PREFIX + 'ALERTS', raising=False)
    def forbidden(*args, **kwargs):
        pytest.fail('Disabled alert accessed storage or email')
    monkeypatch.setattr(alerts.os, 'open', forbidden)
    monkeypatch.setattr(alerts.mail, 'email_readiness', forbidden)
    assert notify(sender=forbidden) == 'disabled'


def test_sanitized_message_and_attempt_receipt_precede_send(configured):
    sent = []
    def sender(message):
        pending = state(configured)
        assert pending['accepted'] is False
        assert pending['attempted_at'] == NOW.isoformat()
        sent.append(message)
        return True
    assert notify(reason='timeout', sender=sender) == 'accepted'
    message = sent[0]
    assert message.recipient == 'operator@example.com'
    assert message.attachments == () and message.purpose == 'billing_ops'
    assert 'critical' in message.text_body and 'UTC' in message.text_body
    for private in ('api-secret-value', str(configured), 'operator@example.com'):
        assert private not in message.text_body + message.html_body
        assert private not in (configured / 'alert-state.json').read_text()
    assert state(configured)['accepted'] and state(configured)['incident_notified']
    assert all(p.stat().st_mode & 0o077 == 0 for p in configured.iterdir())


def test_repeat_cooldown_escalation_and_single_recovery(configured):
    assert notify('ok') == 'healthy_quiet'
    assert not (configured / 'alert-state.json').exists()
    assert notify('warning') == 'accepted'
    assert notify('warning', now=NOW + timedelta(minutes=15)) == 'suppressed'
    assert notify('critical', now=NOW + timedelta(minutes=16)) == 'accepted'
    assert notify('critical', now=NOW + timedelta(hours=6)) == 'suppressed'
    assert notify('critical', now=NOW + timedelta(hours=7)) == 'accepted'
    assert notify('ok', now=NOW + timedelta(hours=7, minutes=1)) == 'accepted'
    assert not state(configured)['incident_notified']
    assert notify('ok', now=NOW + timedelta(hours=14)) == 'healthy_quiet'


def test_failed_or_interrupted_delivery_is_throttled_and_not_marked_accepted(configured):
    assert notify(sender=lambda m: False) == 'delivery_failed'
    assert notify(now=NOW + timedelta(minutes=15)) == 'suppressed'
    assert not state(configured)['accepted']
    def crash(message):
        raise SystemExit('Synthetic process interruption')
    with pytest.raises(SystemExit):
        notify(sender=crash, now=NOW + timedelta(minutes=30))
    assert notify(now=NOW + timedelta(minutes=45)) == 'suppressed'
    assert notify(now=NOW + timedelta(minutes=60)) == 'accepted'


def test_failed_recovery_retries_without_losing_prior_incident(configured):
    notify()
    later = NOW + timedelta(minutes=15)
    assert notify('ok', now=later, sender=lambda m: False) == 'delivery_failed'
    assert state(configured)['incident_notified']
    assert notify('ok', now=later + timedelta(minutes=15)) == 'suppressed'
    assert notify('ok', now=later + timedelta(minutes=30)) == 'accepted'
    assert not state(configured)['incident_notified']


def test_preview_and_overlap_never_send(configured):
    def forbidden(message):
        pytest.fail('Preview/overlap sent email')
    assert notify(sender=forbidden, preview=True) == 'would_send'
    assert not (configured / 'alert-state.json').exists()
    with alerts._lock(configured) as acquired:
        assert acquired
        assert notify(sender=forbidden) == 'busy'


@pytest.mark.parametrize('kind', ['recipient', 'backend', 'unverified', 'directory',
                                  'state_symlink', 'lock_symlink', 'corrupt', 'clock', 'changed_recipient'])
def test_invalid_configuration_or_inventory_never_sends(configured, monkeypatch, kind):
    if kind == 'recipient':
        monkeypatch.setenv(alerts.PREFIX + 'ALERT_RECIPIENT', 'bad\r\nBcc: someone@example.com')
    elif kind == 'backend':
        monkeypatch.setenv('TRADE_PAPER_EMAIL_BACKEND', 'disabled')
    elif kind == 'unverified':
        monkeypatch.setenv('TRADE_PAPER_EMAIL_API_DOMAIN_VERIFIED', 'false')
    elif kind == 'directory':
        configured.chmod(0o755)
    elif kind.endswith('symlink'):
        target = configured.parent / 'protected'
        target.write_text('do not change')
        (configured / ('alert-state.json' if kind == 'state_symlink' else 'alert.lock')).symlink_to(target)
    elif kind == 'corrupt':
        (configured / 'alert-state.json').write_text('invalid')
        (configured / 'alert-state.json').chmod(0o600)
    else:
        notify()
        if kind == 'changed_recipient':
            monkeypatch.setenv(alerts.PREFIX + 'ALERT_RECIPIENT', 'other@example.com')
    def forbidden(message):
        pytest.fail('Invalid configuration sent email')
    with pytest.raises(Exception):
        notify(sender=forbidden, now=NOW - timedelta(seconds=1) if kind == 'clock' else NOW)
    if kind.endswith('symlink'):
        assert target.read_text() == 'do not change'


def test_storage_failure_prevents_delivery(configured, monkeypatch):
    def failure(*args):
        raise OSError('private-volume-path')
    monkeypatch.setattr(alerts, '_write', failure)
    def forbidden(message):
        pytest.fail('Missing durable receipt still sent email')
    with pytest.raises(OSError):
        notify(sender=forbidden)


def test_cli_preview_real_subprocess_does_not_contact_provider(configured):
    result = subprocess.run([sys.executable, '-m', 'app.paddle_live_alerts',
                             '--status', 'critical', '--reason', 'timeout', '--preview'],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0 and json.loads(result.stdout) == {'alert': 'would_send'}
    assert not (configured / 'alert-state.json').exists()


def test_cli_errors_are_sanitized(configured, monkeypatch, capsys):
    def failure(*args, **kwargs):
        raise ValueError('private key address and path')
    monkeypatch.setattr(alerts, 'notify', failure)
    assert alerts.main(['--status', 'critical', '--reason', 'timeout']) == 2
    assert json.loads(capsys.readouterr().out) == {'alert': 'configuration_storage_or_delivery_failure'}
