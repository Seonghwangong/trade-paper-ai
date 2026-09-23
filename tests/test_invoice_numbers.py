import json

import pytest

from app import invoice
from app.validation import DataValidationError
from tests.test_invoice_seller_snapshot import _request


@pytest.fixture
def store(tmp_path, monkeypatch):
    path = tmp_path / 'invoices.json'
    path.write_text('[]')
    users = tmp_path / 'users.json'
    users.write_text(json.dumps([{'account_id': 'account-a'}]))
    monkeypatch.setattr(invoice, 'INVOICE_FILE', path)
    monkeypatch.setattr(invoice, 'USERS_FILE', users)
    monkeypatch.setattr(invoice, 'load_account_company', lambda *args: {})
    monkeypatch.setattr(invoice.buyer_module, 'load_buyers', lambda *args: [])
    monkeypatch.setattr(invoice, 'load_proformas', lambda *args: [])
    monkeypatch.setattr(invoice.product_module, 'enrich_items_from_products', lambda *args: None)
    return path


def create():
    return invoice.create_invoice(_request('account-a'), {
        'seller': 'Sample Seller', 'buyer': 'Sample Buyer', 'currency': 'EUR',
        'items': [{'name': 'Sample', 'quantity': '1.5', 'unit_price': '2.25'}],
    })


def update(number, quantity='2.5', unit_price='3.75'):
    return invoice.update_invoice(number, _request('account-a'), seller='Sample Seller',
        buyer='Sample Buyer', currency='EUR', buyer_address='', buyer_email='',
        item_name='Sample', hs_code='', quantity=quantity, unit_price=unit_price,
        origin='', unit='KG')


def test_fractional_values_remain_numeric_after_create_and_edit(store):
    saved = create()
    assert saved['items'][0]['quantity'] == 1.5
    assert saved['items'][0]['unit_price'] == 2.25
    update(saved['invoice_no'])
    item = json.loads(store.read_text())[0]['items'][0]
    assert item['quantity'] == 2.5
    assert item['unit_price'] == 3.75
    assert item['quantity'] * item['unit_price'] == 9.375


@pytest.mark.parametrize('value', ['abc', 'NaN', 'Infinity', '-1', True])
@pytest.mark.parametrize('field', ['quantity', 'unit_price'])
def test_invalid_edit_leaves_saved_invoice_unchanged(store, field, value):
    saved = create()
    before = store.read_bytes()
    with pytest.raises(DataValidationError):
        update(saved['invoice_no'], **{field: value})
    assert store.read_bytes() == before


@pytest.mark.parametrize('value', ['abc', 'NaN', 'Infinity', '-1', True])
def test_invalid_create_does_not_write_invoice(store, value):
    before = store.read_bytes()
    with pytest.raises(DataValidationError):
        invoice.create_invoice(_request('account-a'), {
            'seller': 'Sample', 'buyer': 'Sample',
            'items': [{'name': 'Item', 'quantity': value, 'unit_price': 1}],
        })
    assert store.read_bytes() == before
