from copy import deepcopy
import pytest
from app.product import enrich_items_from_products

@pytest.mark.parametrize('reverse', [False, True])
@pytest.mark.parametrize('name', ['Fictional bolt', ' fictional BOLT '])
def test_duplicate_names_never_choose_arbitrary_customs_or_units(monkeypatch, reverse, name):
    catalog=[dict(name='Fictional bolt',hs_code='111111',origin='AA',unit='PAIR'),
             dict(name='fictional BOLT ',hs_code='222222',origin='BB',unit='BOX')]
    monkeypatch.setattr('app.product.load_products', lambda account: list(reversed(catalog)) if reverse else catalog)
    items=[dict(name=name,hs_code='',origin='',unit='',quantity=2,unit_price=6)]
    expected=deepcopy(items)
    assert enrich_items_from_products(items,'fictional-account')==expected


def test_explicit_values_and_unique_other_products_are_preserved(monkeypatch):
    monkeypatch.setattr('app.product.load_products', lambda account: [
        dict(name='Fictional bolt',hs_code='111111',origin='AA',unit='PAIR'),
        dict(name='Fictional bolt',hs_code='222222',origin='BB',unit='BOX'),
        dict(name='Unique accessory',hs_code='333333',origin='CC',unit='SET')])
    items=[dict(name='Fictional bolt',hs_code='EXPLICIT',origin='DD',unit='PCS'),
           dict(name='Unique accessory',hs_code='',origin='',unit='')]
    enrich_items_from_products(items,'fictional-account')
    assert items==[dict(name='Fictional bolt',hs_code='EXPLICIT',origin='DD',unit='PCS'),
                   dict(name='Unique accessory',hs_code='333333',origin='CC',unit='SET')]
