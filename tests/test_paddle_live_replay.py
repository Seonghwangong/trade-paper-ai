from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import timedelta
from io import BytesIO
import json
import sqlite3

import pytest

from app import paddle_live_replay as replay, paddle_live_actions as actions
from app.paddle_live_store import PaddleLiveStore, BillingConflict
from app.paddle_live_backup import create_backup, stage_restore, inspect_ledger, BackupError
from app.paddle_live_monitor import diagnose
from tests.test_paddle_live_store import store, bound, send, event, completion, PRICE, OFFER, SECRET, NOW, TXN
from tests.test_paddle_live_monitor import codes

NOTICE = 'ntf_' + 'n' * 26
SETTING = 'ntfset_' + 's' * 26
REPLAY = 'ntf_' + 'r' * 26
CHECKED = NOW + timedelta(hours=1)


class Provider:
    def __init__(self, payload=None):
        payload = deepcopy(event(2) if payload is None else payload)
        payload['notification_id'] = NOTICE
        self.notice = {'id': NOTICE, 'notification_setting_id': SETTING, 'origin': 'event',
                       'status': 'failed', 'type': payload['event_type'], 'payload': payload}
        self.setting = {'id': SETTING, 'type': 'url', 'destination': replay.DESTINATION,
                        'active': True, 'api_version': 1, 'traffic_source': 'platform',
                        'endpoint_secret_key': SECRET,
                        'subscribed_events': [{'name': payload['event_type']}]}
        self.posts = 0
        self.hook = lambda: None
        self.error = None
        self.answer = REPLAY

    def notification(self, identifier):
        assert identifier == NOTICE
        self.hook()
        return deepcopy(self.notice)

    def notification_setting(self, identifier):
        assert identifier == SETTING
        return deepcopy(self.setting)

    def replay_notification(self, identifier):
        assert identifier == NOTICE
        self.posts += 1
        if self.error:
            raise self.error
        return self.answer


@pytest.fixture
def enabled(store, monkeypatch):
    monkeypatch.setenv('TRADE_PAPER_PADDLE_LIVE_REPLAY', '1')
    monkeypatch.setenv('TRADE_PAPER_PADDLE_LIVE_WEBHOOK', '1')
    monkeypatch.delenv('TRADE_PAPER_PADDLE_LIVE_ACCESS', raising=False)
    monkeypatch.delenv('TRADE_PAPER_PADDLE_LIVE_CHECKOUT', raising=False)
    bound(store)
    return store, Provider()


def run(pair, **kwargs):
    return replay.replay(*pair, OFFER, 'account-A', NOTICE, SETTING, SECRET,
                         operator='operator-1', case='case-1', now=CHECKED, **kwargs)


def rows(store):
    with store.connect() as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='live_replay_requests'").fetchone():
            return []
        return db.execute('SELECT * FROM live_replay_requests').fetchall()


def test_preview_preserves_bytes_apply_waits_for_signed_delivery(enabled):
    store, provider = enabled
    before = store.path.read_bytes()
    ro = PaddleLiveStore(store.path, price_id=PRICE, environment='live', read_only=True)
    preview = run((ro, provider))
    assert preview['result'] == 'preview' and provider.posts == 0
    assert store.path.read_bytes() == before and rows(store) == []
    result = run(enabled, expected=preview['digest'])
    assert result['result'] == 'awaiting_signed_delivery' and provider.posts == 1
    assert store.access_for_account('account-A', now=NOW) is None
    assert run(enabled, expected=preview['digest'])['result'] == 'already_requested'
    assert provider.posts == 1
    send(store, provider.notice['payload'])
    assert replay.status(ro, event(2)['event_id'])['result'] == 'received'
    assert store.access_for_account('account-A', now=NOW).starter_access
    assert run(enabled)['result'] == 'already_received'
    assert provider.posts == 1

@pytest.mark.parametrize('kind', ['renewal', 'adjustment', 'delivered'])
def test_other_supported_events_use_original_verified_webhook_path(enabled, kind):
    from tests.test_paddle_live_renewals import renewal
    from tests.test_paddle_live_adjustments import adjustment
    store, _ = enabled
    provider = Provider(renewal() if kind == 'renewal' else adjustment(day=24) if kind == 'adjustment' else event(2))
    if kind == 'delivered':
        provider.notice['status'] = 'delivered'
    pair = (store, provider)
    run(pair, expected=run(pair)['digest'])
    send(store, provider.notice['payload'])
    assert replay.status(store, provider.notice['payload']['event_id'])['result'] == 'received'
    assert provider.posts == 1


def test_replaying_stale_active_does_not_override_later_cancellation(enabled):
    store, provider = enabled
    canceled = event(3, day=25)
    canceled['data'].update(status='canceled', current_billing_period=None)
    send(store, canceled)
    run(enabled, expected=run(enabled)['digest'])
    assert send(store, provider.notice['payload']) == 'stale'
    assert not store.access_for_account('account-A', now=NOW).starter_access


def test_configuration_change_during_preflight_fails_before_reservation(enabled, monkeypatch):
    digest = run(enabled)['digest']
    enabled[1].hook = lambda: monkeypatch.setenv('TRADE_PAPER_PADDLE_LIVE_ACCESS', '1')
    with pytest.raises(BillingConflict):
        run(enabled, expected=digest)
    assert rows(enabled[0]) == [] and enabled[1].posts == 0


def test_expired_preflight_fails_before_reservation(enabled, monkeypatch):
    digest = run(enabled)['digest']
    ticks = iter([0, 61])
    monkeypatch.setattr(replay.time, 'monotonic', lambda: next(ticks))
    with pytest.raises(BillingConflict, match='freshness'):
        run(enabled, expected=digest)
    assert rows(enabled[0]) == [] and enabled[1].posts == 0


def test_conflicting_receipt_never_claims_success(enabled):
    store, _ = enabled
    run(enabled, expected=run(enabled)['digest'])
    with store.connect() as db:
        db.execute('INSERT INTO events VALUES (?, ?, ?, ?)',
                   (event(2)['event_id'], 'a' * 64, NOW.isoformat(), 'bound'))
    with pytest.raises(BillingConflict):
        replay.status(store, event(2)['event_id'])
    with pytest.raises(BackupError):
        inspect_ledger(store.path, PRICE)


def test_existing_consumed_event_cannot_backfill_old_missing_initial_period(enabled):
    store, _ = enabled
    with store.connect() as db:
        db.execute('DELETE FROM live_initial_periods')
    provider = Provider(completion())
    assert run((store, provider))['result'] == 'already_received'
    assert provider.posts == 0
    with store.connect() as db:
        assert db.execute('SELECT * FROM live_initial_periods').fetchall() == []


@pytest.mark.parametrize('flag,value', [('REPLAY', '0'), ('WEBHOOK', '0'), ('ACCESS', '1'), ('CHECKOUT', '1')])
def test_requires_explicit_maintenance_and_reception(enabled, monkeypatch, flag, value):
    monkeypatch.setenv('TRADE_PAPER_PADDLE_LIVE_' + flag, value)
    with pytest.raises(BillingConflict):
        run(enabled)
    assert enabled[1].posts == 0 and rows(enabled[0]) == []


@pytest.mark.parametrize('field,value', [
    ('destination', 'https://evil.test/hook'), ('traffic_source', 'all'),
    ('active', False), ('endpoint_secret_key', 'wrong-secret'), ('api_version', True),
    ('type', 'email'), ('id', 'ntfset_' + 'x' * 26), ('subscribed_events', []),
])
def test_rejects_wrong_destination_and_simulation_configuration(enabled, field, value):
    enabled[1].setting[field] = value
    with pytest.raises((BillingConflict, ValueError)):
        run(enabled)
    assert rows(enabled[0]) == [] and enabled[1].posts == 0


@pytest.mark.parametrize('field,value', [
    ('origin', 'replay'), ('status', 'needs_retry'), ('status', 'not_attempted'),
    ('notification_setting_id', 'ntfset_' + 'x' * 26), ('id', REPLAY),
    ('type', 'transaction.completed'),
])
def test_only_original_terminal_notification_can_be_replayed(enabled, field, value):
    enabled[1].notice[field] = value
    with pytest.raises(BillingConflict):
        run(enabled)
    assert rows(enabled[0]) == []


@pytest.mark.parametrize('age', [-1, 91 * 86400])
def test_future_and_expired_notifications_rejected(enabled, age):
    enabled[1].notice['payload']['occurred_at'] = (CHECKED - timedelta(seconds=age)).isoformat()
    with pytest.raises(BillingConflict):
        run(enabled)


def test_unknown_or_other_owner_not_inferred_from_metadata(enabled):
    data = enabled[1].notice['payload']['data']
    data['customer_id'] = 'ctm_' + 'z' * 26
    data['custom_data'] = {'account_id': 'account-A'}
    with pytest.raises(BillingConflict):
        run(enabled)
    assert rows(enabled[0]) == []


def test_initial_completion_requires_existing_checkout_without_direct_binding(enabled):
    store, _ = enabled
    other = PaddleLiveStore(store.path.parent / 'initial.sqlite3', price_id=PRICE, environment='live')
    provider = Provider(completion())
    with pytest.raises(BillingConflict):
        run((other, provider))
    other.register_checkout(TXN, 'account-A')
    preview = run((other, provider))
    run((other, provider), expected=preview['digest'])
    with other.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM bindings').fetchone()[0] == 0
    send(other, provider.notice['payload'])
    assert replay.status(other, completion()['event_id'])['result'] == 'received'


def test_changed_preview_rejected_before_reserving(enabled):
    digest = run(enabled)['digest']
    enabled[1].notice['payload']['data']['status'] = 'past_due'
    with pytest.raises(BillingConflict, match='Preview changed'):
        run(enabled, expected=digest)
    assert rows(enabled[0]) == [] and enabled[1].posts == 0


def test_timeout_and_restart_never_repeat_post(enabled):
    store, provider = enabled
    digest = run(enabled)['digest']
    provider.error = actions.ProviderUnavailable('synthetic timeout')
    with pytest.raises(actions.ProviderUnavailable):
        run(enabled, expected=digest)
    assert replay.status(store, event(2)['event_id'])['result'] == 'outcome_unknown'
    reopened = PaddleLiveStore(store.path, price_id=PRICE, environment='live')
    assert run((reopened, provider), expected=digest)['result'] == 'already_requested'
    assert provider.posts == 1


@pytest.mark.parametrize('answer', [NOTICE, 'bad-id', None])
def test_malformed_ack_preserves_attempt_guard(enabled, answer):
    digest = run(enabled)['digest']
    enabled[1].answer = answer
    with pytest.raises((ValueError, actions.ProviderUnavailable)):
        run(enabled, expected=digest)
    assert len(rows(enabled[0])) == 1 and rows(enabled[0])[0][-1] is None
    assert enabled[1].posts == 1


def test_concurrent_apply_reserves_one_request(enabled):
    digest = run(enabled)['digest']
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: run(enabled, expected=digest), range(16)))
    assert enabled[1].posts == 1 and len(rows(enabled[0])) == 1
    assert all(r['result'] in ('already_requested', 'awaiting_signed_delivery') for r in results)


def test_receipt_arriving_during_preflight_avoids_replay(enabled):
    digest = run(enabled)['digest']
    enabled[1].hook = lambda: send(enabled[0], event(2))
    assert run(enabled, expected=digest)['result'] == 'already_received'
    assert enabled[1].posts == 0 and rows(enabled[0]) == []


@pytest.mark.parametrize('phase', ['INSERT', 'UPDATE'])
def test_db_failure_preserves_no_post_or_ambiguous_guard(enabled, phase):
    store, provider = enabled
    digest = run(enabled)['digest']
    with store.connect() as db:
        replay.initialize(db)
        db.execute("CREATE TRIGGER reject_replay BEFORE " + phase +
                   " ON live_replay_requests BEGIN SELECT RAISE(ABORT, 'injected'); END")
    with pytest.raises(sqlite3.IntegrityError):
        run(enabled, expected=digest)
    assert provider.posts == (0 if phase == 'INSERT' else 1)
    if phase == 'INSERT':
        assert rows(store) == []
    else:
        assert replay.status(store, event(2)['event_id'])['result'] == 'outcome_unknown'


def test_monitor_backup_restore_preserve_attempt_guard(enabled):
    store, provider = enabled
    digest = run(enabled)['digest']
    run(enabled, expected=digest)
    report = diagnose(store.path, price_id=PRICE, now=CHECKED + timedelta(hours=1))
    assert 'notification_replay_unconfirmed' in codes(report)
    saved = create_backup(store.path, store.path.parent / 'replay.zip', price_id=PRICE)
    destination = store.path.parent / 'restored'
    stage_restore(saved['archive'], destination, price_id=PRICE, expected_sha256=saved['archive_sha256'])
    restored = PaddleLiveStore(destination / 'paddle_live.sqlite3', price_id=PRICE, environment='live')
    assert rows(restored) == rows(store)
    assert run((restored, provider), expected=digest)['result'] == 'already_requested'
    assert provider.posts == 1
    send(restored, event(2))
    assert 'notification_replay_unconfirmed' not in codes(diagnose(restored.path, price_id=PRICE, now=CHECKED + timedelta(hours=1)))


@pytest.mark.parametrize('damage', ['owner', 'digest', 'replay', 'event-type'])
def test_backup_rejects_corrupt_replay_evidence(enabled, damage):
    store, _ = enabled
    run(enabled, expected=run(enabled)['digest'])
    statements = {'owner': "UPDATE live_replay_requests SET account_id='unknown'",
                  'digest': "UPDATE live_replay_requests SET evidence_digest='bad'",
                  'replay': 'UPDATE live_replay_requests SET replay_id=notification_id',
                  'event-type': "UPDATE live_replay_requests SET event_type='unknown'"}
    with store.connect() as db:
        db.execute(statements[damage])
    with pytest.raises(BackupError):
        inspect_ledger(store.path, PRICE)


def test_preview_and_journal_exclude_raw_payload_and_secrets(enabled):
    enabled[1].notice['payload']['data']['custom_data'] = {'private': 'CUSTOMER-PRIVATE'}
    preview = run(enabled)
    run(enabled, expected=preview['digest'])
    saved = json.dumps(preview) + str(rows(enabled[0]))
    assert SECRET not in saved and 'CUSTOMER-PRIVATE' not in saved


def test_api_replay_accepts_only_202_and_fixed_path(monkeypatch):
    requests = []
    class Response(BytesIO):
        status = 202
    class Opener:
        def open(self, req, timeout):
            requests.append(req)
            return Response(json.dumps({'data': {'notification_id': REPLAY}}).encode())
    monkeypatch.setattr(actions, 'build_opener', lambda *args: Opener())
    client = actions.LiveClient('pdl_live_apikey_synthetic')
    assert client.replay_notification(NOTICE) == REPLAY
    assert requests[0].full_url == actions.API + '/notifications/' + NOTICE + '/replay'
    assert requests[0].get_method() == 'POST' and requests[0].data is None
    Response.status = 200
    with pytest.raises(actions.ProviderUnavailable):
        client.replay_notification(NOTICE)
    with pytest.raises(ValueError):
        client.replay_notification('https://evil.test')


def test_cli_default_off_does_not_open_ledger(monkeypatch, capsys):
    from app import paddle_live_runtime as runtime
    monkeypatch.delenv('TRADE_PAPER_PADDLE_LIVE_REPLAY', raising=False)
    monkeypatch.setattr(runtime, 'store', lambda **kw: pytest.fail('must stay closed'))
    with pytest.raises(SystemExit) as result:
        replay.main([])
    assert result.value.code == 2 and 'disabled' in capsys.readouterr().err


def test_cli_preview_apply_status(enabled, monkeypatch, capsys):
    from app import paddle_live_runtime as runtime
    store, provider = enabled
    monkeypatch.setattr(runtime, 'store', lambda **kw: store)
    monkeypatch.setattr(runtime, 'offer', lambda: OFFER)
    monkeypatch.setattr(replay, 'LiveClient', lambda key: provider)
    monkeypatch.setenv('TRADE_PAPER_PADDLE_LIVE_WEBHOOK_SECRET', SECRET)
    monkeypatch.setenv('TRADE_PAPER_PADDLE_LIVE_NOTIFICATION_SETTING_ID', SETTING)
    from datetime import datetime
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls.fromtimestamp(CHECKED.timestamp(), tz)
    monkeypatch.setattr(replay, 'datetime', Clock)
    args = ['--account', 'account-A', '--notification', NOTICE, '--operator', 'operator-1', '--case', 'case-1']
    replay.main(args)
    preview = json.loads(capsys.readouterr().out)
    replay.main(args + ['--apply', preview['digest']])
    assert json.loads(capsys.readouterr().out)['result'] == 'awaiting_signed_delivery'
    replay.main(['--status', event(2)['event_id']])
    assert json.loads(capsys.readouterr().out)['result'] == 'awaiting_signed_delivery'
    assert provider.posts == 1
