import json
from datetime import date

import pytest
from reportlab import rl_config

from app import document_dates, invoice, packing, main
from app.validation import DataValidationError
from tests.test_invoice_seller_snapshot import _request


class FirstDay(date):
    @classmethod
    def today(cls):
        return cls(2026, 11, 5)


class LaterDay(date):
    @classmethod
    def today(cls):
        return cls(2027, 1, 2)


@pytest.fixture
def records(tmp_path, monkeypatch):
    inv = tmp_path / 'invoices.json'
    pk = tmp_path / 'packing_lists.json'
    inv.write_text('[]')
    pk.write_text('[]')
    monkeypatch.setattr(invoice, 'INVOICE_FILE', inv)
    monkeypatch.setattr(main, 'DATA_FILE', inv)
    monkeypatch.setattr(packing, 'PACKING_FILE', pk)
    monkeypatch.setattr(invoice, 'load_proformas', lambda account_id: [])
    monkeypatch.setattr(main.proforma_module, 'load_proformas', lambda account_id: [])
    monkeypatch.setattr(main.shipment_module, 'load_shipments', lambda account_id: [])
    monkeypatch.setattr(document_dates, 'date', FirstDay)
    monkeypatch.setattr(rl_config, 'pageCompression', 0)
    return inv, pk


def payload():
    return {'seller': 'Sample Seller', 'buyer': 'Sample Buyer',
            'items': [{'name': 'Sample Item', 'quantity': 2, 'unit_price': 10,
                       'carton': 1, 'net_weight': 2, 'gross_weight': 3}]}


@pytest.mark.parametrize('route', ['invoice', 'save_invoice', 'packing', 'packing_form'])
def test_creation_date_persists_across_later_downloads(records, monkeypatch, route):
    inv = invoice.create_invoice(_request('a'), payload())
    if route == 'invoice':
        record, field, renderer = inv, 'invoice_date', invoice.create_invoice_pdf
    elif route == 'save_invoice':
        record = main.save_invoice(payload(), 'a')
        field, renderer = 'invoice_date', invoice.create_invoice_pdf
    elif route == 'packing':
        record = packing.create_packing_list(_request('a'), {**payload(), 'invoice_no': inv['invoice_no']})
        field, renderer = 'packing_date', packing.create_packing_list_pdf
    else:
        packing.save_packing(_request('a'), invoice_no=inv['invoice_no'], seller='Sample Seller',
                             buyer='Sample Buyer', item_name=['Sample Item'], hs_code=[''],
                             carton=['1'], net_weight=['2'], gross_weight=['3'], item_id=[])
        record = json.loads(records[1].read_text())[-1]
        field, renderer = 'packing_date', packing.create_packing_list_pdf
    assert record[field] == '2026-11-05'
    before = [path.read_bytes() for path in records]
    monkeypatch.setattr(document_dates, 'date', LaterDay)
    for _ in range(2):
        pdf = renderer(record, {})
        assert b'Date: 2026-11-05' in pdf.body
        assert b'Date: 2027-01-02' not in pdf.body
    assert [path.read_bytes() for path in records] == before


def test_reviewed_import_date_is_preserved_on_create_and_edit(records, monkeypatch):
    inv = invoice.create_invoice(_request('a'), {**payload(), 'invoice_date': '2024-02-29'})
    assert inv['invoice_date'] == '2024-02-29'
    pk = packing.create_packing_list(_request('a'), {
        **payload(), 'invoice_no': inv['invoice_no'], 'packing_date': '2024-03-01'})
    monkeypatch.setattr(document_dates, 'date', LaterDay)
    invoice.update_invoice(inv['invoice_no'], _request('a'), seller='Sample Seller', buyer='Sample Buyer',
                           currency='USD', buyer_address='', buyer_email='', item_name='Revised',
                           hs_code='', quantity='2', unit_price='10')
    packing.update_packing(pk['packing_no'], _request('a'), inv['invoice_no'], 'Sample Seller',
                           'Sample Buyer', ['Revised'], ['2'], [''], ['1'], ['2'], ['3'], item_id=[])
    updated_inv = json.loads(records[0].read_text())[-1]
    updated_pk = json.loads(records[1].read_text())[-1]
    assert b'Date: 2024-02-29' in invoice.create_invoice_pdf(updated_inv, {}).body
    assert b'Date: 2024-03-01' in packing.create_packing_list_pdf(updated_pk, {}).body


@pytest.mark.parametrize('invalid', ['2026-02-30', '2026-13-01', 'tomorrow', '20261105'])
def test_invalid_dates_do_not_write_partial_records(records, invalid):
    inv = invoice.create_invoice(_request('a'), payload())
    before = [path.read_bytes() for path in records]
    for action in (
        lambda: invoice.create_invoice(_request('a'), {**payload(), 'invoice_date': invalid}),
        lambda: main.save_invoice({**payload(), 'invoice_date': invalid}, 'a'),
        lambda: packing.create_packing_list(_request('a'), {
            **payload(), 'invoice_no': inv['invoice_no'], 'packing_date': invalid}),
    ):
        with pytest.raises(DataValidationError):
            action()
        assert [path.read_bytes() for path in records] == before


@pytest.mark.parametrize('value', [None, '', 'old invalid value'])
def test_legacy_dates_are_not_invented_or_written(records, value):
    for field, renderer in [('invoice_date', invoice.create_invoice_pdf), ('packing_date', packing.create_packing_list_pdf)]:
        record = {**payload(), field: value}
        original = json.dumps(record)
        pdf = renderer(record, {})
        assert b'Date: Not recorded' in pdf.body
        assert b'Date: 2026-11-05' not in pdf.body
        assert json.dumps(record) == original
