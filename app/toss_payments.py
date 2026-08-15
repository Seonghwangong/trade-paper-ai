from __future__ import annotations

from datetime import datetime, timezone
import html
import os
from pathlib import Path
import re
import uuid

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from app import auth, subscription
from app.storage import data_path, load_json_strict, locked_json_mutation


router = APIRouter()
PAYMENT_ORDERS_FILE = data_path("payment_orders.json")
ORDER_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{6,64}$")
SUPPORTED_PAYMENT_PLAN = "Starter"


def _text(value):
    return html.escape(str(value or ""), quote=True)


def toss_readiness(environment=None):
    source = os.environ if environment is None else environment
    client_key = str(source.get("TRADE_PAPER_TOSS_CLIENT_KEY", "") or "").strip()
    secret_key = str(source.get("TRADE_PAPER_TOSS_SECRET_KEY", "") or "").strip()
    issues = []
    if not client_key:
        issues.append("Client key is not configured.")
    if not secret_key:
        issues.append("Secret key is not configured.")
    client_match = re.match(r"^(test|live)_ck_", client_key) if client_key else None
    secret_match = re.match(r"^(test|live)_sk_", secret_key) if secret_key else None
    if client_key and not client_match:
        issues.append("Client key format is invalid.")
    if secret_key and not secret_match:
        issues.append("Secret key format is invalid.")
    if client_match and secret_match and client_match.group(1) != secret_match.group(1):
        issues.append("Client and secret key modes do not match.")
    return {
        "provider": "Toss Payments",
        "configuration": "Configured" if not issues else "Not Ready",
        "activation": "Not Active",
        "issues": issues,
    }


def starter_order_spec():
    plan = subscription.PLANS[SUPPORTED_PAYMENT_PLAN]
    price = plan.get("price")
    if not isinstance(price, int) or isinstance(price, bool) or price <= 0:
        raise RuntimeError("Starter price must be a positive server-side integer.")
    if plan.get("currency") != "KRW" or plan.get("billing_cycle") != "Monthly":
        raise RuntimeError("Starter payment catalog must use KRW monthly billing.")
    return {
        "plan": SUPPORTED_PAYMENT_PLAN,
        "order_name": "Trade Paper AI Starter Monthly",
        "amount": price,
        "currency": plan["currency"],
        "billing_cycle": plan["billing_cycle"],
    }


def create_pending_order(account_id, *, path: Path | None = None, order_id=None, now=None):
    owner = str(account_id or "").strip()
    if not owner:
        raise HTTPException(status_code=401, detail="Login required")
    spec = starter_order_spec()
    identifier = str(order_id or f"TPA-{uuid.uuid4().hex}")
    if not ORDER_ID_PATTERN.fullmatch(identifier):
        raise HTTPException(status_code=400, detail="Invalid order ID")
    record = {
        "account_id": owner,
        "order_id": identifier,
        **spec,
        "provider": "Toss Payments",
        "status": "Pending",
        "created_at": (now or datetime.now(timezone.utc)).isoformat(),
    }

    def add(rows):
        if any(isinstance(row, dict) and row.get("order_id") == identifier for row in rows):
            raise HTTPException(status_code=409, detail="Order ID already exists")
        rows.append(record)

    locked_json_mutation(path or PAYMENT_ORDERS_FILE, [], add, list)
    return dict(record)


def account_orders(account_id, *, path: Path | None = None):
    owner = str(account_id or "").strip()
    return [
        dict(row) for row in load_json_strict(path or PAYMENT_ORDERS_FILE, [], list)
        if isinstance(row, dict) and str(row.get("account_id", "") or "").strip() == owner
    ]


def validate_redirect_order(account_id, order_id, amount, *, path: Path | None = None):
    order = next((row for row in account_orders(account_id, path=path) if row.get("order_id") == order_id), None)
    if order is None:
        raise HTTPException(status_code=404, detail="Payment order not found")
    raw_amount = str(amount or "")
    if not re.fullmatch(r"[0-9]+", raw_amount):
        raise HTTPException(status_code=400, detail="Invalid payment amount")
    received_amount = int(raw_amount)
    expected = starter_order_spec()
    protected_fields = ("plan", "order_name", "amount", "currency", "billing_cycle")
    if any(order.get(field) != expected[field] for field in protected_fields) or received_amount != expected["amount"]:
        raise HTTPException(status_code=400, detail="Payment amount does not match the server order")
    return order


def _page(title, body):
    return HTMLResponse(f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{_text(title)}</title><style>*{{box-sizing:border-box}}body{{margin:0;background:#f3f4f6;color:#111827;font-family:Arial,sans-serif}}main{{width:min(820px,calc(100% - 32px));margin:36px auto}}section{{margin:18px 0;padding:26px;border:1px solid #e5e7eb;border-radius:18px;background:#fff}}.price{{font-size:34px;font-weight:800}}.notice{{padding:16px;border-radius:12px;background:#fff7ed;color:#9a3412}}nav,.actions{{display:flex;gap:10px;flex-wrap:wrap}}a{{display:inline-flex;min-height:44px;align-items:center;padding:10px 15px;border-radius:10px;background:#111827;color:#fff;text-decoration:none;font-weight:800}}a.secondary{{background:#e5e7eb;color:#111827}}dt{{font-weight:800}}dd{{margin:4px 0 14px;color:#475569}}@media(max-width:600px){{main{{margin:20px auto}}.actions a{{width:100%;justify-content:center}}}}</style></head><body><main><nav><a class="secondary" href="/">Home</a><a class="secondary" href="/terms">Terms</a><a class="secondary" href="/privacy">Privacy</a><a class="secondary" href="/refund-policy">Refund Policy</a><a class="secondary" href="/contact">Contact</a></nav>{body}</main></body></html>''')


@router.get("/starter")
def starter_product_page():
    spec = starter_order_spec()
    body = f'''<section><p>Trade Paper AI Subscription</p><h1>{_text(spec['plan'])}</h1><p class="price">{_text(subscription.plan_price_label(spec['plan']))}</p><p><strong>Monthly subscription</strong> with unlimited document usage and direct onboarding.</p><ul><li>Unlimited documents</li><li>Connected export document workflow</li><li>Direct onboarding</li></ul><p class="notice">Online payment processing is not active yet. Viewing or applying for Starter does not create a paid subscription or collect payment.</p><div class="actions"><a href="/founding-beta">Apply for Founding Beta</a><a class="secondary" href="/login?next=%2Fsubscription%2Fcheckout%3Fplan%3DStarter">Sign in for purchase details</a></div></section><section><h2>Purchase, cancellation, and refunds</h2><p>No online payment is collected while checkout is inactive. Review how future Starter cancellation and refund requests will be handled before applying.</p><div class="actions"><a class="secondary" href="/refund-policy">Cancellation and Refund Policy</a><a class="secondary" href="/terms">Terms</a><a class="secondary" href="/privacy">Privacy</a><a class="secondary" href="/contact">Contact Trade Paper AI</a></div></section>'''
    return _page("Starter Plan", body)


@router.get("/subscription/checkout")
def checkout_preparation(request: Request, plan: str = "Starter"):
    account_id = str((request.scope.get("trade_paper_user") or {}).get("account_id", "") or "").strip()
    if not account_id:
        raise HTTPException(status_code=401, detail="Login required")
    if plan != SUPPORTED_PAYMENT_PLAN:
        raise HTTPException(status_code=400, detail="Only Starter purchase preparation is available")
    spec = starter_order_spec()
    readiness = toss_readiness()
    body = f'''<section><p>Purchase preparation</p><h1>{_text(spec['order_name'])}</h1><dl><dt>Price</dt><dd>{_text(subscription.plan_price_label(spec['plan']))}</dd><dt>Currency</dt><dd>{_text(spec['currency'])}</dd><dt>Billing cycle</dt><dd>{_text(spec['billing_cycle'])}</dd></dl><p class="notice">Online checkout is not active. No payment order has been created and your current plan has not changed.</p><p>Payment configuration: <strong>{_text(readiness['configuration'])}</strong><br>Activation: <strong>{_text(readiness['activation'])}</strong></p><div class="actions"><a href="/founding-beta">Apply for Founding Beta</a><a class="secondary" href="/subscription">Back to My Subscription</a></div></section>'''
    return _page("Starter Purchase Preparation", body)


@router.get("/admin/payment-readiness")
def payment_readiness_page(request: Request):
    auth.require_admin(request)
    readiness = toss_readiness()
    issues = "".join(f"<li>{_text(issue)}</li>" for issue in readiness["issues"]) or "<li>Credentials use matching recognized key modes.</li>"
    body = f'''<section><h1>Payment Readiness</h1><dl><dt>Provider</dt><dd>{_text(readiness['provider'])}</dd><dt>Configuration</dt><dd>{_text(readiness['configuration'])}</dd><dt>Activation</dt><dd>{_text(readiness['activation'])}</dd></dl><ul>{issues}</ul><p>No client key, secret key, customer identity, or payment payload is displayed.</p></section>'''
    return _page("Payment Readiness", body)
