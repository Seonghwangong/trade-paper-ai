from __future__ import annotations

from datetime import datetime, timezone
import html
from urllib.parse import quote

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app import analytics, auth
from app.documents import DOCUMENT_DEFINITIONS
from app.storage import data_path, load_json_strict


router = APIRouter()
DASHBOARD_SCOPE = "service"
USERS_FILE = data_path("users.json")
EMAIL_HISTORY_FILE = data_path("email_history.json")
BILLING_HISTORY_FILE = data_path("billing_history.json")
BUYERS_FILE = data_path("buyers.json")
SHIPMENTS_FILE = data_path("shipments.json")
DATE_FIELDS = ("created_at", "submitted_at", "shipment_date", "invoice_date", "packing_date", "si_date", "booking_date", "bl_date", "co_date", "issue_date", "date")


def _text(value):
    return html.escape(str(value or ""))


def _record_date(record):
    return next((str(record.get(field, "") or "") for field in DATE_FIELDS if str(record.get(field, "") or "")), "")


def _subscription(record):
    plan = str(record.get("plan", "Free") or "Free")
    status = str(record.get("subscription_status", "Trial") or "Trial")
    return plan, status


def admin_dashboard_metrics(now=None):
    current = now or datetime.now(timezone.utc)
    today, month = current.strftime("%Y-%m-%d"), current.strftime("%Y-%m")
    users = [item for item in load_json_strict(USERS_FILE, [], list) if isinstance(item, dict)]
    subscriptions = [_subscription(item) for item in users]
    document_definitions = [item for item in DOCUMENT_DEFINITIONS if item.dashboard_category != "Master Data"]
    document_rows = []
    document_counts = {}
    for definition in document_definitions:
        rows = [item for item in load_json_strict(data_path(definition.storage_filename), [], list) if isinstance(item, dict)]
        document_counts[definition.label] = len(rows)
        for record in rows:
            identifier = str(record.get(definition.identifier_field, "") or "")
            if identifier:
                document_rows.append({"label": definition.label, "identifier": identifier, "date": _record_date(record)})
    document_rows.sort(key=lambda item: (item["date"], item["identifier"]), reverse=True)
    emails = [item for item in load_json_strict(EMAIL_HISTORY_FILE, [], list) if isinstance(item, dict)]
    emails.sort(key=lambda item: str(item.get("sent_at", "") or ""), reverse=True)
    shipments = [item for item in load_json_strict(SHIPMENTS_FILE, [], list) if isinstance(item, dict)]
    shipments.sort(key=lambda item: (str(item.get("shipment_date", "") or ""), str(item.get("shipment_no", "") or "")), reverse=True)
    buyers = [item for item in load_json_strict(BUYERS_FILE, [], list) if isinstance(item, dict)]
    users.sort(key=lambda item: str(item.get("registered_at", "") or ""), reverse=True)
    active_paid = sum(plan in {"Starter", "Professional"} and status == "Active" for plan, status in subscriptions)
    return {
        "users": {"Total Users": len(users), "Active Users": sum(status == "Active" for _, status in subscriptions), "Trial Users": sum(status == "Trial" for _, status in subscriptions), "Paid Users": active_paid},
        "documents_today": sum(item["date"].startswith(today) for item in document_rows),
        "documents_month": sum(item["date"].startswith(month) for item in document_rows),
        "document_counts": document_counts,
        "revenue": {"MRR": 0, "Active Subscription": sum(status == "Active" for _, status in subscriptions), "Trial Conversion": 0},
        "email": {"Success": sum(item.get("status") == "Success" for item in emails), "Failed": sum(item.get("status") == "Failed" for item in emails), "recent": emails[:5]},
        "shipments": {status: sum(str(item.get("status", "") or "") == status for item in shipments) for status in ("Draft", "In Transit", "Delivered")},
        "customers": {status: sum(str(item.get("status", "Lead") or "Lead") == status for item in buyers) for status in ("Lead", "Prospect", "Customer", "Inactive")},
        "recent_users": users[:5], "recent_documents": document_rows[:5], "recent_shipments": shipments[:5],
        "analytics": analytics.analytics_metrics(now=current),
        "visitor_analytics": analytics.visitor_metrics(now=current),
    }


def _cards(values, suffix=""):
    return "".join(f'<article class="card"><span>{_text(label)}</span><strong>{_text(value)}{suffix}</strong></article>' for label, value in values.items())


def _activity(items, formatter):
    rows = "".join(f'<li>{formatter(item)}</li>' for item in items)
    return rows or '<li class="empty">No recent activity.</li>'


@router.get("/admin/dashboard")
def admin_dashboard(request: Request):
    auth.require_admin(request)
    metrics = admin_dashboard_metrics()
    document_cards = _cards(metrics["document_counts"])
    email_recent = _activity(metrics["email"]["recent"], lambda item: f'{_text(item.get("sent_at"))} · {_text(item.get("document_no"))} · {_text(item.get("status"))}')
    recent_users = _activity(metrics["recent_users"], lambda item: f'{_text(item.get("registered_at") or "Date unavailable")} · {_text(item.get("company") or item.get("email"))}')
    recent_documents = _activity(metrics["recent_documents"], lambda item: f'{_text(item["date"] or "Date unavailable")} · {_text(item["label"])} · {_text(item["identifier"])}')
    recent_shipments = _activity(metrics["recent_shipments"], lambda item: f'{_text(item.get("shipment_date") or "Date unavailable")} · {_text(item.get("shipment_no"))} · {_text(item.get("status"))}')
    revenue = {"MRR": "$0.00", "Active Subscription": metrics["revenue"]["Active Subscription"], "Trial Conversion": "0%"}
    product = metrics["analytics"]
    visitors = metrics["visitor_analytics"]
    trend_rows = "".join(f'<tr><td>{_text(day["date"])}</td><td>{day["total"]}</td><td>{day["events"]["Signup"]}</td><td>{day["events"]["Export Wizard Completed"]}</td><td>{day["events"]["Invoice Created"]}</td><td>{day["events"]["Email Sent"]}</td></tr>' for day in reversed(product["trend"]))
    product_cards = {"Signups": product["signups"], "Wizard Completion": f'{product["wizard_completion_rate"]}%', "Email Send Rate": f'{product["email_send_rate"]}%', "Document Creation Rate": f'{product["document_creation_rate"]}%'}
    visitor_cards = {"Visits": visitors["visits"], "Landing → Signup": f'{visitors["landing_to_signup_rate"]}%', "Signup → Onboarding": f'{visitors["signup_to_onboarding_rate"]}%', "Landing → Onboarding": f'{visitors["landing_to_onboarding_rate"]}%'}
    visitor_trend = "".join(f'<tr><td>{_text(day["date"])}</td><td>{day["total"]}</td><td>{day["pages"]["Landing"]}</td><td>{day["pages"]["Pricing"]}</td><td>{day["pages"]["FAQ"]}</td><td>{day["pages"]["Signup"]}</td></tr>' for day in reversed(visitors["trend"]))
    return HTMLResponse(f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Admin Dashboard</title><style>*{{box-sizing:border-box}}body{{margin:0;background:#F3F4F6;color:#111827;font-family:Arial}}main{{width:min(1320px,calc(100% - 32px));margin:34px auto}}nav,.quick{{display:flex;gap:10px;flex-wrap:wrap}}nav a,.quick a{{padding:11px 15px;border-radius:9px;background:#111827;color:#fff;text-decoration:none;font-weight:bold}}section{{margin-top:28px}}.grid{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:13px}}.card,.activity{{padding:20px;border:1px solid #E5E7EB;border-radius:14px;background:#fff}}.card span{{display:block;color:#64748B;font-size:13px;font-weight:bold}}.card strong{{display:block;margin-top:8px;font-size:25px}}.activity-grid{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px}}.table-wrap{{overflow:auto}}table{{width:100%;border-collapse:collapse;background:#fff}}th,td{{padding:10px;border-bottom:1px solid #E5E7EB;text-align:left}}th{{background:#111827;color:#fff}}ul{{margin:0;padding:0;list-style:none}}li{{padding:10px 0;border-bottom:1px solid #E5E7EB;font-size:13px}}@media(max-width:850px){{.grid,.activity-grid{{grid-template-columns:1fr 1fr}}}}@media(max-width:520px){{.grid,.activity-grid{{grid-template-columns:1fr}}}}</style></head><body><main><nav><a href="/">Dashboard</a><a href="/admin/subscriptions">Subscriptions</a><a href="/admin/email-readiness">Email Readiness</a></nav><h1>Admin Dashboard 2.0</h1><section><h2>Overview</h2><div class="grid">{_cards(metrics["users"])}</div></section><section><h2>Visitor Analytics</h2><p>Anonymous page and acquisition categories only. No IP, email, referrer URL, or tracking cookie is stored.</p><div class="grid">{_cards(visitor_cards)}</div><h3>Visited Pages</h3><div class="grid">{_cards(visitors["pages"])}</div><h3>Acquisition Sources</h3><div class="grid">{_cards(visitors["sources"])}</div><h3>Visitor Trend · Last 30 Days</h3><div class="table-wrap"><table><thead><tr><th>Date</th><th>Visits</th><th>Landing</th><th>Pricing</th><th>FAQ</th><th>Signup</th></tr></thead><tbody>{visitor_trend}</tbody></table></div></section><section><h2>Product Analytics</h2><p>Privacy-minimized product-flow events only. No request or document content is collected.</p><div class="grid">{_cards(product_cards)}</div><h3>Last 30 Days</h3><div class="table-wrap"><table><thead><tr><th>Date</th><th>Total Events</th><th>Signups</th><th>Wizard Completed</th><th>Invoices</th><th>Email Sent</th></tr></thead><tbody>{trend_rows}</tbody></table></div></section><section><h2>Documents</h2><div class="grid">{_cards({"Created Today": metrics["documents_today"], "Created This Month": metrics["documents_month"]})}{document_cards}</div></section><section><h2>Revenue</h2><div class="grid">{_cards(revenue)}</div></section><section><h2>Email</h2><div class="grid">{_cards({"Sent Successfully": metrics["email"]["Success"], "Failed": metrics["email"]["Failed"]})}</div><article class="activity"><h3>Recent Email</h3><ul>{email_recent}</ul></article></section><section><h2>Shipments</h2><div class="grid">{_cards(metrics["shipments"])}</div></section><section><h2>Customers</h2><div class="grid">{_cards(metrics["customers"])}</div></section><section><h2>Recent Activity</h2><div class="activity-grid"><article class="activity"><h3>Recent Signups</h3><ul>{recent_users}</ul></article><article class="activity"><h3>Recent Documents</h3><ul>{recent_documents}</ul></article><article class="activity"><h3>Recent Shipments</h3><ul>{recent_shipments}</ul></article></div></section><section><h2>Quick Actions</h2><div class="quick"><a href="/company">Create Company</a><a href="/invoice">Create Invoice</a><a href="/shipment-form">Create Shipment</a></div></section></main></body></html>''')
