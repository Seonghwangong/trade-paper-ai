import json

import pytest
from fastapi import HTTPException
from app import invoice
from app.validation import DataValidationError
from tests.test_invoice_numbers import store, create
from tests.test_invoice_seller_snapshot import _request


def prepare(store):
    saved = create()
    records = json.loads(store.read_text())
    records[0]['items'].extend([
        {'name': 'Second', 'quantity': 2, 'unit_price': 3, 'item_id': 'second', 'packing_notes': 'preserve'},
        {'name': 'Third', 'quantity': 5, 'unit_price': 7, 'item_id': 'third'},
    ])
    records[0]['seller_address'] = 'Original address'
    records[0]['shipment_id'] = 'SHIP-001'
    store.write_text(json.dumps(records))
    return records[0]


def submit(record, **overrides):
    kwargs = dict(seller='Sample Seller', buyer='Sample Buyer', currency='EUR',
                  buyer_address='', buyer_email='', item_name='First revised', hs_code='111111',
                  quantity='1.25', unit_price='0', origin='KR', unit='PCS',
                  revision=invoice._invoice_revision(record),
                  extra_name=['Second revised', 'Third revised'], extra_hs_code=['222222', ''],
                  extra_quantity=['2.5', '0'], extra_unit_price=['4.25', '9'],
                  extra_origin=['JP', ''], extra_unit=['KG', 'PCS'])
    kwargs.update(overrides)
    return invoice.update_invoice(record['invoice_no'], _request('account-a'), **kwargs)


def test_every_item_updates_without_losing_metadata(store):
    record = prepare(store)
    assert submit(record).status_code == 303
    saved = json.loads(store.read_text())[0]
    assert [i['name'] for i in saved['items']] == ['First revised', 'Second revised', 'Third revised']
    assert [i['quantity'] for i in saved['items']] == [1.25, 2.5, 0]
    assert [i['unit_price'] for i in saved['items']] == [0, 4.25, 9]
    assert saved['items'][1]['packing_notes'] == 'preserve'
    assert saved['items'][2]['item_id'] == 'third'
    assert saved['seller_address'] == 'Original address'
    assert saved['shipment_id'] == 'SHIP-001'


@pytest.mark.parametrize('change', [
    {'extra_quantity': ['NaN', '2']}, {'extra_unit_price': ['3', '-1']},
    {'extra_name': ['Second', ' ']}, {'extra_origin': ['JP']},
    {'extra_hs_code': None}, {'extra_quantity': ['2', '3', '4']},
])
def test_invalid_later_item_does_not_partially_save(store, change):
    record = prepare(store)
    before = store.read_bytes()
    with pytest.raises(DataValidationError):
        submit(record, **change)
    assert store.read_bytes() == before


@pytest.mark.parametrize('target', ['items', 'seller', 'archived_at'])
def test_stale_editor_cannot_overwrite_any_saved_change(store, target):
    record = prepare(store)
    changed = json.loads(store.read_text())
    if target == 'items':
        changed[0]['items'][1]['quantity'] = 500
    else:
        changed[0][target] = 'changed elsewhere'
    store.write_text(json.dumps(changed))
    before = store.read_bytes()
    with pytest.raises(HTTPException) as error:
        submit(record)
    assert error.value.status_code == 409
    assert store.read_bytes() == before


def test_extra_fields_require_revision(store):
    record = prepare(store)
    before = store.read_bytes()
    with pytest.raises(HTTPException) as error:
        submit(record, revision=None)
    assert error.value.status_code == 409
    assert store.read_bytes() == before


def test_cannot_edit_another_accounts_invoice(store):
    record = prepare(store)
    other = json.loads(store.read_text())
    other[0]['account_id'] = 'account-b'
    store.write_text(json.dumps(other))
    before = store.read_bytes()
    with pytest.raises(HTTPException) as error:
        submit(record)
    assert error.value.status_code == 404
    assert store.read_bytes() == before


def test_html_renders_all_items_and_escapes_values(store):
    record = prepare(store)
    record['seller'] = '\"><img src=x onerror=alert(1)>'
    record['items'][2]['name'] = '\"><script>alert(1)</script>'
    store.write_text(json.dumps([record]))
    page = invoice.edit_invoice(record['invoice_no'], _request('account-a')).body.decode()
    assert page.count('class="card invoice-item"') == 3
    assert page.count('name="extra_quantity"') == 2
    assert 'name="revision"' in page
    assert '<script>alert' not in page and '<img src=x' not in page
    assert '&lt;script&gt;' in page and '&lt;img src=x' in page
    assert '/static/invoice-edit.js' in page


def test_one_item_revision_form_has_no_extra_fields(store):
    record = create()
    submit(record, extra_name=None, extra_hs_code=None, extra_quantity=None,
           extra_unit_price=None, extra_origin=None, extra_unit=None)
    assert len(json.loads(store.read_text())[0]['items']) == 1


def test_native_repeated_form_fields_and_conflict_over_http(store):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from urllib.parse import urlencode
    from html.parser import HTMLParser

    class Fields(HTMLParser):
        def __init__(self):
            super().__init__()
            self.fields = []
        def handle_starttag(self, tag, attrs):
            values = dict(attrs)
            if tag == 'input' and values.get('name'):
                self.fields.append((values['name'], values.get('value', '')))

    record = prepare(store)
    app = FastAPI()
    @app.middleware('http')
    async def account(request, call_next):
        request.scope['trade_paper_user'] = {'account_id': 'account-a'}
        return await call_next(request)
    app.include_router(invoice.router)
    client = TestClient(app)
    page = client.get('/edit-invoice/' + record['invoice_no'])
    assert page.status_code == 200
    parser = Fields()
    parser.feed(page.text)
    fields = [(key, '8.5' if key == 'extra_quantity' else value) for key, value in parser.fields]
    payload = urlencode(fields)
    response = client.post('/update-invoice/' + record['invoice_no'], content=payload,
                           headers={'Content-Type': 'application/x-www-form-urlencoded'}, follow_redirects=False)
    assert response.status_code == 303, response.text
    saved = json.loads(store.read_text())[0]
    assert saved['items'][1]['quantity'] == 8.5
    assert saved['items'][2]['quantity'] == 8.5
    assert saved['items'][1]['hs_code'] == ''
    before = store.read_bytes()
    response = client.post('/update-invoice/' + record['invoice_no'], content=payload,
                           headers={'Content-Type': 'application/x-www-form-urlencoded'})
    assert response.status_code == 409
    assert store.read_bytes() == before
