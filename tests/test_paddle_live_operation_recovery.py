from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import sqlite3

import pytest

from app import paddle_live_operation_recovery as recovery
from app.paddle_live_actions import LiveActions, ProviderUnavailable
from app.paddle_live_store import PaddleLiveStore, BillingConflict
from app.paddle_live_backup import create_backup, stage_restore, inspect_ledger, BackupError
from app.paddle_live_monitor import diagnose
from tests.test_paddle_live_store import store, bound, send, event, completion, OFFER, PRICE, TXN, SUB, NOW
from tests.test_paddle_live_actions import Client, offer_config, transaction
from tests.test_paddle_live_monitor import codes


class Provider:
    def __init__(self, data):
        self.data = data
        self.gets = 0
        self.hook = lambda: None

    def transaction(self, target):
        assert target == TXN
        self.gets += 1
        self.hook()
        return deepcopy(self.data)

    def subscription(self, target):
        assert target == SUB
        self.gets += 1
        self.hook()
        return deepcopy(self.data)


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setenv('TRADE_PAPER_PADDLE_LIVE_OPERATION_RECOVERY', '1')
    monkeypatch.delenv('TRADE_PAPER_PADDLE_LIVE_ACCESS', raising=False)
    monkeypatch.delenv('TRADE_PAPER_PADDLE_LIVE_CHECKOUT', raising=False)


@pytest.fixture
def uncertain(store, enabled, offer_config):
    class Timeout(Client):
        def create_checkout(self, price, *, intent):
            self.creates += 1
            self.checkout_data.update(custom_data={'trade_paper_intent': intent},
                                      created_at=NOW.isoformat(), origin='api', payments=[])
            raise ProviderUnavailable('synthetic uncertain create')
    client = Timeout()
    with pytest.raises(ProviderUnavailable):
        LiveActions(store, client).checkout('A', now=NOW.timestamp())
    return store, client, Provider(client.checkout_data)


@pytest.fixture
def pending_cancel(store, enabled):
    bound(store)
    send(store, event(2))
    with store.connect() as db:
        db.execute("INSERT INTO live_operations VALUES ('account-A', 'cancel', ?, ?, NULL)", (NOW.timestamp(), SUB))
    data = event(2)['data']
    data['scheduled_change'] = {'action': 'cancel', 'effective_at': data['current_billing_period']['ends_at']}
    return store, Provider(data)


def run(uncertain, **kwargs):
    store, _, provider = uncertain
    return recovery.recover(store, provider, OFFER, 'A', 'checkout', target=TXN,
                            operator='operator-1', case='case-1', now=kwargs.pop('now', NOW + timedelta(hours=1)), **kwargs)


def cancel(pair, **kwargs):
    return recovery.recover(*pair, OFFER, 'account-A', 'cancel',
                            operator='operator-1', case='case-1', now=NOW + timedelta(hours=1), **kwargs)


def audit(store):
    with store.connect() as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='live_operation_recoveries'").fetchone():
            return []
        return db.execute('SELECT * FROM live_operation_recoveries').fetchall()


def test_uncertain_unpaid_checkout_reuses_same_transaction_after_audited_recovery(uncertain):
    store, original, provider = uncertain
    before = store.path.read_bytes()
    ro = PaddleLiveStore(store.path, price_id=PRICE, environment='live', read_only=True)
    preview = run((ro, original, provider))
    assert preview['provider_result'] == 'ready' and store.path.read_bytes() == before
    result = run(uncertain, expected=preview['digest'])
    assert result['result'] == 'recovered' and original.creates == 1 and len(audit(store)) == 1
    assert LiveActions(store, original).checkout('A', now=NOW.timestamp() + 3601) == TXN
    assert original.creates == 1
    assert run(uncertain, expected=preview['digest'], now=NOW + timedelta(hours=1, seconds=2))['result'] == 'already_confirmed'
    assert store.access_for_account('A', now=NOW) is None


def test_completed_checkout_only_registers_then_requires_signed_events(uncertain):
    store, _, provider = uncertain
    original = provider.data
    provider.data = completion()['data']
    provider.data.update(custom_data=original['custom_data'], created_at=original['created_at'], origin='api')
    result = run(uncertain, expected=run(uncertain)['digest'])
    assert result['provider_result'] == 'awaiting_completion'
    assert store.access_for_account('A', now=NOW) is None
    with pytest.raises(BillingConflict):
        LiveActions(store, Client()).checkout('A', now=NOW.timestamp() + 3601)
    assert 'checkout_confirmation_overdue' in codes(diagnose(store.path, price_id=PRICE, now=NOW + timedelta(hours=2)))
    send(store, completion())
    send(store, event(2))
    assert store.access_for_account('A', now=NOW).starter_access
    assert 'checkout_confirmation_overdue' not in codes(diagnose(store.path, price_id=PRICE, now=NOW + timedelta(hours=2)))


@pytest.mark.parametrize('change', [
    {'custom_data': {'trade_paper_intent': 'a' * 64}}, {'custom_data': None},
    {'origin': 'web'}, {'status': 'paid'}, {'status': 'past_due'}, {'status': 'canceled'},
    {'payments': [{'status': 'captured'}]}, {'currency_code': 'USD'},
    {'created_at': '2026-09-23T00:00:00Z'}, {'created_at': '2026-09-24T00:02:00Z'},
    {'id': 'txn_' + 'x' * 26},
])
def test_unproven_or_unsupported_candidate_never_releases_guard(uncertain, change):
    uncertain[2].data.update(change)
    before = uncertain[0].path.read_bytes()
    with pytest.raises((BillingConflict, ValueError, ProviderUnavailable)):
        run(uncertain)
    assert uncertain[0].path.read_bytes() == before and not audit(uncertain[0])
    assert uncertain[1].creates == 1


def test_legacy_unknown_request_cannot_be_guessed_from_account_or_email(uncertain):
    store, _, provider = uncertain
    with store.connect() as db:
        db.execute('DROP TABLE live_checkout_correlations')
    provider.data['custom_data']['account_id'] = 'A'
    provider.data['customer'] = {'email': 'owner@example.test'}
    with pytest.raises(BillingConflict, match='No trusted'):
        run(uncertain)
    assert audit(store) == []


def test_previously_registered_legacy_target_needs_no_inferred_correlation(uncertain):
    store, _, _ = uncertain
    with store.connect() as db:
        db.execute('DROP TABLE live_checkout_correlations')
        db.execute('INSERT INTO checkouts VALUES (?, ?, ?)', (TXN, 'A', PRICE))
        db.execute("UPDATE live_operations SET target_id=? WHERE account_id='A'", (TXN,))
    assert run(uncertain, expected=run(uncertain)['digest'])['result'] == 'recovered'


def test_candidate_already_registered_to_other_account_is_blocked(uncertain):
    store, _, _ = uncertain
    store.register_checkout(TXN, 'other')
    digest = run(uncertain)['digest']
    with pytest.raises(BillingConflict):
        run(uncertain, expected=digest)
    assert audit(store) == []


def test_provider_change_between_reads_or_preview_blocks_apply(uncertain):
    provider = uncertain[2]
    digest = run(uncertain)['digest']
    provider.data['status'] = 'ready'
    with pytest.raises(BillingConflict, match='Preview changed'):
        run(uncertain, expected=digest)
    def change():
        provider.data['status'] = 'draft' if provider.data['status'] == 'ready' else 'ready'
    provider.hook = change
    with pytest.raises(BillingConflict, match='Provider evidence changed'):
        run(uncertain)
    assert audit(uncertain[0]) == []


@pytest.mark.parametrize('kind', ['scheduled', 'canceled'])
def test_cancel_recovery_acknowledges_existing_provider_result_without_access_write(pending_cancel, kind):
    store, provider = pending_cancel
    if kind == 'canceled':
        provider.data.update(status='canceled', current_billing_period=None, scheduled_change=None)
    before = store.access_for_account('account-A', now=NOW)
    preview = cancel(pending_cancel)
    result = cancel(pending_cancel, expected=preview['digest'])
    assert result['provider_result'] == kind
    assert store.access_for_account('account-A', now=NOW) == before
    assert len(audit(store)) == 1 and provider.gets == 4


@pytest.mark.parametrize('change', [
    {'scheduled_change': None},
    {'scheduled_change': {'action': 'pause', 'effective_at': '2026-10-01T00:00:00Z'}},
    {'scheduled_change': {'action': 'cancel', 'effective_at': '2026-09-29T00:00:00Z'}},
    {'customer_id': 'ctm_' + 'z' * 26},
    {'status': 'past_due'},
])
def test_unconfirmed_or_different_cancel_is_not_retried(pending_cancel, change):
    pending_cancel[1].data.update(change)
    with pytest.raises((BillingConflict, ValueError)):
        cancel(pending_cancel)
    assert audit(pending_cancel[0]) == []


def test_audit_failure_rolls_back_registration_and_operation(uncertain):
    store, _, _ = uncertain
    digest = run(uncertain)['digest']
    with store.connect() as db:
        recovery.initialize_audit(db)
        db.execute("CREATE TRIGGER fail_audit BEFORE INSERT ON live_operation_recoveries BEGIN SELECT RAISE(ABORT, 'injected'); END")
    with pytest.raises(sqlite3.IntegrityError):
        run(uncertain, expected=digest)
    with store.connect() as db:
        assert db.execute('SELECT * FROM checkouts').fetchall() == []
        assert db.execute('SELECT target_id, result FROM live_operations').fetchall() == [(None, None)]


def test_concurrent_apply_records_one_recovery(uncertain):
    digest = run(uncertain)['digest']
    def apply(_):
        try:
            return run(uncertain, expected=digest)['result']
        except BillingConflict:
            return 'conflict'
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(apply, range(16)))
    assert results.count('recovered') == 1 and len(audit(uncertain[0])) == 1
    assert uncertain[1].creates == 1


@pytest.mark.parametrize('flag,value', [('OPERATION_RECOVERY', '0'), ('ACCESS', '1'), ('CHECKOUT', '1')])
def test_maintenance_required_before_provider_reads(uncertain, monkeypatch, flag, value):
    monkeypatch.setenv('TRADE_PAPER_PADDLE_LIVE_' + flag, value)
    with pytest.raises(BillingConflict):
        run(uncertain)
    assert uncertain[2].gets == 0


def test_slow_recovery_does_not_modify_operation(uncertain, monkeypatch):
    digest = run(uncertain)['digest']
    ticks = iter([0, 61])
    monkeypatch.setattr(recovery.time, 'monotonic', lambda: next(ticks))
    with pytest.raises(BillingConflict, match='freshness'):
        run(uncertain, expected=digest)
    assert audit(uncertain[0]) == []


def test_restore_preserves_correlation_guard_and_audited_result(uncertain):
    store, _, _ = uncertain
    run(uncertain, expected=run(uncertain)['digest'])
    saved = create_backup(store.path, store.path.parent / 'operation.zip', price_id=PRICE)
    destination = store.path.parent / 'restored'
    stage_restore(saved['archive'], destination, price_id=PRICE, expected_sha256=saved['archive_sha256'])
    copy = PaddleLiveStore(destination / 'paddle_live.sqlite3', price_id=PRICE, environment='live')
    assert audit(copy) == audit(store)
    assert run((copy, uncertain[1], uncertain[2]), now=NOW + timedelta(hours=1, seconds=2))['result'] == 'already_confirmed'
    assert uncertain[1].creates == 1


@pytest.mark.parametrize('damage', ['token', 'owner', 'audit'])
def test_backup_rejects_corrupt_recovery_evidence(uncertain, damage):
    store, _, _ = uncertain
    run(uncertain, expected=run(uncertain)['digest'])
    with store.connect() as db:
        if damage == 'token':
            db.execute("UPDATE live_checkout_correlations SET token='bad'")
        elif damage == 'owner':
            db.execute("UPDATE live_operation_recoveries SET account_id='other'")
        else:
            db.execute("UPDATE live_operation_recoveries SET evidence='{}'")
    with pytest.raises((BackupError, ValueError, KeyError)):
        inspect_ledger(store.path, PRICE)


def test_raw_payload_and_correlation_not_in_preview_or_audit(uncertain):
    provider = uncertain[2]
    provider.data['private_note'] = 'PRIVATE-RAW'
    preview = run(uncertain)
    run(uncertain, expected=preview['digest'])
    saved = json.dumps(preview) + str(audit(uncertain[0]))
    assert 'PRIVATE-RAW' not in saved
    assert provider.data['custom_data']['trade_paper_intent'] not in saved


def test_cli_default_off_never_opens_store(monkeypatch):
    from app import paddle_live_runtime as runtime
    monkeypatch.delenv('TRADE_PAPER_PADDLE_LIVE_OPERATION_RECOVERY', raising=False)
    monkeypatch.setattr(runtime, 'store', lambda **kw: pytest.fail('must not open'))
    with pytest.raises(SystemExit) as result:
        recovery.main(['--account', 'A', '--kind', 'checkout', '--operator', 'op', '--case', 'case'])
    assert result.value.code == 2

def test_correlation_is_committed_before_provider_create(store, enabled, offer_config):
    class Verify(Client):
        def create_checkout(self, price, *, intent):
            with store.connect() as db:
                assert db.execute('SELECT token FROM live_checkout_correlations WHERE account_id=?', ('A',)).fetchone() == (intent,)
                assert db.execute("SELECT result FROM live_operations WHERE account_id='A'").fetchone() == (None,)
            return super().create_checkout(price, intent=intent)
    client = Verify()
    assert LiveActions(store, client).checkout('A', now=NOW.timestamp()) == TXN
    assert client.creates == 1


def test_failed_correlation_insert_rolls_back_intent_before_any_post(store, enabled, offer_config):
    with store.connect() as db:
        recovery.initialize_correlations(db)
        db.execute("CREATE TRIGGER fail_intent BEFORE INSERT ON live_checkout_correlations BEGIN SELECT RAISE(ABORT, 'injected'); END")
    client = Client()
    with pytest.raises(sqlite3.IntegrityError):
        LiveActions(store, client).checkout('A', now=NOW.timestamp())
    assert client.creates == 0
    with store.connect() as db:
        assert db.execute('SELECT * FROM live_operations').fetchall() == []


def test_receipt_or_operation_change_during_lookup_aborts_recovery(uncertain):
    store, _, provider = uncertain
    digest = run(uncertain)['digest']
    def hook():
        with store.connect() as db:
            db.execute("UPDATE live_operations SET started=started+1 WHERE account_id='A'")
    provider.hook = hook
    with pytest.raises(BillingConflict, match='Local operation changed'):
        run(uncertain, expected=digest)
    assert audit(store) == []


def test_maintenance_change_during_lookup_aborts_recovery(uncertain, monkeypatch):
    digest = run(uncertain)['digest']
    uncertain[2].hook = lambda: monkeypatch.setenv('TRADE_PAPER_PADDLE_LIVE_ACCESS', '1')
    with pytest.raises(BillingConflict):
        run(uncertain, expected=digest)
    assert audit(uncertain[0]) == []


def test_cancellation_expiring_during_review_is_not_acknowledged(pending_cancel, monkeypatch):
    store, provider = pending_cancel
    now = datetime(2026, 9, 30, 23, 59, 59, tzinfo=timezone.utc)
    ticks = iter([0, 2])
    monkeypatch.setattr(recovery.time, 'monotonic', lambda: next(ticks))
    with pytest.raises(BillingConflict, match='boundary'):
        recovery.recover(store, provider, OFFER, 'account-A', 'cancel',
                         operator='op', case='case', now=now)
    assert audit(store) == []


def test_cli_preview_apply_uses_get_only(uncertain, monkeypatch, capsys):
    from app import paddle_live_runtime as runtime
    store, _, provider = uncertain
    monkeypatch.setattr(runtime, 'store', lambda **kw: store)
    monkeypatch.setattr(runtime, 'offer', lambda: OFFER)
    monkeypatch.setattr(recovery, 'LiveClient', lambda key: provider)
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls.fromtimestamp((NOW + timedelta(hours=1)).timestamp(), tz)
    monkeypatch.setattr(recovery, 'datetime', Clock)
    args = ['--account', 'A', '--kind', 'checkout', '--transaction', TXN, '--operator', 'op', '--case', 'case']
    recovery.main(args)
    preview = json.loads(capsys.readouterr().out)
    recovery.main(args + ['--apply', preview['digest']])
    assert json.loads(capsys.readouterr().out)['result'] == 'recovered'
    assert provider.gets == 4 and uncertain[1].creates == 1
