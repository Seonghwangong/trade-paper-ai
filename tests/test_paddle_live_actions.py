from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from io import BytesIO
import json
import sqlite3
from threading import Event
from urllib.error import HTTPError, URLError

import pytest
from fastapi import HTTPException

from app import paddle_live_actions as actions
from app.paddle_live_store import BillingConflict, PaddleLiveStore
from tests.test_paddle_live_store import store, bound, send, event, ledger, PRICE, TXN, SUB, NOW, snapshot

KEY = 'pdl_live_apikey_synthetic_test_only'


def transaction():
    return {'id': TXN, 'status': 'draft', 'collection_mode': 'automatic',
            'items': [{'price': {'id': PRICE}, 'quantity': 1}]}


class Client:
    def __init__(self):
        self.creates = 0
        self.cancels = 0
        self.current = snapshot()
        self.checkout_data = transaction()
        self.create_error = self.cancel_error = None

    def create_checkout(self, price):
        assert price == PRICE
        self.creates += 1
        if self.create_error:
            raise self.create_error
        return deepcopy(self.checkout_data)

    def transaction(self, txn):
        assert txn == TXN
        return deepcopy(self.checkout_data)

    def subscription(self, sub):
        assert sub == SUB
        return deepcopy(self.current)

    def cancel_at_period_end(self, sub):
        assert sub == SUB
        self.cancels += 1
        if self.cancel_error:
            raise self.cancel_error
        self.current['scheduled_change'] = {
            'action': 'cancel', 'effective_at': self.current['current_billing_period']['ends_at']}
        return deepcopy(self.current)


def test_checkout_reuses_only_registered_unpaid_transaction_and_isolates_account(store):
    client = Client()
    service = actions.LiveActions(store, client)
    assert service.checkout('A', now=1000) == TXN
    assert service.checkout('A', now=1001) == TXN
    assert client.creates == 1
    assert store.account_state('A', now=NOW) == (True, None)
    assert store.account_state('B', now=NOW) == (False, None)
    client.checkout_data['status'] = 'completed'
    with pytest.raises(actions.ProviderUnavailable):
        service.checkout('A', now=1002)
    assert client.creates == 1


@pytest.mark.parametrize('later', [999, 1901])
def test_expired_or_clock_rollback_checkout_never_creates_again(store, later):
    client = Client()
    service = actions.LiveActions(store, client)
    service.checkout('A', now=1000)
    with pytest.raises(BillingConflict):
        service.checkout('A', now=later)
    assert client.creates == 1


def test_timeout_survives_restart_and_protects_account_without_transaction(store):
    client = Client()
    client.create_error = actions.ProviderUnavailable('synthetic timeout')
    with pytest.raises(actions.ProviderUnavailable):
        actions.LiveActions(store, client).checkout('A', now=1000)
    reopened = PaddleLiveStore(store.path, price_id=PRICE, environment='live')
    assert reopened.account_state('A', now=NOW) == (True, None)
    with pytest.raises(BillingConflict):
        actions.LiveActions(reopened, client).checkout('A', now=1001)
    assert client.creates == 1
    assert ledger(store)['checkouts'] == []


def test_concurrent_checkout_commits_intent_before_one_provider_call(store):
    entered, release = Event(), Event()
    client = Client()
    original = client.create_checkout
    def create(price):
        entered.set()
        assert release.wait(5)
        return original(price)
    client.create_checkout = create
    service = actions.LiveActions(store, client)
    with ThreadPoolExecutor(max_workers=8) as pool:
        first = pool.submit(service.checkout, 'A', now=1000)
        try:
            assert entered.wait(5)
            others = [pool.submit(service.checkout, 'A', now=1000) for _ in range(7)]
            for future in others:
                with pytest.raises(BillingConflict):
                    future.result(timeout=5)
        finally:
            release.set()
        assert first.result(timeout=5) == TXN
    assert client.creates == 1


def test_storage_failure_after_provider_response_does_not_free_reservation(store):
    with store.connect() as db:
        db.execute("CREATE TRIGGER fail_checkout BEFORE INSERT ON checkouts BEGIN SELECT RAISE(ABORT, 'disk failure'); END")
    client = Client()
    service = actions.LiveActions(store, client)
    with pytest.raises(sqlite3.Error):
        service.checkout('A', now=1000)
    assert ledger(store)['checkouts'] == []
    with store.connect() as db:
        assert db.execute('SELECT target_id, result FROM live_operations').fetchall() == [(None, None)]
    with pytest.raises(BillingConflict):
        service.checkout('A', now=1001)
    assert client.creates == 1


@pytest.mark.parametrize('change', [
    {'id': 'bad'}, {'status': 'paid'}, {'collection_mode': 'manual'},
    {'items': []}, {'items': [{'price': {'id': PRICE}, 'quantity': True}]},
    {'items': [{'price': {'id': 'pri_' + 'z'*26}, 'quantity': 1}]},
])
def test_unexpected_checkout_response_cannot_register_or_retry(store, change):
    client = Client()
    client.checkout_data.update(change)
    service = actions.LiveActions(store, client)
    with pytest.raises(actions.ProviderUnavailable):
        service.checkout('A', now=1000)
    assert ledger(store)['checkouts'] == []
    with pytest.raises(BillingConflict):
        service.checkout('A', now=1001)
    assert client.creates == 1


def test_bound_and_legacy_registered_accounts_cannot_create_second_checkout(store):
    client = Client()
    bound(store)
    store.register_checkout('txn_' + 'e'*26, 'B')
    for account in ('account-A', 'B'):
        with pytest.raises(BillingConflict):
            actions.LiveActions(store, client).checkout(account)
    assert client.creates == 0


def test_cancel_uses_bound_identity_and_webhook_alone_updates_entitlement(store):
    bound(store)
    send(store, event(2))
    before = ledger(store)
    client = Client()
    service = actions.LiveActions(store, client)
    with pytest.raises(BillingConflict):
        service.cancel('account-B', now=NOW)
    assert service.cancel('account-A', now=NOW) == 'scheduled'
    assert service.cancel('account-A', now=NOW) == 'scheduled'
    assert client.cancels == 1
    assert ledger(store) == before
    assert store.access_for_account('account-A', now=NOW).starter_access
    assert not store.access_for_account('account-A', now=NOW).cancellation_pending
    send(store, event(3, data=client.current, day=25))
    assert store.access_for_account('account-A', now=NOW).cancellation_pending


def test_timed_out_cancel_reconciles_by_get_without_resending_post(store):
    bound(store)
    client = Client()
    client.cancel_error = actions.ProviderUnavailable('synthetic timeout')
    service = actions.LiveActions(store, client)
    with pytest.raises(actions.ProviderUnavailable):
        service.cancel('account-A', now=NOW)
    with pytest.raises(BillingConflict):
        service.cancel('account-A', now=NOW)
    client.current['scheduled_change'] = {'action': 'cancel', 'effective_at': '2026-10-01T00:00:00Z'}
    assert service.cancel('account-A', now=NOW) == 'scheduled'
    assert client.cancels == 1


@pytest.mark.parametrize('change', [
    {'id': 'sub_' + 'z'*26}, {'customer_id': 'ctm_' + 'z'*26},
    {'status': 'past_due'}, {'status': 'paused'}, {'status': 'trialing'},
    {'scheduled_change': {'action': 'pause', 'effective_at': '2026-10-01T00:00:00Z'}},
    {'current_billing_period': {'starts_at': '2026-08-01T00:00:00Z', 'ends_at': '2026-09-01T00:00:00Z'}},
])
def test_cancel_rejects_mismatch_or_unsupported_state_without_mutation(store, change):
    bound(store)
    client = Client()
    client.current.update(change)
    with pytest.raises((actions.ProviderUnavailable, BillingConflict)):
        actions.LiveActions(store, client).cancel('account-A', now=NOW)
    assert client.cancels == 0


def test_already_canceled_is_reconciled_without_post(store):
    bound(store)
    client = Client()
    client.current.update(status='canceled', current_billing_period=None)
    assert actions.LiveActions(store, client).cancel('account-A', now=NOW) == 'canceled'
    assert client.cancels == 0


def test_concurrent_cancel_never_posts_twice(store):
    bound(store)
    entered, release = Event(), Event()
    client = Client()
    original = client.cancel_at_period_end
    def cancel(sub):
        entered.set()
        assert release.wait(5)
        return original(sub)
    client.cancel_at_period_end = cancel
    service = actions.LiveActions(store, client)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(service.cancel, 'account-A', now=NOW)
        try:
            assert entered.wait(5)
            with pytest.raises(BillingConflict):
                service.cancel('account-A', now=NOW)
        finally:
            release.set()
        assert first.result(timeout=5) == 'scheduled'
    assert client.cancels == 1


def test_cancel_unexpected_ack_remains_reserved_and_does_not_change_access(store):
    bound(store)
    send(store, event(2))
    before = ledger(store)
    client = Client()
    def bad_cancel(sub):
        client.cancels += 1
        result = snapshot()
        result['scheduled_change'] = {'action': 'cancel', 'effective_at': '2026-09-25T00:00:00Z'}
        return result
    client.cancel_at_period_end = bad_cancel
    service = actions.LiveActions(store, client)
    with pytest.raises(actions.ProviderUnavailable):
        service.cancel('account-A', now=NOW)
    with pytest.raises(BillingConflict):
        service.cancel('account-A', now=NOW)
    assert ledger(store) == before
    assert client.cancels == 1


def test_pending_checkout_is_guarded_through_runtime(store, monkeypatch):
    from app import paddle_live_runtime as runtime
    monkeypatch.setattr(runtime, 'data_path', lambda name: store.path)
    monkeypatch.setenv('TRADE_PAPER_PADDLE_LIVE_PRICE_ID', PRICE)
    monkeypatch.delenv('TRADE_PAPER_PADDLE_SANDBOX_PRICE_ID', raising=False)
    monkeypatch.delenv('TRADE_PAPER_PADDLE_LIVE_ACCESS', raising=False)
    client = Client()
    client.create_error = actions.ProviderUnavailable('timeout')
    with pytest.raises(actions.ProviderUnavailable):
        actions.LiveActions(store, client).checkout('A')
    assert runtime.is_managed('A')
    assert runtime.subscription_override('A', {'plan': 'Starter'}, now=NOW) == {'plan': 'Free', 'status': 'Active'}


def test_cancel_factory_remains_available_when_sales_or_access_disabled(store, monkeypatch):
    monkeypatch.setenv('TRADE_PAPER_PADDLE_LIVE_CANCEL', '1')
    monkeypatch.setenv('TRADE_PAPER_PADDLE_LIVE_API_KEY', KEY)
    for name in ('CHECKOUT', 'WEBHOOK', 'ACCESS', 'WEBHOOK_SECRET'):
        monkeypatch.delenv('TRADE_PAPER_PADDLE_LIVE_' + name, raising=False)
    monkeypatch.setattr(actions.runtime, 'store', lambda: store)
    assert isinstance(actions.configured_service('cancel'), actions.LiveActions)


def test_old_schema_read_only_and_additive_migration_preserve_ledger(store):
    bound(store)
    before = ledger(store)
    with store.connect() as db:
        db.execute('DROP TABLE live_operations')
    old = PaddleLiveStore(store.path, price_id=PRICE, environment='live', read_only=True)
    assert old.account_state('account-A', now=NOW) == (True, None)
    assert old.account_state('B', now=NOW) == (False, None)
    migrated = PaddleLiveStore(store.path, price_id=PRICE, environment='live')
    assert ledger(migrated) == before


def test_transport_fixed_live_host_payload_and_no_redirect(monkeypatch):
    seen = []
    class Response(BytesIO):
        status = 200
    class Opener:
        def open(self, req, timeout):
            seen.append((req, timeout))
            return Response(json.dumps({'data': transaction()}).encode())
    def opener(handler):
        assert isinstance(handler, actions.NoRedirect)
        assert handler.redirect_request(None, None, 302, None, None, 'https://attacker.test') is None
        return Opener()
    monkeypatch.setattr(actions, 'build_opener', opener)
    client = actions.LiveClient(KEY)
    client.create_checkout(PRICE)
    client.transaction(TXN)
    client.subscription(SUB)
    client.cancel_at_period_end(SUB)
    assert [r.full_url for r, _ in seen] == [actions.API + '/transactions',
        actions.API + '/transactions/' + TXN, actions.API + '/subscriptions/' + SUB,
        actions.API + '/subscriptions/' + SUB + '/cancel']
    assert [r.get_method() for r, _ in seen] == ['POST', 'GET', 'GET', 'POST']
    assert json.loads(seen[0][0].data) == {'items': [{'price_id': PRICE, 'quantity': 1}], 'collection_mode': 'automatic'}
    assert json.loads(seen[3][0].data) == {'effective_from': 'next_billing_period'}
    assert all(timeout == 15 and r.get_header('Authorization') == 'Bearer ' + KEY for r, timeout in seen)
    with pytest.raises(ValueError):
        client.subscription('../another-host')


@pytest.mark.parametrize('bad', [b'not json', b'{"data": []}', b'{}', b'x'*262145,
                                URLError(KEY), HTTPError('https://api.paddle.com', 401, KEY, {}, BytesIO())],
                         ids=['malformed', 'wrong-type', 'missing-data', 'oversized', 'network', 'http'])
def test_transport_errors_bounded_and_redacted(monkeypatch, bad):
    class Response(BytesIO):
        status = 200
    class Opener:
        def open(self, *args, **kwargs):
            if isinstance(bad, Exception):
                raise bad
            return Response(bad)
    monkeypatch.setattr(actions, 'build_opener', lambda *args: Opener())
    with pytest.raises(actions.ProviderUnavailable) as caught:
        actions.LiveClient(KEY).create_checkout(PRICE)
    assert KEY not in str(caught.value)


@pytest.mark.parametrize('key', ['', 'legacy_key', 'pdl_sdbx_apikey_test', 'pdl_live_apikey_x\nInjected: secret'])
def test_client_rejects_wrong_environment_or_header_injection(key):
    with pytest.raises(ValueError):
        actions.LiveClient(key)


def test_factory_off_missing_config_and_sandbox_key_never_open_store(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('Factory must not open ledger before validating configuration')
    monkeypatch.setattr(actions.runtime, 'store', forbidden)
    monkeypatch.delenv('TRADE_PAPER_PADDLE_LIVE_CHECKOUT', raising=False)
    with pytest.raises(HTTPException) as caught:
        actions.configured_service('checkout')
    assert caught.value.status_code == 404
    monkeypatch.setenv('TRADE_PAPER_PADDLE_LIVE_CHECKOUT', '1')
    monkeypatch.delenv('TRADE_PAPER_PADDLE_LIVE_ACCESS', raising=False)
    with pytest.raises(actions.runtime.LiveBillingUnavailable):
        actions.configured_service('checkout')
    monkeypatch.setenv('TRADE_PAPER_PADDLE_LIVE_ACCESS', '1')
    monkeypatch.setenv('TRADE_PAPER_PADDLE_LIVE_WEBHOOK', '1')
    monkeypatch.setenv('TRADE_PAPER_PADDLE_LIVE_WEBHOOK_SECRET', 'synthetic-live')
    monkeypatch.setenv('TRADE_PAPER_PADDLE_LIVE_API_KEY', 'pdl_sdbx_apikey_synthetic')
    with pytest.raises(actions.runtime.LiveBillingUnavailable):
        actions.configured_service('checkout')
