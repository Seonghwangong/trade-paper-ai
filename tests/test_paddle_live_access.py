from datetime import datetime, timedelta, timezone
import sqlite3

import pytest

from app import paddle_live_manage as manage, paddle_live_runtime as runtime
from app.paddle_live_store import PaddleLiveStore, BillingConflict
from app.paddle_live_backup import create_backup, stage_restore, inspect_ledger, BackupError
from app.paddle_live_monitor import diagnose
from tests.test_paddle_live_store import store, bound, send, event, completion, PRICE, TXN, SUB, NOW
from tests.test_paddle_live_renewals import renewal, RENEWAL
from tests.test_paddle_live_adjustments import adjustment
from tests.test_paddle_live_manage import ready, http, headers
from tests.test_paddle_live_monitor import codes
from tests.test_paddle_live_reconcile import held, audit

OCT = datetime(2026, 10, 2, tzinfo=timezone.utc)


def next_period(n=3):
    payload = event(n, day=26)
    payload['data']['current_billing_period'] = renewal()['data']['billing_period']
    return payload


@pytest.mark.parametrize('first', ['snapshot', 'completion'])
def test_both_signed_inputs_required_in_either_order(store, first):
    bound(store)
    send(store, event(2))
    inputs = [next_period(), renewal()]
    if first == 'completion':
        inputs.reverse()
    send(store, inputs[0])
    assert not store.access_for_account('account-A', now=OCT).starter_access
    send(store, inputs[1])
    ro = PaddleLiveStore(store.path, price_id=PRICE, environment='live', read_only=True)
    assert ro.access_for_account('account-A', now=OCT).starter_access
    assert ro.access_for_account('account-A', now=OCT).access_until == datetime(2026, 11, 1, tzinfo=timezone.utc)


def test_unpaid_renewal_remains_denied_across_restarts_duplicates_and_future_period(store):
    bound(store)
    send(store, next_period())
    assert send(store, completion(5)) == 'bound'  # Initial payment is not October payment.
    assert send(store, next_period()) == 'duplicate'
    future = renewal(12, id='txn_' + 'f' * 26, billing_period={
        'starts_at': '2026-11-01T00:00:00Z', 'ends_at': '2026-12-01T00:00:00Z'})
    send(store, future)
    ro = PaddleLiveStore(store.path, price_id=PRICE, environment='live', read_only=True)
    result = ro.access_for_account('account-A', now=OCT)
    assert not result.starter_access and result.access_until is None


@pytest.mark.parametrize('field,value', [
    ('starts_at', '2026-10-01T00:00:01Z'), ('ends_at', '2026-10-31T23:59:59Z'),
])
def test_overlap_is_not_exact_period_evidence(store, field, value):
    bound(store)
    send(store, next_period())
    paid = renewal()
    paid['data']['billing_period'][field] = value
    send(store, paid)
    assert not store.access_for_account('account-A', now=OCT).starter_access


def test_equivalent_timezones_and_exclusive_period_boundaries(store):
    bound(store)
    send(store, next_period())
    paid = renewal(billing_period={'starts_at': '2026-10-01T09:00:00+09:00',
                                   'ends_at': '2026-11-01T09:00:00+09:00'})
    send(store, paid)
    start, end = datetime(2026, 10, 1, tzinfo=timezone.utc), datetime(2026, 11, 1, tzinfo=timezone.utc)
    for when, expected in [(start - timedelta(microseconds=1), False), (start, True),
                            (end - timedelta(microseconds=1), True), (end, False)]:
        assert store.access_for_account('account-A', now=when).starter_access is expected


@pytest.mark.parametrize('status', ['past_due', 'canceled', 'paused', 'trialing'])
def test_paid_period_never_overrides_nonactive_snapshot(store, status):
    bound(store)
    snap = next_period()
    snap['data']['status'] = status
    send(store, snap)
    send(store, renewal())
    assert not store.access_for_account('account-A', now=OCT).starter_access


@pytest.mark.parametrize('action', ['cancel', 'pause'])
def test_scheduled_stop_still_caps_paid_access(store, action):
    bound(store)
    snap = next_period()
    snap['data']['scheduled_change'] = {'action': action, 'effective_at': '2026-10-15T00:00:00Z'}
    send(store, snap)
    send(store, renewal())
    result = store.access_for_account('account-A', now=OCT)
    assert result.starter_access and result.access_until == datetime(2026, 10, 15, tzinfo=timezone.utc)
    assert not store.access_for_account('account-A', now=result.access_until).starter_access


def test_refund_hold_and_stale_active_snapshot_survive_late_payment(store):
    bound(store)
    send(store, next_period())
    send(store, adjustment(12, transaction_id=RENEWAL))
    send(store, renewal())
    assert send(store, event(2)) == 'stale'
    assert not store.access_for_account('account-A', now=OCT).starter_access


@pytest.mark.parametrize('missing', ['null-period', 'old-schema', 'lost-receipt'])
def test_missing_initial_evidence_cannot_borrow_first_seen_active_period(store, missing):
    store.register_checkout(TXN, 'account-A')
    send(store, completion(billing_period=None) if missing == 'null-period' else completion())
    send(store, event(2))
    with store.connect() as db:
        if missing == 'old-schema':
            db.execute('DROP TABLE live_initial_periods')
        elif missing == 'lost-receipt':
            db.execute("DELETE FROM events WHERE result='bound'")
    before = store.path.read_bytes()
    ro = PaddleLiveStore(store.path, price_id=PRICE, environment='live', read_only=True)
    result = ro.access_for_account('account-A', now=NOW)
    assert not result.starter_access and result.access_until is None
    assert store.path.read_bytes() == before
    if missing == 'old-schema':
        upgraded = PaddleLiveStore(store.path, price_id=PRICE, environment='live')
        assert not upgraded.access_for_account('account-A', now=NOW).starter_access


@pytest.mark.parametrize('period', [
    {}, {'starts_at': '2026-09-01', 'ends_at': '2026-10-01'},
    {'starts_at': '2026-09-01T00:00:00Z', 'ends_at': '2026-11-01T00:00:00Z'},
    {'starts_at': '2026-10-01T00:00:00Z', 'ends_at': '2026-09-01T00:00:00Z'},
])
def test_invalid_initial_period_rolls_back_binding_and_event(store, period):
    store.register_checkout(TXN, 'account-A')
    with pytest.raises(ValueError):
        send(store, completion(billing_period=period))
    with store.connect() as db:
        assert not db.execute('SELECT * FROM bindings').fetchall()
        assert not db.execute('SELECT * FROM live_initial_periods').fetchall()
        assert not db.execute('SELECT * FROM events').fetchall()


def test_changed_initial_period_cannot_extend_coverage(store):
    bound(store)
    before = store.path.read_bytes()
    with pytest.raises(BillingConflict):
        send(store, completion(9, billing_period=renewal()['data']['billing_period']))
    assert store.path.read_bytes() == before


def test_initial_evidence_rolls_back_if_final_receipt_insert_fails(store):
    store.register_checkout(TXN, 'account-A')
    with store.connect() as db:
        db.execute("CREATE TRIGGER reject_event BEFORE INSERT ON events BEGIN SELECT RAISE(ABORT, 'injected'); END")
    with pytest.raises(sqlite3.IntegrityError):
        send(store, completion())
    with store.connect() as db:
        assert not db.execute('SELECT * FROM live_initial_periods').fetchall()
        assert not db.execute('SELECT * FROM bindings').fetchall()


def test_management_http_and_runtime_agree_and_cancel_stays_available(ready, monkeypatch):
    send(ready.store, next_period())
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return OCT
    monkeypatch.setattr(manage, 'datetime', Clock)
    state = http(manage.PATH + '/status').json()
    assert state['phase'] == 'awaiting_payment' and not state['starter_access']
    assert state['access_until'] is None and state['can_cancel']
    assert 'Do not make another payment' in http().text
    assert runtime.subscription_override('A', {'plan': 'Starter'}, now=OCT)['plan'] == 'Free'
    assert http(manage.PATH + '/cancel', 'POST', headers()).status == 200
    send(ready.store, renewal())
    assert http(manage.PATH + '/status').json()['starter_access']
    assert runtime.subscription_override('A', {}, now=OCT)['plan'] == 'Starter'


@pytest.mark.parametrize('damage', ['orphan', 'wrong-receipt', 'bad-period'])
def test_backup_rejects_corrupt_initial_evidence(store, damage):
    bound(store)
    with store.connect() as db:
        if damage == 'orphan':
            db.execute('DELETE FROM bindings')
        elif damage == 'wrong-receipt':
            db.execute("UPDATE live_initial_periods SET event_id=?", ('evt_' + '9' * 26,))
        else:
            db.execute("UPDATE live_initial_periods SET ends_at=starts_at")
    with pytest.raises((BackupError, ValueError)):
        inspect_ledger(store.path, PRICE)


def test_restore_preserves_paid_and_unpaid_period_decisions(store):
    bound(store)
    send(store, next_period())
    saved = create_backup(store.path, store.path.parent / 'paid-period.zip', price_id=PRICE)
    destination = store.path.parent / 'copy'
    stage_restore(saved['archive'], destination, price_id=PRICE, expected_sha256=saved['archive_sha256'])
    copy = PaddleLiveStore(destination / 'paddle_live.sqlite3', price_id=PRICE, environment='live')
    assert not copy.access_for_account('account-A', now=OCT).starter_access
    send(copy, renewal())
    assert copy.access_for_account('account-A', now=OCT).starter_access
    assert not store.access_for_account('account-A', now=OCT).starter_access


def test_monitor_reports_unpaid_active_period_then_clears(store):
    bound(store)
    send(store, next_period())
    before = store.path.read_bytes()
    result = diagnose(store.path, price_id=PRICE, now=OCT)
    assert 'active_period_payment_unconfirmed' in codes(result) and result['status'] == 'critical'
    assert store.path.read_bytes() == before
    send(store, renewal())
    assert 'active_period_payment_unconfirmed' not in codes(diagnose(store.path, price_id=PRICE, now=OCT))


def test_review_release_cannot_bypass_missing_paid_period(held):
    store, provider = held
    provider.sub = next_period()['data']
    send(store, next_period())
    from app.paddle_live_reconcile import ReviewBlocked, reconcile
    from tests.test_paddle_live_store import OFFER
    with pytest.raises(ReviewBlocked):
        reconcile(store, provider, OFFER, 'account-A', operator='operator', case='test', now=OCT)
    assert audit(store) == ([], [])


def test_same_period_payment_of_another_account_never_grants_access(store):
    from tests.test_paddle_live_renewals import bind_other
    bound(store)
    sub, customer = bind_other(store)
    send(store, next_period(4))
    send(store, renewal(subscription_id=sub, customer_id=customer))
    assert not store.access_for_account('account-A', now=OCT).starter_access
