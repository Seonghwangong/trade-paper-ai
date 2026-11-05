import base64
from io import BytesIO
import json
import pytest
from PIL import Image, ImageDraw
from pypdf import PdfReader
from starlette.requests import Request
from fastapi import HTTPException
from app import company_signature as sig, invoice
from app.validation import DataValidationError


def image_url(text='TEST ONLY', format='PNG'):
    image = Image.new('RGBA' if format=='PNG' else 'RGB', (400, 120), 'white')
    ImageDraw.Draw(image).text((25, 45), text, fill='black')
    buffer=BytesIO();image.save(buffer,format=format)
    return 'data:image/'+('png' if format=='PNG' else 'jpeg')+';base64,'+base64.b64encode(buffer.getvalue()).decode()


def request(account='a'):
    return Request({'type':'http','method':'POST','path':'/invoice','headers':[], 'query_string':b'', 'trade_paper_user':{'account_id':account,'email':account+'@example.com'}})


@pytest.fixture
def storage(tmp_path, monkeypatch):
    companies = tmp_path/'companies.json'
    companies.write_text(json.dumps([{'account_id':'a','name':'Seller A'},{'account_id':'b','name':'Seller B'}]))
    monkeypatch.setattr(sig,'SIGNATURE_FILE',tmp_path/'signatures.json')
    monkeypatch.setattr(sig,'ACCOUNT_COMPANIES_FILE',companies)
    monkeypatch.setattr(invoice,'ACCOUNT_COMPANIES_FILE',companies)
    monkeypatch.setattr(invoice,'INVOICE_FILE',tmp_path/'invoices.json')
    monkeypatch.setattr(invoice,'load_proformas',lambda account:[])
    monkeypatch.setattr(invoice.buyer_module,'load_buyers',lambda account:[])
    monkeypatch.setattr(invoice.product_module,'enrich_items_from_products',lambda *args:None)
    return tmp_path


def payload(**extra):
    return dict(seller='Seller A',buyer='Fictional Buyer',currency='USD',items=[{'name':'Mug','quantity':100,'unit_price':6}],**extra)


@pytest.mark.parametrize('format',['PNG','JPEG'])
def test_normalizes_supported_images(format):
    normalized=sig.normalize_signature(image_url(format=format))
    assert normalized.startswith('data:image/png;base64,')
    with Image.open(BytesIO(base64.b64decode(normalized.split(',')[1]))) as image:
        assert image.size==(400,120)


@pytest.mark.parametrize('value',['https://example.com/a.png','data:image/svg+xml;base64,PHN2Zz4=', 'data:image/png;base64,!!!!', 'data:image/png;base64,'+base64.b64encode(b'not-image').decode(), 'x'*1_400_000, None])
def test_rejects_invalid_or_remote_images(value):
    with pytest.raises(DataValidationError): sig.normalize_signature(value)


def test_rejects_excessive_pixel_dimensions():
    image=Image.new('1',(4096,4096));buffer=BytesIO();image.save(buffer,format='PNG')
    with pytest.raises(DataValidationError): sig.normalize_signature('data:image/png;base64,'+base64.b64encode(buffer.getvalue()).decode())


def test_signature_is_account_scoped_and_requires_auth(storage):
    sig.save_signature(request(),{'image':image_url(),'account_id':'b'})
    assert sig.get_signature(request())['image']
    assert sig.get_signature(request('b'))=={'image':''}
    with pytest.raises(HTTPException): sig.get_signature(request(''))


def test_signature_requires_explicit_choice_and_correct_seller(storage):
    sig.save_signature(request(),{'image':image_url()})
    unsigned=invoice.create_invoice(request(),payload(signature_image=image_url()))
    assert 'signature_image' not in unsigned
    signed=invoice.create_invoice(request(),payload(include_signature=True))
    assert signed['signature_image']==sig.account_signature('a')
    other=payload(include_signature=True);other['seller']='Supplier C'
    with pytest.raises(DataValidationError): invoice.create_invoice(request(),other)
    with pytest.raises(DataValidationError): invoice.create_invoice(request('b'),payload(include_signature=True))


def test_old_invoices_keep_snapshot_when_company_signature_changes(storage):
    first=sig.save_signature(request(),{'image':image_url('FIRST TEST')})['image']
    signed=invoice.create_invoice(request(),payload(include_signature=True))
    sig.save_signature(request(),{'image':image_url('SECOND TEST')})
    assert invoice._owned_invoice(signed['invoice_no'],'a')['signature_image']==first
    unsigned=invoice.create_invoice(request(),payload())
    assert 'signature_image' not in unsigned


def test_pdf_embeds_signature_only_for_signed_invoice(storage):
    signed=payload(signature_image=image_url())
    signed_pdf=PdfReader(BytesIO(invoice.create_invoice_pdf(signed,{}).body))
    unsigned_pdf=PdfReader(BytesIO(invoice.create_invoice_pdf(payload(),{}).body))
    assert len(signed_pdf.pages[-1].images)==1
    assert len(unsigned_pdf.pages[-1].images)==0
    assert 'Authorized Signature' in signed_pdf.pages[-1].extract_text()


def test_edit_existing_invoice_can_apply_preserve_or_remove_signature(storage):
    sig.save_signature(request(),{'image':image_url()})
    record=invoice.create_invoice(request(),payload())
    def update(action, seller='Seller A'):
        invoice.update_invoice(record['invoice_no'],request(),seller=seller,buyer='Fictional Buyer',currency='USD',buyer_address='',buyer_email='',item_name='Mug',quantity='100',unit_price='6',hs_code='',signature_action=action)
        return invoice._owned_invoice(record['invoice_no'],'a')
    signed=update('use');first=signed['signature_image']
    sig.save_signature(request(),{'image':image_url('NEW TEST')})
    assert update('preserve')['signature_image']==first
    assert 'signature_image' not in update('remove')
    update('use')
    assert 'signature_image' not in update('preserve',seller='Another seller')


def test_signature_routes_require_identity_and_do_not_accept_other_account(storage):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    app=FastAPI()
    @app.middleware('http')
    async def identity(request, call_next):
        if request.headers.get('x-test-account'):
            request.scope['trade_paper_user']={'account_id':request.headers['x-test-account']}
        return await call_next(request)
    app.include_router(sig.router)
    client=TestClient(app)
    assert client.get('/company-signature').status_code==401
    assert client.post('/company-signature',json={'image':image_url()}).status_code==401
    response=client.post('/company-signature',headers={'x-test-account':'a'},json={'image':image_url(),'account_id':'b'})
    assert response.status_code==200
    assert client.get('/company-signature',headers={'x-test-account':'b'}).json()=={'image':''}
