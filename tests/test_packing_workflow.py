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
