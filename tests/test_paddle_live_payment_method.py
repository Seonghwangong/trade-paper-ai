from copy import deepcopy

import pytest

from app import auth, paddle_live_manage as manage, paddle_live_payment_method as portal
from app import paddle_live_runtime as runtime
from app.paddle_live_actions import ProviderUnavailable
from tests.test_paddle_live_manage import ready, http, ORIGIN
from tests.test_paddle_live_actions import KEY
from tests.test_paddle_live_store import store, SUB, CUSTOMER, TXN, event, send


URL = 'https://buyer-portal.paddle.com/subscriptions/' + SUB + '/update-payment-method?token=synthetic.token'


@pytest.fixture
def linked(ready, monkeypatch):
    monkeypatch.setenv('TRADE_PAPER_PADDLE_LIVE_PAYMENT_METHOD', '1')
    monkeypatch.setenv('TRADE_PAPER_PADDLE_LIVE_API_KEY', KEY)
    data = {'id': SUB, 'customer_id': CUSTOMER, 'status': 'active', 'collection_mode': 'automatic',
            'management_urls': {'update_payment_method': URL}}
    calls = []
    class Client:
        def subscription(self, sub):
            calls.append(sub)
            return deepcopy(data)
    monkeypatch.setattr(portal, 'LiveClient', lambda key: Client())
    return ready, data, calls


def headers(account='A', purpose='payment-method'):
    return {'Origin': ORIGIN, 'Sec-Fetch-Site': 'same-origin',
            'X-Billing-CSRF': manage.csrf_token(account, purpose),
            'X-Billing-Confirm': 'open-payment-method-portal'}


def post(h=None, **kwargs):
    return http(manage.PATH + '/payment-method', 'POST', headers() if h is None else h, **kwargs)


@pytest.mark.parametrize('status', ['active', 'past_due'])
def test_owner_portal_uses_read_only_bound_subscription_without_sales(linked, monkeypatch, status):
    ready, data, calls = linked
    for flag in ('ACCESS', 'CHECKOUT', 'CANCEL'):
        monkeypatch.delenv('TRADE_PAPER_PADDLE_LIVE_' + flag, raising=False)
    data['status'] = status
    before = ready.store.path.read_bytes()
    response = post(body=b'{"account_id":"B","subscription_id":"wrong"}', query=b'subscription_id=wrong')
    assert response.status == 200 and response.json() == {'url': URL}
    assert response.headers['cache-control'] == 'no-store'
    assert response.headers['referrer-policy'] == 'no-referrer'
    assert calls == [SUB]
    assert ready.store.path.read_bytes() == before
    assert ready.client.creates == ready.client.cancels == 0
    assert http(manage.PATH + '/status').json()['starter_access'] is False


@pytest.mark.parametrize('flag', ['MANAGE', 'PAYMENT_METHOD'])
def test_default_off_never_reads_ledger_or_provider(linked, monkeypatch, flag):
    monkeypatch.delenv('TRADE_PAPER_PADDLE_LIVE_' + flag)
    monkeypatch.setattr(runtime, 'store', lambda **kw: pytest.fail('No ledger access'))
    assert post().status == 404
    assert linked[2] == []


@pytest.mark.parametrize('role', ['Viewer', 'Admin', 'Editor', '', None])
def test_only_owner_can_open(linked, role):
    linked[0].user['role'] = role
    assert post().status == 403
    assert linked[2] == []


def test_other_account_cannot_choose_owner_via_request(linked):
    linked[0].user['account_id'] = 'B'
    assert post(headers('B'), query=b'account_id=A').status == 404
    assert linked[2] == []


def test_anonymous_and_get_do_not_issue_url(linked, monkeypatch):
    assert http(manage.PATH + '/payment-method').status == 405
    monkeypatch.setattr(auth, 'current_user', lambda request: None)
    assert post().status == 303
    assert linked[2] == []


@pytest.mark.parametrize('change', [
    {'Origin': 'https://evil.test'}, {'X-Billing-CSRF': ''},
    {'X-Billing-Confirm': 'cancel-at-period-end'}, {'Sec-Fetch-Site': 'cross-site'},
])
def test_csrf_and_confirmation_rejected_before_provider(linked, change):
    assert post({**headers(), **change}).status == 403
    assert linked[2] == []


def test_account_purpose_and_expired_tokens_are_rejected(linked, monkeypatch):
    assert post(headers('B')).status == 403
    assert post(headers(purpose='cancel')).status == 403
    h = headers(); now = manage.time.time()
    monkeypatch.setattr(manage.time, 'time', lambda: now + 901)
    assert post(h).status == 403
    assert linked[2] == []


@pytest.mark.parametrize('patch', [
    {'id': 'sub_' + 'z'*26}, {'customer_id': 'ctm_' + 'z'*26},
    {'status': 'canceled'}, {'status': 'paused'}, {'status': 'trialing'},
    {'collection_mode': 'manual'}, {'management_urls': None}, {},
])
def test_mismatched_or_incomplete_provider_record_is_redacted(linked, patch):
    if not patch:
        linked[1].clear()
    else:
        linked[1].update(patch)
    response = post()
    assert response.status == 502
    assert 'synthetic.token' not in response.text and SUB not in response.text


@pytest.mark.parametrize('bad', [
    None, '', 'javascript:alert(1)', URL.replace('https:', 'http:'),
    URL.replace('buyer-portal.paddle.com', 'buyer-portal.paddle.com.evil.test'),
    URL.replace('buyer-portal.paddle.com', 'sandbox-buyer-portal.paddle.com'),
    URL.replace('buyer-portal.paddle.com', 'user@buyer-portal.paddle.com'),
    URL.replace('buyer-portal.paddle.com', 'buyer-portal.paddle.com:443'),
    URL.replace(SUB, 'sub_' + 'z'*26), URL.replace('/update-payment-method', '/cancel'),
    URL + '#fragment', URL + '&next=https://evil.test', URL + '&token=duplicate',
    URL.replace('synthetic.token', ''), URL.replace('synthetic.token', '%0Asecret'),
    URL.replace('https:', 'https:\n'), URL + 'a'*4096,
])
def test_unexpected_portal_urls_fail_closed_without_leaking_url(linked, bad):
    linked[1]['management_urls']['update_payment_method'] = bad
    response = post()
    assert response.status == 502
    assert 'synthetic.token' not in response.text and 'evil.test' not in response.text


def test_provider_failure_is_redacted(linked, monkeypatch):
    def fail(sub):
        raise ProviderUnavailable('private raw token')
    monkeypatch.setattr(portal, 'LiveClient', lambda key: type('Client', (), {'subscription': staticmethod(fail)})())
    response = post()
    assert response.status == 502 and 'private' not in response.text


def test_missing_storage_not_created(linked, monkeypatch, tmp_path):
    path = tmp_path / 'absent.sqlite3'
    monkeypatch.setattr(runtime, 'data_path', lambda name: path)
    assert post().status == 503 and not path.exists()
    assert linked[2] == []


def test_review_before_or_during_provider_read_blocks_handoff(linked, monkeypatch):
    review = {'value': False}
    monkeypatch.setattr(portal, 'needs_review', lambda db, sub: review['value'])
    def read(sub):
        review['value'] = True
        return linked[1]
    monkeypatch.setattr(portal, 'LiveClient', lambda key: type('Client', (), {'subscription': staticmethod(read)})())
    assert post().status == 409
    monkeypatch.setattr(portal, 'LiveClient', lambda key: pytest.fail('No provider read during review'))
    assert post().status == 409


def test_page_and_status_never_fetch_or_embed_temporary_link(linked, monkeypatch):
    response = http(manage.PATH)
    assert response.status == 200 and 'Open payment details in Paddle' in response.text
    assert 'paymentCsrf' in response.text and 'Review payment details' in response.text
    assert URL not in response.text and 'synthetic.token' not in response.text
    assert http(manage.PATH + '/status').json()['can_update_payment_method'] is True
    monkeypatch.delenv('TRADE_PAPER_PADDLE_LIVE_PAYMENT_METHOD')
    assert http(manage.PATH + '/status').json()['can_update_payment_method'] is False
    assert linked[2] == []


def test_each_deliberate_request_gets_fresh_link_without_persistence(linked):
    before = linked[0].store.path.read_bytes()
    assert post().json()['url'] == URL
    linked[1]['management_urls']['update_payment_method'] = URL.replace('synthetic.token', 'new.token')
    assert post().json()['url'].endswith('token=new.token')
    assert linked[2] == [SUB, SUB]
    assert linked[0].store.path.read_bytes() == before


def test_binding_change_during_network_read_blocks_url(linked, monkeypatch):
    def read(sub):
        with linked[0].store.connect() as db:
            db.execute('UPDATE bindings SET customer_id=? WHERE account_id=?', ('ctm_' + 'z'*26, 'A'))
        return linked[1]
    monkeypatch.setattr(portal, 'LiveClient', lambda key: type('Client', (), {'subscription': staticmethod(read)})())
    response = post()
    assert response.status == 409 and 'synthetic.token' not in response.text
