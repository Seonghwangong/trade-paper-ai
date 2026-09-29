"""Explicitly enabled, sanitized operator mail; never attach billing data."""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import tempfile

from app import email_delivery as mail
from app.paddle_live_backup import _sync_dir
from app.paddle_live_jobs import _private
from app.paddle_subscription_policy import _instant

PREFIX = 'TRADE_PAPER_PADDLE_LIVE_'
REASONS = {'job_exit', 'timeout', 'launch_failure', 'loop_failed'}
STATUSES = {'ok', 'warning', 'critical'}
REPEAT_SECONDS = 6 * 3600
RETRY_SECONDS = 30 * 60


def configuration():
    if os.environ.get(PREFIX + 'ALERTS') != '1':
        return None
    root = Path(os.environ[PREFIX + 'JOBS_DIRECTORY'])
    if not root.is_absolute():
        raise ValueError('Absolute private directory required')
    _private(root.lstat(), directory=True)
    recipient = mail._safe_address(os.environ.get(PREFIX + 'ALERT_RECIPIENT', ''), 'recipient')
    if mail.email_readiness().get('configuration') != 'Ready':
        raise ValueError('Verified email configuration required')
    return root, recipient


@contextmanager
def _lock(root):
    fd = os.open(root / 'alert.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        _private(os.fstat(fd))
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
        else:
            yield True
    finally:
        os.close(fd)


def _read(root, identity):
    try:
        fd = os.open(root / 'alert-state.json', os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return None
    with os.fdopen(fd) as stream:
        _private(os.fstat(stream.fileno()))
        raw = stream.read(4097)
    if len(raw) > 4096:
        raise ValueError('Invalid alert state')
    state = json.loads(raw)
    if (state['schema'] != 1 or state['identity'] != identity
            or state['status'] not in STATUSES or state['reason'] not in REASONS
            or type(state['accepted']) is not bool
            or type(state['incident_notified']) is not bool):
        raise ValueError('Invalid alert state')
    _instant(state['attempted_at'])
    return state


def _write(root, state):
    fd, name = tempfile.mkstemp(prefix='.alert-', dir=root)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(state, stream, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, root / 'alert-state.json')
        _sync_dir(root)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def message(recipient, status, reason, now):
    labels = {'ok': '점검 복구', 'warning': '점검 경고', 'critical': '점검 실패'}
    reasons = {'job_exit': '정기 백업·점검 결과', 'timeout': '점검 제한 시간 초과',
               'launch_failure': '점검 프로세스 실행 실패', 'loop_failed': '자동 점검 반복 실행 중단'}
    text = (f"Trade Paper AI {labels[status]}\n\n"
            f"확인 시각(UTC): {now.astimezone(timezone.utc).isoformat()}\n"
            f"상태: {status}\n원인: {reasons[reason]}\n\n"
            "운영 서버의 점검 기록과 로그를 확인해 주세요.\n"
            "이 메일은 실제 청구·환불·결제 성공을 확인하는 영수증이 아닙니다.\n"
            "고객 정보, 결제 원장, 백업 파일은 포함하지 않습니다.")
    import html
    return mail.DeliveryMessage(recipient, 'Trade Paper AI — ' + labels[status],
                                text, '<pre>' + html.escape(text) + '</pre>', 'billing_ops')


def notify(status, reason, *, now=None, sender=None, preview=False):
    config = configuration()
    if config is None:
        return 'disabled'
    if status not in STATUSES or reason not in REASONS:
        raise ValueError('Invalid outcome')
    now = datetime.now(timezone.utc) if now is None else now
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise ValueError('Aware clock required')
    root, recipient = config
    identity = hashlib.sha256((str(root) + '\n' + recipient).encode()).hexdigest()
    with _lock(root) as acquired:
        if not acquired:
            return 'busy'
        previous = _read(root, identity)
        incident = previous['incident_notified'] if previous else False
        if previous:
            age = (now - _instant(previous['attempted_at'])).total_seconds()
            if age < 0:
                raise ValueError('Alert clock moved backwards')
            escalation = status == 'critical' and previous['status'] != 'critical'
            recovery = status == 'ok' and incident and previous['status'] != 'ok'
            cooldown = REPEAT_SECONDS if previous['accepted'] else RETRY_SECONDS
            if not escalation and not recovery and age < cooldown:
                return 'suppressed'
        if status == 'ok' and not incident:
            return 'healthy_quiet'
        if preview:
            return 'would_send'
        # Save before sending: a kill/ambiguous response cannot cause a mail storm.
        state = {'schema': 1, 'identity': identity, 'status': status, 'reason': reason,
                 'attempted_at': now.isoformat(), 'accepted': False,
                 'incident_notified': incident}
        _write(root, state)
        accepted = bool((mail.deliver_email if sender is None else sender)(
            message(recipient, status, reason, now)))
        state.update(accepted=accepted,
                     incident_notified=(status != 'ok') if accepted else incident)
        _write(root, state)
        return 'accepted' if accepted else 'delivery_failed'


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--status', choices=sorted(STATUSES), required=True)
    parser.add_argument('--reason', choices=sorted(REASONS), required=True)
    parser.add_argument('--preview', action='store_true')
    args = parser.parse_args(argv)
    try:
        result = notify(args.status, args.reason, preview=args.preview)
    except Exception:
        result = 'configuration_storage_or_delivery_failure'
    print(json.dumps({'alert': result}))
    return 2 if result in ('delivery_failed', 'configuration_storage_or_delivery_failure') else 1 if result == 'busy' else 0


if __name__ == '__main__':
    raise SystemExit(main())
