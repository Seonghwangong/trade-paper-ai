import json
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from urllib.error import URLError

import pytest
from fastapi import HTTPException
from starlette.requests import Request
from app import paddle_sandbox_checkout as checkout
from app.paddle_sandbox_core import SandboxStore

PRICE = 'pri_' + 'a' * 26
TXN = 'txn_' + 'b' * 26
KEY = 'pdl_sdbx_apikey_' + 'c' * 26 + '_' + 'D' * 22 + '_abc'
ORIGIN = 'https://www.tradepaper.ai'


def request(account='a', role='Owner', csrf='', origin=ORIGIN):
    return Request({'type':'http', 'method':'POST', 'path':checkout.PATH,
        'headers':[(b'origin', origin.encode()), (b'x-paddle-test-csrf', csrf.encode())],
        'trade_paper_user':{'account_id':account, 'role':role}})


@pytest.fixture
def configured(monkeypatch, tmp_path):
    for name, value in {'CHECKOUT':'1', 'ENABLED':'1', 'API_KEY':KEY,
        'CLIENT_TOKEN':'test_public', 'PRICE_ID':PRICE, 'WEBHOOK_SECRET':'test-secret',
        'TEST_ACCOUNTS':'a,b'}.items():
        monkeypatch.setenv('TRADE_PAPER_PADDLE_SANDBOX_' + name, value)
    monkeypatch.setenv('TRADE_PAPER_PUBLIC_BASE_URL', ORIGIN)
    store = SandboxStore(tmp_path/'sandbox.sqlite3')
    monkeypatch.setattr(checkout, 'sandbox_store', lambda:store)
    return store


def test_page_contains_public_token_only_and_no_account_identifier(configured):
    response = checkout.checkout_page(request())
    body = response.body.decode()
    assert KEY not in body and 'test_public' in body
    assert 'sandbox:a' not in body and 'transactionId:data.transaction_id' in body
    assert "Environment.set('sandbox')" in body
    assert response.headers['cache-control'] == 'no-store'
    assert response.headers['referrer-policy'] == 'no-referrer'


@pytest.mark.parametrize('name,value,code', [('CHECKOUT','0',404), ('ENABLED','0',404),
    ('API_KEY',KEY.replace('sdbx','live'),503), ('CLIENT_TOKEN','live_token',503),
    ('PRICE_ID','bad',503), ('WEBHOOK_SECRET','',503)])
def test_configuration_fails_closed(configured, monkeypatch, name, value, code):
    monkeypatch.setenv('TRADE_PAPER_PADDLE_SANDBOX_' + name, value)
    with pytest.raises(HTTPException) as error:
        checkout.checkout_page(request())
    assert error.value.status_code == code


@pytest.mark.parametrize('account,role', [('', 'Owner'), ('stranger','Owner'), ('a','Viewer')])
def test_only_allowlisted_authenticated_accounts(configured, account, role):
    with pytest.raises(HTTPException) as error:
        checkout.checkout_page(request(account, role))
    assert error.value.status_code in (401,403)


def test_csrf_cross_account_cross_site_tampering_and_expiry(configured, monkeypatch):
    token = checkout.csrf_token('sandbox:a')
    for req in [request(csrf=''), request(csrf=token+'x'), request(csrf=token,origin='https://evil.test'), request('b',csrf=token)]:
        with pytest.raises(HTTPException) as error:
            checkout.start_checkout(req)
        assert error.value.status_code == 403
    now = checkout.time.time()
    monkeypatch.setattr(checkout.time, 'time', lambda:now+901)
    with pytest.raises(HTTPException):
        checkout.start_checkout(request(csrf=token))


def test_repeated_start_reuses_registered_transaction_and_owner(configured, monkeypatch):
    calls=[]
    monkeypatch.setattr(checkout, 'create_transaction', lambda key,price: calls.append((key,price)) or TXN)
    req = request(csrf=checkout.csrf_token('sandbox:a'))
    assert json.loads(checkout.start_checkout(req).body)['transaction_id'] == TXN
    assert json.loads(checkout.start_checkout(req).body)['transaction_id'] == TXN
    assert calls == [(KEY, PRICE)]
    with configured.connect() as db:
        assert db.execute('SELECT * FROM checkouts').fetchall() == [(TXN,'sandbox:a',PRICE)]
    assert configured.state('sandbox:a') is None


def test_uncertain_network_result_prevents_duplicate_creation(configured, monkeypatch):
    calls=[]
    def fail(*args):
        calls.append(1)
        raise HTTPException(502, 'Uncertain')
    monkeypatch.setattr(checkout, 'create_transaction', fail)
    with pytest.raises(HTTPException) as error:
        checkout.transaction_for('sandbox:a',KEY,PRICE)
    assert error.value.status_code == 502
    with pytest.raises(HTTPException) as error:
        checkout.transaction_for('sandbox:a',KEY,PRICE)
    assert error.value.status_code == 409
    assert len(calls) == 1


def test_expired_or_bound_checkout_cannot_restart(configured, monkeypatch):
    monkeypatch.setattr(checkout,'create_transaction',lambda *args:TXN)
    checkout.transaction_for('sandbox:a',KEY,PRICE,now=100)
    with pytest.raises(HTTPException):
        checkout.transaction_for('sandbox:a',KEY,PRICE,now=1001)
    configured.bind('sub_test','ctm_test','sandbox:a')
    with pytest.raises(HTTPException):
        checkout.transaction_for('sandbox:a',KEY,PRICE,now=101)


def test_concurrent_starts_create_one_transaction(configured, monkeypatch):
    calls=[]
    monkeypatch.setattr(checkout,'create_transaction',lambda *args:calls.append(1) or TXN)
    def start(_):
        try:
            return checkout.transaction_for('sandbox:a',KEY,PRICE)
        except HTTPException as error:
            return error.status_code
    with ThreadPoolExecutor(max_workers=8) as pool:
        results=list(pool.map(start,range(16)))
    assert len(calls)==1
    assert TXN in results and set(results) <= {TXN,409}


class Response(BytesIO):
    status=201


def test_provider_request_is_fixed_sandbox_without_user_data(configured, monkeypatch):
    captured=[]
    class Opener:
        def open(self, req, timeout):
            captured.append((req,timeout))
            return Response(json.dumps({'data':{'id':TXN,'status':'draft','collection_mode':'automatic',
                'items':[{'price':{'id':PRICE},'quantity':1}]}}).encode())
    monkeypatch.setattr(checkout,'build_opener',lambda handler:Opener())
    assert checkout.create_transaction(KEY,PRICE)==TXN
    req, timeout=captured[0]
    assert req.full_url=='https://sandbox-api.paddle.com/transactions'
    assert req.get_header('Authorization')=='Bearer '+KEY
    assert timeout==15
    assert json.loads(req.data)=={'collection_mode':'automatic','items':[{'price_id':PRICE,'quantity':1}]}
    assert checkout.NoRedirect().redirect_request(None,None,302,None,None,'https://evil.test') is None


@pytest.mark.parametrize('raw', [b'{}', b'not-json', b'x'*262145,
    json.dumps({'data':{'id':TXN,'status':'completed','collection_mode':'automatic','items':[]}}).encode()])
def test_invalid_provider_response_is_sanitized(configured,monkeypatch,raw):
    class Opener:
        def open(self,*args,**kwargs): return Response(raw)
    monkeypatch.setattr(checkout,'build_opener',lambda handler:Opener())
    with pytest.raises(HTTPException) as error:
        checkout.create_transaction(KEY,PRICE)
    assert error.value.status_code==502 and KEY not in error.value.detail


def test_real_app_requires_login_allowlist_and_csrf(configured, monkeypatch):
    from app import main, auth
    from tests.test_paddle_sandbox import TestClient
    client = TestClient(main.app)
    monkeypatch.setattr(auth, 'current_user', lambda request: None)
    assert client.post(checkout.PATH, content=b'{}').status_code == 303
    monkeypatch.setattr(auth, 'company_setup_complete', lambda *args: True)
    monkeypatch.setattr(auth, 'current_user', lambda request: {'account_id':'outsider','role':'Owner'})
    assert client.post(checkout.PATH, content=b'{}').status_code == 403
    monkeypatch.setattr(auth, 'current_user', lambda request: {'account_id':'a','role':'Owner'})
    assert client.post(checkout.PATH, content=b'{}').status_code == 403
    calls=[]
    monkeypatch.setattr(checkout,'create_transaction',lambda *args:calls.append(1) or TXN)
    headers={'Origin':ORIGIN, 'X-Paddle-Test-CSRF':checkout.csrf_token('sandbox:a')}
    response=client.post(checkout.PATH,content=b'{"account_id":"b","price":"other"}',headers=headers)
    assert response.status_code == 200 and calls==[1]
    with configured.connect() as db:
        assert db.execute('SELECT account_id FROM checkouts').fetchone()[0]=='sandbox:a'
