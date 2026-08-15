from __future__ import annotations

from datetime import datetime, timezone
import html

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app import billing
from app.storage import data_path, load_json_strict, locked_json_mutation


router = APIRouter()
USERS_FILE = data_path("users.json")
BILLING_HISTORY_FILE = data_path("billing_history.json")
USAGE_EVENTS_FILE = data_path("usage_events.json")

PLANS = {
    "Free": {"monthly_document_limit": 5, "summary": "Up to 5 documents per month", "price": 0, "currency": "USD", "billing_cycle": None},
    "Starter": {"monthly_document_limit": None, "summary": "Unlimited documents", "price": 29_000, "currency": "KRW", "billing_cycle": "Monthly"},
    "Professional": {"monthly_document_limit": None, "summary": "Unlimited documents and professional workflow", "price": None, "currency": None, "billing_cycle": None},
}
SUBSCRIPTION_STATUSES = ("Trial", "Active", "Expired", "Cancelled")
PLAN_ORDER = tuple(PLANS)
PAID_PLAN_NOTICE = "Online payment is being prepared. Starter and Professional cannot be activated yet."
DOCUMENT_CREATION_PATHS = frozenset({
    "/save-invoice", "/invoice", "/packing-list", "/packing", "/si",
    "/shipment", "/booking", "/bl", "/co", "/quotation", "/proforma",
    "/container", "/customs", "/inspection", "/insurance", "/weight",
})


def _text(value):
    return html.escape(str(value or ""))


def _attr(value):
    return html.escape(str(value or ""), quote=True)


def plan_price_label(plan_name):
    plan = PLANS[plan_name]
    price = plan["price"]
    if price is None:
        return "Contact"
    amount = f"₩{price:,.0f}" if plan["currency"] == "KRW" else f"${price:,.0f}"
    cycle = {"Monthly": "month"}.get(plan.get("billing_cycle"), str(plan.get("billing_cycle") or "").casefold())
    return f"{amount} / {cycle}" if cycle else amount


def _account_id(request: Request):
    user = request.scope.get("trade_paper_user") or {}
    account_id = str(user.get("account_id", "") or "").strip()
    if not account_id:
        raise HTTPException(status_code=401, detail="Login required")
    return account_id


def subscription_for_account(account_id, users_file=None):
    users = load_json_strict(users_file or USERS_FILE, [], list)
    record = next((item for item in users if isinstance(item, dict) and str(item.get("account_id", "") or "") == account_id), {})
    plan = str(record.get("plan", "Free") or "Free")
    if plan not in PLANS:
        plan = "Free"
    status = str(record.get("subscription_status", "Trial") or "Trial")
    if status not in SUBSCRIPTION_STATUSES:
        status = "Trial"
    return {"plan": plan, "status": status}


def _month_key(now=None):
    value = now or datetime.now(timezone.utc)
    return value.strftime("%Y-%m")


def monthly_usage(account_id, now=None, usage_file=None):
    month = _month_key(now)
    return sum(
        1 for item in load_json_strict(usage_file or USAGE_EVENTS_FILE, [], list)
        if isinstance(item, dict) and item.get("account_id") == account_id and str(item.get("created_at", "")).startswith(month)
    )


def usage_summary(account_id, now=None):
    subscription = subscription_for_account(account_id)
    used = monthly_usage(account_id, now)
    limit = PLANS[subscription["plan"]]["monthly_document_limit"]
    allowed = subscription["status"] in {"Trial", "Active"} and (limit is None or used < limit)
    return {**subscription, "used": used, "limit": limit, "allowed": allowed}


def is_document_creation(request: Request):
    return request.method == "POST" and request.url.path in DOCUMENT_CREATION_PATHS


def record_document_usage(account_id, path, now=None):
    event = {"account_id": account_id, "path": str(path), "created_at": (now or datetime.now(timezone.utc)).isoformat()}
    locked_json_mutation(USAGE_EVENTS_FILE, [], lambda rows: rows.append(event), list)


def usage_limit_response(summary):
    status = summary["status"]
    message = "Your subscription is not active." if status not in {"Trial", "Active"} else "The Free plan monthly limit of 5 documents has been reached."
    return HTMLResponse(f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Usage Limit</title><style>body{{margin:0;background:#F3F4F6;color:#111827;font-family:Arial}}main{{min-height:100vh;display:grid;place-items:center;padding:24px}}section{{max-width:560px;padding:34px;background:#fff;border-radius:18px;text-align:center}}a{{display:inline-block;margin-top:16px;padding:12px 17px;border-radius:9px;background:#111827;color:#fff;text-decoration:none;font-weight:bold}}</style></head><body><main><section><h1>Usage Limit Reached</h1><p>{_text(message)}</p><a href="/pricing">View Plans</a></section></main></body></html>''', status_code=402)


def _page(title, body):
    return HTMLResponse(f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{_text(title)}</title><style>*{{box-sizing:border-box}}body{{margin:0;background:#F3F4F6;color:#111827;font-family:Arial,sans-serif}}main{{width:min(1100px,calc(100% - 32px));margin:36px auto}}nav{{display:flex;gap:10px;margin-bottom:24px}}a,.button,button{{display:inline-flex;min-height:44px;align-items:center;padding:10px 15px;border:0;border-radius:9px;background:#111827;color:#fff;text-decoration:none;font-weight:800;cursor:pointer}}.grid{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px}}.card,.summary{{padding:24px;background:#fff;border:1px solid #E5E7EB;border-radius:16px}}.card.current{{border:2px solid #2563EB}}.badge{{display:inline-block;padding:6px 9px;border-radius:999px;background:#DBEAFE;color:#1E3A8A;font-weight:800}}.danger{{background:#B91C1C}}.muted{{color:#64748B}}table{{width:100%;border-collapse:collapse;background:#fff}}th,td{{padding:12px;border-bottom:1px solid #E5E7EB;text-align:left}}th{{background:#111827;color:#fff}}select{{min-height:42px;padding:8px;border:1px solid #CBD5E1;border-radius:8px}}form{{display:flex;gap:8px;align-items:center;flex-wrap:wrap}}@media(max-width:720px){{.grid{{grid-template-columns:1fr}}}}</style></head><body><main><nav><a href="/">Dashboard</a><a href="/subscription">My Subscription</a></nav>{body}</main></body></html>''')


@router.get("/pricing")
def pricing(request: Request):
    account_id = _account_id(request)
    current = subscription_for_account(account_id)
    def action(name):
        if name == current["plan"]:
            return '<span class="badge">Current Plan</span>'
        if name != "Free":
            return '<a class="button" href="/subscription/checkout?plan=Starter">Purchase details</a>' if name == "Starter" else '<span class="badge">Contact us</span>'
        return f'<form method="post" action="/subscription/plan"><input type="hidden" name="plan" value="{_attr(name)}"><button type="submit">Choose {_text(name)}</button></form>'
    cards = "".join(
        f'''<article class="card{' current' if name == current['plan'] else ''}"><h2>{_text(name)}</h2><p><strong>{_text(plan_price_label(name))}</strong></p><p>{_text(config['summary'])}</p>{action(name)}</article>'''
        for name, config in PLANS.items()
    )
    return _page("Pricing", f'<h1>Pricing</h1><p>{_text(PAID_PLAN_NOTICE)}</p><section class="grid">{cards}</section>')


@router.get("/subscription")
def subscription_page(request: Request):
    account_id = _account_id(request)
    summary = usage_summary(account_id)
    history = billing.account_billing_history(account_id, BILLING_HISTORY_FILE)
    rows = "".join(f'<tr><td>{_text(item.get("created_at"))}</td><td>{_text(item.get("event"))}</td><td>{_text(item.get("plan"))}</td><td>{_text(item.get("status"))}</td><td>${float(item.get("amount", 0) or 0):.2f}</td></tr>' for item in history) or '<tr><td colspan="5">No billing history.</td></tr>'
    limit = "Unlimited" if summary["limit"] is None else str(summary["limit"])
    actions = '' if summary["plan"] == "Free" else '<form method="post" action="/subscription/plan"><input type="hidden" name="plan" value="Free"><button type="submit">Downgrade to Free</button></form>'
    cancel = '' if summary["status"] == "Cancelled" else '<form method="post" action="/subscription/cancel"><button class="danger" type="submit">Cancel Subscription</button></form>'
    invoice_rows = billing.account_invoice_history(account_id, BILLING_HISTORY_FILE)
    invoices = "".join(f'<tr><td>{_text(item.get("created_at"))}</td><td>{_text(item.get("invoice_no"))}</td><td>${float(item.get("amount", 0) or 0):.2f}</td></tr>' for item in invoice_rows) or '<tr><td colspan="3">Payment integration is not active. Invoices will appear here after a payment provider is connected.</td></tr>'
    body = f'''<h1>My Subscription</h1><section class="summary"><span class="badge">{_text(summary['status'])}</span><h2>{_text(summary['plan'])}</h2><p>Documents this month: {summary['used']} / {limit}</p><div>{actions}{cancel}</div><p class="muted">{_text(PAID_PLAN_NOTICE)}</p></section><h2>Billing History</h2><table><thead><tr><th>Date</th><th>Event</th><th>Plan</th><th>Status</th><th>Amount</th></tr></thead><tbody>{rows}</tbody></table><h2>Invoice History</h2><table><thead><tr><th>Date</th><th>Invoice</th><th>Amount</th></tr></thead><tbody>{invoices}</tbody></table>'''
    return _page("My Subscription", body)


@router.post("/subscription/plan")
def change_plan(request: Request, plan: str = Form("")):
    account_id = _account_id(request)
    if plan not in PLANS:
        raise HTTPException(status_code=400, detail="Invalid plan")
    if plan != "Free":
        return HTMLResponse(f'<h1>Online payment coming soon</h1><p>{_text(PAID_PLAN_NOTICE)}</p><a href="/pricing">Back to Pricing</a>', status_code=403)
    status = "Active"
    def update(users):
        record = next((item for item in users if isinstance(item, dict) and item.get("account_id") == account_id), None)
        if record is None:
            raise HTTPException(status_code=404, detail="Account not found")
        record["plan"] = plan
        record["subscription_status"] = status
    locked_json_mutation(USERS_FILE, [], update, list)
    billing.record_billing_event(account_id, plan, status, "Plan Change", path=BILLING_HISTORY_FILE)
    from app.audit_log import record_request_audit
    record_request_audit(request, "Change", "Subscription", plan, path=USERS_FILE.with_name("audit_log.json"))
    return RedirectResponse("/subscription", status_code=303)


@router.post("/subscription/cancel")
def cancel_subscription(request: Request):
    account_id = _account_id(request)
    subscription = subscription_for_account(account_id)
    def update(users):
        record = next((item for item in users if isinstance(item, dict) and item.get("account_id") == account_id), None)
        if record is None:
            raise HTTPException(status_code=404, detail="Account not found")
        record["subscription_status"] = "Cancelled"
    locked_json_mutation(USERS_FILE, [], update, list)
    billing.record_billing_event(account_id, subscription["plan"], "Cancelled", "Subscription Cancelled", path=BILLING_HISTORY_FILE)
    from app.audit_log import record_request_audit
    record_request_audit(request, "Cancel", "Subscription", subscription["plan"], path=USERS_FILE.with_name("audit_log.json"))
    return RedirectResponse("/subscription", status_code=303)


@router.get("/admin/subscriptions")
def subscription_admin(request: Request):
    _account_id(request)
    users = [item for item in load_json_strict(USERS_FILE, [], list) if isinstance(item, dict)]
    paid = sum(subscription_for_account(str(item.get("account_id", "")))["plan"] in {"Starter", "Professional"} and subscription_for_account(str(item.get("account_id", "")))["status"] == "Active" for item in users)
    billing = load_json_strict(BILLING_HISTORY_FILE, [], list)
    month = _month_key()
    mrr = sum(float(item.get("amount", 0) or 0) for item in billing if isinstance(item, dict) and str(item.get("created_at", "")).startswith(month) and item.get("status") == "Active")
    rows = "".join(f'''<tr><td>{_text(item.get("company"))}</td><td>{_text(item.get("email"))}</td><td>{_text(subscription_for_account(str(item.get("account_id", "")))["plan"])}</td><td><form method="post" action="/admin/subscriptions/{_attr(item.get('account_id'))}/status"><select name="status">{''.join(f'<option value="{status}"{" selected" if status == subscription_for_account(str(item.get("account_id", "")))["status"] else ""}>{status}</option>' for status in SUBSCRIPTION_STATUSES)}</select><button>Save</button></form></td></tr>''' for item in users)
    return _page("Subscription Admin", f'<h1>Subscription Admin</h1><section class="grid"><div class="card"><h2>Subscribers</h2><strong>{len(users)}</strong></div><div class="card"><h2>Paid Users</h2><strong>{paid}</strong></div><div class="card"><h2>MRR</h2><strong>${mrr:.2f}</strong></div></section><h2>Accounts</h2><table><thead><tr><th>Company</th><th>Email</th><th>Plan</th><th>Status</th></tr></thead><tbody>{rows}</tbody></table>')


@router.post("/admin/subscriptions/{account_id}/status")
def update_subscription_status(account_id: str, request: Request, status: str = Form("")):
    _account_id(request)
    if status not in SUBSCRIPTION_STATUSES:
        raise HTTPException(status_code=400, detail="Invalid subscription status")
    def update(users):
        record = next((item for item in users if isinstance(item, dict) and item.get("account_id") == account_id), None)
        if record is None:
            raise HTTPException(status_code=404, detail="Account not found")
        record["subscription_status"] = status
    locked_json_mutation(USERS_FILE, [], update, list)
    from app.audit_log import record_audit
    actor = request.scope.get("trade_paper_user") or {}
    record_audit(account_id, actor.get("email"), "Change", "Subscription", status, path=USERS_FILE.with_name("audit_log.json"))
    return RedirectResponse("/admin/subscriptions", status_code=303)
