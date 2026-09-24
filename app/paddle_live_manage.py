"""Default-off, owner-only billing status and cancellation UI."""
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import time

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from app import auth, email_delivery, paddle_live_runtime as runtime
from app.paddle_live_actions import BillingConflict, ProviderUnavailable, configured_service
from app.paddle_live_store import _account
from app.paddle_subscription_policy import evaluate_snapshot

router = APIRouter()
PATH = '/subscription/paddle'
TTL = 900
HEADERS = {'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer',
           'X-Frame-Options': 'DENY', 'X-Content-Type-Options': 'nosniff'}


def enabled():
    return os.environ.get('TRADE_PAPER_PADDLE_LIVE_MANAGE') == '1'


def owner(request):
    if not enabled():
        raise HTTPException(404, 'Not found')
    user = request.scope.get('trade_paper_user') or {}
    if not user.get('account_id'):
        raise HTTPException(401, 'Sign in to manage billing.')
    if user.get('role') != 'Owner':
        raise HTTPException(403, 'Only the account owner can manage billing.')
    try:
        return _account(user['account_id'])
    except ValueError:
        raise HTTPException(403, 'Account unavailable.') from None


def csrf_token(account, purpose='cancel'):
    value = str(int(time.time())) + '.' + secrets.token_hex(16)
    digest = hmac.new(auth._SESSION_SECRET, ('live-billing-' + purpose + ':' + account + ':' + value).encode(),
                      hashlib.sha256).hexdigest()
    return value + '.' + digest


def public_origin():
    try:
        origin = email_delivery.public_base_url()
    except email_delivery.EmailConfigurationError:
        raise runtime.LiveBillingUnavailable() from None
    if not origin.startswith('https://'):
        raise runtime.LiveBillingUnavailable()
    return origin


def validate_request(request, account, purpose='cancel', confirmation='cancel-at-period-end'):
    origin = public_origin()
    try:
        stamp, nonce, signature = request.headers.get('x-billing-csrf', '').split('.')
        if (request.headers.get('origin') != origin
                or request.headers.get('sec-fetch-site') not in (None, 'same-origin')
                or request.headers.get('x-billing-confirm') != confirmation
                or not 0 <= time.time() - int(stamp) <= TTL
                or not re.fullmatch(r'[a-f0-9]{32}', nonce)):
            raise ValueError()
        expected = hmac.new(auth._SESSION_SECRET,
            ('live-billing-' + purpose + ':' + account + ':' + stamp + '.' + nonce).encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError()
    except (ValueError, TypeError):
        raise HTTPException(403, 'Reload this page and confirm your request.') from None


def status_for(account, *, now=None):
    """One read transaction; never make an API request or create a missing DB."""
    now = datetime.now(timezone.utc) if now is None else now
    store = runtime.store(read_only=True)
    with store.connect() as db:
        db.execute('BEGIN')
        row = db.execute('SELECT b.subscription_id, b.customer_id, s.snapshot FROM checkouts c '
                         'LEFT JOIN bindings b ON b.transaction_id=c.transaction_id '
                         'LEFT JOIN snapshots s ON s.subscription_id=b.subscription_id '
                         'WHERE c.account_id=?', (account,)).fetchone()
        extended = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='live_operations'").fetchone()
        operations = dict(db.execute('SELECT kind, result FROM live_operations WHERE account_id=?', (account,))) if extended else {}
    if row is None and 'checkout' not in operations:
        raise HTTPException(404, 'No connected billing record for this account.')
    decision = None
    if row and row[2]:
        decision = evaluate_snapshot(json.loads(row[2]), subscription_id=row[0], customer_id=row[1],
                                     price_id=store.price_id, now=now)
    pending = 'cancel' in operations and operations['cancel'] is None
    acknowledged = operations.get('cancel') in ('scheduled', 'canceled')
    cancellation = 'pending' if pending else 'awaiting_update' if acknowledged else 'none'
    if decision and decision.provider_status == 'canceled':
        cancellation = 'canceled'
    elif decision and decision.cancellation_pending:
        cancellation = 'scheduled'
    linked = bool(row and row[0])
    return {'phase': 'confirmed' if decision else 'awaiting_confirmation',
            'provider_status': decision.provider_status if decision else None,
            'starter_access': bool(decision and decision.starter_access
                                   and os.environ.get('TRADE_PAPER_PADDLE_LIVE_ACCESS') == '1'),
            'access_until': decision.access_until.isoformat() if decision and decision.access_until else None,
            'cancellation': cancellation,
            'can_cancel': linked and cancellation in ('none', 'pending')
                          and os.environ.get('TRADE_PAPER_PADDLE_LIVE_CANCEL') == '1'}


def error_response(error):
    if isinstance(error, BillingConflict):
        code, message = 409, 'This request needs billing support review. Do not submit another payment.'
    elif isinstance(error, ProviderUnavailable):
        code, message = 502, 'We could not confirm the request. Check its status or contact billing support.'
    elif isinstance(error, HTTPException):
        code, message = error.status_code, error.detail
    else:
        code, message = 503, 'Billing is temporarily unavailable. Contact billing support if this continues.'
    return JSONResponse({'detail': message}, status_code=code, headers=HEADERS)


FAILURES = (HTTPException, BillingConflict, ProviderUnavailable, sqlite3.Error, OSError, ValueError)


@router.get(PATH + '/status')
def billing_status(request: Request):
    try:
        return JSONResponse(status_for(owner(request)), headers=HEADERS)
    except FAILURES as error:
        return error_response(error)


@router.post(PATH + '/cancel')
def cancel_billing(request: Request):
    try:
        account = owner(request)
        validate_request(request, account)
        status_for(account)
        # Browser/query/body identifiers are deliberately never read.
        result = configured_service('cancel').cancel(account)
        return JSONResponse({'request_status': result,
            'message': 'The billing provider confirmed your request. Refresh to check the subscription update.'}, headers=HEADERS)
    except FAILURES as error:
        return error_response(error)


@router.get(PATH)
def billing_page(request: Request):
    try:
        account = owner(request)
        state = status_for(account)
        config = json.dumps({'path': PATH, 'csrf': csrf_token(account), 'state': state}).replace('<', '\\u003c')
        return HTMLResponse(PAGE.replace('__CONFIG__', config), headers=HEADERS)
    except FAILURES as error:
        return error_response(error)


PAGE = '''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Manage billing · Trade Paper AI</title><style>
*{box-sizing:border-box}body{margin:0;background:#f4f6f9;color:#17243a;font:16px/1.6 system-ui,sans-serif}main{max-width:760px;margin:48px auto;padding:0 20px}a{color:#2359bc}nav{margin-bottom:26px}.card{background:#fff;border:1px solid #dce3ec;border-radius:18px;padding:28px;margin-bottom:20px}h1{font-size:30px;line-height:1.2;margin:8px 0 14px}h2{font-size:20px;margin:0 0 12px}.brand{font-size:12px;letter-spacing:.08em;font-weight:700;color:#58677e}.muted{color:#58677e}dl{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin:24px 0}dt,dd{margin:0}dd{text-align:right;font-weight:600}button{font:inherit;min-height:46px;border-radius:10px;padding:10px 18px;cursor:pointer;border:1px solid #c3cddc;background:white;color:#17243a}button.primary{color:#fff;background:#263d60;border-color:#263d60}button:disabled{opacity:.5;cursor:default}label{display:flex;gap:12px;align-items:flex-start;margin:20px 0}input{margin-top:7px;width:18px;height:18px;flex-shrink:0}.notice{padding:14px 16px;background:#eef3fb;border-radius:10px}#message:empty{display:none}button:focus-visible,a:focus-visible,input:focus-visible{outline:3px solid #4c85e3;outline-offset:3px}[hidden]{display:none!important}@media(max-width:500px){main{margin:24px auto}.card{padding:22px}dl{grid-template-columns:1fr}dd{text-align:left;margin-top:-8px}}
</style></head><body><main><nav><a href="/subscription">← My subscription</a></nav><section class="card"><div class="brand">TRADE PAPER AI</div><h1>Manage billing</h1><p class="muted">View your subscription and manage renewal.</p><dl><dt>Subscription</dt><dd id="subscription-state">Checking…</dd><dt>Starter access</dt><dd id="access-state">Checking…</dd><dt>Current access ends</dt><dd id="access-end">Checking…</dd></dl><p id="confirmation" class="notice" role="status" aria-live="polite"></p><button id="refresh" type="button">Refresh status</button></section><section class="card"><h2>Cancel renewal</h2><p>For an active subscription, cancellation takes effect at the end of the current billing period. This request does not issue a refund.</p><p id="cancel-status" class="muted"></p><div id="cancel-controls" hidden><label><input id="consent" type="checkbox"><span>I want to stop renewal at the end of my current billing period.</span></label><button id="cancel" class="primary" type="button" disabled>Cancel renewal</button></div><p id="message" class="notice" role="status" aria-live="polite"></p><p><a href="/contact">Contact billing support</a></p></section><noscript>Enable JavaScript to refresh billing or request cancellation. You can also contact billing support.</noscript></main><script>
const config=__CONFIG__;
const el=id=>document.getElementById(id);
let current=config.state,busy=false,timer=null,polls=0;
const labels={active:'Active',trialing:'Trial',past_due:'Payment overdue',paused:'Paused',canceled:'Canceled'};
function render(s){
 current=s;
 el('subscription-state').textContent=labels[s.provider_status]||'Awaiting confirmation';
 el('access-state').textContent=s.starter_access?'Available':'Not active';
 el('access-end').textContent=s.access_until?new Date(s.access_until).toLocaleString():'Not confirmed';
 el('confirmation').textContent=s.phase==='confirmed'?'Subscription status confirmed by our server.':'Waiting for subscription confirmation. Do not make another payment.';
 const text={none:'No cancellation has been confirmed.',pending:'A previous request is unconfirmed. Check the request below or contact support.',awaiting_update:'Your request was acknowledged. Waiting for the subscription update.',scheduled:'Renewal cancellation is confirmed for the end of the current billing period.',canceled:'This subscription is canceled.'};
 el('cancel-status').textContent=text[s.cancellation];
 el('cancel-controls').hidden=!s.can_cancel;
 el('cancel').textContent=s.cancellation==='pending'?'Check cancellation request':'Cancel renewal';
 updateButtons();
}
function updateButtons(){el('cancel').disabled=busy||!current.can_cancel||!el('consent').checked;el('refresh').disabled=busy;}
async function call(path,options={}){
 const r=await fetch(path,{credentials:'same-origin',cache:'no-store',...options});
 if(r.redirected)throw new Error('Your session has expired. Reload this page to sign in.');
 let data;try{data=await r.json();}catch(_){throw new Error('Billing is temporarily unavailable. Please reload or contact support.');}
 if(!r.ok)throw new Error(data.detail||'Unable to complete this request.');
 return data;
}
function schedule(){clearTimeout(timer);if((current.phase!=='confirmed'||current.cancellation==='awaiting_update')&&polls++<12)timer=setTimeout(refresh,5000);}
async function refresh(){
 if(busy)return;busy=true;updateButtons();
 try{render(await call(config.path+'/status'));el('message').textContent='';schedule();}
 catch(e){current.can_cancel=false;el('message').textContent=e.message;}
 finally{busy=false;updateButtons();}
}
el('refresh').addEventListener('click',()=>{polls=0;refresh();});
el('consent').addEventListener('change',updateButtons);
el('cancel').addEventListener('click',async()=>{
 if(busy||!current.can_cancel||!el('consent').checked)return;
 busy=true;clearTimeout(timer);updateButtons();
 try{
  const result=await call(config.path+'/cancel',{method:'POST',headers:{'X-Billing-CSRF':config.csrf,'X-Billing-Confirm':'cancel-at-period-end'}});
  el('message').textContent=result.message;el('consent').checked=false;
  current.can_cancel=false;el('cancel-controls').hidden=true;
  current.cancellation='awaiting_update';el('cancel-status').textContent='Your request was acknowledged. Waiting for the subscription update.';
  polls=0;schedule();
 }catch(e){el('message').textContent=e.message;current.can_cancel=false;el('cancel-status').textContent='Cancellation is not confirmed. Refresh status or contact billing support.';}
 finally{busy=false;updateButtons();}
});
render(current);schedule();
</script></body></html>'''
