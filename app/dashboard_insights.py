from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import html

from app import storage
from app.documents import DOCUMENT_DEFINITIONS, document_url


DATE_FIELDS = (
    "created_at", "updated_at", "sent_at", "invoice_date", "shipment_date",
    "packing_date", "si_date", "booking_date", "bl_date", "co_date",
    "issue_date", "date",
)


def _date(record):
    return next((str(record.get(field, "") or "") for field in DATE_FIELDS if record.get(field)), "")


def _owned(records, account_id):
    owner = str(account_id or "").strip()
    return [
        record for record in records
        if isinstance(record, dict)
        and str(record.get("account_id", "") or "").strip() == owner
        and not record.get("archived_at")
    ]


def _load_owned(filename, account_id):
    return _owned(storage.load_json_strict(storage.data_path(filename), [], list), account_id)


def _product_name(item):
    if not isinstance(item, dict):
        return ""
    return next((
        str(item.get(field, "") or "").strip()
        for field in ("name", "item_name", "product", "description")
        if str(item.get(field, "") or "").strip()
    ), "")


def dashboard_insights(account_id, now=None):
    current = now or datetime.now(timezone.utc)
    month = current.strftime("%Y-%m")
    start_date = current.date().toordinal() - 29
    trend = {
        datetime.fromordinal(start_date + offset).strftime("%Y-%m-%d"): {
            "Documents": 0, "Shipments": 0, "Emails": 0,
        }
        for offset in range(30)
    }

    document_rows = []
    invoices = []
    shipments = []
    for definition in DOCUMENT_DEFINITIONS:
        if definition.dashboard_category == "Master Data":
            continue
        rows = _load_owned(definition.storage_filename, account_id)
        if definition.key == "invoice":
            invoices = rows
        if definition.key == "shipment":
            shipments = rows
        for record in rows:
            date = _date(record)[:10]
            document_rows.append((date, definition, record))
            if date in trend:
                trend[date]["Documents"] += 1

    emails = _load_owned("email_history.json", account_id)
    for record in shipments:
        date = _date(record)[:10]
        if date in trend:
            trend[date]["Shipments"] += 1
    for record in emails:
        date = _date(record)[:10]
        if date in trend:
            trend[date]["Emails"] += 1

    buyers = Counter()
    products = Counter()
    for invoice in invoices:
        buyer = str(invoice.get("buyer", "") or invoice.get("buyer_name", "") or "").strip()
        if buyer:
            buyers[buyer] += 1
        for item in invoice.get("items", []) or []:
            name = _product_name(item)
            if name:
                products[name] += 1

    recent = []
    invoice_definition = next(item for item in DOCUMENT_DEFINITIONS if item.key == "invoice")
    shipment_definition = next(item for item in DOCUMENT_DEFINITIONS if item.key == "shipment")
    for record in invoices:
        identifier = str(record.get("invoice_no", "") or "")
        recent.append({"type": "Invoice", "identifier": identifier, "date": _date(record), "url": document_url(invoice_definition, "edit", identifier)})
    for record in shipments:
        identifier = str(record.get("shipment_no", "") or "")
        recent.append({"type": "Shipment", "identifier": identifier, "date": _date(record), "url": document_url(shipment_definition, "detail", identifier)})
    for record in emails:
        identifier = str(record.get("document_no", "") or "")
        shipment_no = str(record.get("shipment_no", "") or "")
        recent.append({"type": "Email", "identifier": identifier, "date": _date(record), "url": f"/shipment/{shipment_no}" if shipment_no else "/"})
    recent.sort(key=lambda row: (row["date"], row["identifier"]), reverse=True)

    return {
        "month": {
            "Documents": sum(date.startswith(month) for date, _, _ in document_rows),
            "Shipments": sum(_date(record).startswith(month) for record in shipments),
            "Emails": sum(_date(record).startswith(month) for record in emails),
        },
        "top_buyers": buyers.most_common(5),
        "top_products": products.most_common(5),
        "recent": recent[:5],
        "trend": [{"date": date, **counts} for date, counts in trend.items()],
    }


def _text(value):
    return html.escape(str(value or ""), quote=True)


def render_dashboard_insights(insights):
    cards = "".join(f'<article class="insight-card"><span>{_text(label)}</span><strong>{count}</strong></article>' for label, count in insights["month"].items())
    buyers = "".join(f"<li><span>{_text(name)}</span><strong>{count}</strong></li>" for name, count in insights["top_buyers"])
    products = "".join(f"<li><span>{_text(name)}</span><strong>{count}</strong></li>" for name, count in insights["top_products"])
    recent = "".join(f'<li><span><b>{_text(row["type"])}</b> · {_text(row["identifier"] or "—")}</span><a href="{_text(row["url"])}">Open</a></li>' for row in insights["recent"])
    trend = "".join(f'<tr><td>{_text(row["date"])}</td><td>{row["Documents"]}</td><td>{row["Shipments"]}</td><td>{row["Emails"]}</td></tr>' for row in insights["trend"])
    empty = '<li class="insight-empty">No activity yet.</li>'
    return f'''<section class="section personal-insights" data-dashboard-scope="account">
<h2 class="section-title">Your Trade Activity</h2>
<h3>This Month</h3><div class="insight-card-grid">{cards}</div>
<div class="insight-columns"><article><h3>Top Buyers</h3><ol>{buyers or empty}</ol></article>
<article><h3>Top Products</h3><ol>{products or empty}</ol></article>
<article><h3>Recent Activity</h3><ol>{recent or empty}</ol></article></div>
<details><summary>30-Day Trend</summary><div class="table-wrap"><table><thead><tr><th>Date</th><th>Documents</th><th>Shipments</th><th>Emails</th></tr></thead><tbody>{trend}</tbody></table></div></details></section>'''
