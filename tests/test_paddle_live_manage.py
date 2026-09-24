import asyncio
from datetime import datetime
import json
import re
import sqlite3
from types import SimpleNamespace

import pytest
from starlette.requests import Request

from app import auth, main, subscription
from app import paddle_live_manage as manage
from app import paddle_live_runtime as runtime
from app.paddle_live_actions import LiveActions, ProviderUnavailable
from tests.test_paddle_live_actions import Client
from tests.test_paddle_live_store import store, send, completion, event, ledger, TXN, SUB, PRICE, NOW
from tests.test_subscription import _files, _request

ORIGIN = 'https://www.tradepaper.ai'


def http(path=manage.PATH, method='GET', headers=None, body=b'', query=b''):
    async def run():
        messages = []
        async def receive():
            return {'type': 'http.request', 'body': body, 'more_body': False}
        async def send(message):
            messages.append(message)
        scope = {'type': 'http', 'asgi': {'version': '3.0'}, 'http_version': '1.1',
                 'method': method, 'path': path, 'raw_path': path.encode(), 'query_string': query,
                 'scheme': 'https', 'server': ('www.tradepaper.ai', 443), 'client': ('127.0.0.1', 1),
                 'headers': [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]}
        await main.app(scope, receive, send)
        start = next(m for m in messages if m['type'] == 'http.response.start')
        raw = b''.join(m.get('body', b'') for m in messages if m['type'] == 'http.response.body')
        return SimpleNamespace(status=start['status'], text=raw.decode(),
            headers={k.decode(): v.decode() for k, v in start['headers']}, json=lambda: json.loads(raw))
    return asyncio.run(run())


@pytest.fixture
def ready(store, monkeypatch):
    store.register_checkout(TXN, 'A')
    send(store, completion())
    send(store, event(2))
    monkeypatch.setattr(runtime, 'data_path', lambda name: store.path)
    monkeypatch.setenv('TRADE_PAPER_PADDLE_LIVE_PRICE_ID', PRICE)
    monkeypatch.delenv('TRADE_PAPER_PADDLE_SANDBOX_PRICE_ID', raising=False)
    for flag in ('MANAGE', 'ACCESS', 'CANCEL'):
        monkeypatch.setenv('TRADE_PAPER_PADDLE_LIVE_' + flag, '1')
    monkeypatch.setenv('TRADE_PAPER_PUBLIC_BASE_URL', ORIGIN)
    user = {'account_id': 'A', 'role': 'Owner'}
    monkeypatch.setattr(auth, 'current_user', lambda request: user)
    # Billing access must not be blocked by incomplete company setup.
    monkeypatch.setattr(auth, 'company_setup_complete', lambda *args: False)
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW
    monkeypatch.setattr(manage, 'datetime', Clock)
    client = Client()
    service = LiveActions(store, client)
    calls = []
    def cancel(account):
        calls.append(account)
        return service.cancel(account, now=NOW)
    monkeypatch.setattr(manage, 'configured_service', lambda operation: SimpleNamespace(cancel=cancel))
    return SimpleNamespace(store=store, client=client, user=user, calls=calls)


def headers(account='A'):
    return {'Origin': ORIGIN, 'X-Billing-CSRF': manage.csrf_token(account),
            'X-Billing-Confirm': 'cancel-at-period-end', 'Sec-Fetch-Site': 'same-origin'}


def test_owner_page_and_status_show_no_identifiers_secrets_or_payment_widget(ready):
    response = http()
    assert response.status == 200
    assert 'Manage billing' in response.text and 'Cancel renewal' in response.text
    assert 'type="checkbox"' in response.text and 'disabled' in response.text
    assert 'request does not issue a refund' in response.text
    for value in (TXN, SUB, PRICE, 'api_key', 'Paddle.Checkout', 'paddle.js'):
        assert value not in response.text
    assert response.headers['cache-control'] == 'no-store'
    assert response.headers['x-frame-options'] == 'DENY'
    response = http(manage.PATH + '/status')
    assert response.status == 200
    assert response.json()['starter_access'] is True
    assert response.json()['cancellation'] == 'none'
    assert ready.client.creates == ready.client.cancels == 0


@pytest.mark.parametrize('suffix,method', [('', 'GET'), ('/status', 'GET'), ('/cancel', 'POST')])
def test_routes_default_off_before_store_or_service_access(ready, monkeypatch, suffix, method):
    monkeypatch.delenv('TRADE_PAPER_PADDLE_LIVE_MANAGE')
    monkeypatch.setattr(runtime, 'store', lambda **kw: pytest.fail('Store must stay closed'))
    response = http(manage.PATH + suffix, method, headers())
    assert response.status == 404
    assert response.headers['cache-control'] == 'no-store'
    assert ready.calls == []


@pytest.mark.parametrize('role', ['Viewer', 'Admin', 'Editor', '', None])
def test_only_owner_can_read_or_cancel(ready, role):
    ready.user['role'] = role
    for suffix, method in [('', 'GET'), ('/status', 'GET'), ('/cancel', 'POST')]:
        assert http(manage.PATH + suffix, method, headers()).status == 403
    assert ready.calls == []


def test_anonymous_requests_require_login(ready, monkeypatch):
    monkeypatch.setattr(auth, 'current_user', lambda request: None)
    for path, method in [(manage.PATH, 'GET'), (manage.PATH + '/cancel', 'POST')]:
        response = http(path, method, headers())
        assert response.status == 303 and response.headers['location'].startswith('/login?')
    assert ready.calls == []


@pytest.mark.parametrize('change', [
    {'Origin': 'https://evil.test'}, {'Origin': ''}, {'X-Billing-CSRF': ''},
    {'X-Billing-CSRF': 'broken'}, {'X-Billing-Confirm': ''}, {'Sec-Fetch-Site': 'cross-site'},
])
def test_csrf_and_explicit_confirmation_required(ready, change):
    request_headers = {**headers(), **change}
    assert http(manage.PATH + '/cancel', 'POST', request_headers).status == 403
    assert ready.calls == []


def test_token_cross_account_expiry_future_and_tampering(ready, monkeypatch):
    token = headers()
    for value in [headers('B'), {**token, 'X-Billing-CSRF': token['X-Billing-CSRF'] + 'x'}]:
        assert http(manage.PATH + '/cancel', 'POST', value).status == 403
    now = manage.time.time()
    for delta in (901, -10):
        monkeypatch.setattr(manage.time, 'time', lambda: now + delta)
        assert http(manage.PATH + '/cancel', 'POST', token).status == 403
    assert ready.calls == []


def test_cross_account_query_body_never_changes_server_owned_target(ready):
    before = ledger(ready.store)
    response = http(manage.PATH + '/cancel', 'POST', headers(),
                    body=b'{"account_id":"B","subscription_id":"sub_attacker"}', query=b'account_id=B')
    assert response.status == 200 and response.json()['request_status'] == 'scheduled'
    assert ready.calls == ['A'] and ready.client.cancels == 1
    assert ledger(ready.store) == before
    state = http(manage.PATH + '/status').json()
    assert state['cancellation'] == 'awaiting_update' and state['starter_access'] is True
    assert state['can_cancel'] is False
    send(ready.store, event(3, data=ready.client.current, day=25))
    assert http(manage.PATH + '/status').json()['cancellation'] == 'scheduled'


def test_other_account_cannot_read_or_cancel_first_account(ready):
    ready.user['account_id'] = 'B'
    for suffix, method in [('', 'GET'), ('/status', 'GET'), ('/cancel', 'POST')]:
        response = http(manage.PATH + suffix, method, headers('B'), query=b'account_id=A')
        assert response.status == 404
        assert TXN not in response.text and SUB not in response.text
    assert ready.calls == []


def test_uncertain_cancellation_shows_pending_and_never_resends_post(ready):
    ready.client.cancel_error = ProviderUnavailable('private-provider-response')
    response = http(manage.PATH + '/cancel', 'POST', headers())
    assert response.status == 502 and 'private-provider-response' not in response.text
    status = http(manage.PATH + '/status').json()
    assert status['cancellation'] == 'pending' and status['can_cancel'] is True
    assert http(manage.PATH + '/cancel', 'POST', headers()).status == 409
    assert ready.client.cancels == 1


def test_store_errors_are_redacted_and_reads_never_create_missing_database(ready, monkeypatch, tmp_path):
    for payload in (None, b'corrupt private database contents'):
        path = tmp_path / ('absent.sqlite3' if payload is None else 'broken.sqlite3')
        if payload is not None:
            path.write_bytes(payload)
        monkeypatch.setattr(runtime, 'data_path', lambda name: path)
        for suffix, method in [('', 'GET'), ('/status', 'GET'), ('/cancel', 'POST')]:
            response = http(manage.PATH + suffix, method, headers())
            assert response.status == 503
            assert str(path) not in response.text and 'private' not in response.text
        if payload is None:
            assert not path.exists()
    assert ready.calls == []


def test_disabled_access_does_not_block_cancellation(ready, monkeypatch):
    monkeypatch.delenv('TRADE_PAPER_PADDLE_LIVE_ACCESS')
    assert http(manage.PATH + '/status').json()['starter_access'] is False
    assert http(manage.PATH + '/cancel', 'POST', headers()).status == 200


def test_cancel_switch_hides_button_and_factory_refuses_post(ready, monkeypatch):
    from app.paddle_live_actions import configured_service
    monkeypatch.delenv('TRADE_PAPER_PADDLE_LIVE_CANCEL')
    monkeypatch.setattr(manage, 'configured_service', configured_service)
    assert http(manage.PATH + '/status').json()['can_cancel'] is False
    assert http(manage.PATH + '/cancel', 'POST', headers()).status == 404
    assert ready.client.cancels == 0


def test_link_shown_only_to_enabled_managed_owner(ready, tmp_path, monkeypatch):
    _files(tmp_path, monkeypatch)
    request = _request()
    request.scope['trade_paper_user']['role'] = 'Owner'
    assert 'href="/subscription/paddle"' in subscription.subscription_page(request).body.decode()
    request.scope['trade_paper_user']['role'] = 'Viewer'
    assert 'href="/subscription/paddle"' not in subscription.subscription_page(request).body.decode()
    request.scope['trade_paper_user']['role'] = 'Owner'
    monkeypatch.delenv('TRADE_PAPER_PADDLE_LIVE_MANAGE')
    assert 'href="/subscription/paddle"' not in subscription.subscription_page(request).body.decode()


def test_non_https_or_missing_public_origin_blocks_cancel(ready, monkeypatch):
    for origin in ('', 'http://www.tradepaper.ai', 'https://www.tradepaper.ai/path'):
        monkeypatch.setenv('TRADE_PAPER_PUBLIC_BASE_URL', origin)
        assert http(manage.PATH + '/cancel', 'POST', headers()).status == 503
    assert ready.calls == []
