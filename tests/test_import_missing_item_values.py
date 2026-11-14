import pytest
from app.document_import import parse_rows, spreadsheet_rows
from tests.test_document_import import workbook


@pytest.mark.parametrize('cells', [
    '<c r="B2"><f>1+1</f><v>999</v></c><c r="C2"><f>3*2</f><v>888</v></c>',
    '<c r="B2"><f>1+1</f><v>999</v></c>',
    '',
])
def test_named_xlsx_item_remains_reviewable_when_numbers_are_unavailable(cells):
    sheet = ('<row><c r="A1" t="inlineStr"><is><t>Description</t></is></c>'
             '<c r="B1" t="inlineStr"><is><t>Qty</t></is></c>'
             '<c r="C1" t="inlineStr"><is><t>Unit Price</t></is></c></row>'
             '<row><c r="A2" t="inlineStr"><is><t>Fictional precision bolt</t></is></c>' + cells + '</row>')
    rows, _, warnings = spreadsheet_rows(workbook(sheet), 'fictional.xlsx')
    draft = parse_rows(rows)
    assert len(draft['items']) == 1
    assert draft['items'][0]['name'] == 'Fictional precision bolt'
    assert draft['items'][0]['quantity'] == draft['items'][0]['unit_price'] == ''
    assert '999' not in draft['source_text'] and '888' not in draft['source_text']
    if cells:
        assert warnings == ['formulas_skipped']


def test_missing_values_do_not_hide_one_item_among_valid_items_or_import_totals():
    draft = parse_rows([['Description', 'Qty', 'Unit Price', 'HS Code'],
                        ['Fictional bolt', '2', '6', ''],
                        ['Fictional washer', '', '', '123456'],
                        ['Total', '', '12', ''], ['Footer', '', '', '']])
    assert [item['name'] for item in draft['items']] == ['Fictional bolt', 'Fictional washer']
    assert draft['items'][1]['hs_code'] == '123456'
    assert draft['items'][1]['quantity'] == draft['items'][1]['unit_price'] == ''
    assert 'no_items' not in draft['warnings']


def test_blank_and_unnamed_rows_still_do_not_become_items():
    draft = parse_rows([['Description', 'Qty', 'Unit Price'], ['', '', ''], ['', '2', '6']])
    assert draft['items'] == []
