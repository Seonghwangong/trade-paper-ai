import asyncio
from io import BytesIO
import json
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app import document_import as subject


CSV = b'Seller,Demo Export Ltd\nBuyer,Example Buyer Ltd\nInvoice Date,2026-10-25\nCurrency,USD\nDescription,Qty,Unit,Unit Price,Amount\nCotton shirts,100,PCS,8.50,850\nSteel bolts,50,PCS,2,100\n'


def test_csv_and_positioned_text_preserve_values_without_inventing_fields():
    rows, sheets, warnings = subject.spreadsheet_rows(CSV, 'source.csv')
    draft = subject.parse_rows(rows)
    assert draft['fields']['buyer'] == 'Example Buyer Ltd'
    assert draft['fields']['invoice_date'] == '2026-10-25'
    assert draft['items'][0]['quantity'] == '100'
    assert draft['items'][0]['unit_price'] == '8.50'
    assert 'buyer_address' not in draft['fields']
    assert draft['warnings'] == []
    assert draft == subject.parse_rows(subject.text_rows(draft['source_text']))


def test_korean_headers_missing_prices_and_conflicting_parties_are_visible():
    result = subject.parse_rows([['수출자: 수출회사'], ['수입자: ABC'], ['수입자: XYZ'], ['작성일', '06/04/2026'], ['통화', '$'], ['품명', '수량', '단가'], ['면 티셔츠', '1,000', '8,50']])
    assert result['fields']['buyer'] == 'ABC'
    assert result['fields']['invoice_date'] == ''
    assert result['fields']['currency'] == ''
    assert result['items'][0]['quantity'] == '1000'
    assert result['items'][0]['unit_price'] == ''
    assert 'conflicting_buyer' in result['warnings']


@pytest.mark.parametrize('value', ['NaN', 'Infinity', '-1', '8,50', '1e3', '1000000000001', '=SUM(A1:A5)', '<script>', '12.00USD'])
def test_unreadable_numbers_never_become_zero_or_formulas(value):
    assert subject.number(value) == ''


def test_total_mismatch_and_duplicate_columns_require_review():
    result = subject.parse_rows([['Description','Qty','Unit Price','Amount'], ['T-shirt','10','5','500']])
    assert result['items'][0]['check_amount'] is True
    duplicate = subject.parse_rows([['Description','Qty','Qty'], ['A','1','2']])
    assert not duplicate['items']
    assert 'duplicate_headers' in duplicate['warnings']


def test_text_pdf_empty_columns_currency_prefix_and_footer_are_not_shifted_into_items():
    result = subject.parse_rows([
        ['SELLER','BUYER'],['Demo Export','Demo Import'],
        ['Item','HS Code','Qty','Unit Price','Total'],
        ['Shirts','','100','USD 8.50','USD 850.00'],
        ['TOTAL: USD 850.00'],['Authorized Signature:'],
    ])
    assert result['fields']['seller']=='Demo Export'
    assert result['fields']['buyer']=='Demo Import'
    assert result['fields']['currency']=='USD'
    assert len(result['items'])==1
    assert result['items'][0]['hs_code']==''
    assert result['items'][0]['quantity']=='100'
    assert result['items'][0]['unit_price']=='8.50'


def test_flat_spreadsheet_can_repeat_buyer_and_currency_columns():
    result=subject.parse_rows([['Buyer','Currency','Description','Qty','Unit Price'],['ABC','USD','Bolts','50','2']])
    assert result['fields']['buyer']=='ABC'
    assert result['fields']['currency']=='USD'
    assert result['items'][0]['name']=='Bolts'


def test_imported_other_seller_does_not_inherit_account_address(tmp_path,monkeypatch):
    from app import invoice, buyer, product
    file=tmp_path/'invoices.json';file.write_text('[]')
    monkeypatch.setattr(invoice,'INVOICE_FILE',file)
    monkeypatch.setattr(invoice,'load_account_company',lambda *a:{'name':'Own Company','address':'Private address','email':'own@example.com'})
    monkeypatch.setattr(buyer,'load_buyers',lambda *a:[])
    monkeypatch.setattr(product,'enrich_items_from_products',lambda *a:None)
    monkeypatch.setattr(invoice,'load_proformas',lambda *a:[])
    monkeypatch.setattr(invoice,'link_direct_document',lambda *a:None)
    monkeypatch.setattr(invoice,'record_request_audit',lambda *a,**k:None)
    result=invoice.create_invoice(request(b''),{'seller':'Other Seller','buyer':'Buyer','items':[{'name':'A','quantity':1,'unit_price':2}]})
    assert result['seller_address']==''
    assert result['seller_email']==''


def workbook(sheet='<row r="1"><c r="A1" t="inlineStr"><is><t>Description</t></is></c><c r="B1" t="inlineStr"><is><t>Qty</t></is></c><c r="C1" t="inlineStr"><is><t>Unit Price</t></is></c></row><row r="2"><c r="A2" t="inlineStr"><is><t>Bolts</t></is></c><c r="B2"><v>100</v></c><c r="C2"><f>1+1</f><v>2</v></c></row>', target='worksheets/sheet1.xml'):
    stream=BytesIO()
    with ZipFile(stream,'w',ZIP_DEFLATED) as z:
        z.writestr('xl/workbook.xml','<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Order" sheetId="1" r:id="rId1"/></sheets></workbook>')
        z.writestr('xl/_rels/workbook.xml.rels',f'<Relationships><Relationship Id="rId1" Target="{target}"/></Relationships>')
        z.writestr('xl/worksheets/sheet1.xml',f'<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>{sheet}</sheetData></worksheet>')
    return stream.getvalue()


def test_xlsx_formula_is_not_executed_or_replaced_by_stale_cached_value():
    rows,sheets,warnings=subject.spreadsheet_rows(workbook(),'source.xlsx')
    result=subject.parse_rows(rows)
    assert sheets==['Order']
    assert warnings==['formulas_skipped']
    assert result['items'][0]['quantity']=='100'
    assert result['items'][0]['unit_price']==''


def test_xlsx_refuses_traversal_oversized_archives_and_entities():
    with pytest.raises(ValueError): subject.spreadsheet_rows(workbook(target='../outside.xml'),'a.xlsx')
    with pytest.raises(ValueError): subject.spreadsheet_rows(workbook(sheet='<!DOCTYPE x>'),'a.xlsx')
    stream=BytesIO()
    with ZipFile(stream,'w',ZIP_DEFLATED) as z: z.writestr('large',b'0'*(21*1024*1024))
    with pytest.raises(ValueError,match='too_large'): subject.spreadsheet_rows(stream.getvalue(),'a.xlsx')


def request(body, content_type='application/json', user=None):
    async def receive(): return {'type':'http.request','body':body,'more_body':False}
    return Request({'type':'http','method':'POST','path':'/import-document/extract','headers':[(b'content-type',content_type.encode())],'query_string':b'','trade_paper_user':user or {'account_id':'a','role':'Owner'}},receive)


def test_extraction_is_read_only_and_account_guarded(tmp_path,monkeypatch):
    monkeypatch.chdir(tmp_path)
    response=asyncio.run(subject.extract(request(json.dumps({'text':'Buyer: Demo\nDescription\tQty\tUnit Price\nShirt\t2\t4'}).encode())))
    assert json.loads(response.body)['items'][0]['quantity']=='2'
    assert response.headers['cache-control']=='no-store'
    assert not list(tmp_path.iterdir())
    for user in ({'account_id':''},{'account_id':'a','role':'Viewer'}):
        with pytest.raises(HTTPException) as error: asyncio.run(subject.extract(request(b'{}',user=user)))
        assert error.value.status_code in (401,403)


def test_multipart_csv_is_bounded_and_parsed_after_reading_stream():
    body=b'--boundary\r\nContent-Disposition: form-data; name="file"; filename="source.csv"\r\nContent-Type: text/csv\r\n\r\n'+CSV+b'\r\n--boundary\r\nContent-Disposition: form-data; name="sheet"\r\n\r\n0\r\n--boundary--\r\n'
    response=asyncio.run(subject.extract(request(body,'multipart/form-data; boundary=boundary')))
    assert len(json.loads(response.body)['items'])==2
    with pytest.raises(HTTPException) as error: asyncio.run(subject.extract(request(b'x'*(subject.MAX_FILE+65537))))
    assert error.value.status_code==413


def test_preview_escapes_company_bootstrap_and_is_not_public(tmp_path,monkeypatch):
    file=tmp_path/'companies.json';file.write_text(json.dumps([{'account_id':'a','name':'</script><script>alert(1)</script>'}]))
    monkeypatch.setattr(subject,'ACCOUNT_COMPANIES_FILE',file)
    page=subject.import_page(request(b'')).body.decode()
    assert '\\u003c/script>' in page
    assert '</script><script>alert' not in page
    from app.auth import PUBLIC_PATHS
    assert '/import-document' not in PUBLIC_PATHS
    assert '/import-document/extract' not in PUBLIC_PATHS


def test_import_uses_existing_document_creation_and_never_sends_email():
    from app.subscription import DOCUMENT_CREATION_PATHS
    assert '/invoice' in DOCUMENT_CREATION_PATHS
    js=Path(subject.__file__).with_name('static').joinpath('document_import.js').read_text()
    assert "fetch('/invoice'" in js
    assert '.innerHTML' not in js
    assert "'/document-email" not in js
    assert 'isEvalSupported:false' in js
    assert "workerPath:'/static/vendor/" in js
