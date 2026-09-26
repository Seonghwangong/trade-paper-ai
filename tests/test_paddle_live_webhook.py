import asyncio
from datetime import datetime, timezone
import hashlib
import hmac
import json
import sqlite3
import time

import pytest
from fastapi import HTTPException

from app import auth, main, subscription
from app import paddle_live_runtime as runtime
from app import paddle_live_webhook as adapter
from app.paddle_sandbox_core import MAX_BODY
from tests.test_paddle_sandbox import TestClient
from tests.test_paddle_live_store import completion, event, SECRET, PRICE, PRODUCT, TXN, snapshot, NOW
from tests.test_subscription import _files, _request


@pytest.fixture
def live(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, 'data_path', lambda name: tmp_path / name)
    monkeypatch.setenv('TRADE_PAPER_PADDLE_LIVE_PRICE_ID', PRICE)
    monkeypatch.setenv('TRADE_PAPER_PADDLE_LIVE_WEBHOOK_SECRET', SECRET)
    monkeypatch.setenv('TRADE_PAPER_PADDLE_LIVE_WEBHOOK', '1')
    monkeypatch.setenv('TRADE_PAPER_PADDLE_LIVE_PRODUCT_ID', PRODUCT)
    monkeypatch.setenv('TRADE_PAPER_PADDLE_LIVE_TAX_MODE', 'internal')
    monkeypatch.delenv('TRADE_PAPER_PADDLE_LIVE_ACCESS', raising=False)
    monkeypatch.delenv('TRADE_PAPER_PADDLE_SANDBOX_PRICE_ID', raising=False)
    monkeypatch.delenv('TRADE_PAPER_PADDLE_SANDBOX_WEBHOOK_SECRET', raising=False)
    return TestClient(main.app), tmp_path


def post(client, payload, secret=SECRET, **headers):
    raw = json.dumps(payload).encode()
    stamp = int(time.time())
    digest = hmac.new(secret.encode(), str(stamp).encode() + b':' + raw, hashlib.sha256).hexdigest()
    return client.post(adapter.WEBHOOK_PATH, content=raw,
                       headers={'Paddle-Signature': f'ts={stamp};h1={digest}', **headers})


def initialize(client):
    store = runtime.store()
    store.register_checkout(TXN, 'A')
    assert post(client, completion()).status_code == 200
    return store


def test_default_off_and_missing_configuration_do_not_create_database(live, monkeypatch):
    client, path = live
    monkeypatch.delenv('TRADE_PAPER_PADDLE_LIVE_WEBHOOK')
    assert client.post(adapter.WEBHOOK_PATH, content=b'{}').status_code == 404
    monkeypatch.setenv('TRADE_PAPER_PADDLE_LIVE_WEBHOOK', '1')
    monkeypatch.delenv('TRADE_PAPER_PADDLE_LIVE_WEBHOOK_SECRET')
    assert client.post(adapter.WEBHOOK_PATH, content=b'{}').status_code == 503
    assert not (path / 'paddle_live.sqlite3').exists()


def test_missing_offer_contract_is_unavailable_before_database_open(live, monkeypatch):
    client, path = live
    monkeypatch.delenv('TRADE_PAPER_PADDLE_LIVE_PRODUCT_ID')
    assert post(client, completion()).status_code == 503
    assert not (path / 'paddle_live.sqlite3').exists()


def test_unsigned_and_tampered_requests_do_not_open_live_database(live):
    client, path = live
    assert client.post(adapter.WEBHOOK_PATH, content=b'{}').status_code == 401
    assert post(client, event(), secret='wrong').status_code == 401
    assert not (path / 'paddle_live.sqlite3').exists()


def test_actual_app_signed_flow_updates_authoritative_access_without_json_writes(live, monkeypatch):
    client, path = live
    users, history, usage = _files(path, monkeypatch)
    before = {p: p.read_bytes() for p in (users, history, usage)}
    initialize(client)
    assert post(client, event(2)).json()['result'] == 'applied'
    # Receiving signed state does not enable paid access on its own.
    assert subscription.subscription_for_account('A', now=NOW) == {'plan': 'Free', 'status': 'Active'}
    monkeypatch.setenv('TRADE_PAPER_PADDLE_LIVE_ACCESS', '1')
    assert subscription.subscription_for_account('A', now=NOW) == {'plan': 'Starter', 'status': 'Active'}
    assert subscription.usage_summary('A', now=NOW)['limit'] is None
    assert subscription.subscription_for_account('B', now=NOW)['plan'] == 'Professional'
    assert subscription.subscription_for_account('not-a-user', now=NOW)['plan'] == 'Free'
    assert {p: p.read_bytes() for p in before} == before


def test_cancellation_returns_to_free_allowance_and_stale_event_cannot_restore_paid(live, monkeypatch):
    client, path = live
    users, _, usage = _files(path, monkeypatch)
    initialize(client)
    monkeypatch.setenv('TRADE_PAPER_PADDLE_LIVE_ACCESS', '1')
    assert post(client, event(2)).status_code == 200
    cancelled = snapshot()
    cancelled.update(status='canceled', current_billing_period=None)
    assert post(client, event(3, data=cancelled, day=25)).status_code == 200
    assert post(client, event(4, day=23)).json()['result'] == 'stale'
    assert subscription.subscription_for_account('A', now=NOW) == {'plan': 'Free', 'status': 'Active'}
    usage.write_text(json.dumps([{'account_id': 'A', 'created_at': '2026-09-01'}] * 5))
    assert not subscription.usage_summary('A', now=NOW)['allowed']
    assert json.loads(users.read_text())[0].get('plan') is None


def test_expired_active_snapshot_cannot_keep_unlimited_access(live, monkeypatch):
    client, path = live
    _files(path, monkeypatch)
    initialize(client)
    monkeypatch.setenv('TRADE_PAPER_PADDLE_LIVE_ACCESS', '1')
    assert post(client, event(2)).status_code == 200
    expired = datetime(2026, 10, 1, tzinfo=timezone.utc)
    assert subscription.usage_summary('A', now=expired)['limit'] == 5


def test_provider_reservation_blocks_local_mutations_without_json_marker(live, monkeypatch):
    client, path = live
    users, history, _ = _files(path, monkeypatch)
    initialize(client)
    before = users.read_bytes(), history.read_bytes()
    for action in (lambda: subscription.change_plan(_request(), 'Free'),
                   lambda: subscription.cancel_subscription(_request()),
                   lambda: subscription.update_subscription_status('A', _request('B'), 'Active')):
        with pytest.raises(HTTPException) as error:
            action()
        assert error.value.status_code == 409
    assert before == (users.read_bytes(), history.read_bytes())
    assert not (path / 'audit_log.json').exists()
    page = subscription.subscription_page(_request()).body.decode()
    assert 'Contact billing support' in page
    assert 'action="/subscription/cancel"' not in page


def test_unbound_events_return_retryable_conflict_then_succeed(live):
    client, _ = live
    assert post(client, completion()).status_code == 409
    assert post(client, event(2)).status_code == 409
    initialize(client)
    assert post(client, event(2)).status_code == 200
    assert post(client, event(2)).json()['result'] == 'duplicate'


def test_payload_limits_malformed_and_simulator_events(live):
    client, _ = live
    assert client.post(adapter.WEBHOOK_PATH, content=b'x' * (MAX_BODY + 1)).status_code == 413
    assert client.post(adapter.WEBHOOK_PATH, content=b'{}', headers={'Content-Length': str(MAX_BODY + 1)}).status_code == 413
    assert client.post(adapter.WEBHOOK_PATH, content=b'{}', headers={'Content-Length': 'bad'}).status_code == 400
    malformed = event()
    malformed['event_id'] = 'ntfsimevt_' + 'a' * 26
    assert post(client, malformed).status_code == 400


def test_configured_sandbox_secret_or_price_cannot_be_reused(live, monkeypatch):
    client, path = live
    monkeypatch.setenv('TRADE_PAPER_PADDLE_SANDBOX_WEBHOOK_SECRET', SECRET)
    assert post(client, event()).status_code == 503
    monkeypatch.delenv('TRADE_PAPER_PADDLE_SANDBOX_WEBHOOK_SECRET')
    monkeypatch.setenv('TRADE_PAPER_PADDLE_SANDBOX_PRICE_ID', PRICE)
    assert post(client, event()).status_code == 503
    assert not (path / 'paddle_live.sqlite3').exists()


def test_storage_failure_is_not_acknowledged_or_exposed(live, monkeypatch):
    client, _ = live
    initialize(client)
    def fail(*args, **kwargs):
        raise sqlite3.OperationalError('private database path or secret')
    monkeypatch.setattr(runtime.PaddleLiveStore, 'apply_signed_event', fail)
    response = post(client, event(2))
    assert response.status_code == 503
    assert 'private' not in json.dumps(response.json())


def test_read_projection_never_initializes_or_repairs_database(live, monkeypatch):
    client, path = live
    _files(path, monkeypatch)
    assert subscription.subscription_for_account('A')['plan'] == 'Free'
    assert not (path / 'paddle_live.sqlite3').exists()
    monkeypatch.setenv('TRADE_PAPER_PADDLE_LIVE_ACCESS', '1')
    with pytest.raises(runtime.LiveBillingUnavailable):
        subscription.subscription_for_account('A')
    assert not (path / 'paddle_live.sqlite3').exists()
    initialize(client)
    db = path / 'paddle_live.sqlite3'
    before = db.read_bytes()
    assert subscription.subscription_for_account('A')['plan'] == 'Free'
    assert db.read_bytes() == before
    db.write_bytes(b'corrupt database')
    with pytest.raises(runtime.LiveBillingUnavailable):
        subscription.subscription_for_account('A')
    assert db.read_bytes() == b'corrupt database'


def test_provider_marked_stale_json_cannot_grant_paid_access_when_disabled(live, monkeypatch):
    _, path = live
    users, _, _ = _files(path, monkeypatch)
    records = json.loads(users.read_text())
    records[0].update(plan='Starter', subscription_status='Active', billing_provider='paddle')
    users.write_text(json.dumps(records))
    assert subscription.subscription_for_account('A')['plan'] == 'Free'


def test_document_middleware_blocks_storage_failure_before_creating_document(live, monkeypatch):
    _, path = live
    _files(path, monkeypatch)
    monkeypatch.setenv('TRADE_PAPER_PADDLE_LIVE_ACCESS', '1')
    monkeypatch.setattr(auth, 'current_user', lambda req: {'account_id': 'A', 'email': 'a@example.test'})
    monkeypatch.setattr(auth, 'company_setup_complete', lambda *args: True)
    calls = []
    async def app(scope, receive, send):
        calls.append('document-handler')
    response = TestClient(auth.AuthenticationMiddleware(app)).post('/invoice', content=b'')
    assert response.status_code == 503 and calls == []
