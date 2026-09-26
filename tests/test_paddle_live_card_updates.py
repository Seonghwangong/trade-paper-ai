from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import timedelta
import json

import pytest

from app.paddle_live_store import PaddleLiveStore, BillingConflict
from app.paddle_live_backup import create_backup, stage_restore, inspect_ledger, BackupError
from app.paddle_live_monitor import diagnose
from tests.test_paddle_live_store import store, bound, send, completion, event, ledger, PRICE, SUB, CUSTOMER, TXN, NOW
from tests.test_paddle_live_adjustments import adjustment
from tests.test_paddle_live_renewals import renewal, bind_other
from tests.test_paddle_live_monitor import Provider, CHECKED, codes
from tests.test_paddle_live_webhook import live, post, initialize
from tests.test_subscription import _files
from tests.test_paddle_live_replay import enabled, Provider as ReplayProvider, run
from app import paddle_live_replay as replay

CARD = 'txn_' + 'k' * 26


def card(n=40, **changes):
    payload = completion(n, id=CARD, origin='subscription_payment_method_change', payments=[])
    data = payload['data']
    for key in ('subtotal', 'discount', 'tax', 'total', 'grand_total', 'grand_total_tax', 'credit', 'credit_to_balance', 'balance'):
        data['details']['totals'][key] = '0'
    for key in data['details']['adjusted_totals']:
        if key != 'currency_code': data['details']['adjusted_totals'][key] = '0'
    for name in ('totals', 'unit_totals'):
        data['details']['line_items'][0][name] = {k: '0' for k in ('subtotal', 'discount', 'tax', 'total')}
    data['items'][0]['proration'] = {'rate': '0'}
    data['billing_period']['ends_at'] = data['billing_period']['starts_at']
    data.update(changes)
    return payload


def records(store):
    with store.connect() as db:
        return {name: db.execute('SELECT * FROM ' + name + ' ORDER BY 1').fetchall()
                for name in ('live_payment_methods', 'live_payment_method_receipts', 'events')}


def test_card_update_never_becomes_paid_period_or_changes_access(store):
    bound(store); send(store, event(2))
    before = ledger(store)
    assert send(store, card()) == 'payment_method_recorded'
    after = ledger(store)
    assert all(before[k] == after[k] for k in ('checkouts', 'bindings', 'snapshots'))
    assert store.access_for_account('account-A', now=NOW).starter_access
    future = event(3, day=25)
    future['data']['current_billing_period'] = renewal()['data']['billing_period']
    send(store, future)
    assert not store.access_for_account('account-A', now=NOW + timedelta(days=10)).starter_access
    with store.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM live_renewals').fetchone() == (0,)
        assert db.execute('SELECT COUNT(*) FROM live_initial_periods').fetchone() == (1,)


def test_duplicate_and_distinct_events_keep_one_nonfinancial_record(store):
    bound(store)
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda _: send(store, card()), range(12)))
    assert results.count('payment_method_recorded') == 1 and results.count('duplicate') == 11
    assert send(store, card(41)) == 'payment_method_existing'
    assert len(records(store)['live_payment_methods']) == 1
    assert inspect_ledger(store.path, PRICE)['tables']['live_payment_method_receipts'] == 2


def test_unbound_early_event_is_not_consumed_and_retries_after_binding(store):
    with pytest.raises(BillingConflict): send(store, card())
    assert not records(store)['events']
    bound(store)
    assert send(store, card()) == 'payment_method_recorded'


@pytest.mark.parametrize('changes', [
    {'customer_id': 'ctm_' + 'z'*26}, {'subscription_id': 'sub_' + 'z'*26},
    {'id': TXN}, {'status': 'ready'}, {'origin': 'subscription_recurring'},
    {'currency_code': 'USD'}, {'discount_id': 'dsc_fake'}, {'collection_mode': 'manual'},
    {'items': []}, {'details': None}, {'payments': [{'amount': '1'}]}, {'payments': None},
])
def test_invalid_or_unowned_card_events_leave_no_receipt(store, changes):
    bound(store); before = records(store)
    with pytest.raises((ValueError, BillingConflict)): send(store, card(**changes))
    assert records(store) == before


@pytest.mark.parametrize('part', ['subtotal', 'tax', 'total', 'grand_total', 'grand_total_tax', 'discount', 'credit', 'credit_to_balance', 'balance'])
def test_every_nonzero_total_is_rejected(store, part):
    bound(store); payload = card(); payload['data']['details']['totals'][part] = '1'
    with pytest.raises(ValueError): send(store, payload)
    assert not records(store)['live_payment_methods']


@pytest.mark.parametrize('part', ['line', 'unit', 'quantity', 'proration', 'price', 'adjusted'])
def test_calculated_item_and_catalog_mismatches_rejected(store, part):
    bound(store); p = card(); d = p['data']
    if part == 'line': d['details']['line_items'][0]['totals']['total'] = '1'
    if part == 'unit': d['details']['line_items'][0]['unit_totals']['total'] = '1'
    if part == 'quantity': d['items'][0]['quantity'] = True
    if part == 'proration': d['items'][0]['proration']['rate'] = '1'
    if part == 'price': d['items'][0]['price']['unit_price']['amount'] = '1'
    if part == 'adjusted': d['details']['adjusted_totals']['total'] = '1'
    with pytest.raises(ValueError): send(store, p)


def test_transaction_identity_cannot_switch_to_purchase_or_renewal(store):
    bound(store); send(store, card())
    with pytest.raises(BillingConflict): store.register_checkout(CARD, 'account-B')
    with pytest.raises(BillingConflict): send(store, renewal(41, id=CARD))
    send(store, renewal())
    with pytest.raises(BillingConflict): send(store, card(42, id=renewal()['data']['id']))


def test_card_receipt_cannot_release_refund_hold_or_revive_cancellation(store):
    bound(store); send(store, event(2)); send(store, adjustment(10))
    send(store, card())
    assert not store.access_for_account('account-A', now=NOW).starter_access
    canceled = event(3, day=25); canceled['data'].update(status='canceled', current_billing_period=None)
    send(store, canceled); send(store, card(41))
    assert not store.access_for_account('account-A', now=NOW).starter_access


def test_backup_restore_retains_dedupe_without_entitlements(store):
    bound(store); send(store, card()); send(store, card(41))
    saved = create_backup(store.path, store.path.parent/'card.zip', price_id=PRICE)
    target = store.path.parent/'restored'
    stage_restore(saved['archive'], target, price_id=PRICE, expected_sha256=saved['archive_sha256'])
    restored = PaddleLiveStore(target/'paddle_live.sqlite3', price_id=PRICE, environment='live')
    assert records(restored) == records(store)
    assert send(restored, card()) == 'duplicate'
    assert restored.access_for_account('account-A', now=NOW) is None


@pytest.mark.parametrize('damage', ['record', 'receipt', 'event', 'result', 'terms', 'account', 'table'])
def test_backup_rejects_missing_or_conflicting_receipt_evidence(store, damage):
    bound(store); send(store, card())
    with store.connect() as db:
        if damage == 'record': db.execute('DELETE FROM live_payment_methods')
        if damage == 'receipt': db.execute('DELETE FROM live_payment_method_receipts')
        if damage == 'event': db.execute('DELETE FROM events WHERE event_id=?', (card()['event_id'],))
        if damage == 'result': db.execute("UPDATE events SET result='bound' WHERE event_id=?", (card()['event_id'],))
        if damage == 'terms': db.execute("UPDATE live_payment_methods SET terms='{}'")
        if damage == 'account': db.execute("UPDATE live_payment_methods SET account_id='account-B'")
        if damage == 'table': db.execute('DROP TABLE live_payment_method_receipts')
    with pytest.raises((ValueError, BackupError)): inspect_ledger(store.path, PRICE)


def test_legacy_read_only_schema_migrates_only_on_writable_open(store):
    bound(store)
    with store.connect() as db:
        db.execute('DROP TABLE live_payment_method_receipts'); db.execute('DROP TABLE live_payment_methods')
    before = store.path.read_bytes()
    ro = PaddleLiveStore(store.path, price_id=PRICE, environment='live', read_only=True)
    assert ro.account_state('account-A', now=NOW) == (True, None)
    inspect_ledger(store.path, PRICE)
    assert store.path.read_bytes() == before
    writable = PaddleLiveStore(store.path, price_id=PRICE, environment='live')
    assert send(writable, card()) == 'payment_method_recorded'


def test_monitor_tracks_missing_receipts_and_changed_provider_amount(store):
    bound(store); send(store, event(2))
    provider = Provider([card()])
    result = diagnose(store.path, price_id=PRICE, client=provider, now=CHECKED)
    assert 'provider_events_missing_locally' in codes(result)
    send(store, card())
    result = diagnose(store.path, price_id=PRICE, client=provider, now=CHECKED)
    assert result['provider']['payment_method'] == 1
    assert codes(result) == {'verified_backup_not_configured'}
    provider.rows[0]['data']['details']['totals']['balance'] = '1'
    assert 'provider_receipt_conflict' in codes(diagnose(store.path, price_id=PRICE, client=provider, now=CHECKED))


def test_replay_requests_redelivery_without_applying_unsigned_card_payload(enabled):
    store, _ = enabled; provider = ReplayProvider(card()); pair = (store, provider)
    preview = run(pair)
    assert not records(store)['live_payment_methods'] and provider.posts == 0
    assert run(pair, expected=preview['digest'])['result'] == 'awaiting_signed_delivery'
    assert not records(store)['live_payment_methods'] and provider.posts == 1
    assert send(store, provider.notice['payload']) == 'payment_method_recorded'
    assert replay.status(store, card()['event_id'])['result'] == 'received'
    assert run(pair)['result'] == 'already_received' and provider.posts == 1
    inspect_ledger(store.path, PRICE)


def test_signed_http_card_update_preserves_json_and_access(live, monkeypatch):
    client, path = live
    files = _files(path, monkeypatch)
    initialize(client); post(client, event(2))
    before = {p: p.read_bytes() for p in files}
    assert post(client, card(), secret='incorrect').status_code == 401
    response = post(client, card())
    assert response.status_code == 200 and response.json()['result'] == 'payment_method_recorded'
    assert {p: p.read_bytes() for p in files} == before
    assert post(client, card()).json()['result'] == 'duplicate'


def test_receipts_do_not_store_card_details_or_custom_data(store):
    bound(store)
    send(store, card(custom_data={'account_id': 'PRIVATE-ACCOUNT'}, customer={'email': 'PRIVATE-EMAIL'},
                     payments=[{'amount': '0', 'card': {'last4': 'PRIVATE-CARD'}}]))
    assert 'PRIVATE' not in str(records(store))


def test_adjustment_ownership_conflicts_both_arrival_orders(store):
    bound(store); sub, customer = bind_other(store)
    send(store, card())
    with pytest.raises(BillingConflict):
        send(store, adjustment(50, subscription_id=sub, customer_id=customer, transaction_id=CARD))
    other = 'txn_' + 'w'*26
    send(store, adjustment(51, subscription_id=sub, customer_id=customer, transaction_id=other))
    with pytest.raises(BillingConflict): send(store, card(52, id=other))
