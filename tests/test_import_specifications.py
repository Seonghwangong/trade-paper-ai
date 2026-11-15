"""Keep explicit source specifications in the shared Invoice/Packing description."""
from io import BytesIO
from xml.sax.saxutils import escape

import pytest
from pypdf import PdfReader

from app import document_import as subject, invoice, packing
from app.packing_workflow import proposal
from tests.test_document_import import workbook


@pytest.mark.parametrize('header', ['규격', '제품 규격', 'Specification', 'Specifications', 'Spec', 'Specs'])
def test_explicit_specification_is_preserved_with_name_and_quantity(header):
    rows = [['품명', header, '수량', '단가'],
            ['머그', '450 ml / 80 × 110 mm', '12.5', '6'],
            ['머그', '300 ml / 70 × 90 mm', '24', '5']]
    draft = subject.parse_rows(rows)
    assert [r['name'] for r in draft['items']] == [
        '머그 / 450 ml / 80 × 110 mm', '머그 / 300 ml / 70 × 90 mm']
    assert [r['quantity'] for r in draft['items']] == ['12.5', '24']
    assert 'specification_joined' in draft['warnings']
    assert '450 ml / 80 × 110 mm' in draft['source_text']
    assert all('specification' not in r for r in draft['items'])


def test_empty_specs_do_not_change_existing_names_or_invent_product_names():
    result = subject.parse_rows([['Item', 'Spec', 'Qty', 'Unit Price'],
                                ['Mug', '', '2', '3'], ['', '450 ml', '2', '3']])
    assert [r['name'] for r in result['items']] == ['Mug']
    assert 'specification_joined' not in result['warnings']
    assert 'unrecognized_rows' in result['warnings']


def test_unrecognized_columns_are_not_assumed_to_be_product_specifications():
    result = subject.parse_rows([['Item', 'Box Size', 'Qty'], ['Mug', '40 x 50 x 60 cm', '2']])
    assert result['items'][0]['name'] == 'Mug'


def test_duplicate_spec_columns_remain_explicitly_ambiguous():
    result = subject.parse_rows([['Item', 'Spec', '규격', 'Qty'], ['Mug', '450 ml', '300 ml', '2']])
    assert result['items'] == []
    assert 'duplicate_headers' in result['warnings']


@pytest.mark.parametrize('kind', ['csv', 'xlsx', 'positioned_text'])
def test_specification_survives_source_extraction_and_both_pdfs(kind):
    rows = [['Description', 'Specification', 'Qty', 'Unit Price', 'Unit'],
            ['Fictional mug', '450 ml / SPEC-END-001', '2', '6', 'PCS']]
    if kind == 'csv':
        extracted, _, _ = subject.spreadsheet_rows(('\n'.join(','.join(r) for r in rows)).encode(), 'test.csv')
    elif kind == 'xlsx':
        sheet = ''.join('<row>' + ''.join(f'<c r="{chr(65+i)}{n}" t="inlineStr"><is><t>{escape(c)}</t></is></c>' for i,c in enumerate(row)) + '</row>' for n,row in enumerate(rows,1))
        extracted, _, _ = subject.spreadsheet_rows(workbook(sheet), 'test.xlsx')
    else:
        extracted = subject.text_rows('\n'.join('\t'.join(r) for r in rows))
    draft = subject.parse_rows(extracted)
    source = {'invoice_no':'INV-SPEC', 'seller':'Fictional Seller', 'buyer':'Fictional Buyer',
              'currency':'USD', 'invoice_date':'2026-11-15', 'items':draft['items']}
    # Same shared description feeds the existing Packing flow; no guessed weights.
    packed = proposal({'packing_no':'PK-SPEC','invoice_no':'INV-SPEC','items':[]}, source)
    assert packed['items'][0]['name'] == 'Fictional mug / 450 ml / SPEC-END-001'
    assert packed['items'][0]['quantity'] == '2'
    assert packed['items'][0]['net_weight'] == ''
    for row in packed['items']:
        row.update(carton=1, net_weight=1, gross_weight=2)  # explicitly fictional QA inputs
    for renderer, record in [(invoice.create_invoice_pdf, source),(packing.create_packing_list_pdf, packed)]:
        pdf = renderer(record, {})
        text = ''.join((p.extract_text() or '') for p in PdfReader(BytesIO(pdf.body)).pages)
        assert 'SPEC-END-001' in text
        assert '450 ml' in text
