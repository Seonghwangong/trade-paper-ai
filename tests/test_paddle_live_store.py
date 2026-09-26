from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import hmac
import json
import sqlite3

import pytest
from fastapi import HTTPException

from app.paddle_live_store import BillingConflict, PaddleLiveStore
from app.paddle_live_offer import Offer, validate_completed_transaction
from app.paddle_sandbox_core import SandboxStore
from tests.test_paddle_subscription_policy import snapshot, SUB, CUSTOMER, PRICE, NOW

TXN = 'txn_' + 'd' * 26
PRODUCT = 'pro_' + 'p' * 26
OFFER = Offer(PRICE, PRODUCT, 'internal')
SECRET = 'synthetic-live-destination-secret'
CLOCK = int(NOW.timestamp())


def event(n=1, kind='subscription.updated', data=None, day=24):
    return {'event_id': 'evt_' + f'{n:026d}', 'notification_id': 'ntf_original',
            'event_type': kind, 'occurred_at': f'2026-09-{day:02d}T00:00:00Z',
            'data': snapshot() if data is None else data}


def completion(n=1, **changes):
    price = {'id': PRICE, 'product_id': PRODUCT, 'status': 'active', 'type': 'standard',
             'billing_cycle': {'interval': 'month', 'frequency': 1}, 'trial_period': None,
             'unit_price': {'amount': '29000', 'currency_code': 'KRW'},
             'unit_price_overrides': [], 'tax_mode': 'internal',
             'quantity': {'minimum': 1, 'maximum': 1}}
    money = {'subtotal': '26364', 'discount': '0', 'tax': '2636', 'total': '29000'}
    totals = {**money, 'credit': '0', 'credit_to_balance': '0', 'balance': '0',
              'grand_total': '29000', 'grand_total_tax': '2636', 'currency_code': 'KRW'}
    data = {'id': TXN, 'subscription_id': SUB, 'customer_id': CUSTOMER,
            'billing_period': snapshot()['current_billing_period'],
            'status': 'completed', 'collection_mode': 'automatic',
            'currency_code': 'KRW', 'discount_id': None,
            'items': [{'price': price, 'quantity': 1, 'proration': None}],
            'details': {'totals': totals, 'line_items': [{
                'price_id': PRICE, 'quantity': 1, 'proration': None,
                'product': {'id': PRODUCT, 'type': 'standard', 'status': 'active'},
                'totals': money, 'unit_totals': money}],
                'adjusted_totals': {key: totals[key] for key in (
                    'subtotal', 'tax', 'total', 'grand_total', 'grand_total_tax', 'currency_code')}},
            'payments': [{'status': 'captured', 'amount': '29000'}]}
    data.update(changes)
    return event(n, 'transaction.completed', data)


def signed(payload, secret=SECRET):
    raw = json.dumps(payload).encode()
    digest = hmac.new(secret.encode(), str(CLOCK).encode() + b':' + raw, hashlib.sha256).hexdigest()
    return raw, f'ts={CLOCK};h1={digest}'


def send(store, payload):
    raw, sig = signed(payload)
    return store.apply_signed_event(raw, sig, secret=SECRET, offer=OFFER, now=CLOCK)


@pytest.fixture
def store(tmp_path):
    return PaddleLiveStore(tmp_path / 'live.sqlite3', price_id=PRICE, environment='live')


def bound(store):
    store.register_checkout(TXN, 'account-A')
    assert send(store, completion()) == 'bound'


def ledger(store):
    with store.connect() as db:
        return {table: db.execute('SELECT * FROM ' + table).fetchall()
                for table in ('checkouts', 'bindings', 'events', 'snapshots')}


def test_completion_only_binds_and_subscription_snapshot_drives_access(store):
    bound(store)
    assert store.access_for_account('account-A', now=NOW) is None
    assert send(store, event(2)) == 'applied'
    assert store.access_for_account('account-A', now=NOW).starter_access
    assert store.access_for_account('account-B', now=NOW) is None
    assert not store.access_for_account('account-A', now=datetime(2026, 10, 1, tzinfo=timezone.utc)).starter_access
    reopened = PaddleLiveStore(store.path, price_id=PRICE, environment='live')
    assert reopened.access_for_account('account-A', now=NOW).starter_access


def test_subscription_before_completion_retries_without_consuming_event(store):
    store.register_checkout(TXN, 'account-A')
    early = event(2)
    with pytest.raises(BillingConflict):
        send(store, early)
    assert ledger(store)['events'] == []
    assert send(store, completion()) == 'bound'
    assert send(store, early) == 'applied'


def test_unregistered_transaction_does_not_bind_from_custom_data(store):
    payload = completion(custom_data={'account_id': 'account-A'}, customer={'email': 'owner@example.test'})
    assert send(store, payload) == 'unregistered'
    assert ledger(store)['bindings'] == ledger(store)['events'] == []
    store.register_checkout(TXN, 'account-A')
    assert send(store, payload) == 'bound'


def test_notification_replay_cannot_overwrite_processed_event(store):
    bound(store)
    original = event(2)
    assert send(store, original) == 'applied'
    before = ledger(store)
    replay = deepcopy(original)
    replay['notification_id'] = 'ntf_replayed'
    replay['data']['status'] = 'canceled'
    assert send(store, replay) == 'duplicate'
    assert ledger(store) == before


def test_stale_active_event_cannot_reactivate_canceled_subscription(store):
    bound(store)
    canceled = snapshot()
    canceled.update(status='canceled', current_billing_period=None)
    assert send(store, event(3, data=canceled, day=25)) == 'applied'
    assert send(store, event(2, day=24)) == 'stale'
    assert not store.access_for_account('account-A', now=NOW).starter_access
    assert len(ledger(store)['events']) == 3


def test_equal_time_equivalent_event_acknowledged_but_conflict_rolls_back(store):
    bound(store)
    assert send(store, event(2)) == 'applied'
    equivalent = event(3)
    equivalent['data']['custom_data'] = {'irrelevant': 'not-stored'}
    equivalent['data']['items'][0]['price']['description'] = 'ignored metadata'
    assert send(store, equivalent) == 'equivalent'
    before = ledger(store)
    conflicting = snapshot()
    conflicting['scheduled_change'] = {'action': 'cancel', 'effective_at': '2026-10-01T00:00:00Z'}
    with pytest.raises(BillingConflict):
        send(store, event(4, data=conflicting))
    assert ledger(store) == before


def test_scheduled_cancel_preserves_remaining_access_without_extending_period(store):
    bound(store)
    data = snapshot()
    data['scheduled_change'] = {'action': 'cancel', 'effective_at': '2026-10-01T00:00:00Z'}
    assert send(store, event(2, data=data)) == 'applied'
    decision = store.access_for_account('account-A', now=NOW)
    assert decision.starter_access and decision.cancellation_pending
    assert not store.access_for_account('account-A', now=decision.access_until).starter_access


def test_concurrent_deliveries_apply_only_once(store):
    bound(store)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: send(store, event(2)), range(16)))
    assert results.count('applied') == 1
    assert results.count('duplicate') == 15
    assert len(ledger(store)['events']) == 2


def test_event_and_snapshot_rollback_together_on_database_failure(store):
    bound(store)
    with store.connect() as db:
        db.execute("CREATE TRIGGER reject_event BEFORE INSERT ON events BEGIN SELECT RAISE(ABORT, 'injected write failure'); END")
    before = ledger(store)
    with pytest.raises(sqlite3.IntegrityError):
        send(store, event(2))
    assert ledger(store) == before
    with store.connect() as db:
        db.execute('DROP TRIGGER reject_event')
    assert send(store, event(2)) == 'applied'


def test_ownership_cannot_be_reassigned(store):
    bound(store)
    before = ledger(store)
    assert store.register_checkout(TXN, 'account-A') == 'existing'
    with pytest.raises(BillingConflict):
        store.register_checkout(TXN, 'account-B')
    with pytest.raises(BillingConflict):
        store.register_checkout('txn_' + 'e' * 26, 'account-A')
    with pytest.raises(BillingConflict):
        send(store, completion(4, customer_id='ctm_' + 'e' * 26))
    assert ledger(store) == before
    store.register_checkout('txn_' + 'e' * 26, 'account-B')
    with pytest.raises(BillingConflict):
        send(store, completion(5, id='txn_' + 'e' * 26))
    assert store.access_for_account('account-B', now=NOW) is None


@pytest.mark.parametrize('patch', [
    {'items': []}, {'items': [{'price': {'id': PRICE}, 'quantity': True}]},
    {'items': [{'price': {'id': 'pri_' + 'e' * 26}, 'quantity': 1}]},
    {'status': 'draft'}, {'collection_mode': 'manual'}, {'subscription_id': 'invalid'},
])
def test_invalid_completions_cannot_create_binding(store, patch):
    store.register_checkout(TXN, 'account-A')
    before = ledger(store)
    with pytest.raises(ValueError):
        send(store, completion(**patch))
    assert ledger(store) == before


@pytest.mark.parametrize(('path', 'value'), [
    (('currency_code',), 'USD'),
    (('discount_id',), 'dsc_' + 'x' * 26),
    (('items', 0, 'price', 'product_id'), 'pro_' + 'x' * 26),
    (('details', 'totals', 'subtotal'), '1'),
    (('details', 'totals', 'credit'), '1'),
    (('details', 'totals', 'total'), '31900'),
    (('details', 'adjusted_totals', 'total'), '1'),
    (('details', 'line_items', 0, 'price_id'), 'pri_' + 'x' * 26),
    (('details', 'line_items', 0, 'totals', 'discount'), '1'),
    (('payments', 0, 'amount'), '1'),
])
def test_changed_financial_terms_never_bind_or_consume_event(store, path, value):
    store.register_checkout(TXN, 'account-A')
    payload = completion()
    target = payload['data']
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = value
    before = ledger(store)
    with pytest.raises(ValueError):
        send(store, payload)
    assert ledger(store) == before
    assert send(store, completion()) == 'bound'


def test_explicit_external_tax_offer_accepts_exact_tax_added_total():
    data = completion()['data']
    data['items'][0]['price']['tax_mode'] = 'external'
    for totals in (data['details']['totals'], data['details']['adjusted_totals'],
                   data['details']['line_items'][0]['totals'],
                   data['details']['line_items'][0]['unit_totals']):
        totals['total'] = '31900'
        totals['subtotal'] = '29000'
        totals['tax'] = '2900'
    data['details']['totals']['grand_total'] = '31900'
    data['details']['adjusted_totals']['grand_total'] = '31900'
    data['details']['totals']['grand_total_tax'] = '2900'
    data['details']['adjusted_totals']['grand_total_tax'] = '2900'
    data['payments'][0]['amount'] = '31900'
    validate_completed_transaction(data, Offer(PRICE, PRODUCT, 'external'))


def test_unsigned_tampered_expired_or_simulated_events_have_no_effect(store):
    before = ledger(store)
    raw, sig = signed(event())
    for body, signature, clock in [(raw + b' ', sig, CLOCK), (raw, '', CLOCK), (raw, sig, CLOCK + 6)]:
        with pytest.raises(HTTPException):
            store.apply_signed_event(body, signature, secret=SECRET, now=clock)
    simulated = event()
    simulated['event_id'] = 'ntfsimevt_' + 'a' * 26
    with pytest.raises(ValueError):
        send(store, simulated)
    with pytest.raises(ValueError):
        store.apply_signed_event(raw, sig, secret='', now=CLOCK)
    assert ledger(store) == before


def test_sandbox_database_and_wrong_live_config_are_rejected(tmp_path, store):
    path = tmp_path / 'sandbox.sqlite3'
    SandboxStore(path)
    before = path.read_bytes()
    with pytest.raises(ValueError):
        PaddleLiveStore(path, price_id=PRICE, environment='live')
    assert path.read_bytes() == before
    with pytest.raises(ValueError):
        PaddleLiveStore(store.path, price_id='pri_' + 'e' * 26, environment='live')
    with pytest.raises(ValueError):
        PaddleLiveStore(tmp_path / 'unused.sqlite3', price_id=PRICE, environment='sandbox')
    assert not (tmp_path / 'unused.sqlite3').exists()
    with pytest.raises(ValueError):
        store.register_checkout(TXN, 'sandbox:account-A')


def test_snapshot_contains_no_email_or_browser_ownership_metadata(store):
    bound(store)
    data = snapshot()
    data.update(custom_data={'account_id': 'victim'}, customer={'email': 'private@example.test'})
    assert send(store, event(2, data=data)) == 'applied'
    serialized = str(ledger(store))
    assert 'private@example.test' not in serialized and 'victim' not in serialized
    assert store.access_for_account('victim', now=NOW) is None


def test_malformed_inactive_period_is_rejected_without_consuming_event(store):
    bound(store)
    data = snapshot()
    data.update(status='canceled', current_billing_period={})
    before = ledger(store)
    with pytest.raises(ValueError):
        send(store, event(2, data=data))
    assert ledger(store) == before
    data['current_billing_period'] = None
    assert send(store, event(2, data=data)) == 'applied'


def test_unknown_customer_cannot_overwrite_existing_snapshot(store):
    bound(store)
    assert send(store, event(2)) == 'applied'
    before = ledger(store)
    data = snapshot()
    data['customer_id'] = 'ctm_' + 'z' * 26
    with pytest.raises(BillingConflict):
        send(store, event(3, data=data, day=25))
    assert ledger(store) == before
