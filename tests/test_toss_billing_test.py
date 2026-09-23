import json
from io import BytesIO
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app import toss_billing_test as billing, toss_payments


def request(owner="account-a", state="", role="Owner"):
    return Request({"type": "http", "method": "GET", "path": "/subscription/billing-test/success", "headers": [(b"cookie", (billing.COOKIE + "=" + state).encode())],
        "trade_paper_user": {"account_id": owner, "role": role}})


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setenv("TRADE_PAPER_TOSS_TEST_BILLING", "true")
    monkeypatch.setenv("TRADE_PAPER_TOSS_CLIENT_KEY", "test_ck_example")
    monkeypatch.setenv("TRADE_PAPER_TOSS_SECRET_KEY", "test_sk_example")
    monkeypatch.setenv("TRADE_PAPER_PUBLIC_BASE_URL", "https://www.tradepaper.ai")


def test_checkout_contains_only_public_key_and_secure_bound_cookie(configured):
    response = toss_payments.checkout_preparation(request())
    body = response.body.decode()
    assert 'https://js.tosspayments.com/v2/standard' in body
    assert "test_ck_example" in body and "test_sk_example" not in body
    assert "requestBillingAuth" in body and "requestPayment" not in body
    assert "account-a" not in body
    config = json.loads(body.split('const billingReview = ')[1].split(';\n')[0])
    state = parse_qs(urlsplit(config['successUrl']).query)['state'][0]
    assert billing.validate_state(request(state=state), state) == config['customerKey']
    cookie = response.headers['set-cookie']
    for flag in ('HttpOnly', 'Secure', 'SameSite=lax', 'Max-Age=900'):
        assert flag in cookie
    assert response.headers['cache-control'] == 'no-store'
    assert response.headers['referrer-policy'] == 'no-referrer'


@pytest.mark.parametrize('key,value', [
    ('TRADE_PAPER_TOSS_TEST_BILLING','false'),
    ('TRADE_PAPER_TOSS_CLIENT_KEY','live_ck_example'),
    ('TRADE_PAPER_TOSS_SECRET_KEY','live_sk_example'),
    ('TRADE_PAPER_PUBLIC_BASE_URL','https://evil.test/path'),
])
def test_disabled_or_live_config_cannot_start(configured, monkeypatch, key, value):
    monkeypatch.setenv(key,value)
    assert not billing.enabled()
    assert 'requestBillingAuth' not in toss_payments.checkout_preparation(request()).body.decode()


def test_cross_account_missing_cookie_tampering_expiry_and_viewer_denied(configured, monkeypatch):
    state, customer = billing.new_state('account-a')
    for req, token in [(request('account-b',state),state), (request(),state), (request(state=state),state+'x'), (request('',state),state), (request(state=state,role='Viewer'),state)]:
        with pytest.raises(HTTPException) as exc:
            billing.validate_state(req,token)
        assert exc.value.status_code in (400,401,403)
    now = billing.time.time()
    monkeypatch.setattr(billing.time,'time',lambda: now+901)
    with pytest.raises(HTTPException) as exc:
        billing.validate_state(request(state=state),state)
    assert exc.value.status_code == 400


def test_success_verifies_server_side_without_exposing_or_storing_keys(configured, monkeypatch):
    state, customer = billing.new_state('account-a')
    calls=[]
    def verify(secret, auth_key, received_customer):
        calls.append((secret,auth_key,received_customer))
        return True
    monkeypatch.setattr(billing,'verify_registration',verify)
    response = billing.billing_test_success(request(state=state),state,customer,'one-use-auth')
    assert calls == [('test_sk_example','one-use-auth',customer)]
    assert response.status_code == 200
    assert 'Test card registration verified' in response.body.decode()
    assert 'one-use-auth' not in response.body.decode() and customer not in response.body.decode()
    assert 'Max-Age=0' in response.headers['set-cookie']
    calls.clear()
    response = billing.billing_test_success(request(state=state),state,'wrong','one-use-auth')
    assert response.status_code == 400 and calls == []
    monkeypatch.setattr(billing,'verify_registration',lambda *args: False)
    assert billing.billing_test_success(request(state=state),state,customer,'expired-auth').status_code == 502


def test_fail_never_reflects_provider_messages(configured):
    state,_=billing.new_state('account-a')
    response=billing.billing_test_fail(request(state=state),state)
    assert response.status_code == 200
    assert 'cancelled or failed' in response.body.decode()
    assert 'history.replaceState' in response.body.decode()


def test_exchange_validates_provider_customer_and_sanitizes_errors(monkeypatch):
    class Response(BytesIO):
        status=200
    def opener(req,timeout):
        assert req.full_url == billing.ENDPOINT and timeout == 10
        assert json.loads(req.data) == {'authKey':'auth', 'customerKey':'customer'}
        return Response(json.dumps({'customerKey':'customer','billingKey':'private-billing-key'}).encode())
    monkeypatch.setattr(billing,'urlopen',opener)
    assert billing.verify_registration('test_sk_example','auth','customer') is True
    monkeypatch.setattr(billing,'urlopen',lambda *a,**k: Response(b'{"customerKey":"other","billingKey":"key"}'))
    assert billing.verify_registration('test_sk_example','auth','customer') is False
    def failed(*a,**k):
        raise HTTPError(billing.ENDPOINT,400,'private-error',{},None)
    monkeypatch.setattr(billing,'urlopen',failed)
    assert billing.verify_registration('test_sk_example','auth','customer') is False


@pytest.mark.browser
@pytest.mark.parametrize('browser_name', ['chromium', 'webkit'])
@pytest.mark.parametrize('width', [390, 1280])
def test_billing_review_browser(configured, browser_name, width):
    from playwright.sync_api import sync_playwright
    body = toss_payments.checkout_preparation(request()).body.decode()
    with sync_playwright() as playwright:
        browser = getattr(playwright, browser_name).launch(headless=True)
        try:
            page = browser.new_page(viewport={'width':width,'height':900})
            page.route('https://js.tosspayments.com/v2/standard', lambda route: route.fulfill(content_type='application/javascript', body='''window.TossPayments = key => ({payment: customer => ({requestBillingAuth: async options => {window.testBillingCall = {key, customer, options}; throw new Error('test cancellation');}})});'''))
            page.set_content(body)
            page.get_by_role('button',name='Open test card registration').click()
            assert page.get_by_role('alert').inner_text() == 'Please confirm that you understand this is a test.'
            assert page.evaluate('window.testBillingCall === undefined')
            page.get_by_role('checkbox').check()
            page.get_by_role('button',name='Open test card registration').click()
            page.wait_for_function('window.testBillingCall !== undefined')
            call = page.evaluate('window.testBillingCall')
            assert call['options']['method'] == 'CARD'
            assert call['options']['windowTarget'] == 'self'
            assert call['options']['successUrl'].startswith('https://www.tradepaper.ai/subscription/billing-test/success?state=')
            assert 'test_sk_' not in json.dumps(call)
            assert page.get_by_role('button',name='Open test card registration').is_enabled()
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        finally:
            browser.close()


def test_expired_login_does_not_copy_authorization_into_login_url(monkeypatch):
    import asyncio
    from app import auth
    monkeypatch.setattr(auth,'current_user',lambda request: None)
    messages=[]
    async def send(message):
        messages.append(message)
    async def receive():
        return {'type':'http.request','body':b''}
    async def downstream(*args):
        pytest.fail('Unauthenticated callback must not reach provider')
    scope={'type':'http','method':'GET','scheme':'https','server':('www.tradepaper.ai',443),
           'path':'/subscription/billing-test/success','query_string':b'authKey=private&customerKey=customer','headers':[]}
    asyncio.run(auth.AuthenticationMiddleware(downstream)(scope,receive,send))
    location=dict(messages[0]['headers'])[b'location'].decode()
    assert 'private' not in location and 'authKey' not in location
    assert location == '/login?next=%2Fsubscription%2Fcheckout%3Fplan%3DStarter'
