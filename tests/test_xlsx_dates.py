from io import BytesIO
from zipfile import ZipFile, ZIP_DEFLATED

import pytest

from app import document_import
from tests.test_document_import import workbook


def dated_workbook(value, format_id=14, date1904=False, text=False):
    cell = (f'<c r="B1" s="1" t="inlineStr"><is><t>{value}</t></is></c>' if text
            else f'<c r="B1" s="1"><v>{value}</v></c>')
    sheet = (
        '<row r="1"><c r="A1" t="inlineStr"><is><t>Invoice Date</t></is></c>' + cell + '</row>'
        '<row r="2"><c r="A2" t="inlineStr"><is><t>Description</t></is></c>'
        '<c r="B2" t="inlineStr"><is><t>Qty</t></is></c>'
        '<c r="C2" t="inlineStr"><is><t>Unit Price</t></is></c></row>'
        '<row r="3"><c r="A3" t="inlineStr"><is><t>Fictional Mug</t></is></c>'
        '<c r="B3"><v>12</v></c><c r="C3"><v>6</v></c></row>'
    )
    output = BytesIO()
    with ZipFile(BytesIO(workbook(sheet))) as source, ZipFile(output, 'w', ZIP_DEFLATED) as target:
        for name in source.namelist():
            content = source.read(name)
            if date1904 and name == 'xl/workbook.xml':
                content = content.replace(b'<sheets>', b'<workbookPr date1904="1"/><sheets>')
            target.writestr(name, content)
        target.writestr('xl/styles.xml',
            '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            '<numFmts count="1"><numFmt numFmtId="164" formatCode="yyyy-mm-dd"/></numFmts>'
            f'<cellXfs count="2"><xf numFmtId="0"/><xf numFmtId="{format_id}"/></cellXfs></styleSheet>')
    return output.getvalue()


@pytest.mark.parametrize('format_id', [14, 164])
@pytest.mark.parametrize('serial,date1904,expected', [
    ('1', False, '1900-01-01'), ('59', False, '1900-02-28'),
    ('61', False, '1900-03-01'), ('45292', False, '2024-01-01'),
    ('0', True, '1904-01-01'), ('43830', True, '2024-01-01'),
])
def test_explicit_excel_date_format_preserves_source_calendar_day(format_id, serial, date1904, expected):
    rows, _, _ = document_import.spreadsheet_rows(dated_workbook(serial, format_id, date1904), 'order.xlsx')
    draft = document_import.parse_rows(rows)
    assert draft['fields']['invoice_date'] == expected
    assert expected in draft['source_text']
    assert draft['items'][0]['quantity'] == '12'
    assert draft['items'][0]['unit_price'] == '6'


@pytest.mark.parametrize('serial', ['0', '-1', '60', '45292.5', 'NaN', '999999999'])
def test_invalid_or_time_bearing_serial_is_not_guessed(serial):
    rows, _, _ = document_import.spreadsheet_rows(dated_workbook(serial), 'order.xlsx')
    draft = document_import.parse_rows(rows)
    assert draft['fields']['invoice_date'] == ''
    assert serial in draft['source_text']


@pytest.mark.parametrize('format_id,text', [(0, False), (17, False), (14, True)])
def test_plain_number_partial_date_or_text_is_not_assumed_to_be_a_date(format_id, text):
    rows, _, _ = document_import.spreadsheet_rows(dated_workbook('45292', format_id, text=text), 'order.xlsx')
    draft = document_import.parse_rows(rows)
    assert draft['fields']['invoice_date'] == ''
    assert '45292' in draft['source_text']
