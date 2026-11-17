import pytest
from app.order_ai import validate_result
from tests.test_order_ai import SOURCE, response_data

@pytest.mark.parametrize('key,value,quote', [
    ('quantity','5','Quantity: 500 pcs'),
    ('quantity','500','Quantity: 1500 pcs'),
    ('quantity','5','Quantity: -5 pcs'),
    ('quantity','1','Quantity: 1e3 pcs'),
    ('unit_price','8','Unit Price: USD 8.50'),
    ('unit_price','50','Unit Price: USD 8.50'),
    ('amount','4','Total: USD 4,000.00'),
])
def test_partial_numeric_evidence_is_not_an_accepted_value(key,value,quote):
    data=response_data();data['items'][0][key]={'value':value,'evidence':quote}
    result=validate_result(data,SOURCE+'\n'+quote)
    assert result['items'][0][key]==''
    assert 'ai_unsupported' in result['warnings']
    assert result['item_evidence'][0][key]==quote
    assert len(result['items'])==1


def test_complete_numeric_evidence_is_accepted_without_provider_calls():
    result=validate_result(response_data(),SOURCE)
    assert result['items'][0]['quantity']=='500'
    assert result['items'][0]['unit_price']=='8.00'
    assert result['items'][0]['amount']=='4000.00'
    assert 'ai_unsupported' not in result['warnings']
