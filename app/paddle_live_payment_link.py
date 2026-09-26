"""Default-off landing page for already-existing Paddle transactions.

All provider calls are GETs. A URL is a lookup hint, never an ownership proof.
"""
import hashlib
import json
import os
import re

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from app import paddle_live_checkout as buy, paddle_live_manage as manage
from app import paddle_live_payment_method as payment_method, paddle_live_runtime as runtime
from app.paddle_live_actions import LiveClient, _transaction
from app.paddle_live_adjustments import needs_review
from app.paddle_live_offer import validate_price, validate_transaction_offer, _money
from app.paddle_live_store import _id

router = APIRouter()
PATH = '/subscription/paddle-payment'


def owner(request):
    if os.environ.get('TRADE_PAPER_PADDLE_LIVE_PAYMENT_LINK') != '1':
        raise HTTPException(404, 'Not found')
    return manage.owner(request)


def transaction_id(value):
    try:
        return _id(value, 'txn')
    except ValueError:
        raise HTTPException(400, 'Open a valid billing link or contact billing support.') from None


def local_state(account, txn):
    store = runtime.store(read_only=True)
    with store.connect() as db:
        db.execute('BEGIN')
        registered = db.execute('SELECT account_id FROM checkouts WHERE transaction_id=?', (txn,)).fetchone()
        bound = db.execute('SELECT subscription_id, customer_id FROM bindings WHERE account_id=?', (account,)).fetchone()
        if registered and registered != (account,):
            raise HTTPException(404, 'Billing link unavailable for this account.')
        if bound and needs_review(db, bound[0]):
            raise HTTPException(409, 'Contact billing support before opening this payment.')
        if not registered and not bound:
            raise HTTPException(404, 'Billing link unavailable for this account.')
        return registered, bound


def existing_subscription_terms(data, offer):
    """Accept only zero-value card updates or one full overdue Starter renewal."""
    if data.get('origin') == 'subscription_payment_method_change':
        from app.paddle_live_card_updates import validate_zero_transaction
        terms = validate_zero_transaction(data, offer, statuses=('draft', 'ready'))
        return 'payment-method', terms['total']
    items = data['items']
    if (data['collection_mode'] != 'automatic' or data['currency_code'] != offer.currency
            or data['discount_id'] is not None or not isinstance(items, list) or len(items) != 1
            or type(items[0]['quantity']) is not int or items[0]['quantity'] != 1):
        raise ValueError('Unsupported billing link')
    validate_price(items[0]['price'], offer, with_product=False)
    totals = data['details']['totals']
    keys = ('subtotal', 'tax', 'total', 'grand_total', 'grand_total_tax',
            'discount', 'credit', 'credit_to_balance', 'balance')
    values = {key: _money(totals[key]) for key in keys}
    if totals['currency_code'] != offer.currency:
        raise ValueError('Unexpected payment currency')
    if data['origin'] == 'subscription_recurring':
        if (data['status'] != 'past_due' or items[0].get('proration') is not None
                or values['discount'] or values['credit'] or values['credit_to_balance']
                or values['subtotal'] + values['tax'] != values['total']
                or values['grand_total'] != values['total'] or values['grand_total_tax'] != values['tax']
                or values['balance'] != values['grand_total']
                or (values['total'] if offer.tax_mode == 'internal' else values['subtotal']) != int(offer.amount)):
            raise ValueError('Unsupported overdue payment')
        from app.paddle_live_access import period
        period(data['billing_period'])
        kind = 'overdue'
    else:
        raise ValueError('Unsupported payment purpose')
    lines = data['details']['line_items']
    if not isinstance(lines, list) or len(lines) != 1:
        raise ValueError('Unsupported payment lines')
    line = lines[0]
    if kind == 'overdue' and line.get('proration') is not None:
        raise ValueError('Unsupported prorated renewal')
    expected = {key: totals[key] for key in ('subtotal', 'discount', 'tax', 'total')}
    if (line['price_id'] != offer.price_id or line['product']['id'] != offer.product_id
            or type(line['quantity']) is not int or line['quantity'] != 1
            or line['totals'] != expected or line['unit_totals'] != expected
            or any(data['details']['adjusted_totals'][key] != totals[key]
                   for key in ('subtotal', 'tax', 'total', 'grand_total', 'grand_total_tax', 'currency_code'))
            or any(payment['status'] == 'captured' and _money(payment['amount']) != 0
                   for payment in data['payments'])):
        raise ValueError('Payment needs reconciliation')
    return kind, totals['grand_total']


def selection(request, account, txn):
    registered, bound = local_state(account, txn)
    if bound is None:
        buy.owner(request)
        offer, token, client = buy.configuration()
        buy.ensure_free(account)
        state = buy.checkout_state(account)
        if not registered or state != {'phase': 'pending', 'can_open': True}:
            raise HTTPException(409, 'This purchase needs billing support review.')
        validate_price(client.price(offer.price_id), offer)
        data = client.transaction(txn)
        _transaction(data, offer.price_id, txn)
        validate_transaction_offer(data, offer)
        kind, amount = 'purchase', offer.amount
    else:
        if registered:
            raise HTTPException(409, 'This purchase is already linked. Check Manage billing.')
        if not payment_method.enabled():
            raise HTTPException(404, 'Not found')
        secret = os.environ.get('TRADE_PAPER_PADDLE_LIVE_WEBHOOK_SECRET', '')
        token = os.environ.get('TRADE_PAPER_PADDLE_LIVE_CLIENT_TOKEN', '')
        if (os.environ.get('TRADE_PAPER_PADDLE_LIVE_WEBHOOK') != '1' or not secret
                or secret == os.environ.get('TRADE_PAPER_PADDLE_SANDBOX_WEBHOOK_SECRET')
                or not re.fullmatch(r'live_[A-Za-z0-9]+', token)):
            raise runtime.LiveBillingUnavailable()
        manage.public_origin()
        offer = runtime.offer()
        client = LiveClient(os.environ.get('TRADE_PAPER_PADDLE_LIVE_API_KEY', ''))
        data = client.transaction(txn)
        if data.get('id') != txn or (data.get('subscription_id'), data.get('customer_id')) != bound:
            raise HTTPException(404, 'Billing link unavailable for this account.')
        try:
            kind, amount = existing_subscription_terms(data, offer)
            sub = client.subscription(bound[0])
            if ((sub['id'], sub['customer_id']) != bound or sub['collection_mode'] != 'automatic'
                    or sub['status'] != ('active' if kind == 'payment-method' else 'past_due')):
                raise ValueError('Subscription no longer matches payment')
        except (KeyError, TypeError, IndexError, AttributeError):
            raise ValueError('Incomplete payment state') from None
    if local_state(account, txn) != (registered, bound):
        raise HTTPException(409, 'Billing changed. Reopen the link or contact support.')
    context = hashlib.sha256(json.dumps({'transaction': txn, 'kind': kind, 'amount': amount,
        'offer': vars(offer)}, sort_keys=True).encode()).hexdigest()
    return {'transaction': txn, 'kind': kind, 'amount': amount, 'currency': offer.currency,
            'tax': offer.tax_mode, 'context': context}, token


@router.get(PATH)
def payment_link_page(request: Request):
    try:
        account = owner(request)
        values = request.query_params.getlist('_ptxn')
        if len(values) != 1:
            raise HTTPException(400, 'Open a valid billing link or contact billing support.')
        choice, _ = selection(request, account, transaction_id(values[0]))
        config = json.dumps({'path': PATH, **choice,
            'csrf': manage.csrf_token(account, 'payment-link:' + choice['context'])}).replace('<', '\\u003c')
        return HTMLResponse(PAGE.replace('__CONFIG__', config), headers=manage.HEADERS)
    except manage.FAILURES as error:
        return manage.error_response(error)


@router.post(PATH + '/open')
def open_payment_link(request: Request):
    try:
        account = owner(request)
        txn = transaction_id(request.headers.get('x-billing-transaction'))
        context = request.headers.get('x-billing-context', '')
        if not re.fullmatch(r'[a-f0-9]{64}', context):
            raise HTTPException(403, 'Reopen the billing link and confirm your request.')
        manage.validate_request(request, account, 'payment-link:' + context, 'open-existing-payment')
        choice, token = selection(request, account, txn)
        if choice['context'] != context:
            raise HTTPException(409, 'Payment details changed. Reopen the billing link before continuing.')
        return JSONResponse({'transaction_id': txn, 'token': token}, headers=manage.HEADERS)
    except manage.FAILURES as error:
        return manage.error_response(error)


PAGE = '''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Review payment · Trade Paper AI</title><style>
*{box-sizing:border-box}body{margin:0;background:#f4f6f9;color:#17243a;font:16px/1.6 system-ui}main{max-width:660px;margin:40px auto;padding:0 20px}.card{background:white;padding:28px;border:1px solid #dce3ec;border-radius:18px}a{color:#2359bc}nav{display:flex;gap:16px;flex-wrap:wrap;margin:20px 0}label{display:flex;gap:12px;margin:24px 0}input{width:18px;height:18px;flex-shrink:0;margin-top:5px}button{font:inherit;min-height:46px;padding:10px 18px;border-radius:10px;background:#263d60;color:white;border:0}button:disabled{opacity:.5}button:focus-visible,a:focus-visible,input:focus-visible{outline:3px solid #4c85e3;outline-offset:3px}</style></head><body><main><nav><a href="/subscription/paddle">Manage billing</a></nav><section class="card"><p>TRADE PAPER AI</p><h1 id="title">Review payment</h1><p id="description"></p><p id="amount"></p><p id="terms"></p><nav><a href="/terms">Terms</a><a href="/privacy">Privacy</a><a href="/refund-policy">Refund policy</a></nav><label><input id="consent" type="checkbox"><span>I have reviewed these details and want to continue to Paddle.</span></label><button id="open" type="button" disabled>Continue to Paddle</button><p id="message" role="status" aria-live="polite"></p><p>Only payment confirmation from our server can activate Starter. <a href="/contact">Contact billing support</a> if anything looks incorrect.</p></section></main><script>
const config=__CONFIG__, el=id=>document.getElementById(id);
// Remove the SDK's automatic transaction selector before loading Paddle.js.
history.replaceState(null,'',config.path);
const names={purchase:'Resume Starter purchase','payment-method':'Update payment method',overdue:'Review overdue payment'};
el('title').textContent=names[config.kind];
el('description').textContent=config.kind==='payment-method'?'Update the payment method for your existing subscription. No charge is due for this update.':config.kind==='overdue'?'This is an unpaid renewal for your existing subscription. Review the amount in Paddle before paying.':'Resume your existing payment request. This page does not create another purchase.';
el('amount').textContent=config.kind==='payment-method'?'Amount due: ₩0':config.kind==='purchase'?'Starter: ₩29,000 / month. '+(config.tax==='internal'?'Tax included.':'Applicable tax is added in Paddle.'):'Amount due: ₩'+Number(config.amount).toLocaleString('en-US')+' KRW.';
el('terms').textContent=config.kind==='purchase'?'Renews monthly until canceled. No free trial. Cancel renewal from Manage billing before the next renewal.':'Your subscription continues under its existing terms. Review the final details in Paddle before confirming.';
let busy=false,overlay=false,initialized=false,finished=false;
function buttons(){el('open').disabled=busy||overlay||finished||!el('consent').checked;}
el('consent').addEventListener('change',buttons);
el('open').addEventListener('click',async()=>{
 if(busy||overlay||finished||!el('consent').checked)return;busy=true;buttons();
 try{
  if(!window.Paddle)throw new Error('Payment window unavailable. Reopen this link to try again.');
  const r=await fetch(config.path+'/open',{method:'POST',credentials:'same-origin',cache:'no-store',headers:{'X-Billing-CSRF':config.csrf,'X-Billing-Context':config.context,'X-Billing-Transaction':config.transaction,'X-Billing-Confirm':'open-existing-payment'}});
  if(r.redirected)throw new Error('Sign in again, then reopen the original billing link.');
  const data=await r.json();if(!r.ok)throw new Error(data.detail||'Payment unavailable. Reopen the link or contact support.');
  if(new URLSearchParams(location.search).has('_ptxn'))throw new Error('Reopen the original billing link to continue.');
  if(!initialized){Paddle.Initialize({token:data.token,checkout:{settings:{showAddDiscounts:false}},eventCallback:event=>{if(event.name==='checkout.closed'){overlay=false;buttons();}if(event.name==='checkout.completed'){overlay=false;finished=true;el('message').textContent='Submitted to Paddle. Check Manage billing for confirmed status.';buttons();}}});initialized=true;}
  overlay=true;Paddle.Checkout.open({transactionId:data.transaction_id});
 }catch(e){finished=true;el('message').textContent=e.message;}
 finally{busy=false;buttons();}
});buttons();
</script><script src="https://cdn.paddle.com/paddle/v2/paddle.js"></script></body></html>'''
