import json
from datetime import date
from urllib.parse import urlencode

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app import document_import, invoice, packing


def request(account='a', role='Owner', source='INV-001'):
    return Request({'type': 'http', 'method': 'GET', 'path': '/import-document',
                    'headers': [], 'query_string': urlencode({'repeat_invoice': source}).encode(),
                    'trade_paper_user': {'account_id': account, 'email': 'owner@example.com', 'role': role}})


@pytest.fixture
def records(tmp_path, monkeypatch):
    path = tmp_path / 'invoices.json'
    original = {'account_id': 'a', 'invoice_no': 'INV-001', 'invoice_date': '2020-01-02',
                'seller': 'Previous Seller', 'seller_address': '', 'buyer': 'Buyer',
                'buyer_email': 'buyer@example.com', 'currency': 'EUR', 'trade_term': 'FOB',
                'payment_term': '30 days', 'shipment_no': 'SHP-009', 'pi_no': 'PI-008',
                'source_reference': 'OLD-PO', 'pdf_path': 'private.pdf', 'paid': True,
                'items': [{'name': 'Cup', 'quantity': 120, 'unit': 'PCS', 'unit_price': 3.25,
                           'hs_code': '123456', 'origin': 'KR', 'net_weight': 99}]}
    path.write_text(json.dumps([original]))
    monkeypatch.setattr(invoice, 'INVOICE_FILE', path)
    monkeypatch.setattr(document_import, 'load_account_company', lambda *args: {'name': 'Current Seller', 'address': 'Current address'})
    monkeypatch.setattr(packing, 'load_packing_lists', lambda *args: [])
    return path, original


def bootstrap(response):
    text = response.body.decode()
    return json.loads(text.split('<script id="import-config" type="application/json">')[1].split('</script>')[0])


def test_repeat_draft_is_read_only_preserves_values_and_omits_old_links(records):
    path, original = records
    before = path.read_bytes()
    response = document_import.import_page(request())
    draft = bootstrap(response)['repeat_draft']
    assert response.headers['cache-control'] == 'no-store'
    assert draft['fields']['invoice_date'] == date.today().isoformat()
    assert draft['fields']['seller'] == 'Previous Seller'
    assert draft['fields']['seller_address'] == ''
    assert draft['fields']['source_reference'] == ''
    assert draft['fields']['currency'] == 'EUR'
    assert draft['items'][0]['quantity'] == '120'
    assert draft['items'][0]['unit_price'] == '3.25'
    assert draft['items'][0]['hs_code'] == '123456'
    assert '2020-01-02' in draft['source_text']
    assert draft['restored'] is True  # Prevent company fallback over saved blank addresses.
    assert 'repeat_review' in draft['warnings']
    assert not {'invoice_no', 'pi_no', 'shipment_no', 'pdf_path', 'paid'} & draft['fields'].keys()
    assert 'net_weight' not in draft['items'][0]
    assert path.read_bytes() == before
    assert json.loads(path.read_text()) == [original]


@pytest.mark.parametrize('account,role,number,status', [('b','Owner','INV-001',404),
    ('a','Owner','INV-999',404), ('','Owner','INV-001',401), ('a','Viewer','INV-001',403)])
def test_repeat_guarded_before_source_exposure(records, account, role, number, status):
    path, _ = records
    before = path.read_bytes()
    with pytest.raises(HTTPException) as error:
        document_import.import_page(request(account, role, number))
    assert error.value.status_code == status
    assert path.read_bytes() == before


def test_archived_and_oversized_invoices_are_not_silently_partial(records):
    path, original = records
    for changes, code in [({'archived_at': '2026-11-01'}, 404),
                          ({'items': original['items'] * 101}, 422),
                          ({'buyer': 'x' * 1001}, 422)]:
        path.write_text(json.dumps([{**original, **changes}]))
        before = path.read_bytes()
        with pytest.raises(HTTPException) as error:
            document_import.import_page(request())
        assert error.value.status_code == code
        assert path.read_bytes() == before


def test_bootstrap_escapes_invoice_text_and_list_links_to_review(records):
    path, original = records
    original['buyer'] = '</script><script>alert(1)</script>'
    path.write_text(json.dumps([original]))
    response = document_import.import_page(request())
    assert original['buyer'] not in response.body.decode()
    assert bootstrap(response)['repeat_draft']['fields']['buyer'] == original['buyer']
    listing = invoice.invoice_list(request()).body.decode()
    assert 'Repeat order' in listing
    assert '/import-document?repeat_invoice=INV-001' in listing
