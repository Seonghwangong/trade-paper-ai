"""Allowlisted sandbox checkout. Never activates production entitlements."""
import hashlib
import hmac
import json
import os
import re
import secrets
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request as HttpRequest, build_opener, HTTPRedirectHandler

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from app import auth, email_delivery
from app.paddle_sandbox_webhook import sandbox_store

router = APIRouter()
PATH = '/subscription/paddle-test'
API = 'https://sandbox-api.paddle.com/transactions'
TTL = 900


def configuration():
    if (os.environ.get('TRADE_PAPER_PADDLE_SANDBOX_CHECKOUT') != '1'
            or os.environ.get('TRADE_PAPER_PADDLE_SANDBOX_ENABLED') != '1'):
        raise HTTPException(404, 'Not found')
    key = os.environ.get('TRADE_PAPER_PADDLE_SANDBOX_API_KEY', '')
    token = os.environ.get('TRADE_PAPER_PADDLE_SANDBOX_CLIENT_TOKEN', '')
    price = os.environ.get('TRADE_PAPER_PADDLE_SANDBOX_PRICE_ID', '')
    if (not re.fullmatch(r'pdl_sdbx_apikey_[a-z0-9]{26}_[A-Za-z0-9]{22}_[A-Za-z0-9]{3}', key)
            or not re.fullmatch(r'test_[A-Za-z0-9]+', token)
            or not re.fullmatch(r'pri_[a-z0-9]{26}', price)
            or not os.environ.get('TRADE_PAPER_PADDLE_SANDBOX_WEBHOOK_SECRET')):
        raise HTTPException(503, 'Sandbox checkout is not configured')
    try:
        origin = email_delivery.public_base_url()
    except email_delivery.EmailConfigurationError:
        raise HTTPException(503, 'Sandbox origin is not configured') from None
    if not origin.startswith('https://'):
        raise HTTPException(503, 'HTTPS sandbox origin required')
    return key, token, price, origin


def owner(request):
    user = request.scope.get('trade_paper_user') or {}
    account = user.get('account_id')
    if not isinstance(account, str) or not account.strip():
        raise HTTPException(401, 'Login required')
    allowed = {v.strip() for v in os.environ.get('TRADE_PAPER_PADDLE_SANDBOX_TEST_ACCOUNTS', '').split(',') if v.strip()}
    if user.get('role') == 'Viewer' or account not in allowed:
        raise HTTPException(403, 'Sandbox test account required')
    return 'sandbox:' + account


def csrf_token(account):
    value = str(int(time.time())) + '.' + secrets.token_hex(16)
    signature = hmac.new(auth._SESSION_SECRET, ('paddle-checkout:' + account + ':' + value).encode(), hashlib.sha256).hexdigest()
    return value + '.' + signature


def validate_csrf(request, account, origin):
    try:
        token = request.headers.get('x-paddle-test-csrf', '')
        stamp, nonce, signature = token.split('.')
        if (request.headers.get('origin') != origin or not 0 <= time.time() - int(stamp) <= TTL
                or not re.fullmatch(r'[a-f0-9]{32}', nonce)):
            raise ValueError()
        expected = hmac.new(auth._SESSION_SECRET, ('paddle-checkout:' + account + ':' + stamp + '.' + nonce).encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError()
    except (ValueError, TypeError):
        raise HTTPException(403, 'Reload the test page and try again') from None


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def create_transaction(key, price):
    # No email, account identifiers, or client-supplied custom_data sent to Paddle.
    req = HttpRequest(API, data=json.dumps({'items': [{'price_id': price, 'quantity': 1}],
        'collection_mode': 'automatic'}).encode(), method='POST', headers={
        'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json', 'Paddle-Version': '1'})
    try:
        with build_opener(NoRedirect()).open(req, timeout=15) as response:
            raw = response.read(262145)
            if response.status not in (200, 201) or len(raw) > 262144:
                raise ValueError()
            data = json.loads(raw)['data']
        items = data['items']
        if (not re.fullmatch(r'txn_[a-z0-9]{26}', data['id'])
                or data['status'] not in ('draft', 'ready')
                or data['collection_mode'] != 'automatic'
                or len(items) != 1 or items[0]['price']['id'] != price
                or type(items[0]['quantity']) is not int or items[0]['quantity'] != 1):
            raise ValueError()
        return data['id']
    except (HTTPError, URLError, OSError, ValueError, KeyError, TypeError, IndexError):
        # Do not echo provider errors, keys or response data. A timeout may have created a draft.
        raise HTTPException(502, 'Test checkout needs operator review before retrying') from None


def transaction_for(account, key, price, now=None):
    now = time.time() if now is None else now
    store = sandbox_store()
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        if db.execute('SELECT 1 FROM bindings WHERE account_id=?', (account,)).fetchone():
            raise HTTPException(409, 'A test subscription is already linked')
        attempt = db.execute('SELECT started, transaction_id FROM checkout_attempts WHERE account_id=?', (account,)).fetchone()
        if attempt:
            registered = db.execute('SELECT transaction_id FROM checkouts WHERE account_id=? AND price_id=?', (account, price)).fetchone()
            if attempt[1] and registered == (attempt[1],) and 0 <= now - attempt[0] <= TTL:
                return attempt[1]
            raise HTTPException(409, 'Previous test checkout needs operator review')
        if db.execute('SELECT 1 FROM checkouts WHERE account_id=?', (account,)).fetchone():
            raise HTTPException(409, 'Previous test checkout needs operator review')
        # Persist before calling Paddle. Concurrent requests/timeouts must never create a second draft.
        db.execute('INSERT INTO checkout_attempts VALUES (?, ?, NULL)', (account, now))
    transaction = create_transaction(key, price)
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        db.execute('INSERT INTO checkouts VALUES (?, ?, ?)', (transaction, account, price))
        db.execute('UPDATE checkout_attempts SET transaction_id=? WHERE account_id=?', (transaction, account))
    return transaction


def response_headers():
    return {'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer', 'X-Frame-Options': 'DENY'}


def account_checkout_status(account, now=None):
    now = time.time() if now is None else now
    store = sandbox_store()
    with store.connect() as db:
        # One read transaction keeps binding and shadow state consistent.
        db.execute('BEGIN')
        state = db.execute('SELECT app_status FROM states WHERE account_id=?', (account,)).fetchone()
        binding = db.execute('SELECT 1 FROM bindings WHERE account_id=?', (account,)).fetchone()
        attempt = db.execute('SELECT started, transaction_id FROM checkout_attempts WHERE account_id=?', (account,)).fetchone()
        registered = db.execute('SELECT transaction_id FROM checkouts WHERE account_id=?', (account,)).fetchone()
    if binding:
        return {'phase': 'confirmed' if state else 'pending',
                'subscription_status': state[0] if state else None, 'can_start': False}
    if attempt:
        reusable = (attempt[1] and registered == (attempt[1],) and 0 <= now-attempt[0] <= TTL)
        return {'phase': 'pending' if reusable else 'review',
                'subscription_status': None, 'can_start': bool(reusable)}
    return {'phase': 'review' if registered else 'ready', 'subscription_status': None,
            'can_start': not bool(registered)}


@router.get(PATH + '/status')
def checkout_status(request: Request):
    configuration()
    result = account_checkout_status(owner(request))
    return JSONResponse({'environment': 'sandbox', **result}, headers=response_headers())


@router.get(PATH)
def checkout_page(request: Request):
    _, token, _, _ = configuration()
    account = owner(request)
    config = json.dumps({'token': token, 'csrf': csrf_token(account), 'path': PATH}).replace('<', '\\u003c')
    body = '''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Test checkout · Trade Paper AI</title><style>body{font:18px system-ui;max-width:680px;margin:48px auto;padding:24px;line-height:1.6}button{padding:12px 18px;font:inherit}label{display:block;margin:24px 0}</style></head><body><h1>Starter test checkout</h1><p>Paddle Sandbox only. Use a test card. This does not charge real money or activate a paid Trade Paper AI plan.</p><label><input id="consent" type="checkbox"> I understand this is a test checkout.</label><button id="start" type="button">Open test checkout</button><p id="status" role="status"></p><p id="server-status" role="status">Checking server confirmation...</p><button id="refresh-status" type="button">Check confirmation</button><a href="/subscription">Back to subscription</a><script src="https://cdn.paddle.com/paddle/v2/paddle.js"></script><script>
const config = __CONFIG__;
const status = document.getElementById('status');
const start = document.getElementById('start');
let initialized=false;
let canStart=false;
let statusTimer=null;
let checks=0;
let checking=false;
let opening=false;
start.disabled=true;
const confirmation=document.getElementById('server-status');
async function checkStatus() {
 if (checking) return;
 checking=true;
 clearTimeout(statusTimer);
 try {
  const r=await fetch(config.path+'/status', {credentials:'same-origin', cache:'no-store'});
  if (!r.ok || r.redirected) throw new Error();
  const result=await r.json();
  if (result.environment!=='sandbox') throw new Error();
  canStart=result.can_start===true;
  if (result.phase==='confirmed') confirmation.textContent='Server confirmed: '+result.subscription_status+' (test only). No paid plan has been activated.';
  else if (result.phase==='pending') {
   confirmation.textContent='Waiting for server confirmation. This can take a few minutes. Do not make another payment.';
   if (++checks<36) statusTimer=setTimeout(checkStatus,5000);
   else confirmation.textContent='Server confirmation is still pending. Use Check confirmation to check again.';
  } else if (result.phase==='review') confirmation.textContent='This test checkout needs operator review before another attempt.';
  else confirmation.textContent='Ready for a test checkout.';
 } catch (_) {
  canStart=false;
  confirmation.textContent='Unable to check confirmation. Check your connection or reload to sign in again.';
 } finally {checking=false;start.disabled=opening || !canStart;}
}
document.getElementById('refresh-status').addEventListener('click',()=>{checks=0;checkStatus();});
checkStatus();
start.addEventListener('click', async () => {
 if (!canStart || opening) return;
 if (!document.getElementById('consent').checked) {status.textContent='Please confirm this is a test.';return;}
 opening=true;
 start.disabled=true;
 try {
  if (!window.Paddle) throw new Error('Payment window unavailable. Reload and try again.');
  const r=await fetch(config.path, {method:'POST', credentials:'same-origin', headers:{'X-Paddle-Test-CSRF':config.csrf}});
  const data=await r.json();
  if (!r.ok) throw new Error(data.detail || 'Test checkout unavailable.');
  if (!initialized) {
  Paddle.Environment.set('sandbox');
  Paddle.Initialize({token:config.token,eventCallback:event=>{
   if (event.name==='checkout.completed') {status.textContent='Test payment completed. Checking server confirmation...';checks=0;checkStatus();}
  }});
  initialized=true;
  }
  Paddle.Checkout.open({transactionId:data.transaction_id});
  status.textContent='Test checkout opened.';
 } catch (error) {status.textContent=error.message || 'Test checkout unavailable.';}
 finally {opening=false;checkStatus();}
});
</script></body></html>'''.replace('__CONFIG__', config)
    return HTMLResponse(body, headers=response_headers())


@router.post(PATH)
def start_checkout(request: Request):
    key, _, price, origin = configuration()
    account = owner(request)
    validate_csrf(request, account, origin)
    # No request body or query parameters are trusted for account, price, or transaction.
    transaction = transaction_for(account, key, price)
    return JSONResponse({'environment': 'sandbox', 'transaction_id': transaction}, headers=response_headers())
