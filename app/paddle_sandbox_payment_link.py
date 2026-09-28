"""Allowlisted existing-transaction Sandbox UI; never writes billing state."""
import hashlib
import json
import sqlite3
from urllib.error import HTTPError, URLError
from urllib.request import Request as HttpRequest, build_opener

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from app import paddle_sandbox_checkout as sandbox, paddle_live_manage as manage
from app.paddle_live_payment_link import PAGE, existing_subscription_terms, transaction_id
from app.paddle_live_offer import Offer, validate_completed_transaction
from app.paddle_live_card_updates import validate_zero_transaction
from app.storage import data_path

router = APIRouter()
PATH = '/subscription/paddle-test/payment'


def owner(request):
    config = sandbox.configuration()
    account = sandbox.owner(request)
    if (request.scope.get('trade_paper_user') or {}).get('role') != 'Owner':
        raise HTTPException(403, 'Only the test account owner can manage billing.')
    return account, config


def binding(account):
    # Opening this page must never initialize a ledger or register a purchase.
    path = data_path('paddle_sandbox.sqlite3').resolve().as_uri() + '?mode=ro'
    with sqlite3.connect(path, uri=True) as db:
        row = db.execute('SELECT b.subscription_id,b.customer_id,s.provider_status '
                         'FROM bindings b LEFT JOIN states s ON s.account_id=b.account_id '
                         'AND s.subscription_id=b.subscription_id WHERE b.account_id=?', (account,)).fetchone()
    if not row:
        raise HTTPException(404, 'No connected test subscription.')
    return row


def fetch_transaction(key, txn):
    req = HttpRequest(sandbox.API + '/' + txn, method='GET', headers={
        'Authorization': 'Bearer ' + key, 'Paddle-Version': '1'})
    try:
        with build_opener(sandbox.NoRedirect()).open(req, timeout=15) as response:
            raw = response.read(262145)
            if response.status != 200 or len(raw) > 262144:
                raise ValueError()
            return json.loads(raw)['data']
    except (HTTPError, URLError, OSError, ValueError, KeyError, TypeError):
        raise HTTPException(502, 'Could not check the existing test transaction. Try again later.') from None


def selection(account, config, txn, *, completed=False):
    key, token, price, _ = config
    before = binding(account)
    data = fetch_transaction(key, txn)
    if (not isinstance(data, dict) or data.get('id') != txn
            or (data.get('subscription_id'), data.get('customer_id')) != before[:2]):
        raise HTTPException(404, 'Test transaction unavailable for this account.')
    try:
        # The configured price and authenticated provider response fix the catalog.
        offer = Offer(price, data['items'][0]['price']['product_id'], 'internal')
        if completed and data.get('status') == 'completed':
            if data.get('origin') == 'subscription_payment_method_change':
                validate_zero_transaction(data, offer)
            elif data.get('origin') == 'subscription_recurring':
                validate_completed_transaction(data, offer)
            else:
                raise ValueError('Not an existing subscription payment')
            kind, amount = 'completed', data['details']['totals']['total']
        else:
            kind, amount = existing_subscription_terms(data, offer)
            if before[2] != ('active' if kind == 'payment-method' else 'past_due'):
                raise HTTPException(409, 'Waiting for matching server confirmation. Reopen this link later.')
    except (KeyError, TypeError, IndexError, AttributeError, ValueError):
        raise HTTPException(409, 'This test transaction is completed, changed, or unsupported. Check confirmation.') from None
    if binding(account) != before:
        raise HTTPException(409, 'Test billing changed. Reopen the original link.')
    context = hashlib.sha256(json.dumps({'transaction': txn, 'kind': kind, 'amount': amount,
        'offer': vars(offer)}, sort_keys=True).encode()).hexdigest()
    return {'transaction': txn, 'kind': kind, 'amount': amount, 'currency': 'KRW',
            'tax': 'internal', 'context': context}, token, before[2]


def query_transaction(request):
    values = request.query_params.getlist('_ptxn')
    if len(values) != 1:
        raise HTTPException(400, 'Open one valid existing test transaction link.')
    return transaction_id(values[0])


@router.get(PATH)
def payment_page(request: Request):
    try:
        account, config = owner(request)
        choice, _, _ = selection(account, config, query_transaction(request))
        settings = json.dumps({'path': PATH, **choice,
            'csrf': manage.csrf_token(account, 'payment-link:' + choice['context'])}).replace('<', '\\u003c')
        body = PAGE.replace('__CONFIG__', settings)
        body = body.replace('href="/subscription/paddle"', 'href="/subscription/paddle-test"')
        body = body.replace('<p>TRADE PAPER AI</p>', '<p>TRADE PAPER AI · SANDBOX TEST ONLY</p>')
        body = body.replace('Only payment confirmation from our server can activate Starter.',
                            'Use an official test card. No real money or paid access is involved.')
        body = body.replace('if(!initialized){Paddle.Initialize',
                            "if(!initialized){Paddle.Environment.set('sandbox');Paddle.Initialize")
        body = body.replace('Submitted to Paddle. Check Manage billing for confirmed status.',
                            'Submitted to Paddle. Use Check confirmation to verify the test result.')
        body = body.replace('</section>', '<button id="check-confirmation" type="button">Check confirmation</button>'
                            '<p id="confirmation" role="status" aria-live="polite"></p></section>', 1)
        body = body.replace('});buttons();', '''});buttons();
el('check-confirmation').addEventListener('click',async()=>{
 el('check-confirmation').disabled=true;
 try {
  const r=await fetch(config.path+'/status?_ptxn='+encodeURIComponent(config.transaction),{credentials:'same-origin',cache:'no-store'});
  if(r.redirected)throw new Error('Sign in again and reopen the original link.');
  const d=await r.json();if(!r.ok||d.environment!=='sandbox')throw new Error(d.detail||'Confirmation unavailable.');
  el('confirmation').textContent='Paddle transaction: '+d.transaction_status+'. Sandbox subscription: '+d.subscription_status+'. No paid plan has been activated.';
 }catch(e){el('confirmation').textContent=e.message;}
 finally{el('check-confirmation').disabled=false;}
});''')
        return HTMLResponse(body, headers=manage.HEADERS)
    except manage.FAILURES as error:
        return manage.error_response(error)


@router.post(PATH + '/open')
def open_payment(request: Request):
    try:
        account, config = owner(request)
        txn = transaction_id(request.headers.get('x-billing-transaction'))
        context = request.headers.get('x-billing-context', '')
        manage.validate_request(request, account, 'payment-link:' + context, 'open-existing-payment')
        choice, token, _ = selection(account, config, txn)
        if choice['context'] != context:
            raise HTTPException(409, 'Payment details changed. Reopen the original link.')
        return JSONResponse({'transaction_id': txn, 'token': token}, headers=manage.HEADERS)
    except manage.FAILURES as error:
        return manage.error_response(error)


@router.get(PATH + '/status')
def confirmation(request: Request):
    try:
        account, config = owner(request)
        choice, _, status = selection(account, config, query_transaction(request), completed=True)
        return JSONResponse({'environment': 'sandbox', 'transaction_status':
            'completed' if choice['kind'] == 'completed' else 'pending',
            'subscription_status': status}, headers=manage.HEADERS)
    except manage.FAILURES as error:
        return manage.error_response(error)
