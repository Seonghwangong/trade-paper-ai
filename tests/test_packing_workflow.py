from copy import deepcopy
import json
import pytest
from fastapi import HTTPException
from starlette.requests import Request
from app import packing, invoice
from app.packing_workflow import calculate, proposal, fingerprint


def request(account='a'):
    return Request({'type':'http','method':'POST','path':'/packing-sync/PK-1','query_string':b'',
                    'headers':[], 'trade_paper_user':{'account_id':account,'email':account+'@example.com'}})


def endpoint(path, method):
    return next(r.endpoint for r in packing.router.routes if r.path == path and method in r.methods)


def item(name='Shirt', qty=100, **extra):
    return dict(name=name,quantity=qty,hs_code='6109',origin='Korea',unit='PCS',**extra)


def test_calculation_partial_carton_and_decimal_precision():
    assert calculate(dict(quantity=101, units_per_carton=20, net_per_unit='.2', tare_per_carton='.5')) == {
        'carton':6,'net_weight':'20.200','gross_weight':'23.200'}
    assert calculate(dict(quantity='3',units_per_carton='10',net_per_unit='.1',tare_per_carton='0'))['net_weight']=='0.300'


@pytest.mark.parametrize('field,value', [('quantity','NaN'),('quantity','inf'),('quantity',-1),('units_per_carton',0),('net_per_unit','bad'),('tare_per_carton',-1),('quantity',100000001)])
def test_invalid_calculations(field,value):
    data=dict(quantity=10, units_per_carton=2, net_per_unit=.2, tare_per_carton=.1)
    data[field]=value
    with pytest.raises(HTTPException): calculate(data)


def test_proposal_retains_only_unambiguous_unchanged_physical_totals():
    old={'items':[item(item_id='stable',carton=5,net_weight=20,gross_weight=25), item('Changed',10,carton=1,net_weight=2,gross_weight=3)]}
    source={'items':[item('Changed',20),item(),item('New',5)]}
    result=proposal(old,source)
    assert result['items'][0]['net_weight']==''
    assert result['items'][1]['net_weight']==20
    assert result['items'][1]['item_id']=='stable'
    assert result['items'][2]['carton']==''
    assert old['items'][0]['net_weight']==20
    duplicate=proposal({'items':[item(carton=1),item(carton=2)]},{'items':[item(),item()]})
    assert all(r['carton']=='' for r in duplicate['items'])


@pytest.fixture
def records(tmp_path,monkeypatch):
    source={'account_id':'a','invoice_no':'INV-1','seller':'Seller','buyer':'New Buyer','items':[item(qty=120)]}
    current={'account_id':'a','packing_no':'PK-1','invoice_no':'INV-1','seller':'Seller','buyer':'Old Buyer',
             'items':[item(carton=5,net_weight=20,gross_weight=25)]}
    other={'account_id':'b','packing_no':'PK-2','invoice_no':'INV-2','items':[]}
    users=tmp_path/'users.json';users.write_text('[]')
    ip=tmp_path/'invoices.json';ip.write_text(json.dumps([source]))
    pp=tmp_path/'packing_lists.json';pp.write_text(json.dumps([current,other]))
    monkeypatch.setattr(invoice,'INVOICE_FILE',ip);monkeypatch.setattr(invoice,'USERS_FILE',users)
    monkeypatch.setattr(packing,'PACKING_FILE',pp);monkeypatch.setattr(packing,'USERS_FILE',users)
    return current,source,other,pp,ip


def test_preview_escape_and_tenant_isolation(records):
    current,source,other,pp,ip=records
    source['buyer']='<script>alert(1)</script>';ip.write_text(json.dumps([source]))
    preview=endpoint('/packing-sync/{packing_no}','GET')
    assert '&lt;script&gt;' in preview('PK-1',request()).body.decode()
    with pytest.raises(HTTPException) as error: preview('PK-1',request('b'))
    assert error.value.status_code==404
    with pytest.raises(HTTPException) as error: preview('PK-1',request(''))
    assert error.value.status_code==401


def test_preview_shows_quantity_difference_without_writing_documents(records):
    current, source, other, pp, ip = records
    before = pp.read_bytes(), ip.read_bytes()
    preview = endpoint('/packing-sync/{packing_no}', 'GET')('PK-1', request())
    body = preview.body.decode()
    assert 'id="item-comparison"' in body
    assert '-20' in body and 'Quantity differs' in body
    assert 'Line net weight' in body and '20 kg' in body
    assert (pp.read_bytes(), ip.read_bytes()) == before


def test_apply_guards_stale_preview_and_updates_only_selected_account(records):
    current,source,other,pp,ip=records
    apply=endpoint('/packing-sync/{packing_no}','POST')
    before=pp.read_bytes()
    with pytest.raises(HTTPException) as error: apply('PK-1',request(),'stale',['6'],['24'],['27'])
    assert error.value.status_code==409 and pp.read_bytes()==before
    version=fingerprint([current,source])
    with pytest.raises(HTTPException): apply('PK-1',request(),version,['6'],['24'],['20'])
    assert pp.read_bytes()==before
    assert apply('PK-1',request(),version,['6'],['24'],['27']).status_code==303
    stored=json.loads(pp.read_text())
    assert stored[0]['buyer']=='New Buyer' and stored[0]['items'][0]['quantity']==120
    assert stored[0]['items'][0]['net_weight']=='24'
    assert stored[1]==other
    assert json.loads(ip.read_text())==[source]
    assert json.loads(pp.with_name('packing_lists.backup.json').read_text())[0]==current
    with pytest.raises(HTTPException) as error: apply('PK-1',request(),version,['6'],['24'],['27'])
    assert error.value.status_code==409


def test_defaults_are_private_and_overwrite_only_matching_profile(records):
    save=endpoint('/packing-defaults','POST');get=endpoint('/packing-defaults','GET')
    settings=dict(name='Shirt',quantity=100,units_per_carton=20,net_per_unit='.2',tare_per_carton='.5',account_id='b')
    save(request(), settings)
    assert len(get(request()))==1 and get(request('b'))==[]
    save(request(),dict(settings,net_per_unit='.3'))
    assert len(get(request()))==1 and get(request())[0]['net_per_unit']=='.3'
    with pytest.raises(HTTPException): save(request(''),settings)


def test_matching_documents_skip_comparison_and_preserve_packing(records, monkeypatch):
    current, source, other, pp, ip = records
    source = deepcopy(current)
    source['invoice_no'] = 'INV-1'
    source['items'][0]['quantity'] = '100.00'
    ip.write_text(json.dumps([source]))
    monkeypatch.setattr(packing, 'load_account_company', lambda *args: {})
    monkeypatch.setattr(packing, '_buyer_master', lambda *args: {})
    before = pp.read_bytes(), ip.read_bytes()
    preview = endpoint('/packing-sync/{packing_no}', 'GET')('PK-1', request())
    assert preview.status_code == 303
    assert preview.headers['location'] == '/edit-packing/PK-1?lang=en'
    for body in (packing.edit_packing('PK-1', request()).body.decode(), packing.packing_list(request()).body.decode()):
        assert '/packing-sync/PK-1' not in body
    assert (pp.read_bytes(), ip.read_bytes()) == before


def test_changed_documents_still_offer_review_without_changing_weights(records, monkeypatch):
    current, source, other, pp, ip = records
    monkeypatch.setattr(packing, 'load_account_company', lambda *args: {})
    monkeypatch.setattr(packing, '_buyer_master', lambda *args: {})
    before = pp.read_bytes(), ip.read_bytes()
    for body in (packing.edit_packing('PK-1', request()).body.decode(), packing.packing_list(request()).body.decode()):
        assert '/packing-sync/PK-1' in body
    assert (pp.read_bytes(), ip.read_bytes()) == before


@pytest.mark.parametrize('quantities,expected', [([''],''),(['   '],''),([None],''),([],''),(['0'],0),([0],0),(['2.5'],2.5)])
def test_packing_edit_preserves_unknown_quantity_and_explicit_numbers(records,monkeypatch,quantities,expected):
    current,source,other,pp,ip=records
    monkeypatch.setattr(packing,'INVOICE_FILE',ip)
    monkeypatch.setattr(packing,'direct_document_shipment_no',lambda *args:'')
    monkeypatch.setattr(packing,'shipment_detail_redirect_url',lambda *args:'/packing-list')
    before_invoice=ip.read_bytes()
    packing.update_packing('PK-1',request(),invoice_no='INV-1',seller='Seller',buyer='Old Buyer',
        item_name=['Shirt'],quantity=quantities,hs_code=['6109'],carton=['5'],net_weight=['20'],
        gross_weight=['25'],item_id=[],origin=['Korea'],unit=['PCS'])
    stored=json.loads(pp.read_text())
    assert stored[0]['items'][0]['quantity']==expected
    assert stored[1]==other
    assert ip.read_bytes()==before_invoice


@pytest.mark.parametrize('quantity', [0,120])
def test_refresh_preview_preserves_explicit_zero_inputs_without_writes(records,quantity):
    current,source,other,pp,ip=records
    current['items'][0].update(quantity=quantity,carton=0,net_weight=0,gross_weight=0)
    source['items'][0]['quantity']=quantity
    pp.write_text(json.dumps([current,other]));ip.write_text(json.dumps([source]))
    before=pp.read_bytes(),ip.read_bytes()
    body=endpoint('/packing-sync/{packing_no}','GET')('PK-1',request()).body.decode()
    for key in ('carton','net_weight','gross_weight'):
        assert f'required value="0"' in body.split(f'name="{key}" type="number"',1)[1].split('>',1)[0]
    assert f'name="quantity" value="{quantity}"' in body
    assert (pp.read_bytes(),ip.read_bytes())==before


def test_refresh_inputs_match_existing_server_measurement_bounds(records):
    body=endpoint('/packing-sync/{packing_no}','GET')('PK-1',request()).body.decode()
    for key,minimum,step in [('carton','1','1'),('net_weight','0.000001','0.000001'),('gross_weight','0.000001','0.000001')]:
        attributes=body.split(f'name="{key}" type="number"',1)[1].split('>',1)[0]
        assert f'min="{minimum}"' in attributes
        assert f'step="{step}"' in attributes
        assert 'max="100000000"' in attributes


def test_long_imported_name_can_save_and_reload_packing_settings(records):
    save = endpoint('/packing-defaults', 'POST')
    get = endpoint('/packing-defaults', 'GET')
    name = 'N' * 1000 + ' / ' + 'S' * 1000
    settings = dict(name=name, hs_code='', origin='', unit='PCS',
                    units_per_carton=20, net_per_unit='.2', tare_per_carton='.5')
    original_documents = records[3].read_bytes(), records[4].read_bytes()
    save(request(), settings)
    assert get(request())[0] == settings
    assert get(request('b')) == []
    save(request(), dict(settings, net_per_unit='.3'))
    assert len(get(request())) == 1
    assert get(request())[0]['name'] == name
    assert get(request())[0]['net_per_unit'] == '.3'
    assert (records[3].read_bytes(), records[4].read_bytes()) == original_documents


@pytest.mark.parametrize('field,value', [('name', 'N' * 2004), ('hs_code', 'H' * 201),
                                        ('origin', 'O' * 201), ('unit', 'U' * 201)])
def test_packing_settings_keep_field_limits_without_mutation(records, field, value):
    save = endpoint('/packing-defaults', 'POST')
    get = endpoint('/packing-defaults', 'GET')
    settings = dict(name='Fictional shirt', units_per_carton=20,
                    net_per_unit='.2', tare_per_carton='.5')
    save(request(), settings)
    before = get(request())
    with pytest.raises(HTTPException) as error:
        save(request(), dict(settings, **{field: value}))
    assert error.value.status_code == 422
    assert get(request()) == before
