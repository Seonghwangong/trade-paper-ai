import json
import re
from copy import deepcopy
from io import BytesIO

import pytest
from starlette.requests import Request

from app import paddle_sandbox_payment_link as link
from tests.test_paddle_checkout import configured, ORIGIN
from tests.test_paddle_live_card_updates import card
from tests.test_paddle_live_renewals import renewal
from tests.test_paddle_live_store import PRICE, SUB, CUSTOMER


def request(account='a', role='Owner', query=None, headers=None, method='GET'):
    return Request({'type': 'http', 'method': method, 'path': link.PATH,
        'query_string': (query or '').encode(),
        'headers': [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()],
        'trade_paper_user': {'account_id': account, 'role': role}})


@pytest.fixture
def ready(configured, monkeypatch):
    monkeypatch.setenv('TRADE_PAPER_PADDLE_SANDBOX_PRICE_ID', PRICE)
    monkeypatch.setattr(link, 'data_path', lambda name: configured.path)
    configured.bind(SUB, CUSTOMER, 'sandbox:a')
    with configured.connect() as db:
        db.execute('INSERT INTO states VALUES (?,?,?,?,?,?)',
                   ('sandbox:a', SUB, 'active', 'Active', '2026-09-28T00:00:00Z', None))
    data = card()['data']; data['status'] = 'ready'
    monkeypatch.setattr(link, 'fetch_transaction', lambda key, txn: deepcopy(data))
    return configured, data


def page(ready):
    return link.payment_page(request(query='_ptxn=' + ready[1]['id']))


def launch_config(response):
    return json.loads(re.search(r'const config=(.*?), el=id=>', response.body.decode()).group(1))


def launch(c, account='a', **changes):
    headers = {'Origin': ORIGIN, 'Sec-Fetch-Site': 'same-origin',
               'X-Billing-CSRF': c['csrf'], 'X-Billing-Context': c['context'],
               'X-Billing-Transaction': c['transaction'], 'X-Billing-Confirm': 'open-existing-payment'}
    headers.update(changes)
    return link.open_payment(request(account=account, method='POST', headers=headers))


def test_existing_card_ui_uses_real_confirmation_and_no_ledger_write(ready):
    before = ready[0].path.read_bytes()
    response = page(ready); assert response.status_code == 200
    body = response.body.decode(); c = launch_config(response)
    assert c['kind'] == 'payment-method' and c['amount'] == '0'
    assert 'test_public' not in body and 'sandbox:a' not in body
    assert "Paddle.Environment.set('sandbox')" in body
    assert body.index('history.replaceState') < body.index('src="https://cdn.paddle.com')
    assert 'SANDBOX TEST ONLY' in body and 'No real money or paid access' in body
    assert "el('check-confirmation').addEventListener" in body
    assert response.headers['cache-control'] == 'no-store'
    assert response.headers['referrer-policy'] == 'no-referrer'
    assert json.loads(launch(c).body) == {'transaction_id': ready[1]['id'], 'token': 'test_public'}
    assert launch(c).status_code == 200
    assert ready[0].path.read_bytes() == before


@pytest.mark.parametrize('account,role', [('other','Owner'), ('a','Viewer'), ('a','Editor'), ('a','Admin'), ('','Owner')])
def test_no_provider_or_storage_before_authorization(ready, monkeypatch, account, role):
    monkeypatch.setattr(link, 'binding', lambda *_: pytest.fail('Unauthorized storage read'))
    assert link.payment_page(request(account, role, '_ptxn='+ready[1]['id'])).status_code in (401,403)


@pytest.mark.parametrize('flag', ['ENABLED', 'CHECKOUT'])
def test_disabled_before_database(ready, monkeypatch, flag):
    monkeypatch.setenv('TRADE_PAPER_PADDLE_SANDBOX_'+flag, '0')
    monkeypatch.setattr(link, 'binding', lambda *_: pytest.fail('Disabled storage read'))
    assert page(ready).status_code == 404


@pytest.mark.parametrize('query', ['', '_ptxn=x', '_ptxn=x&_ptxn=y'])
def test_invalid_lookup_rejected(ready, query):
    assert link.payment_page(request(query=query)).status_code == 400


@pytest.mark.parametrize('field,value', [('customer_id','ctm_'+'z'*26), ('subscription_id','sub_'+'z'*26), ('id','txn_'+'z'*26)])
def test_other_transaction_cannot_open(ready, field, value):
    txn = ready[1]['id']; ready[1][field] = value
    assert link.payment_page(request(query='_ptxn='+txn)).status_code == 404


@pytest.mark.parametrize('headers', [{'Origin':'https://evil.test'}, {'X-Billing-CSRF':'bad'},
    {'Sec-Fetch-Site':'cross-site'}, {'X-Billing-Confirm':'other'}])
def test_csrf_before_provider(ready, monkeypatch, headers):
    c = launch_config(page(ready))
    monkeypatch.setattr(link, 'fetch_transaction', lambda *_: pytest.fail('Invalid CSRF provider read'))
    assert launch(c, **headers).status_code == 403


def test_cross_account_and_changed_transaction_blocked(ready):
    c = launch_config(page(ready))
    assert launch(c, account='b').status_code == 403
    ready[1]['details']['totals']['grand_total'] = '29000'
    assert launch(c).status_code == 409


@pytest.mark.parametrize('kind', ['card','renewal'])
def test_completed_confirmation_cannot_reopen_and_preserves_ledger(ready, kind):
    store, data = ready
    if kind == 'renewal':
        data.clear(); data.update(renewal()['data']); data['status']='past_due'
        data['payments']=[]; data['details']['totals']['balance']='29000'
        with store.connect() as db: db.execute("UPDATE states SET provider_status='past_due'")
    c = launch_config(page(ready)); before=store.path.read_bytes()
    data['status']='completed'
    if kind == 'renewal':
        data['payments']=[{'status':'error','amount':'29000'},{'status':'captured','amount':'29000'}]
        data['details']['totals']['balance']='0'
    assert launch(c).status_code == 409
    response=link.confirmation(request(query='_ptxn='+data['id']))
    assert response.status_code == 200
    assert json.loads(response.body)['transaction_status']=='completed'
    assert store.path.read_bytes()==before


def test_missing_ledger_is_not_created(ready):
    ready[0].path.unlink()
    assert page(ready).status_code == 503
    assert not ready[0].path.exists()


def test_provider_reads_fixed_sandbox_transaction_only(monkeypatch):
    calls=[]
    class Response(BytesIO): status=200
    class Client:
        def open(self, req, timeout):
            calls.append(req)
            return Response(b'{"data":{"status":"ready"}}')
    monkeypatch.setattr(link, 'build_opener', lambda handler: Client())
    assert link.fetch_transaction('test-key', 'txn_'+'a'*26)['status']=='ready'
    assert calls[0].full_url=='https://sandbox-api.paddle.com/transactions/txn_'+'a'*26
    assert calls[0].method=='GET' and calls[0].data is None
