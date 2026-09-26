from copy import deepcopy
import json
import re

import pytest

from app import paddle_live_payment_link as link, paddle_live_runtime as runtime
from tests.test_paddle_live_checkout import ready, headers as purchase_headers
from tests.test_paddle_live_actions import offer_config, Client
from tests.test_paddle_live_manage import http, ORIGIN
from tests.test_paddle_live_store import store, PRICE, TXN, SUB, CUSTOMER, completion, event, send

LINK_TXN = 'txn_' + 'e'*26


@pytest.fixture
def pending(ready, monkeypatch):
    monkeypatch.setenv('TRADE_PAPER_PADDLE_LIVE_PAYMENT_LINK', '1')
    monkeypatch.setenv('TRADE_PAPER_PADDLE_LIVE_PAYMENT_METHOD', '1')
    assert http(link.buy.PATH, 'POST', purchase_headers()).status == 200
    monkeypatch.setattr(link, 'LiveClient', lambda key: ready[1])
    return ready


def page(txn=TXN, query=None):
    return http(link.PATH, query=('_ptxn=' + txn).encode() if query is None else query)


def config(response):
    return json.loads(re.search(r'const config=(.*?), el=id=>', response.text).group(1))


def launch(c, **changes):
    return http(link.PATH + '/open', 'POST', {**{
        'Origin': ORIGIN, 'Sec-Fetch-Site': 'same-origin',
        'X-Billing-CSRF': c['csrf'], 'X-Billing-Context': c['context'],
        'X-Billing-Transaction': c['transaction'], 'X-Billing-Confirm': 'open-existing-payment'}, **changes})


def existing(pending, kind, monkeypatch):
    store, client, _ = pending
    send(store, completion()); send(store, event(2))
    data = completion()['data']
    data.update(id=LINK_TXN, origin='subscription_payment_method_change' if kind == 'payment-method' else 'subscription_recurring',
                status='ready' if kind == 'payment-method' else 'past_due', payments=[])
    if kind == 'payment-method':
        for key in ('subtotal', 'discount', 'tax', 'total', 'grand_total', 'grand_total_tax', 'credit', 'credit_to_balance', 'balance'):
            data['details']['totals'][key] = '0'
        for key in data['details']['adjusted_totals']:
            if key != 'currency_code': data['details']['adjusted_totals'][key] = '0'
        for name in ('totals', 'unit_totals'):
            data['details']['line_items'][0][name] = {key: '0' for key in ('subtotal', 'discount', 'tax', 'total')}
        data['items'][0]['proration'] = {'rate': '0'}
    else:
        data['details']['totals']['balance'] = '29000'
    sub = {'id': SUB, 'customer_id': CUSTOMER, 'collection_mode': 'automatic',
           'status': 'active' if kind == 'payment-method' else 'past_due'}
    client.transaction = lambda txn: deepcopy(data)
    client.subscription = lambda sub_id: deepcopy(sub)
    for flag in ('CHECKOUT', 'ACCESS', 'CANCEL'):
        monkeypatch.delenv('TRADE_PAPER_PADDLE_LIVE_' + flag, raising=False)
    return data, sub


def test_purchase_resumes_only_reserved_transaction_without_provider_write(pending):
    store, client, _ = pending; before = store.path.read_bytes()
    response = page(); assert response.status == 200
    c = config(response)
    assert c['kind'] == 'purchase' and 'token' not in c
    assert 'live_publictest' not in response.text
    assert response.headers['referrer-policy'] == 'no-referrer'
    assert response.text.index('history.replaceState') < response.text.index('src="https://cdn.paddle.com')
    assert launch(c).json() == {'transaction_id': TXN, 'token': 'live_publictest'}
    assert launch(c).status == 200
    assert client.creates == 1 and store.path.read_bytes() == before


@pytest.mark.parametrize('kind', ['payment-method', 'overdue'])
def test_existing_subscription_works_with_sales_disabled_without_writes(pending, monkeypatch, kind):
    existing(pending, kind, monkeypatch)
    before = pending[0].path.read_bytes()
    response = page(LINK_TXN); assert response.status == 200
    c = config(response)
    assert c['kind'] == kind
    assert c['amount'] == ('0' if kind == 'payment-method' else '29000')
    assert launch(c).json()['transaction_id'] == LINK_TXN
    assert pending[0].path.read_bytes() == before and pending[1].creates == 1


@pytest.mark.parametrize('query', [b'', b'_ptxn=', b'_ptxn=x', b'_ptxn=x&_ptxn=y', b'_ptxn=%3Cscript%3E'])
def test_bad_links_fail_before_ledger_or_provider(pending, monkeypatch, query):
    monkeypatch.setattr(runtime, 'store', lambda **kw: pytest.fail('No ledger access'))
    assert page(query=query).status == 400


@pytest.mark.parametrize('flag', ['PAYMENT_LINK', 'MANAGE'])
def test_default_off_before_storage(pending, monkeypatch, flag):
    c = config(page())
    monkeypatch.delenv('TRADE_PAPER_PADDLE_LIVE_' + flag)
    monkeypatch.setattr(runtime, 'store', lambda **kw: pytest.fail('No ledger access'))
    assert page().status == launch(c).status == 404


@pytest.mark.parametrize('role', ['Viewer', 'Admin', 'Editor'])
def test_owner_only(pending, role):
    c = config(page()); pending[2]['role'] = role
    assert page().status == launch(c).status == 403


def test_other_account_and_unregistered_transaction_fail_closed(pending):
    assert page(LINK_TXN).status == 404
    pending[2]['account_id'] = 'B'
    assert page().status == 404


@pytest.mark.parametrize('flag', ['CHECKOUT', 'ACCESS', 'WEBHOOK', 'CANCEL'])
def test_initial_purchase_still_requires_sales_gates(pending, monkeypatch, flag):
    monkeypatch.delenv('TRADE_PAPER_PADDLE_LIVE_' + flag)
    assert page().status in (404, 503)
    assert pending[1].creates == 1


def test_expired_purchase_or_completed_binding_never_reopens(pending):
    with pending[0].connect() as db:
        db.execute('UPDATE live_operations SET started=1')
    assert page().status == 409
    send(pending[0], completion())
    assert page().status == 409


@pytest.mark.parametrize('change', [
    {'Origin': 'https://evil.test'}, {'X-Billing-CSRF': 'bad'}, {'X-Billing-Context': 'bad'},
    {'X-Billing-Confirm': 'other'}, {'Sec-Fetch-Site': 'cross-site'},
])
def test_launch_rejects_csrf_before_provider(pending, monkeypatch, change):
    c = config(page())
    monkeypatch.setattr(link, 'selection', lambda *a: pytest.fail('No provider reads'))
    assert launch(c, **change).status == 403


def test_launch_token_bound_to_account_transaction_and_terms(pending, monkeypatch):
    c = config(page())
    pending[2]['account_id'] = 'B'
    assert launch(c).status == 403
    pending[2]['account_id'] = 'A'
    assert launch(c, **{'X-Billing-Transaction': LINK_TXN}).status == 404
    monkeypatch.setenv('TRADE_PAPER_PADDLE_LIVE_TAX_MODE', 'external')
    assert launch(c).status == 503


@pytest.mark.parametrize('patch', [
    {'id': TXN}, {'customer_id': 'ctm_' + 'z'*26}, {'subscription_id': 'sub_' + 'z'*26},
    {'origin': 'subscription_update'}, {'status': 'completed'}, {'collection_mode': 'manual'},
    {'currency_code': 'USD'}, {'discount_id': 'dsc_discount'}, {'items': []}, {'details': {}},
])
def test_existing_link_ownership_and_terms_fail_closed(pending, monkeypatch, patch):
    data, _ = existing(pending, 'overdue', monkeypatch); data.update(patch)
    response = page(LINK_TXN)
    assert response.status in (404, 503) and 'live_publictest' not in response.text


@pytest.mark.parametrize('kind', ['payment-method', 'overdue'])
def test_card_update_amount_and_partially_paid_renewal_blocked(pending, monkeypatch, kind):
    data, _ = existing(pending, kind, monkeypatch)
    data['details']['totals']['balance'] = '1'
    assert page(LINK_TXN).status == 503


def test_recheck_before_click_rejects_new_charge_or_completed_payment(pending, monkeypatch):
    data, _ = existing(pending, 'payment-method', monkeypatch)
    c = config(page(LINK_TXN))
    data['details']['totals']['grand_total'] = '29000'
    assert launch(c).status == 503
    data['status'] = 'completed'
    assert launch(c).status == 503


def test_changed_total_requires_fresh_consent(pending, monkeypatch):
    data, _ = existing(pending, 'overdue', monkeypatch)
    monkeypatch.setenv('TRADE_PAPER_PADDLE_LIVE_TAX_MODE', 'external')
    data['items'][0]['price']['tax_mode'] = 'external'
    for target in (data['details']['totals'], data['details']['adjusted_totals']):
        target.update(subtotal='29000', tax='2900', total='31900', grand_total='31900', grand_total_tax='2900')
    data['details']['totals']['balance'] = '31900'
    money = dict(subtotal='29000', discount='0', tax='2900', total='31900')
    data['details']['line_items'][0].update(totals=money, unit_totals=money)
    c = config(page(LINK_TXN))
    for target in (data['details']['totals'], data['details']['adjusted_totals']):
        target.update(tax='3000', total='32000', grand_total='32000', grand_total_tax='3000')
    data['details']['totals']['balance'] = '32000'
    money = dict(subtotal='29000', discount='0', tax='3000', total='32000')
    data['details']['line_items'][0].update(totals=money, unit_totals=money)
    assert launch(c).status == 409


def test_review_or_subscription_change_blocks_launch(pending, monkeypatch):
    _, sub = existing(pending, 'overdue', monkeypatch)
    c = config(page(LINK_TXN)); sub['status'] = 'canceled'
    assert launch(c).status == 503
    monkeypatch.setattr(link, 'needs_review', lambda *args: True)
    assert page(LINK_TXN).status == 409


def test_missing_ledger_not_created(pending, monkeypatch, tmp_path):
    path = tmp_path / 'absent.sqlite3'
    monkeypatch.setattr(runtime, 'data_path', lambda name: path)
    assert page().status == 503 and not path.exists()


@pytest.mark.parametrize('kind', ['payment-method', 'overdue'])
@pytest.mark.parametrize('change', ['quantity', 'proration', 'line', 'credit', 'captured'])
def test_ambiguous_money_and_items_are_rejected(pending, monkeypatch, kind, change):
    data, _ = existing(pending, kind, monkeypatch)
    if change == 'quantity': data['items'][0]['quantity'] = True
    if change == 'proration': data['items'][0]['proration'] = {'rate': '0.5'}
    if change == 'line': data['details']['line_items'][0]['unit_totals']['total'] = '1'
    if change == 'credit': data['details']['totals']['credit'] = '1'
    if change == 'captured': data['payments'] = [{'status': 'captured', 'amount': '1'}]
    assert page(LINK_TXN).status == 503


def test_token_expiry_and_payment_method_flag(pending, monkeypatch):
    existing(pending, 'payment-method', monkeypatch)
    c = config(page(LINK_TXN)); now = link.manage.time.time()
    with monkeypatch.context() as m:
        m.setattr(link.manage.time, 'time', lambda: now + 901)
        assert launch(c).status == 403
    monkeypatch.delenv('TRADE_PAPER_PADDLE_LIVE_PAYMENT_METHOD')
    assert page(LINK_TXN).status == launch(c).status == 404


def test_review_arrives_during_provider_read(pending, monkeypatch):
    data, _ = existing(pending, 'overdue', monkeypatch)
    review = {'pending': False}
    monkeypatch.setattr(link, 'needs_review', lambda *a: review['pending'])
    def transaction(txn):
        review['pending'] = True
        return deepcopy(data)
    pending[1].transaction = transaction
    assert page(LINK_TXN).status == 409
