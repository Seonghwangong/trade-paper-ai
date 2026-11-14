from copy import deepcopy
from io import BytesIO
import pytest
from pypdf import PdfReader
from app.invoice import create_invoice_pdf
from app.packing import create_packing_list_pdf

@pytest.mark.parametrize('renderer', [create_invoice_pdf, create_packing_list_pdf])
@pytest.mark.parametrize('unit', ['PAIR', '가상 단위 묶음 ' * 18 + 'UNIT-END'])
def test_explicit_quantity_unit_is_present_without_changing_source(renderer, unit):
    payload={'invoice_no':'INV-UNIT','packing_no':'PK-UNIT','seller':'Fictional Seller','buyer':'Fictional Buyer',
             'currency':'USD','invoice_date':'2026-11-14','packing_date':'2026-11-14',
             'items':[{'name':'Fictional gloves','quantity':2,'unit_price':6,'unit':unit,
                       'carton':1,'net_weight':2,'gross_weight':3}]}
    original=deepcopy(payload)
    pdf=PdfReader(BytesIO(renderer(payload,{}).body))
    text='\n'.join(page.extract_text() for page in pdf.pages)
    assert ''.join(('Unit: '+unit).split()) in ''.join(text.split())
    assert text.count('Unit:')==1
    assert payload==original
    if renderer is create_invoice_pdf:
        assert 'TOTAL: USD 12.00' in text
    else:
        assert 'Total Cartons: 1' in text
        assert 'Total Net Weight: 2' in text
        assert 'Total Gross Weight: 3' in text

@pytest.mark.parametrize('renderer', [create_invoice_pdf, create_packing_list_pdf])
def test_missing_unit_is_not_invented(renderer):
    payload={'seller':'Fictional Seller','buyer':'Fictional Buyer','items':[{'name':'Fictional gloves','quantity':2,'unit_price':6}]}
    text='\n'.join(page.extract_text() for page in PdfReader(BytesIO(renderer(payload,{}).body)).pages)
    assert 'Unit:' not in text
    assert 'PCS' not in text
