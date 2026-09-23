import hashlib
import hmac
import importlib.util
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi import HTTPException
import asyncio
from types import SimpleNamespace

class TestClient:
    __test__ = False

    def __init__(self, app):
        self.app = app

    def post(self, path, *, content, headers=None):
        async def run():
            messages = []
            async def receive():
                return {"type": "http.request", "body": content, "more_body": False}
            async def send(message):
                messages.append(message)
            scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
                     "method": "POST", "path": path, "raw_path": path.encode(),
                     "query_string": b"", "scheme": "http", "server": ("test", 80),
                     "client": ("127.0.0.1", 1),
                     "headers": [(k.lower().encode(), v.encode()) for k,v in (headers or {}).items()]}
            await self.app(scope, receive, send)
            status = next(m["status"] for m in messages if m["type"] == "http.response.start")
            body = b"".join(m.get("body", b"") for m in messages if m["type"] == "http.response.body")
            return SimpleNamespace(status_code=status, json=lambda: json.loads(body))
        return asyncio.run(run())

from app import paddle_sandbox_core as server
SECRET = 'test-webhook-secret-not-a-real-credential'
NOW = 1800000000
PRICE = 'pri_sandbox_starter'


def event(event_id='evt_1', status='active', day='23', **data):
    return {'event_id': event_id, 'event_type': 'subscription.updated',
            'occurred_at': f'2026-09-{day}T10:00:00Z',
            'data': {'id': 'sub_test', 'customer_id': 'ctm_test', 'status': status,
                     'items': [{'price': {'id': PRICE}, 'quantity': 1}], **data}}


def signed(payload, stamp=NOW, secret=SECRET):
    raw = json.dumps(payload).encode()
    digest = hmac.new(secret.encode(), str(stamp).encode()+b':'+raw, hashlib.sha256).hexdigest()
    return raw, {'Paddle-Signature': f'ts={stamp};h1={digest}'}


@pytest.fixture
def setup(tmp_path):
    app = server.create_app(database=tmp_path/'sandbox.sqlite3', secret=SECRET, price_id=PRICE, clock=lambda:NOW)
    store = app.state.sandbox_store
    store.bind('sub_test', 'ctm_test', 'test-account-A')
    return TestClient(app), store


def send(client, payload, **kwargs):
    raw, headers = signed(payload, **kwargs)
    return client.post('/webhooks/paddle', content=raw, headers=headers)


def test_active_state_and_isolation(setup):
    client, store = setup
    assert send(client, event()).json()['result'] == 'applied'
    assert store.state('test-account-A')['app_status'] == 'Active'
    assert store.state('other-account') is None


@pytest.mark.parametrize('kwargs', [{'secret':'wrong'}, {'stamp':NOW-6}, {'stamp':NOW+6}])
def test_invalid_signature_does_not_write(setup, kwargs):
    client, store = setup
    assert send(client, event(), **kwargs).status_code == 401
    assert store.state('test-account-A') is None


def test_raw_body_tampering(setup):
    client, store = setup
    raw, headers = signed(event())
    assert client.post('/webhooks/paddle', content=raw+b' ', headers=headers).status_code == 401
    assert store.state('test-account-A') is None


def test_duplicate_and_conflicting_id(setup):
    client, store = setup
    assert send(client, event()).status_code == 200
    assert send(client, event()).json()['result'] == 'duplicate'
    assert send(client, event(status='canceled')).status_code == 409
    assert store.state('test-account-A')['app_status'] == 'Active'


def test_late_event_cannot_reactivate_cancelled(setup):
    client, store = setup
    assert send(client, event('evt_cancel', 'canceled', '24')).status_code == 200
    assert send(client, event()).json()['result'] == 'stale'
    assert store.state('test-account-A')['app_status'] == 'Cancelled'


def test_scheduled_cancel_keeps_current_access(setup):
    client, store = setup
    assert send(client, event(scheduled_change={'action':'cancel'})).status_code == 200
    assert store.state('test-account-A')['app_status'] == 'Active'
    assert store.state('test-account-A')['scheduled_action'] == 'cancel'


@pytest.mark.parametrize('status,expected', [('past_due','Expired'),('paused','Expired'),('trialing','Trial')])
def test_subscription_states(setup, status, expected):
    client, store = setup
    assert send(client, event(status=status)).status_code == 200
    assert store.state('test-account-A')['app_status'] == expected


def test_untrusted_account_and_customer_do_not_bind(setup):
    client, store = setup
    bad=event(customer_id='ctm_attacker',custom_data={'account_id':'test-account-A'})
    assert send(client, bad).status_code == 409
    assert store.state('test-account-A') is None
    assert send(client, event(custom_data={'account_id':'other-account'})).status_code == 200
    assert store.state('other-account') is None


def test_unknown_binding_can_be_retried(setup):
    client, store = setup
    payload=event(id='sub_other',customer_id='ctm_other')
    assert send(client,payload).status_code == 409
    store.bind('sub_other','ctm_other','test-account-B')
    assert send(client,payload).status_code == 200
    assert store.state('test-account-B')['app_status']=='Active'


@pytest.mark.parametrize('patch', [{'items':[]},{'items':[{'price':{'id':'pri_wrong'},'quantity':1}]},{'status':'unknown'}])
def test_invalid_subscription_rejected(setup,patch):
    client,store=setup
    assert send(client,event(**patch)).status_code==400
    assert store.state('test-account-A') is None


def test_equal_timestamps_require_reconciliation(setup):
    client,store=setup
    send(client,event())
    assert send(client,event('evt_new','canceled')).status_code==409
    assert store.state('test-account-A')['app_status']=='Active'


def test_concurrent_duplicates_apply_once(setup):
    _,store=setup
    payload=event()
    raw,_=signed(payload)
    def apply(_): return store.apply(payload,raw,PRICE)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results=list(pool.map(apply,range(16)))
    assert results.count('applied')==1
    assert results.count('duplicate')==15


def test_payload_limits_and_bad_json(setup):
    client,store=setup
    assert client.post('/webhooks/paddle',content=b'x'*(server.MAX_BODY+1)).status_code==413
    for payload in ([],None,{'event_id':'evt_a','event_type':[]}):
        assert send(client,payload).status_code==400


def test_production_configuration_refused(tmp_path):
    with pytest.raises(ValueError):
        server.create_app(database=tmp_path/'bad.sqlite3',secret=SECRET,price_id=PRICE,environment='live')


def test_payment_failure_and_browser_success_do_not_grant_access(setup):
    client,store=setup
    for kind in ('transaction.payment_failed','checkout.completed'):
        payload=event()
        payload['event_type']=kind
        assert send(client,payload).json()['result']=='ignored'
    assert store.state('test-account-A') is None


def test_timezone_equivalent_timestamp_needs_reconciliation(setup):
    client,store=setup
    send(client,event())
    payload=event('evt_offset')
    payload['occurred_at']='2026-09-23T19:00:00+09:00'
    assert send(client,payload).status_code==409


def test_missing_signature_is_rejected(setup):
    client,store=setup
    assert client.post('/webhooks/paddle',content=json.dumps(event()).encode()).status_code==401
    assert store.state('test-account-A') is None


def test_real_app_webhook_disabled_and_unsigned_requests_fail_closed(monkeypatch, tmp_path):
    import time
    from app import main, paddle_sandbox_webhook as adapter
    monkeypatch.delenv('TRADE_PAPER_PADDLE_SANDBOX_ENABLED', raising=False)
    client = TestClient(main.app)
    assert client.post(adapter.WEBHOOK_PATH, content=b'{}').status_code == 404
    monkeypatch.setenv('TRADE_PAPER_PADDLE_SANDBOX_ENABLED', '1')
    monkeypatch.delenv('TRADE_PAPER_PADDLE_SANDBOX_WEBHOOK_SECRET', raising=False)
    assert client.post(adapter.WEBHOOK_PATH, content=b'{}').status_code == 503
    monkeypatch.setenv('TRADE_PAPER_PADDLE_SANDBOX_WEBHOOK_SECRET', SECRET)
    monkeypatch.setenv('TRADE_PAPER_PADDLE_SANDBOX_PRICE_ID', PRICE)
    store = server.SandboxStore(tmp_path/'isolated.sqlite3')
    store.bind('sub_test','ctm_test','sandbox:test-A')
    monkeypatch.setattr(adapter, 'sandbox_store', lambda:store)
    assert client.post(adapter.WEBHOOK_PATH, content=b'{}').status_code == 401
    raw, headers = signed(event(), stamp=int(time.time()))
    response = client.post(adapter.WEBHOOK_PATH, content=raw, headers=headers)
    assert response.status_code == 200
    assert response.json()['result']=='applied'
    assert store.state('sandbox:test-A')['app_status']=='Active'


def test_signed_simulator_event_and_replay(setup):
    client, store = setup
    payload = event('ntfsimevt_01m36vhykc0w9jar1677be63sc')
    assert send(client, payload, secret='wrong').status_code == 401
    assert store.state('test-account-A') is None
    assert send(client, payload).json()['result'] == 'applied'
    assert send(client, payload).json()['result'] == 'duplicate'
    assert store.state('test-account-A')['app_status'] == 'Active'


@pytest.mark.parametrize('event_id', ['ntfsimntf_123', 'unknown_123', '', None])
def test_unrecognized_event_id_rejected(setup, event_id):
    client, store = setup
    assert send(client, event(event_id)).status_code == 400
    assert store.state('test-account-A') is None

TXN = 'txn_' + 'a' * 26
SUB = 'sub_' + 'b' * 26
CUSTOMER = 'ctm_' + 'c' * 26


def completion(**changes):
    payload = event('evt_checkout')
    payload['event_type'] = 'transaction.completed'
    payload['data'] = {'id': TXN, 'subscription_id': SUB, 'customer_id': CUSTOMER,
        'status': 'completed', 'collection_mode': 'automatic',
        'items': [{'price': {'id': PRICE}, 'quantity': 1}],
        'custom_data': {'account_id': 'attacker'}, **changes}
    return payload


def test_checkout_binding_waits_for_signed_server_registered_transaction(setup):
    client, store = setup
    payload = completion()
    assert send(client, payload).json()['result'] == 'ignored'
    store.register_checkout(TXN, 'sandbox:buyer', PRICE)
    assert send(client, payload, secret='wrong').status_code == 401
    sub_event = event('evt_subscription', id=SUB, customer_id=CUSTOMER)
    assert send(client, sub_event).status_code == 409
    assert send(client, payload).json()['result'] == 'bound'
    assert store.state('sandbox:buyer') is None
    assert send(client, payload).json()['result'] == 'duplicate'
    assert send(client, sub_event).json()['result'] == 'applied'
    assert store.state('sandbox:buyer')['app_status'] == 'Active'
    assert store.state('attacker') is None


@pytest.mark.parametrize('patch', [
    {'status': 'paid'}, {'collection_mode': 'manual'}, {'subscription_id': None},
    {'customer_id': 'bad'}, {'items': []},
    {'items': [{'price': {'id': PRICE}, 'quantity': True}]},
    {'items': [{'price': {'id': 'pri_other'}, 'quantity': 1}]},
])
def test_invalid_completion_cannot_consume_checkout(setup, patch):
    client, store = setup
    store.register_checkout(TXN, 'sandbox:buyer', PRICE)
    assert send(client, completion(**patch)).status_code == 400
    assert send(client, completion()).json()['result'] == 'bound'


def test_simulated_completion_cannot_establish_ownership(setup):
    client, store = setup
    store.register_checkout(TXN, 'sandbox:buyer', PRICE)
    payload = completion()
    payload['event_id'] = 'ntfsimevt_test'
    assert send(client, payload).json()['result'] == 'ignored'
    assert send(client, event(id=SUB, customer_id=CUSTOMER)).status_code == 409


def test_checkout_binding_conflicts_are_atomic(setup):
    client, store = setup
    store.register_checkout(TXN, 'sandbox:buyer', PRICE)
    store.bind(SUB, CUSTOMER, 'sandbox:other')
    assert send(client, completion()).status_code == 409
    with store.connect() as db:
        assert db.execute('SELECT count(*) FROM events').fetchone()[0] == 0
        assert db.execute('SELECT account_id FROM bindings WHERE subscription_id=?', (SUB,)).fetchone()[0] == 'sandbox:other'


def test_checkout_registration_cannot_overwrite_owner(setup):
    _, store = setup
    store.register_checkout(TXN, 'sandbox:buyer', PRICE)
    with pytest.raises(server.sqlite3.IntegrityError):
        store.register_checkout(TXN, 'sandbox:attacker', PRICE)
    with pytest.raises(ValueError):
        store.register_checkout(TXN, 'production-account', PRICE)


def test_concurrent_checkout_completion_binds_once(setup):
    _, store = setup
    store.register_checkout(TXN, 'sandbox:buyer', PRICE)
    payload = completion()
    raw, _ = signed(payload)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: store.apply(payload, raw, PRICE), range(16)))
    assert results.count('bound') == 1
    assert results.count('duplicate') == 15
