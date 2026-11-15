import pytest
from app.document_import import parse_rows

@pytest.mark.parametrize('name', ['Total station', 'Total care shampoo', 'Subtotal mounting bracket', '합계 센서', '총계 측정기'])
def test_total_prefix_in_real_item_name_does_not_end_the_table(name):
    draft=parse_rows([['Description','Qty','Unit Price'],[name,'2','6'],['Fictional accessory','3','5'],['Total','','27'],['Footer','','']])
    assert [item['name'] for item in draft['items']]==[name,'Fictional accessory']
    assert [item['quantity'] for item in draft['items']]==['2','3']
    assert [item['unit_price'] for item in draft['items']]==['6','5']

@pytest.mark.parametrize('label', ['TOTAL', 'Grand total', 'Subtotal', '합계', '총계', 'TOTAL: USD 27.00', 'Total: $27.00'])
def test_actual_totals_still_end_the_item_table(label):
    draft=parse_rows([['Description','Qty','Unit Price'],['Fictional accessory','3','5'],[label,'','27'],['Footer','','']])
    assert [item['name'] for item in draft['items']]==['Fictional accessory']
