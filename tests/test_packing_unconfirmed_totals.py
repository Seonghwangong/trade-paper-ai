from copy import deepcopy
from io import BytesIO
import pytest
from pypdf import PdfReader
from app.packing import create_packing_list_pdf

@pytest.mark.parametrize('field,label', [('carton','Cartons'),('net_weight','Net Weight'),('gross_weight','Gross Weight')])
@pytest.mark.parametrize('value', ['',None,'TBC','NaN','Infinity',-1])
def test_unconfirmed_measurement_does_not_publish_a_zero_or_partial_total(field,label,value):
    items=[dict(name='Fictional box A',quantity=2,carton=1,net_weight=2,gross_weight=3),
           dict(name='Fictional box B',quantity=3,carton=1,net_weight=2,gross_weight=3)]
    items[1][field]=value
    payload={'seller':'Fictional Seller','buyer':'Fictional Buyer','items':items}
    original=deepcopy(payload)
    text='\n'.join(p.extract_text() for p in PdfReader(BytesIO(create_packing_list_pdf(payload,{}).body)).pages)
    assert f'Total {label}: Not confirmed' in text
    assert 'Fictional box A' in text and 'Fictional box B' in text
    assert payload==original


def test_fractional_carton_is_not_silently_rounded_down_in_total():
    payload={'items':[dict(name='Fictional box',quantity=2,carton='1.5',net_weight=2,gross_weight=3)]}
    text=PdfReader(BytesIO(create_packing_list_pdf(payload,{}).body)).pages[0].extract_text()
    assert 'Total Cartons: Not confirmed' in text
    assert '1.5' in text


def test_explicit_zero_and_complete_measurements_still_have_numeric_totals():
    payload={'items':[dict(name='Fictional box',quantity=2,carton=0,net_weight=0,gross_weight=0)]}
    text=PdfReader(BytesIO(create_packing_list_pdf(payload,{}).body)).pages[0].extract_text()
    for label in ('Cartons','Net Weight','Gross Weight'):
        assert f'Total {label}: 0' in text
    assert 'Not confirmed' not in text
