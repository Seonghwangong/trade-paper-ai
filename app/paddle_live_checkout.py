"""Default-off, allowlisted Owner checkout pilot. Not a public sales release."""
import hashlib
import json
import os
import re
import time

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from app import paddle_live_manage as manage, paddle_live_runtime as runtime, subscription
from app.paddle_live_actions import LiveClient, configured_service, TTL
from app.paddle_live_offer import expected_offer, validate_price

router = APIRouter()
PATH = '/subscription/paddle-buy'


def owner(request):
    if os.environ.get('TRADE_PAPER_PADDLE_LIVE_CHECKOUT') != '1':
        raise HTTPException(404, 'Not found')
    account = manage.owner(request)
    allowed = {a.strip() for a in os.environ.get('TRADE_PAPER_PADDLE_LIVE_PILOT_ACCOUNTS', '').split(',') if a.strip()}
    if account not in allowed:
        raise HTTPException(403, 'Checkout is not available for this account yet.')
    return account


def configuration():
    if any(os.environ.get('TRADE_PAPER_PADDLE_LIVE_' + flag) != '1'
           for flag in ('CHECKOUT', 'WEBHOOK', 'ACCESS', 'MANAGE', 'CANCEL')):
        raise runtime.LiveBillingUnavailable()
    secret = os.environ.get('TRADE_PAPER_PADDLE_LIVE_WEBHOOK_SECRET')
    token = os.environ.get('TRADE_PAPER_PADDLE_LIVE_CLIENT_TOKEN', '')
    if (not secret or secret == os.environ.get('TRADE_PAPER_PADDLE_SANDBOX_WEBHOOK_SECRET')
            or not re.fullmatch(r'live_[A-Za-z0-9]+', token)):
        raise runtime.LiveBillingUnavailable()
    manage.public_origin()
    return (expected_offer(runtime.price_id()), token,
            LiveClient(os.environ.get('TRADE_PAPER_PADDLE_LIVE_API_KEY', '')))


def purpose(offer):
    terms = json.dumps(vars(offer), sort_keys=True, separators=(',', ':'))
    return 'checkout:' + hashlib.sha256(terms.encode()).hexdigest()


def checkout_state(account, *, now=None):
    now = time.time() if now is None else now
    store = runtime.store(read_only=True)
    with store.connect() as db:
        db.execute('BEGIN')
        bound = db.execute('SELECT 1 FROM bindings WHERE account_id=?', (account,)).fetchone()
        registered = db.execute('SELECT transaction_id, price_id FROM checkouts WHERE account_id=?', (account,)).fetchone()
        extended = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='live_operations'").fetchone()
        attempt = db.execute("SELECT started, target_id, result FROM live_operations WHERE account_id=? AND kind='checkout'",
                             (account,)).fetchone() if extended else None
    if bound:
        state = manage.status_for(account)
        if state['billing_review']:
            return {'phase': 'review', 'can_open': False}
        return {'phase': 'confirmed' if state['starter_access'] else 'linked', 'can_open': False}
    if attempt:
        reusable = (attempt[2] == 'ready' and 0 <= now - attempt[0] <= TTL
                    and registered == (attempt[1], store.price_id))
        return {'phase': 'pending' if reusable else 'review', 'can_open': bool(reusable)}
    return {'phase': 'review' if registered else 'ready', 'can_open': not bool(registered)}


def ensure_free(account):
    if subscription.subscription_for_account(account)['plan'] != 'Free':
        raise HTTPException(409, 'An existing paid plan needs billing support review before another purchase.')


@router.get(PATH + '/status')
def checkout_status(request: Request):
    try:
        account = owner(request)
        configuration()
        return JSONResponse(checkout_state(account), headers=manage.HEADERS)
    except manage.FAILURES as error:
        return manage.error_response(error)


@router.post(PATH)
def start_checkout(request: Request):
    try:
        account = owner(request)
        offer, _, _ = configuration()
        manage.validate_request(request, account, purpose(offer), 'starter-monthly')
        ensure_free(account)
        if not checkout_state(account)['can_open']:
            raise HTTPException(409, 'A previous purchase needs confirmation or billing support review.')
        transaction = configured_service('checkout').checkout(account)
        return JSONResponse({'transaction_id': transaction}, headers=manage.HEADERS)
    except manage.FAILURES as error:
        return manage.error_response(error)


@router.get(PATH)
def checkout_page(request: Request):
    try:
        account = owner(request)
        offer, token, client = configuration()
        ensure_free(account)
        state = checkout_state(account)
        validate_price(client.price(offer.price_id), offer)
        config = json.dumps({'token': token, 'path': PATH, 'state': state,
            'csrf': manage.csrf_token(account, purpose(offer))}).replace('<', '\\u003c')
        tax = 'Tax included.' if offer.tax_mode == 'internal' else 'Applicable tax is added at checkout.'
        return HTMLResponse(PAGE.replace('__CONFIG__', config).replace('__TAX__', tax), headers=manage.HEADERS)
    except manage.FAILURES as error:
        return manage.error_response(error)


PAGE = '''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Starter checkout · Trade Paper AI</title><style>
*{box-sizing:border-box}body{margin:0;background:#f4f6f9;color:#17243a;font:16px/1.6 system-ui}main{max-width:660px;margin:48px auto;padding:0 20px}.card{background:white;padding:28px;border:1px solid #dce3ec;border-radius:18px}a{color:#2359bc}h1{margin-bottom:8px}.price{font-size:28px;font-weight:700;margin:8px 0}.muted{color:#58677e}label{display:flex;gap:12px;align-items:flex-start;margin:24px 0}input{width:18px;height:18px;flex-shrink:0;margin-top:6px}button{font:inherit;min-height:46px;padding:10px 18px;border-radius:10px;border:1px solid #bdc8d8;cursor:pointer;background:white}button.primary{background:#263d60;color:white}button:disabled{opacity:.5;cursor:default}.notice{padding:14px;background:#eef3fb;border-radius:10px}nav{margin:20px 0;display:flex;flex-wrap:wrap;gap:16px}button:focus-visible,input:focus-visible,a:focus-visible{outline:3px solid #4c85e3;outline-offset:3px}@media(max-width:480px){main{margin:24px auto}.card{padding:22px}}</style></head><body><main><nav><a href="/subscription">← My subscription</a></nav><section class="card"><p class="muted">TRADE PAPER AI</p><h1>Starter monthly</h1><p class="price">₩29,000 / month</p><p>__TAX__ Charged in KRW. Review the final total in the payment window before paying.</p><p>Unlimited documents. Renews monthly until canceled. No free trial. Cancel renewal from Manage billing before your next renewal.</p><nav><a href="/terms">Terms</a><a href="/privacy">Privacy</a><a href="/refund-policy">Refund policy</a></nav><label><input id="consent" type="checkbox"><span>I understand this is a recurring monthly subscription and have reviewed the terms and refund policy.</span></label><button id="open" class="primary" type="button" disabled>Continue to payment</button><p id="message" role="status" aria-live="polite"></p><p id="confirmation" class="notice" role="status" aria-live="polite"></p><button id="refresh" type="button">Check payment status</button><nav><a href="/subscription/paddle">Manage billing</a><a href="/contact">Contact support</a></nav><noscript>Enable JavaScript to open checkout, or contact support.</noscript></section></main><script src="https://cdn.paddle.com/paddle/v2/paddle.js"></script><script>
const config=__CONFIG__, el=id=>document.getElementById(id);
let current=config.state,busy=false,initialized=false,overlayOpen=false,timer=null,polls=0;
function buttons(){el('open').disabled=busy||overlayOpen||!current.can_open||!el('consent').checked;el('refresh').disabled=busy;}
function render(s){current=s;if(s.phase==='confirmed')el('message').textContent='Your Starter subscription is ready to use.';const messages={ready:'Ready to open the payment window.',pending:'A payment request exists. Check its status before continuing. Reopening uses the same request.',review:'This payment request needs support review. Do not make another payment.',linked:'Your subscription is linked. Check Manage billing for its current status.',confirmed:'Starter access is confirmed.'};el('confirmation').textContent=messages[s.phase]||'Unable to confirm payment.';buttons();}
async function call(path,options={}){const r=await fetch(path,{credentials:'same-origin',cache:'no-store',...options});if(r.redirected)throw new Error('Reload this page to sign in again.');let data;try{data=await r.json();}catch(_){throw new Error('Checkout is unavailable. Contact support if this continues.');}if(!r.ok)throw new Error(data.detail||'Unable to confirm payment.');return data;}
function schedule(){clearTimeout(timer);if(['pending','linked'].includes(current.phase)&&polls++<12)timer=setTimeout(refresh,5000);}
async function refresh(){if(busy)return;busy=true;buttons();try{render(await call(config.path+'/status'));schedule();}catch(e){current.can_open=false;el('message').textContent=e.message;}finally{busy=false;buttons();}}
el('consent').addEventListener('change',buttons);el('refresh').addEventListener('click',()=>{polls=0;refresh();});
el('open').addEventListener('click',async()=>{
 if(busy||overlayOpen||!current.can_open||!el('consent').checked)return;busy=true;clearTimeout(timer);buttons();
 try{
  if(!window.Paddle)throw new Error('Payment window is unavailable. Reload this page.');
  if(!initialized){Paddle.Initialize({token:config.token,checkout:{settings:{showAddDiscounts:false}},eventCallback:event=>{if(event.name==='checkout.closed'){overlayOpen=false;buttons();}if(event.name==='checkout.completed'){overlayOpen=false;el('message').textContent='Payment submitted. Waiting for subscription confirmation.';polls=0;refresh();}}});initialized=true;}
  const data=await call(config.path,{method:'POST',headers:{'X-Billing-CSRF':config.csrf,'X-Billing-Confirm':'starter-monthly'}});
  current={phase:'pending',can_open:false};render(current);
  overlayOpen=true;Paddle.Checkout.open({transactionId:data.transaction_id});el('message').textContent='Payment window opened. Complete or close it before continuing.';
 }catch(e){overlayOpen=false;current.can_open=false;el('message').textContent=e.message;}
 finally{busy=false;buttons();polls=0;schedule();}
});render(current);schedule();
</script></body></html>'''
