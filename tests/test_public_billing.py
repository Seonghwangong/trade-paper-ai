"""Customer release changes eligibility, never account ownership or paid proof."""
import pytest
from app import auth, public_billing, subscription, landing, release_pages, toss_payments
from app import paddle_live_checkout as buy
from tests.test_paddle_live_checkout import ready, headers
from tests.test_paddle_live_actions import offer_config
from tests.test_paddle_live_store import store, send, completion, event, TXN
from tests.test_paddle_live_manage import http


@pytest.fixture
def public(ready, monkeypatch):
    monkeypatch.setenv('TRADE_PAPER_PADDLE_LIVE_PUBLIC_CHECKOUT', '1')
    monkeypatch.setenv('TRADE_PAPER_PADDLE_LIVE_PILOT_ACCOUNTS', 'other-owner')
    return ready


def test_public_owner_flow_still_requires_signed_paid_evidence(public):
    ledger, client, user = public
    assert http(buy.PATH).status == 200
    assert http(buy.PATH + '/status').json()['phase'] == 'ready'
    assert http(buy.PATH, 'POST', {}).status == 403
    assert client.creates == 0
    assert http(buy.PATH, 'POST', headers()).json()['transaction_id'] == TXN
    assert http(buy.PATH, 'POST', headers()).json()['transaction_id'] == TXN
    assert client.creates == 1
    assert http(buy.PATH + '/status').json()['phase'] == 'pending'
    send(ledger, completion())
    assert http(buy.PATH + '/status').json()['phase'] == 'linked'
    send(ledger, event(2))
    assert http(buy.PATH + '/status').json()['phase'] == 'confirmed'
    assert http(buy.PATH, 'POST', headers()).status == 409


@pytest.mark.parametrize('role', ['Viewer', 'Admin', 'Staff', ''])
def test_public_release_does_not_remove_owner_authorization(public, role):
    _, client, user = public
    user['role'] = role
    assert http(buy.PATH).status == 403
    assert http(buy.PATH, 'POST', headers()).status == 403
    assert client.creates == 0


def test_public_release_requires_login_and_ignores_forged_account(public, monkeypatch):
    _, client, _ = public
    bad = {**headers(), 'X-Billing-CSRF': buy.manage.csrf_token('B', buy.purpose(buy.expected_offer(buy.runtime.price_id())))}
    assert http(buy.PATH, 'POST', bad).status == 403
    monkeypatch.setattr(auth, 'current_user', lambda request: None)
    assert http(buy.PATH).status == 303
    assert http(buy.PATH, 'POST', headers()).status == 303
    assert client.creates == 0


@pytest.mark.parametrize('flag', ['PUBLIC_CHECKOUT', 'CHECKOUT', 'WEBHOOK', 'ACCESS', 'MANAGE', 'CANCEL'])
def test_missing_flag_closes_public_purchase_and_copy(public, monkeypatch, flag):
    _, client, _ = public
    monkeypatch.delenv('TRADE_PAPER_PADDLE_LIVE_' + flag)
    assert not public_billing.enabled()
    assert http(buy.PATH).status in (403, 404)
    assert 'not active yet' in landing.landing_page().body.decode()
    assert client.creates == 0


def test_public_pages_and_existing_purchase_link_agree(public):
    _, client, _ = public
    for route in ('/starter', '/pricing', '/subscription', '/getting-started', '/privacy', '/terms', '/refund-policy', '/founding-beta'):
        response = http(route)
        assert response.status == 200, (route, response.status)
        assert 'not active yet' not in response.text, route
        assert 'cannot be activated yet' not in response.text, route
    assert public_billing.NOTICE in landing.landing_page().body.decode()
    assert 'href="/subscription/paddle-buy">Subscribe to Starter' in http('/starter').text
    assert 'href="/subscription/paddle-buy">Subscribe to Starter' in http('/subscription').text
    response = http('/subscription/checkout', query=b'plan=Starter&lang=ko')
    assert response.status == 303
    assert response.headers['location'] == '/subscription/paddle-buy?lang=ko'
    assert http('/subscription/checkout', query=b'plan=Professional').status == 400
    assert http('/subscription/plan', 'POST', {'Content-Type': 'application/x-www-form-urlencoded'}, b'plan=Starter').status == 403
    assert client.creates == 0


def test_public_switch_is_strict_and_does_not_treat_wildcard_as_release(ready, monkeypatch):
    _, client, user = ready
    user['account_id'] = 'stranger'
    monkeypatch.setenv('TRADE_PAPER_PADDLE_LIVE_PILOT_ACCOUNTS', '*')
    for value in ('', '0', 'true', '*'):
        monkeypatch.setenv('TRADE_PAPER_PADDLE_LIVE_PUBLIC_CHECKOUT', value)
        assert http(buy.PATH).status == 403
    assert client.creates == 0


def test_logged_out_starter_has_language_picker_without_charge(public, monkeypatch):
    _, client, _ = public
    monkeypatch.setattr(auth, 'current_user', lambda request: None)
    page = http('/starter', query=b'lang=ko')
    assert page.status == 200
    assert 'data-language="ko"' in page.text
    assert 'workspace-language.js' in page.text
    assert client.creates == 0
