from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timezone
import json
import sqlite3

import pytest

from app import paddle_live_runtime as runtime, subscription
from app.paddle_live_store import BillingConflict, PaddleLiveStore
from app.paddle_live_offer import validate_completed_transaction
from tests.test_paddle_live_store import (
    store, bound, send, event, completion, signed, PRICE, TXN, SUB, CUSTOMER,
    OFFER, CLOCK, SECRET, NOW,
)
from tests.test_paddle_live_webhook import live, post, initialize
from tests.test_paddle_live_manage import ready, http, headers
from tests.test_subscription import _files


def adjustment(n=10, *, action='refund', status='approved', kind='full', day=25, **changes):
    data = {'id': 'adj_' + 'a' * 26, 'subscription_id': SUB, 'customer_id': CUSTOMER,
            'transaction_id': TXN, 'action': action, 'status': status, 'type': kind,
            'currency_code': 'KRW', 'totals': {'total': '29000', 'currency_code': 'KRW'}}
    data.update(changes)
    return event(n, 'adjustment.created' if status == 'pending_approval' else 'adjustment.updated', data, day)


def evidence(store):
    with store.connect() as db:
        return (db.execute('SELECT * FROM live_adjustment_events ORDER BY event_id').fetchall(),
                db.execute('SELECT * FROM events ORDER BY event_id').fetchall())


@pytest.mark.parametrize('action', ['refund', 'credit', 'chargeback', 'chargeback_warning',
                                    'chargeback_reverse', 'chargeback_warning_reverse', 'credit_reverse'])
def test_approved_adjustments_latch_review_across_future_active_events_and_restart(store, action):
    bound(store)
    send(store, event(2))
    assert store.access_for_account('account-A', now=NOW).starter_access
    assert send(store, adjustment(action=action)) == 'adjustment_review'
    send(store, event(3, day=26))
    reopened = PaddleLiveStore(store.path, price_id=PRICE, environment='live', read_only=True)
    assert not reopened.access_for_account('account-A', now=NOW).starter_access
    assert reopened.access_for_account('account-A', now=NOW).access_until is None


@pytest.mark.parametrize('status', ['pending_approval', 'rejected'])
def test_pending_or_rejected_refund_does_not_revoke_access(store, status):
    bound(store)
    send(store, event(2))
    assert send(store, adjustment(status=status)) == 'adjustment_recorded'
    assert store.access_for_account('account-A', now=NOW).starter_access


@pytest.mark.parametrize('kind', ['full', 'partial', None])
def test_partial_or_unknown_scope_requires_review_and_does_not_guess_refund_policy(store, kind):
    bound(store)
    send(store, event(2))
    send(store, adjustment(kind=kind, totals={'total': '100', 'currency_code': 'KRW'}))
    assert not store.access_for_account('account-A', now=NOW).starter_access


def test_late_pending_rejected_and_reversal_cannot_clear_hold(store):
    bound(store)
    send(store, event(2))
    send(store, adjustment(action='chargeback'))
    send(store, adjustment(11, action='chargeback', status='reversed', day=26))
    send(store, adjustment(12, action='chargeback', status='pending_approval', day=24))
    send(store, adjustment(13, action='chargeback', status='rejected', day=27))
    assert not store.access_for_account('account-A', now=NOW).starter_access
    assert len(evidence(store)[0]) == 4


def test_signed_renewal_adjustment_uses_existing_binding_not_browser_metadata(store):
    bound(store)
    send(store, event(2))
    send(store, adjustment(transaction_id='txn_' + 'r' * 26,
                           reason='private dispute reason', custom_data={'account_id': 'victim'}))
    assert not store.access_for_account('account-A', now=NOW).starter_access
    assert store.account_state('victim', now=NOW) == (False, None)
    assert 'private dispute' not in str(evidence(store)) and 'victim' not in str(evidence(store))


def test_early_adjustment_retries_after_binding_without_consuming_event(store):
    store.register_checkout(TXN, 'account-A')
    with pytest.raises(BillingConflict):
        send(store, adjustment())
    assert evidence(store) == ([], [])
    send(store, completion())
    assert send(store, adjustment()) == 'adjustment_review'
    send(store, event(2))
    assert not store.access_for_account('account-A', now=NOW).starter_access


@pytest.mark.parametrize('changes,error', [
    ({'customer_id': 'ctm_' + 'z' * 26}, BillingConflict),
    ({'subscription_id': 'sub_' + 'z' * 26}, BillingConflict),
    ({'subscription_id': None}, ValueError),
    ({'id': 'not-an-adjustment'}, ValueError),
    ({'status': 'unknown'}, ValueError),
    ({'action': 'unknown'}, ValueError),
    ({'type': 'invalid'}, ValueError),
    ({'currency_code': 'USD'}, ValueError),
    ({'totals': {'total': True, 'currency_code': 'KRW'}}, ValueError),
])
def test_invalid_or_wrong_owner_adjustments_never_change_evidence(store, changes, error):
    bound(store)
    before = evidence(store)
    with pytest.raises(error):
        send(store, adjustment(**changes))
    assert evidence(store) == before


def test_known_other_account_transaction_cannot_revoke_this_account(store):
    bound(store)
    txn_b = 'txn_' + 'b' * 26
    store.register_checkout(txn_b, 'account-B')
    with pytest.raises(BillingConflict):
        send(store, adjustment(transaction_id=txn_b))
    assert evidence(store)[0] == []


def test_adjustment_identity_is_immutable_and_review_is_account_scoped(store):
    bound(store)
    send(store, event(2))
    txn_b, sub_b, ctm_b = 'txn_' + 'b' * 26, 'sub_' + 'b' * 26, 'ctm_' + 'b' * 26
    store.register_checkout(txn_b, 'account-B')
    send(store, completion(3, id=txn_b, subscription_id=sub_b, customer_id=ctm_b))
    snapshot_b = event(4)
    snapshot_b['data'].update(id=sub_b, customer_id=ctm_b)
    send(store, snapshot_b)
    send(store, adjustment())
    before = evidence(store)
    with pytest.raises(BillingConflict):
        send(store, adjustment(11, subscription_id=sub_b, customer_id=ctm_b, transaction_id=txn_b))
    assert evidence(store) == before
    assert store.access_for_account('account-B', now=NOW).starter_access
    assert not store.access_for_account('account-A', now=NOW).starter_access


def test_duplicate_and_concurrent_delivery_record_one_hold(store):
    bound(store)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: send(store, adjustment()), range(16)))
    assert results.count('adjustment_review') == 1 and results.count('duplicate') == 15
    assert len(evidence(store)[0]) == 1


def test_adjustment_and_event_are_atomic_on_storage_failure(store):
    bound(store)
    with store.connect() as db:
        db.execute("CREATE TRIGGER reject_adjustment_event BEFORE INSERT ON events "
                   "BEGIN SELECT RAISE(ABORT, 'test failure'); END")
    before = evidence(store)
    with pytest.raises(sqlite3.IntegrityError):
        send(store, adjustment())
    assert evidence(store) == before
    with store.connect() as db:
        db.execute('DROP TRIGGER reject_adjustment_event')
    assert send(store, adjustment()) == 'adjustment_review'


def test_unsigned_adjustment_does_not_revoke_access(store):
    from fastapi import HTTPException
    bound(store)
    send(store, event(2))
    raw, _ = signed(adjustment())
    with pytest.raises(HTTPException):
        store.apply_signed_event(raw, '', secret=SECRET, offer=OFFER, now=CLOCK)
    assert store.access_for_account('account-A', now=NOW).starter_access


def test_old_schema_read_only_does_not_migrate_and_writable_open_adds_table(store):
    bound(store)
    send(store, event(2))
    with store.connect() as db:
        db.execute('DROP TABLE live_adjustment_events')
    original = store.path.read_bytes()
    old = PaddleLiveStore(store.path, price_id=PRICE, environment='live', read_only=True)
    assert old.access_for_account('account-A', now=NOW).starter_access
    assert store.path.read_bytes() == original
    migrated = PaddleLiveStore(store.path, price_id=PRICE, environment='live')
    send(migrated, adjustment())
    assert not old.access_for_account('account-A', now=NOW).starter_access


def test_http_hold_updates_access_without_json_writes_or_sales_catalog(live, monkeypatch):
    client, path = live
    users, history, usage = _files(path, monkeypatch)
    initialize(client)
    monkeypatch.setenv('TRADE_PAPER_PADDLE_LIVE_ACCESS', '1')
    post(client, event(2))
    before = {p: p.read_bytes() for p in (users, history, usage)}
    assert subscription.usage_summary('A', now=NOW)['limit'] is None
    monkeypatch.delenv('TRADE_PAPER_PADDLE_LIVE_PRODUCT_ID')
    monkeypatch.delenv('TRADE_PAPER_PADDLE_LIVE_TAX_MODE')
    assert post(client, adjustment()).json()['result'] == 'adjustment_review'
    assert post(client, event(3, day=26)).status_code == 200
    assert subscription.subscription_for_account('A', now=NOW)['plan'] == 'Free'
    assert subscription.usage_summary('A', now=NOW)['limit'] == 5
    assert {p: p.read_bytes() for p in before} == before


def test_review_ui_does_not_claim_access_and_cancellation_remains_available(ready):
    from app import paddle_live_manage as manage, paddle_live_checkout as buy
    send(ready.store, adjustment())
    state = http(manage.PATH + '/status').json()
    assert state['phase'] == 'review' and state['billing_review'] is True
    assert not state['starter_access'] and state['access_until'] is None
    assert state['can_cancel'] is True
    assert buy.checkout_state('A') == {'phase': 'review', 'can_open': False}
    assert 'You can still cancel renewal below.' in http().text
    response = http(manage.PATH + '/cancel', 'POST', headers())
    assert response.status == 200 and ready.client.cancels == 1
    assert not ready.store.access_for_account('A', now=NOW).starter_access


def test_tax_inclusive_completion_uses_net_subtotal_plus_tax():
    valid = completion()['data']  # KRW 26,364 + 2,636 VAT = 29,000 total.
    validate_completed_transaction(valid, OFFER)
    invalid = deepcopy(valid)
    for totals in (invalid['details']['totals'], invalid['details']['adjusted_totals'],
                   invalid['details']['line_items'][0]['totals'], invalid['details']['line_items'][0]['unit_totals']):
        totals['subtotal'] = '29000'
    with pytest.raises(ValueError):
        validate_completed_transaction(invalid, OFFER)
