from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import sqlite3

import pytest

from app.paddle_live_store import PaddleLiveStore, BillingConflict
from app.paddle_live_backup import create_backup, stage_restore, inspect_ledger, BackupError
from app.paddle_live_monitor import diagnose
from tests.test_paddle_live_store import store, bound, send, completion, event, ledger, PRICE, SUB, CUSTOMER, TXN, NOW
from tests.test_paddle_live_adjustments import adjustment
from tests.test_paddle_live_webhook import live, post, initialize
from tests.test_subscription import _files
from tests.test_paddle_live_monitor import Provider, CHECKED, codes

RENEWAL = 'txn_' + 'r' * 26


def renewal(n=10, **changes):
    data = {'id': RENEWAL, 'origin': 'subscription_recurring',
            'billing_period': {'starts_at': '2026-10-01T00:00:00Z', 'ends_at': '2026-11-01T00:00:00Z'}}
    data.update(changes)
    return completion(n, **data)


def records(store):
    with store.connect() as db:
        return {table: db.execute('SELECT * FROM ' + table + ' ORDER BY 1').fetchall()
                for table in ('live_renewals', 'live_renewal_receipts', 'events')}


def test_recurring_completion_preserves_binding_and_does_not_extend_access(store):
    bound(store)
    send(store, event(2))
    before = ledger(store)
    assert send(store, renewal()) == 'renewal_recorded'
    after = ledger(store)
    assert all(before[k] == after[k] for k in ('checkouts', 'bindings', 'snapshots'))
    row = records(store)['live_renewals'][0]
    assert row[:4] == (RENEWAL, SUB, CUSTOMER, 'account-A')
    assert json.loads(row[4])['total'] == '29000'
    later = datetime(2026, 10, 2, tzinfo=timezone.utc)
    assert not store.access_for_account('account-A', now=later).starter_access
    updated = event(3, day=26)
    updated['data']['current_billing_period'] = renewal()['data']['billing_period']
    send(store, updated)
    assert store.access_for_account('account-A', now=later).starter_access


def test_duplicate_event_and_distinct_event_for_same_transaction_record_one_payment(store):
    bound(store)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: send(store, renewal()), range(16)))
    assert results.count('renewal_recorded') == 1 and results.count('duplicate') == 15
    assert send(store, renewal(11)) == 'renewal_existing'
    rows = records(store)
    assert len(rows['live_renewals']) == 1 and len(rows['live_renewal_receipts']) == 2
    assert inspect_ledger(store.path, PRICE)['tables']['live_renewals'] == 1


def test_early_renewal_retries_after_binding_without_consuming_event(store):
    with pytest.raises(BillingConflict):
        send(store, renewal())
    assert not records(store)['events']
    bound(store)
    assert send(store, renewal()) == 'renewal_recorded'


@pytest.mark.parametrize('changes', [
    {'customer_id': 'ctm_' + 'z' * 26}, {'subscription_id': 'sub_' + 'z' * 26},
    {'id': TXN}, {'billing_period': None},
    {'billing_period': {'starts_at': '2026-11-01T00:00:00Z', 'ends_at': '2026-10-01T00:00:00Z'}},
    {'billing_period': {'starts_at': '2026-10-01', 'ends_at': '2026-11-01'}},
    {'billing_period': {'starts_at': '2026-10-01T00:00:00Z', 'ends_at': '2027-11-01T00:00:00Z'}},
    {'currency_code': 'USD'}, {'payments': []},
])
def test_invalid_owner_period_or_payment_is_atomic(store, changes):
    bound(store)
    before = records(store)
    with pytest.raises((BillingConflict, ValueError)):
        send(store, renewal(**changes))
    assert records(store) == before


@pytest.mark.parametrize('origin', ['subscription_charge', 'subscription_update', 'subscription_payment_method_change', None])
def test_nonrecurring_unknown_transactions_cannot_bind_or_become_renewals(store, origin):
    bound(store)
    before = records(store)
    if origin == 'subscription_payment_method_change':
        # Card updates now have their own zero-only receipt contract.
        with pytest.raises(ValueError, match='must not contain money'):
            send(store, renewal(origin=origin))
    else:
        assert send(store, renewal(origin=origin)) == 'unregistered'
    assert records(store) == before
    assert records(store)['live_renewals'] == []


def test_same_transaction_changed_period_rolls_back_new_receipt(store):
    bound(store)
    send(store, renewal())
    before = records(store)
    with pytest.raises(BillingConflict):
        send(store, renewal(11, billing_period={'starts_at': '2026-11-01T00:00:00Z', 'ends_at': '2026-12-01T00:00:00Z'}))
    assert records(store) == before


def test_different_event_ids_racing_same_transaction_store_one_payment(store):
    bound(store)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda n: send(store, renewal(n)), range(10, 26)))
    assert results.count('renewal_recorded') == 1 and results.count('renewal_existing') == 15
    assert len(records(store)['live_renewals']) == 1


def test_renewal_receipt_and_payment_roll_back_if_event_insert_fails(store):
    bound(store)
    with store.connect() as db:
        db.execute("CREATE TRIGGER reject_event BEFORE INSERT ON events BEGIN SELECT RAISE(ABORT, 'injected'); END")
    before = records(store)
    with pytest.raises(sqlite3.IntegrityError):
        send(store, renewal())
    assert records(store) == before


def test_late_completion_does_not_reactivate_canceled_or_release_dispute(store):
    bound(store)
    canceled = event(2)
    canceled['data'].update(status='canceled', current_billing_period=None)
    send(store, canceled)
    send(store, renewal())
    assert not store.access_for_account('account-A', now=NOW).starter_access
    send(store, event(3, day=25))
    assert send(store, adjustment(12, transaction_id=RENEWAL)) == 'adjustment_review'
    send(store, renewal(11))
    reopened = PaddleLiveStore(store.path, price_id=PRICE, environment='live', read_only=True)
    assert not reopened.access_for_account('account-A', now=NOW).starter_access


def test_renewal_and_initial_checkouts_cannot_reuse_transaction_ids(store):
    bound(store)
    send(store, renewal())
    with pytest.raises(BillingConflict):
        store.register_checkout(RENEWAL, 'account-B')
    other = 'txn_' + 'b' * 26
    store.register_checkout(other, 'account-B')
    with pytest.raises(BillingConflict):
        send(store, renewal(11, id=other))


def bind_other(store):
    txn, sub, customer = 'txn_' + 'b' * 26, 'sub_' + 'b' * 26, 'ctm_' + 'z' * 26
    store.register_checkout(txn, 'account-B')
    send(store, completion(3, id=txn, subscription_id=sub, customer_id=customer))
    return sub, customer


def test_renewal_and_adjustment_ownership_conflicts_both_arrival_orders(store):
    bound(store)
    sub, customer = bind_other(store)
    send(store, renewal())
    with pytest.raises(BillingConflict):
        send(store, adjustment(11, subscription_id=sub, customer_id=customer, transaction_id=RENEWAL))
    another = 'txn_' + 't' * 26
    send(store, adjustment(12, id='adj_' + 'z' * 26, subscription_id=sub, customer_id=customer, transaction_id=another))
    with pytest.raises(BillingConflict):
        send(store, renewal(13, id=another))
    with pytest.raises(BillingConflict):
        send(store, renewal(14, subscription_id=sub, customer_id=customer))


def test_legacy_read_only_schema_is_preserved_until_writable_migration(store):
    bound(store)
    with store.connect() as db:
        db.execute('DROP TABLE live_renewal_receipts')
        db.execute('DROP TABLE live_renewals')
    before = store.path.read_bytes()
    ro = PaddleLiveStore(store.path, price_id=PRICE, environment='live', read_only=True)
    assert ro.account_state('account-A', now=NOW) == (True, None)
    assert inspect_ledger(store.path, PRICE)['tables']['bindings'] == 1
    assert store.path.read_bytes() == before
    migrated = PaddleLiveStore(store.path, price_id=PRICE, environment='live')
    assert send(migrated, renewal()) == 'renewal_recorded'


def test_renewal_backup_restore_keeps_dedupe_and_exact_payment_terms(store):
    bound(store)
    send(store, renewal())
    send(store, renewal(11))
    saved = create_backup(store.path, store.path.parent / 'renewal.zip', price_id=PRICE)
    directory = store.path.parent / 'restored'
    stage_restore(saved['archive'], directory, price_id=PRICE, expected_sha256=saved['archive_sha256'])
    restored = PaddleLiveStore(directory / 'paddle_live.sqlite3', price_id=PRICE, environment='live')
    assert records(restored) == records(store)
    assert send(restored, renewal()) == 'duplicate'


@pytest.mark.parametrize('damage', ['missing-payment', 'missing-receipt', 'changed-terms', 'cross-account', 'missing-table'])
def test_backup_verifier_rejects_incomplete_or_conflicting_renewal_evidence(store, damage):
    bound(store)
    send(store, renewal())
    with store.connect() as db:
        if damage == 'missing-payment':
            db.execute('DELETE FROM live_renewals')
        elif damage == 'missing-receipt':
            db.execute('DELETE FROM live_renewal_receipts')
        elif damage == 'changed-terms':
            db.execute("UPDATE live_renewals SET terms_digest='bad'")
        elif damage == 'cross-account':
            db.execute("UPDATE live_renewals SET account_id='account-B'")
        else:
            db.execute('DROP TABLE live_renewals')
    with pytest.raises(BackupError):
        inspect_ledger(store.path, PRICE)


def test_monitor_reports_missing_renewal_then_clears_after_signed_receipt(store):
    bound(store)
    send(store, event(2))
    provider = Provider([renewal()])
    before = diagnose(store.path, price_id=PRICE, client=provider, now=CHECKED)
    assert 'provider_events_missing_locally' in codes(before)
    send(store, renewal())
    after = diagnose(store.path, price_id=PRICE, client=provider, now=CHECKED)
    assert codes(after) == {'verified_backup_not_configured'}
    assert after['provider']['renewal'] == 1


def test_http_recurring_event_is_acknowledged_and_users_json_unchanged(live, monkeypatch):
    client, path = live
    users, history, usage = _files(path, monkeypatch)
    initialize(client)
    post(client, event(2))
    before = {p: p.read_bytes() for p in (users, history, usage)}
    response = post(client, renewal())
    assert response.status_code == 200 and response.json()['result'] == 'renewal_recorded'
    assert post(client, renewal()).json()['result'] == 'duplicate'
    assert {p: p.read_bytes() for p in before} == before


def test_renewal_does_not_store_raw_customer_metadata(store):
    bound(store)
    send(store, renewal(custom_data={'account_id': 'PRIVATE-CUSTOMER'},
                        address={'secret': 'PRIVATE-ADDRESS'}, invoice_number='PRIVATE-INVOICE'))
    assert 'PRIVATE-' not in str(records(store))
