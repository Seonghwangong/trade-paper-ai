import pytest
from app.document_import import parse_rows, spreadsheet_rows


@pytest.mark.parametrize('metadata', [
    [['Currency', 'usd'], ['Currency', 'USD']],
    [['Currency'], ['usd'], ['Currency'], ['USD']],
])
def test_same_currency_case_is_not_a_conflict(metadata):
    draft = parse_rows(metadata + [['Description', 'Qty', 'Unit Price'], ['Fictional bolts', '2', 'USD 6']])
    assert draft['fields']['currency'] == 'USD'
    assert 'conflicting_currency' not in draft['warnings']
    assert draft['items'][0]['unit_price'] == '6'


@pytest.mark.parametrize('currencies', [('USD', 'EUR'), ('USD', 'EUR', 'USD'), ('usd', 'eur')])
def test_mixed_currency_column_requires_explicit_review(currencies):
    rows = [['Description', 'Qty', 'Unit Price', 'Currency']]
    rows += [[f'Fictional item {i}', '2', '6', currency] for i, currency in enumerate(currencies)]
    source = '\n'.join(','.join(row) for row in rows).encode()
    parsed_rows, _, _ = spreadsheet_rows(source, 'fictional.csv')
    draft = parse_rows(parsed_rows)
    assert draft['fields']['currency'] == ''
    assert 'conflicting_currency' in draft['warnings']
    assert len(draft['items']) == len(currencies)
    assert all(item['quantity'] == '2' and item['unit_price'] == '6' for item in draft['items'])
    for currency in currencies:
        assert currency in draft['source_text']


def test_mixed_price_prefixes_do_not_assign_first_currency_to_all_items():
    draft = parse_rows([['Currency', 'USD'], ['Description', 'Qty', 'Unit Price'],
                        ['Fictional bolts', '2', 'USD 6'], ['Fictional nuts', '3', 'EUR 5']])
    assert draft['fields']['currency'] == ''
    assert 'conflicting_currency' in draft['warnings']
    assert [item['unit_price'] for item in draft['items']] == ['6', '5']


def test_xlsx_mixed_currency_metadata_keeps_items_and_source_for_review():
    from tests.test_document_import import workbook
    rows = [['Currency', 'USD'], ['Currency', 'EUR'],
            ['Description', 'Qty', 'Unit Price'], ['Fictional washer', '2', '6']]
    sheet = ''.join('<row>' + ''.join(
        f'<c r="{chr(65+i)}{r+1}" t="inlineStr"><is><t>{value}</t></is></c>'
        for i, value in enumerate(row)) + '</row>' for r, row in enumerate(rows))
    parsed, _, _ = spreadsheet_rows(workbook(sheet), 'fictional.xlsx')
    draft = parse_rows(parsed)
    assert draft['fields']['currency'] == ''
    assert draft['warnings'] == ['conflicting_currency']
    assert draft['items'][0]['quantity'] == '2'
    assert 'USD' in draft['source_text'] and 'EUR' in draft['source_text']
