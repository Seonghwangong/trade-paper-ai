import pytest
from reportlab import rl_config
from app import invoice, packing
from tests.test_invoice_seller_snapshot import _request


@pytest.mark.parametrize('currency, expected', [('EUR', 'EUR'), (' jpy ', 'JPY'), ('', 'USD'), (None, 'USD')])
def test_invoice_pdf_and_list_use_record_currency(currency, expected, monkeypatch):
    record = {'invoice_no': 'INV-CURRENCY', 'seller': 'Sample Exporter', 'buyer': 'Sample Buyer',
              'currency': currency, 'items': [{'name': 'Sample Item', 'quantity': 2, 'unit_price': 25}]}
    monkeypatch.setattr(rl_config, 'pageCompression', 0)
    pdf = invoice.create_invoice_pdf(record, {})
    assert f'{expected} 25.00'.encode() in pdf.body
    assert f'TOTAL: {expected} 50.00'.encode() in pdf.body
    if expected != 'USD':
        assert b'USD' not in pdf.body
    monkeypatch.setattr(invoice, 'load_invoices', lambda account_id: [record])
    monkeypatch.setattr(packing, 'load_packing_lists', lambda account_id: [])
    listing = invoice.invoice_list(_request('account-a')).body.decode()
    assert f'{expected} 50' in listing
