from copy import deepcopy
from html.parser import HTMLParser
from urllib.parse import quote

from starlette.requests import Request
from app import packing


class FormParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.inputs = []
        self.forms = []
        self.injected = []
    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if 'data-security-probe' in values or tag == 'tp-security-probe':
            self.injected.append((tag, values))
        if tag == 'input': self.inputs.append(values)
        if tag == 'form': self.forms.append(values)


def test_packing_edit_treats_saved_and_master_values_as_text(monkeypatch):
    # Inert markers, never executed. Test only isolated records, not production.
    marker = '" data-security-probe="yes"><tp-security-probe> & 한글'
    number = 'PK-' + marker
    source = {key: marker for key in ('invoice_no','seller','seller_address','seller_email','seller_phone','buyer','buyer_address','buyer_email')}
    source['packing_no'] = number
    source['items'] = [{key: marker for key in ('name','quantity','hs_code','origin','unit','carton','net_weight','gross_weight','item_id')}]
    # Exercise legacy address fallback as well as directly saved fields.
    source['seller_address'] = ''
    before = deepcopy(source)
    monkeypatch.setattr(packing, '_owned_packing', lambda *a: source)
    monkeypatch.setattr(packing, '_owned_invoice', lambda *a: source)
    monkeypatch.setattr(packing, 'load_account_company', lambda *a: {'address':marker})
    monkeypatch.setattr(packing, '_buyer_master', lambda *a: {})
    request = Request({'type':'http','method':'GET','path':'/edit-packing/PK-test',
                       'query_string':b'', 'headers':[], 'trade_paper_user':{'account_id':'security-test'}})
    response = packing.edit_packing(number, request)
    parser = FormParser(); parser.feed(response.body.decode())
    assert parser.injected == []
    values = {row.get('name'):row.get('value') for row in parser.inputs}
    for key in ('seller','seller_address','seller_email','seller_phone','buyer','buyer_address','buyer_email','invoice_no',
                'item_name','quantity','hs_code','origin','unit','carton','net_weight','gross_weight','item_id'):
        assert values[key] == marker
    assert any(f.get('action') == '/update-packing/' + quote(number,safe='') for f in parser.forms)
    assert source == before
