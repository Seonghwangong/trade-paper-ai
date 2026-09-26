from copy import deepcopy
from datetime import datetime
import json
import re

import pytest

from app import auth, subscription
from app import paddle_live_checkout as buy, paddle_live_runtime as runtime
from app.paddle_live_actions import LiveActions, BillingConflict
from app.paddle_live_offer import expected_offer, validate_price, validate_transaction_offer
from tests.test_paddle_live_actions import Client, price, transaction, PRODUCT, KEY, offer_config
from tests.test_paddle_live_store import store, PRICE, TXN, send, completion, event, NOW
from tests.test_paddle_live_manage import http, ORIGIN
from tests.test_subscription import _files


@pytest.fixture
def ready(store, tmp_path, monkeypatch, offer_config):
    _files(tmp_path, monkeypatch)
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW
    monkeypatch.setattr(buy.manage, 'datetime', Clock)
    monkeypatch.setattr(runtime, 'data_path', lambda name: store.path)
    for flag in ('CHECKOUT', 'MANAGE', 'CANCEL', 'ACCESS', 'WEBHOOK'):
        monkeypatch.setenv('TRADE_PAPER_PADDLE_LIVE_' + flag, '1')
    for key, value in {'PRICE_ID': PRICE, 'PILOT_ACCOUNTS': 'A,B', 'API_KEY': KEY,
                       'WEBHOOK_SECRET': 'synthetic-live-secret', 'CLIENT_TOKEN': 'live_publictest'}.items():
        monkeypatch.setenv('TRADE_PAPER_PADDLE_LIVE_' + key, value)
    monkeypatch.delenv('TRADE_PAPER_PADDLE_SANDBOX_PRICE_ID', raising=False)
    monkeypatch.delenv('TRADE_PAPER_PADDLE_SANDBOX_WEBHOOK_SECRET', raising=False)
    monkeypatch.setenv('TRADE_PAPER_PUBLIC_BASE_URL', ORIGIN)
    user = {'account_id': 'A', 'role': 'Owner'}
    monkeypatch.setattr(auth, 'current_user', lambda request: user)
    monkeypatch.setattr(auth, 'company_setup_complete', lambda *args: True)
    client = Client()
    monkeypatch.setattr(buy, 'LiveClient', lambda key: client)
    service = LiveActions(store, client)
    monkeypatch.setattr(buy, 'configured_service', lambda operation: service)
    return store, client, user


def headers():
    return {'Origin': ORIGIN, 'X-Billing-CSRF': buy.manage.csrf_token('A', buy.purpose(expected_offer(PRICE))),
            'X-Billing-Confirm': 'starter-monthly', 'Sec-Fetch-Site': 'same-origin'}


@pytest.mark.parametrize('patch', [
    {'id': 'pri_' + 'z'*26}, {'product_id': 'pro_' + 'z'*26}, {'status': 'archived'}, {'type': 'custom'},
    {'unit_price': {'amount': '2900', 'currency_code': 'KRW'}},
    {'unit_price': {'amount': '29000', 'currency_code': 'USD'}},
    {'unit_price': {'amount': 29000, 'currency_code': 'KRW'}},
    {'tax_mode': 'account_setting'}, {'tax_mode': 'external'},
    {'unit_price_overrides': [{'country_codes': ['US'], 'unit_price': {'amount': '10', 'currency_code': 'USD'}}]},
    {'trial_period': {'interval': 'day', 'frequency': 7}},
    {'billing_cycle': None}, {'billing_cycle': {'interval': 'year', 'frequency': 1}},
    {'billing_cycle': {'interval': 'month', 'frequency': True}},
    {'quantity': {'minimum': 1, 'maximum': 100}}, {'quantity': {'minimum': True, 'maximum': 1}},
    {'product': {'id': PRODUCT, 'status': 'archived', 'type': 'standard'}}, {'product': None},
])
def test_bad_catalog_never_creates_intent_or_transaction(ready, patch):
    store, client, _ = ready
    bad = price(); bad.update(patch)
    client.price = lambda price_id: bad
    assert http(buy.PATH).status == 503
    assert http(buy.PATH, 'POST', headers()).status == 503
    assert client.creates == 0
    with store.connect() as db:
        assert db.execute('SELECT * FROM live_operations').fetchall() == []
        assert db.execute('SELECT * FROM checkouts').fetchall() == []


@pytest.mark.parametrize('patch', [
    {'currency_code': 'USD'}, {'discount_id': 'dsc_fake'}, {'subscription_id': 'sub_existing'},
    {'items': [{'price': {**price(), 'unit_price': {'amount': '999', 'currency_code': 'KRW'}}, 'quantity': 1}]},
])
def test_terms_changed_between_preflight_and_response_block_release_and_retry(ready, patch):
    store, client, _ = ready
    client.checkout_data.update(patch)
    assert http(buy.PATH, 'POST', headers()).status == 503
    assert client.creates == 1
    assert http(buy.PATH + '/status').json() == {'phase': 'review', 'can_open': False}
    assert http(buy.PATH, 'POST', headers()).status == 409
    assert client.creates == 1
    with store.connect() as db:
        assert db.execute('SELECT * FROM checkouts').fetchall() == []


def test_page_discloses_terms_only_public_token_and_reads_without_mutation(ready):
    store, client, _ = ready
    response = http(buy.PATH)
    assert response.status == 200
    for text in ('₩29,000 / month', 'Tax included.', 'Renews monthly', 'No free trial', 'live_publictest'):
        assert text in response.text
    for private in (KEY, PRICE, PRODUCT, 'synthetic-live-secret'):
        assert private not in response.text
    assert response.headers['cache-control'] == 'no-store'
    assert "Paddle.Environment.set('sandbox')" not in response.text
    assert 'transactionId:data.transaction_id' in response.text
    assert client.creates == 0
    with store.connect() as db:
        assert db.execute('SELECT * FROM live_operations').fetchall() == []


def test_transaction_and_signed_confirmation_flow_uses_only_session_account(ready):
    store, client, _ = ready
    assert http(buy.PATH + '/status').json() == {'phase': 'ready', 'can_open': True}
    before = subscription.USERS_FILE.read_bytes()
    response = http(buy.PATH, 'POST', headers(), b'{"account_id":"B","price_id":"wrong"}', b'account_id=B')
    assert response.status == 200 and response.json()['transaction_id'] == TXN
    assert http(buy.PATH, 'POST', headers()).json()['transaction_id'] == TXN
    assert client.creates == 1
    assert http(buy.PATH + '/status').json()['phase'] == 'pending'
    with store.connect() as db:
        assert db.execute('SELECT account_id FROM checkouts').fetchall() == [('A',)]
    send(store, completion())
    assert http(buy.PATH + '/status').json()['phase'] == 'linked'
    send(store, event(2))
    assert http(buy.PATH + '/status').json()['phase'] == 'confirmed'
    assert http(buy.PATH, 'POST', headers()).status == 409
    assert client.creates == 1 and subscription.USERS_FILE.read_bytes() == before


@pytest.mark.parametrize('query', [
    b'_ptxn=' + TXN.encode(), b'_ptxn=txn_' + b'z' * 26,
    b'_ptxn=', b'_ptxn', b'%5Fptxn=txn_untrusted',
    b'campaign=email&_ptxn=txn_untrusted',
    b'_ptxn=&_ptxn=txn_untrusted',
])
def test_payment_link_cannot_bypass_server_transaction_selection(ready, monkeypatch, query):
    store, client, _ = ready
    before = store.path.read_bytes()
    monkeypatch.setattr(buy, 'configuration', lambda: pytest.fail('No provider setup for URL transactions'))
    monkeypatch.setattr(runtime, 'store', lambda **kw: pytest.fail('No ledger reads for URL transactions'))
    response = http(buy.PATH, query=query)
    assert response.status == 400
    assert response.headers['cache-control'] == 'no-store'
    assert 'paddle.js' not in response.text and 'live_publictest' not in response.text
    assert 'txn_' not in response.text
    assert client.creates == 0 and store.path.read_bytes() == before


def test_payment_link_cannot_reopen_even_a_registered_transaction(ready):
    store, client, _ = ready
    assert http(buy.PATH, 'POST', headers()).json()['transaction_id'] == TXN
    assert http(buy.PATH, query=('_ptxn=' + TXN).encode()).status == 400
    assert http(buy.PATH).status == 200
    assert http(buy.PATH, 'POST', headers()).json()['transaction_id'] == TXN
    assert client.creates == 1


def test_unrelated_query_preserves_normal_checkout(ready):
    assert http(buy.PATH, query=b'campaign=email').status == 200


@pytest.mark.parametrize('suffix,method', [('', 'GET'), ('/status', 'GET'), ('', 'POST')])
def test_default_off_returns_404_without_storage_or_provider(ready, monkeypatch, suffix, method):
    monkeypatch.delenv('TRADE_PAPER_PADDLE_LIVE_CHECKOUT')
    monkeypatch.setattr(runtime, 'store', lambda **kw: pytest.fail('No ledger access when disabled'))
    assert http(buy.PATH + suffix, method, headers()).status == 404


@pytest.mark.parametrize('role,account', [('Viewer', 'A'), ('Admin', 'A'), ('Owner', 'stranger')])
def test_allowlist_and_owner_required(ready, role, account):
    _, client, user = ready
    user.update(role=role, account_id=account)
    assert http(buy.PATH).status == 403
    assert http(buy.PATH, 'POST', headers()).status == 403
    assert client.creates == 0


def test_missing_supporting_flags_or_tokens_fail_closed(ready, monkeypatch):
    for flag in ('CANCEL', 'ACCESS', 'WEBHOOK'):
        with monkeypatch.context() as m:
            m.setenv('TRADE_PAPER_PADDLE_LIVE_' + flag, '0')
            assert http(buy.PATH).status == 503
    for token in ('', 'test_sandbox'):
        monkeypatch.setenv('TRADE_PAPER_PADDLE_LIVE_CLIENT_TOKEN', token)
        assert http(buy.PATH).status == 503


def test_consent_csrf_origin_and_cross_purpose_tokens_are_checked_before_api(ready):
    _, client, _ = ready
    for bad in ({'X-Billing-Confirm': ''}, {'Origin': 'https://evil.test'}, {'X-Billing-CSRF': ''},
                {'X-Billing-CSRF': buy.manage.csrf_token('A')}, {'X-Billing-CSRF': buy.manage.csrf_token('B', buy.purpose(expected_offer(PRICE)))}):
        assert http(buy.PATH, 'POST', {**headers(), **bad}).status == 403
    assert client.creates == 0


def test_catalog_terms_change_invalidates_old_consent(ready, monkeypatch):
    old = headers()
    monkeypatch.setenv('TRADE_PAPER_PADDLE_LIVE_TAX_MODE', 'external')
    assert http(buy.PATH, 'POST', old).status == 403


def test_existing_paid_account_cannot_start_another_purchase(ready):
    _, client, user = ready
    user['account_id'] = 'B'
    assert http(buy.PATH).status == 409
    assert client.creates == 0


def test_expired_attempt_requires_review_without_new_provider_call(ready):
    store, client, _ = ready
    service = LiveActions(store, client)
    service.checkout('A', now=100)
    assert http(buy.PATH + '/status').json() == {'phase': 'review', 'can_open': False}
    assert http(buy.PATH, 'POST', headers()).status == 409
    assert client.creates == 1


def test_advertised_plan_drift_requires_explicit_code_review(offer_config, monkeypatch):
    monkeypatch.setitem(subscription.PLANS['Starter'], 'price', 39000)
    with pytest.raises(ValueError):
        expected_offer(PRICE)


def test_price_transport_requests_included_product(monkeypatch):
    from app.paddle_live_actions import LiveClient
    client = LiveClient(KEY)
    calls=[]
    monkeypatch.setattr(client, '_request', lambda *args: calls.append(args) or price())
    client.price(PRICE)
    assert calls == [('GET', '/prices/' + PRICE + '?include=product')]
