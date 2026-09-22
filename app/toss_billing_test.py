"""Opt-in Toss billing registration review; never charges or activates a plan."""
from __future__ import annotations

import base64
import hashlib
import hmac
import html
import json
import os
import re
import secrets
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request as HttpRequest, urlopen

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from app import auth, email_delivery, subscription

router = APIRouter()
COOKIE = "trade_paper_billing_review"
TTL = 900
ENDPOINT = "https://api.tosspayments.com/v1/billing/authorizations/issue"


def configuration():
    if os.environ.get("TRADE_PAPER_TOSS_TEST_BILLING", "").lower() != "true":
        raise HTTPException(503, "Test billing is not enabled")
    client = os.environ.get("TRADE_PAPER_TOSS_CLIENT_KEY", "").strip()
    secret = os.environ.get("TRADE_PAPER_TOSS_SECRET_KEY", "").strip()
    if not re.fullmatch(r"test_ck_[A-Za-z0-9]+", client) or not re.fullmatch(r"test_sk_[A-Za-z0-9]+", secret):
        raise HTTPException(503, "Matching test API keys are required")
    try:
        origin = email_delivery.public_base_url()
    except email_delivery.EmailConfigurationError:
        raise HTTPException(503, "Test billing origin is not configured") from None
    return client, secret, origin


def enabled():
    try:
        configuration()
        return True
    except HTTPException:
        return False


def _owner(request):
    user = request.scope.get("trade_paper_user") or {}
    owner = str(user.get("account_id") or "").strip()
    if not owner:
        raise HTTPException(401, "Login required")
    if user.get("role") == "Viewer":
        raise HTTPException(403, "Viewer role is read-only")
    return owner


def _sign(value):
    return hmac.new(auth._SESSION_SECRET, ("toss-billing-review:" + value).encode(), hashlib.sha256).hexdigest()


def new_state(owner):
    payload = {"owner": _sign(owner), "customer": "review_" + secrets.token_hex(16), "issued": int(time.time())}
    encoded = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    return encoded + "." + _sign(encoded), payload["customer"]


def validate_state(request, state):
    owner = _owner(request)
    try:
        if len(state) > 1024 or not hmac.compare_digest(state, request.cookies.get(COOKIE, "")):
            raise ValueError()
        encoded, signature = state.split(".")
        if not hmac.compare_digest(signature, _sign(encoded)):
            raise ValueError()
        payload = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
        if not hmac.compare_digest(payload["owner"], _sign(owner)) or not 0 <= time.time() - payload["issued"] <= TTL:
            raise ValueError()
        return payload["customer"]
    except (ValueError, KeyError, TypeError):
        raise HTTPException(400, "This test session is invalid or expired. Start again from purchase details.") from None


def _response(title, message, status=200):
    response = HTMLResponse(f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{html.escape(title)}</title><style>body{{font:18px system-ui;max-width:720px;margin:48px auto;padding:24px;line-height:1.6}}a{{color:#174ea6}}</style></head><body><h1>{html.escape(title)}</h1><p>{html.escape(message)}</p><p>Test mode only. No charge was made and your subscription has not changed.</p><a href="/subscription/checkout?plan=Starter">Return to purchase details</a><script>history.replaceState(null, '', location.pathname);</script></body></html>''', status_code=status)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.delete_cookie(COOKIE, path="/subscription/billing-test")
    return response


def checkout_section(request):
    owner = _owner(request)
    client, _, origin = configuration()
    state, customer = new_state(owner)
    config = json.dumps({"clientKey": client, "customerKey": customer,
        "successUrl": origin + "/subscription/billing-test/success?state=" + quote(state, safe=""),
        "failUrl": origin + "/subscription/billing-test/fail?state=" + quote(state, safe="")}).replace("<", "\\u003c")
    section = '''<section><h2>Test card registration</h2><p>This review flow opens the Toss Payments test billing window. It does not charge your card, start recurring payments, or activate Starter.</p><p>Starter: __STARTER_PRICE__. Service period: 1 month per payment when paid service becomes available.</p><p><a class="secondary" href="/refund-policy">Cancellation and refunds</a></p><label><input id="billing-review-consent" type="checkbox"> I understand this is a test registration with no payment.</label><p><button id="billing-review-start" type="button" style="padding:14px;font:inherit;cursor:pointer">Open test card registration</button></p><p id="billing-review-error" role="alert"></p></section>'''
    section = section.replace("__STARTER_PRICE__", html.escape(subscription.plan_price_label("Starter")))
    section += '<script src="https://js.tosspayments.com/v2/standard"></script><script>\nconst billingReview = ' + config + ''';
document.getElementById('billing-review-start').addEventListener('click', async function () {
  const error = document.getElementById('billing-review-error');
  error.textContent = '';
  if (!document.getElementById('billing-review-consent').checked) {
    error.textContent = 'Please confirm that you understand this is a test.'; return;
  }
  this.disabled = true;
  try {
    const payment = TossPayments(billingReview.clientKey).payment({customerKey: billingReview.customerKey});
    await payment.requestBillingAuth({method: 'CARD', successUrl: billingReview.successUrl, failUrl: billingReview.failUrl, windowTarget: 'self'});
  } catch (_) {
    error.textContent = 'The test registration could not be opened or was cancelled. Please try again.';
    this.disabled = false;
  }
});</script>'''
    return section, state, origin.startswith("https:")


def verify_registration(secret, auth_key, customer):
    """Exchange the one-use auth key. Never persist or return billing/card data."""
    credentials = base64.b64encode((secret + ":").encode()).decode()
    request = HttpRequest(ENDPOINT, data=json.dumps({"authKey": auth_key, "customerKey": customer}).encode(),
        headers={"Authorization": "Basic " + credentials, "Content-Type": "application/json"}, method="POST")
    try:
        with urlopen(request, timeout=10) as result:
            payload = json.loads(result.read(65537))
            if result.status != 200 or payload.get("customerKey") != customer or not payload.get("billingKey"):
                return False
            return True
    except (HTTPError, URLError, OSError, ValueError, AttributeError):
        return False


@router.get("/subscription/billing-test/success")
def billing_test_success(request: Request, state: str = "", customerKey: str = "", authKey: str = ""):
    try:
        customer = validate_state(request, state)
        _, secret, _ = configuration()
        if customerKey != customer or not authKey or len(authKey) > 300:
            raise HTTPException(400, "The test registration response is invalid.")
    except HTTPException as error:
        return _response("Test registration unavailable", error.detail, error.status_code)
    if not verify_registration(secret, authKey, customer):
        return _response("Test registration not verified", "Toss Payments could not verify this registration. Return to purchase details and start a new test.", 502)
    return _response("Test card registration verified", "Toss Payments verified the test registration. The test billing key is not retained or used for recurring charges.")


@router.get("/subscription/billing-test/fail")
def billing_test_fail(request: Request, state: str = ""):
    try:
        validate_state(request, state)
        configuration()
    except HTTPException as error:
        return _response("Test registration unavailable", error.detail, error.status_code)
    return _response("Test card registration cancelled or failed", "Registration was not completed. You can start a new test from purchase details.")
