import codecs
import pytest
from app.document_import import spreadsheet_rows, parse_rows

SOURCE = 'Seller,가상 수출회사\r\nBuyer,가상 바이어\r\nInvoice Date,2026-11-13\r\nCurrency,USD\r\nDescription,Qty,Unit Price\r\n"가상 볼트, 규격 A",2,6\r\n'

@pytest.mark.parametrize('bom,encoding', [(codecs.BOM_UTF16_LE,'utf-16-le'), (codecs.BOM_UTF16_BE,'utf-16-be')])
@pytest.mark.parametrize('delimiter', [',', '\t'])
def test_bom_marked_utf16_spreadsheets_preserve_korean_and_values(bom, encoding, delimiter):
    source = SOURCE if delimiter == ',' else SOURCE.replace(',', '\t').replace('"가상 볼트\t 규격 A"', '가상 볼트, 규격 A')
    rows, sheets, warnings = spreadsheet_rows(bom + source.encode(encoding), 'fictional.csv')
    draft = parse_rows(rows)
    assert draft['fields']['seller'] == '가상 수출회사'
    assert draft['fields']['buyer'] == '가상 바이어'
    assert draft['fields']['invoice_date'] == '2026-11-13'
    assert draft['fields']['currency'] == 'USD'
    assert draft['items'][0]['name'] == '가상 볼트, 규격 A'
    assert draft['items'][0]['quantity'] == '2'
    assert draft['items'][0]['unit_price'] == '6'
    assert draft['warnings'] == warnings == []
    assert '\x00' not in draft['source_text']

@pytest.mark.parametrize('encoding', ['utf-8-sig', 'cp949'])
def test_existing_csv_encodings_remain_supported(encoding):
    rows, _, _ = spreadsheet_rows(SOURCE.encode(encoding), 'fictional.csv')
    assert parse_rows(rows)['items'][0]['name'] == '가상 볼트, 규격 A'

@pytest.mark.parametrize('raw', [b'\xff\xfeA', b'\xfe\xff\x00', b'\xff\xfe\x00\xd8'])
def test_invalid_bom_marked_utf16_is_rejected(raw):
    with pytest.raises(ValueError, match='encoding'):
        spreadsheet_rows(raw, 'broken.csv')
