import asyncio
from copy import deepcopy
import json
from urllib.parse import urlencode

import pytest
from fastapi import HTTPException
from starlette.requests import Request
from app import invoice, packing, document_import, order_changes as change
from app.packing_workflow import proposal, fingerprint


def request(payload=None, account='a', role='Owner', query=None):
    async def receive():
        return {'type': 'http.request', 'body': json.dumps(payload or {}).encode(), 'more_body': False}
    return Request({'type':'http','method':'POST' if payload is not None else 'GET',
        'path':'/order-changes/INV-1/preview','headers':[],
        'query_string':urlencode(query or {}).encode(),
        'trade_paper_user':{'account_id':account,'email':'fictional@example.com','role':role}}, receive)


def candidate(qty='600', **fields):
    return {'fields':fields,'items':[{'name':'Cotton shirts','quantity':qty,'unit_price':'8','unit':'PCS'}]}


@pytest.fixture
def records(tmp_path, monkeypatch):
    current={'account_id':'a','invoice_no':'INV-1','invoice_date':'2026-11-19',
        'seller':'Sample Exporter','seller_address':'Old address','buyer':'Sample Buyer',
        'buyer_email':'buyer@example.com','currency':'USD','signature_image':'old-test-signature',
        'items':[{'name':'Cotton shirts','quantity':500,'unit_price':8,'unit':'PCS','hs_code':'6109','origin':'KR'}]}
    pack={'account_id':'a','packing_no':'PK-1','invoice_no':'INV-1','seller':'Sample Exporter',
          'buyer':'Sample Buyer','items':[{**current['items'][0],'carton':25,'net_weight':100,'gross_weight':110}]}
    ip,pp,up=(tmp_path/name for name in ['invoices.json','packing_lists.json','users.json'])
    ip.write_text(json.dumps([current,{**deepcopy(current),'account_id':'b','invoice_no':'INV-OTHER'}]))
    pp.write_text(json.dumps([pack]));up.write_text('[]')
    monkeypatch.setattr(invoice,'INVOICE_FILE',ip);monkeypatch.setattr(invoice,'USERS_FILE',up)
    monkeypatch.setattr(packing,'PACKING_FILE',pp);monkeypatch.setattr(packing,'USERS_FILE',up)
    monkeypatch.setattr(document_import,'load_account_company',lambda *args:{})
    return current,pack,ip,pp


def preview(data, **kwargs):
    return json.loads(asyncio.run(change.preview('INV-1',request(data,**kwargs))).body)


def apply(data, **kwargs):
    return asyncio.run(change.apply('INV-1',request(data,**kwargs)))


def test_preview_selective_apply_preserves_original_and_packing(records):
    current,pack,ip,pp=records
    before=ip.read_bytes();packing_before=pp.read_bytes()
    data={'candidate':candidate(buyer='New Buyer')}
    diff=preview(data)
    assert ip.read_bytes()==before and pp.read_bytes()==packing_before
    assert {c['id'] for c in diff['changes']}=={'field:buyer','item:0:quantity'}
    response=apply({**data,'version':diff['version'],'confirmed':True,'selected':['item:0:quantity']})
    assert response.status_code==200
    updated=json.loads(ip.read_text())[0]
    assert updated['items'][0]['quantity']==600 and updated['buyer']==current['buyer']
    assert updated['items'][0]['hs_code']=='6109' and updated['seller_address']=='Old address'
    assert 'signature_image' not in updated
    assert updated[change.HISTORY][0]['document']==current
    assert pp.read_bytes()==packing_before
    suggested=proposal(pack,updated)
    assert suggested['items'][0]['quantity']==600
    assert suggested['items'][0]['carton']==suggested['items'][0]['net_weight']==''
    assert json.loads(ip.read_text())[1]['items'][0]['quantity']==500


@pytest.mark.parametrize('account,role,code',[('b','Owner',404),('','Owner',401),('a','Viewer',403)])
def test_preview_and_apply_account_guards(records,account,role,code):
    current,_,ip,pp=records;before=(ip.read_bytes(),pp.read_bytes())
    for endpoint in [preview,apply]:
        with pytest.raises(HTTPException) as exc:
            endpoint({'candidate':candidate(),'confirmed':True},account=account,role=role)
        assert exc.value.status_code==code
    assert before==(ip.read_bytes(),pp.read_bytes())


def test_replay_and_concurrent_edit_refused(records):
    _,_,ip,_=records
    data={'candidate':candidate()};diff=preview(data)
    payload={**data,'version':diff['version'],'confirmed':True,'selected':['item:0:quantity']}
    apply(payload);saved=ip.read_bytes()
    with pytest.raises(HTTPException) as exc:apply(payload)
    assert exc.value.status_code==409 and ip.read_bytes()==saved
    another={'candidate':candidate('700')};diff=preview(another)
    rows=json.loads(ip.read_text());rows[0]['buyer']='Concurrent Buyer';ip.write_text(json.dumps(rows))
    with pytest.raises(HTTPException) as exc:apply({**another,'version':diff['version'],'confirmed':True,'selected':['item:0:quantity']})
    assert exc.value.status_code==409
    assert json.loads(ip.read_text())[0]['buyer']=='Concurrent Buyer'


@pytest.mark.parametrize('extra',[{'confirmed':False},{'selected':[]},{'selected':['item:99:quantity']},{'selected':['item:0:quantity','item:0:quantity']}])
def test_explicit_valid_selection_required(records,extra):
    _,_,ip,_=records;before=ip.read_bytes();data={'candidate':candidate()};diff=preview(data)
    with pytest.raises(HTTPException):apply({**data,'version':diff['version'],'confirmed':True,'selected':['item:0:quantity'],**extra})
    assert ip.read_bytes()==before


def test_changed_preview_payload_cannot_reuse_confirmation(records):
    diff=preview({'candidate':candidate()})
    with pytest.raises(HTTPException) as exc:apply({'candidate':candidate('999'),'version':diff['version'],'confirmed':True,'selected':['item:0:quantity']})
    assert exc.value.status_code==409


def test_reordered_rows_match_full_names_and_missing_rows_are_optional(records):
    current,_,_,_=records
    current['items'].append({'name':'Bowl / Blue','quantity':20,'unit_price':3})
    source=change.clean_candidate({'fields':{},'items':[{'name':'Bowl / Blue','quantity':25},{'name':' cotton   SHIRTS ','quantity':600}]})
    result=change.compare(current,source)
    assert not result['unresolved']
    assert any(c['id']=='item:0:quantity' for c in result['changes'])
    source=change.clean_candidate(candidate())
    result=change.compare(current,source)
    assert any(c['id']=='remove:1' for c in result['changes'])
    updated=change.selected_record(current,source,result,['item:0:quantity'])
    assert len(updated['items'])==2 and updated['items'][1]==current['items'][1]


def test_renamed_or_duplicate_items_need_explicit_mapping(records):
    current,_,_,_=records;raw=candidate();raw['items'][0]['name']='Cotton shirts / XL'
    source=change.clean_candidate(raw)
    assert len(change.compare(current,source)['unresolved'])==1
    mapped=change.compare(current,source,{'0':0})
    assert not mapped['unresolved']
    assert {'item:0:name','item:0:quantity'}=={c['id'] for c in mapped['changes']}
    current['items']*=2
    assert change.compare(current,change.clean_candidate(candidate()))['unresolved']
    raw['items']*=2;source=change.clean_candidate(raw)
    with pytest.raises(HTTPException):change.compare(current,source,{'0':0,'1':0})


@pytest.mark.parametrize('value',['NaN','Infinity','-1','1e30',True,'abc'])
def test_invalid_numbers_rejected(records,value):
    with pytest.raises(HTTPException):change.clean_candidate(candidate(value))


def test_explicit_zero_and_partial_order_supported(records):
    current,_,_,_=records;source=change.clean_candidate(candidate('0'))
    result=change.compare(current,source)
    assert change.selected_record(current,source,result,['item:0:quantity'])['items'][0]['quantity']==0
    source=change.clean_candidate({'fields':{'buyer':'New'},'items':[]})
    result=change.compare(current,source)
    updated=change.selected_record(current,source,result,['field:buyer'])
    assert updated['items']==current['items']
    assert updated['invoice_date']==current['invoice_date']


def test_history_routes_are_private_and_do_not_use_current_company(records,monkeypatch):
    current,pack,ip,pp=records
    data={'candidate':candidate()};diff=preview(data)
    apply({**data,'version':diff['version'],'confirmed':True,'selected':['item:0:quantity']})
    from starlette.responses import Response
    captured=[]
    monkeypatch.setattr(invoice,'create_invoice_pdf',lambda document,company:(captured.append((document,company)) or Response(b'pdf')))
    response=change.history_pdf('INV-1',0,request())
    assert captured==[(current,{})] and response.headers['cache-control']=='no-store'
    for index,account in [(0,'b'),(-1,'a'),(2,'a')]:
        with pytest.raises(HTTPException):change.history_pdf('INV-1',index,request(account=account))


def test_packing_confirmation_archives_original_and_keeps_actual_review(records):
    current,pack,ip,pp=records
    data={'candidate':candidate()};diff=preview(data)
    apply({**data,'version':diff['version'],'confirmed':True,'selected':['item:0:quantity']})
    updated=json.loads(ip.read_text())[0]
    endpoint=next(r.endpoint for r in packing.router.routes if r.path=='/packing-sync/{packing_no}' and 'POST' in r.methods)
    endpoint('PK-1',request(),version=fingerprint([pack,updated]),carton=['30'],net_weight=['120'],gross_weight=['135'])
    actual=json.loads(pp.read_text())[0]
    assert actual['items'][0]['quantity']==600 and actual['items'][0]['carton']==30
    assert actual[change.HISTORY][0]['document']==pack
    text=change.status('INV-1',request(query={'lang':'ko'})).body.decode()
    assert '/order-changes/packing/PK-1/history/0/pdf' in text


def test_revision_import_bootstrap_is_separate_and_guarded(records):
    def config(query):
        response=document_import.import_page(request(query=query))
        return json.loads(response.body.decode().split('<script id="import-config" type="application/json">')[1].split('</script>')[0])
    normal=config({});revised=config({'revise_invoice':'INV-1'})
    assert revised['revise_invoice']=='INV-1' and revised['draft_scope']!=normal['draft_scope']
    assert revised['repeat_draft'] is None
    with pytest.raises(HTTPException):document_import.import_page(request(account='b',query={'revise_invoice':'INV-1'}))


def test_http_routes_reject_unauthenticated_and_oversized_requests(records):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    app=FastAPI()
    @app.middleware('http')
    async def identity(request,call_next):
        if request.headers.get('x-test-account'):
            request.scope['trade_paper_user']={'account_id':request.headers['x-test-account'],'role':'Owner'}
        return await call_next(request)
    app.include_router(change.router)
    client=TestClient(app)
    assert client.post('/order-changes/INV-1/preview',json={'candidate':candidate()}).status_code==401
    assert client.get('/order-changes/INV-1/status').status_code==401
    headers={'x-test-account':'a'}
    response=client.post('/order-changes/INV-1/preview',headers=headers,json={'candidate':candidate()})
    assert response.status_code==200 and response.json()['changes'][0]['id']=='item:0:quantity'
    assert client.post('/order-changes/INV-1/preview',headers=headers,content='x'*500001).status_code==413
    assert client.post('/order-changes/INV-1/preview',headers=headers,content='invalid-json').status_code==422
