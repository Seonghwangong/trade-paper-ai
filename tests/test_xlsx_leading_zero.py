from io import BytesIO
from zipfile import ZipFile, ZIP_DEFLATED

import pytest

from app import document_import
from tests.test_document_import import workbook


def code_workbook(value, format_code='000000', text=False, formula=False):
    cell = (f'<c r="B2" s="1" t="inlineStr"><is><t>{value}</t></is></c>' if text
            else f'<c r="B2" s="1">' + ('<f>1+1</f>' if formula else '') + f'<v>{value}</v></c>')
    sheet = (
        '<row r="1"><c r="A1" t="inlineStr"><is><t>Description</t></is></c>'
        '<c r="B1" t="inlineStr"><is><t>HS Code</t></is></c>'
        '<c r="C1" t="inlineStr"><is><t>Qty</t></is></c>'
        '<c r="D1" t="inlineStr"><is><t>Unit Price</t></is></c></row>'
        '<row r="2"><c r="A2" t="inlineStr"><is><t>Fictional source item</t></is></c>'
        + cell + '<c r="C2"><v>12</v></c><c r="D2"><v>3</v></c></row>'
    )
    output = BytesIO()
    with ZipFile(BytesIO(workbook(sheet))) as source, ZipFile(output, 'w', ZIP_DEFLATED) as target:
        for name in source.namelist():
            target.writestr(name, source.read(name))
        if format_code is not None:
            target.writestr('xl/styles.xml',
                '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                f'<numFmts count="1"><numFmt numFmtId="164" formatCode="{format_code}"/></numFmts>'
                '<cellXfs count="2"><xf numFmtId="0"/><xf numFmtId="164"/></cellXfs></styleSheet>')
    return output.getvalue()


@pytest.mark.parametrize('value,format_code,expected', [
    ('10121', '000000', '010121'), ('123', '000000', '000123'),
    ('1234567', '000000', '1234567'), ('0', '000000', '000000'),
    ('00000123', '000000', '000123'), ('1', '00', '01'),
    ('10121', None, '10121'), ('10121', '0.00', '10121'),
    ('10121', '000000;000000;0', '10121'), ('0', '000000;000000;0', '0'),
    ('10121', '000-000', '10121'), ('12.5', '000000', '12.5'),
    ('-12', '000000', '-12'), ('1e3', '000000', '1e3'),
])
def test_preserve_only_explicit_whole_number_zero_padding(value, format_code, expected):
    rows, _, _ = document_import.spreadsheet_rows(code_workbook(value, format_code), 'source.xlsx')
    draft = document_import.parse_rows(rows)
    assert draft['items'][0]['hs_code'] == expected
    assert expected in draft['source_text']
    assert draft['items'][0]['quantity'] == '12'
    assert draft['items'][0]['unit_price'] == '3'


def test_numeric_text_is_not_padded_just_because_a_number_style_exists():
    rows, _, _ = document_import.spreadsheet_rows(code_workbook('10121', text=True), 'source.xlsx')
    assert document_import.parse_rows(rows)['items'][0]['hs_code'] == '10121'


def test_zero_padding_does_not_use_a_formula_cached_result():
    rows, _, warnings = document_import.spreadsheet_rows(code_workbook('10121', formula=True), 'source.xlsx')
    assert document_import.parse_rows(rows)['items'][0]['hs_code'] == ''
    assert 'formulas_skipped' in warnings
