from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from app.account_buyer import ensure_legacy_buyer_ownership, public_buyer
from app.auth import USERS_FILE
from app.storage import data_path, load_json_strict, locked_json_mutation
from app.validation import require_text
from app.referential_integrity import find_soft_warnings, render_delete_page
from app.ui import html_escape

router = APIRouter()

BUYER_FILE = data_path("buyers.json")
CUSTOMER_STATUSES = ("Lead", "Prospect", "Customer", "Inactive")


def _account_id(request):
    user = request.scope.get("trade_paper_user") or {}
    account_id = str(user.get("account_id", "") or "").strip()
    if not account_id:
        raise HTTPException(status_code=401, detail="Authenticated account is required")
    return account_id


def load_buyer_records():
    return ensure_legacy_buyer_ownership(BUYER_FILE, USERS_FILE)


def owned_buyer_entries(account_id):
    owner = str(account_id or "").strip()
    return [
        (index, record)
        for index, record in enumerate(load_buyer_records())
        if isinstance(record, dict) and str(record.get("account_id", "") or "").strip() == owner
    ]


def load_buyers(account_id):
    return [public_buyer(record) for _, record in owned_buyer_entries(account_id)]


def search_buyer_records(account_id):
    return [
        {**public_buyer(record), "_storage_index": index}
        for index, record in owned_buyer_entries(account_id)
    ]


def _owned_buyer(index, account_id):
    records = load_buyer_records()
    if (
        index < 0
        or index >= len(records)
        or not isinstance(records[index], dict)
        or str(records[index].get("account_id", "") or "").strip() != str(account_id or "").strip()
    ):
        raise HTTPException(status_code=404, detail="Buyer not found")
    return records[index]


def _number(value):
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _buyer_match(record, buyer):
    names = {
        str(record.get(field, "") or "").strip().casefold()
        for field in ("buyer", "buyer_name", "consignee", "consignee_name", "customer")
    }
    emails = {
        str(record.get(field, "") or "").strip().casefold()
        for field in ("buyer_email", "consignee_email", "importer_email")
    }
    name = str(buyer.get("name", "") or "").strip().casefold()
    email = str(buyer.get("email", "") or "").strip().casefold()
    return bool((name and name in names) or (email and email in emails))


def buyer_workspace_metrics(buyer, account_id):
    from app import invoice as invoice_module, shipment as shipment_module
    from app import document_email
    invoices = [record for record in invoice_module.owned_invoice_records(account_id) if _buyer_match(record, buyer)]
    shipments = [record for record in shipment_module.owned_shipment_records(account_id) if _buyer_match(record, buyer)]
    invoices.sort(key=lambda record: (str(record.get("invoice_date", "") or record.get("date", "")), str(record.get("invoice_no", ""))), reverse=True)
    shipments.sort(key=lambda record: (str(record.get("shipment_date", "") or ""), str(record.get("shipment_no", ""))), reverse=True)
    buyer_email = str(buyer.get("email", "") or "").strip().casefold()
    emails = [
        record for record in load_json_strict(document_email.HISTORY_FILE, default=[], expected_type=list)
        if isinstance(record, dict)
        and record.get("account_id") == account_id
        and buyer_email
        and str(record.get("recipient", "") or "").strip().casefold() == buyer_email
    ]
    emails.sort(key=lambda record: str(record.get("sent_at", "") or ""), reverse=True)
    invoice_dates = [str(record.get("invoice_date", "") or record.get("date", "")) for record in invoices]
    shipment_dates = [str(record.get("shipment_date", "") or "") for record in shipments]
    latest_dates = [value for value in invoice_dates + shipment_dates if value]
    total = sum(
        sum(_number(item.get("quantity")) * _number(item.get("unit_price")) for item in record.get("items", []) if isinstance(item, dict))
        for record in invoices
    )
    return {
        "transaction_count": len(invoices),
        "total_invoice_amount": total,
        "last_transaction_date": max(latest_dates) if latest_dates else "",
        "latest_shipment": shipments[0] if shipments else None,
        "latest_email": emails[0] if emails else None,
    }


def _status_options(current):
    selected = current if current in CUSTOMER_STATUSES else "Lead"
    return "".join(
        f'<option value="{status}"{" selected" if status == selected else ""}>{status}</option>'
        for status in CUSTOMER_STATUSES
    )


@router.get("/buyer-data")
def buyer_data(request: Request):
    return [
        {key: record.get(key, "") for key in ("name", "address", "email", "country")}
        for record in load_buyers(_account_id(request))
    ]


@router.get("/buyers")
def buyer_list(request: Request, search: str = ""):
    entries = owned_buyer_entries(_account_id(request))
    query = str(search or "").strip().casefold()
    if query:
        entries = [(index, buyer) for index, buyer in entries if any(query in str(buyer.get(field, "") or "").casefold() for field in ("name", "email", "country", "status"))]
    next_action = (
        '<a href="/product-form"><button style="padding:13px 22px;background:#1D4ED8;color:white;border:none;border-radius:10px;font-size:16px;">Next: Add Product →</button></a>'
        if entries else ""
    )

    html = """
<h1 style="font-family:Arial;text-align:center;font-size:48px;margin-bottom:10px;">
Buyer Master
</h1>

<p style="font-family:Arial;text-align:center;font-size:16px;color:#6B7280;margin-top:0;margin-bottom:35px;">
Manage registered buyer information
</p>

<div style="font-family:Arial;width:94%;margin:auto;">

<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:25px;gap:20px;">
<div style="display:flex;gap:12px;">
<a href="/audit-log?document=Buyer"><button type="button" style="padding:13px 22px;background:#374151;color:white;border:none;border-radius:10px;font-size:16px;">History</button></a>
<a href="/buyer-form">
<button style="padding:13px 22px;background:#111827;color:white;border:none;border-radius:10px;font-size:16px;">
+ Add Buyer
</button>
</a>

<a href="/">
<button style="padding:13px 22px;background:#111827;color:white;border:none;border-radius:10px;font-size:16px;">
← Dashboard
</button>
</a>
</div>
""" + next_action + """
</div>

<p style="font-size:18px;font-weight:bold;margin:25px 0;">
Total Buyers : """ + str(len(entries)) + """
</p>
<form action="/buyers" method="get" style="display:flex;gap:10px;margin-bottom:20px;"><input list="buyer-search-options" name="search" value=""" + html_escape(search, attribute=True) + """" placeholder="Search buyer" autocomplete="off" style="flex:1;padding:13px;border:1px solid #D1D5DB;border-radius:10px;"><datalist id="buyer-search-options">""" + "".join(f'<option value="{html_escape(buyer.get("name", ""), attribute=True)}">' for _, buyer in owned_buyer_entries(_account_id(request))) + """</datalist><button type="submit" style="padding:13px 22px;background:#111827;color:white;border:0;border-radius:10px;">Search</button></form>

<div style="background:white;border:1px solid #E5E7EB;border-radius:16px;overflow:hidden;">

<table style="width:100%;border-collapse:collapse;table-layout:fixed;">
<tr style="background:#F9FAFB;">
<th style="padding:14px;width:8%;">No</th>
<th style="width:18%;">Name</th>
<th style="width:30%;">Address</th>
<th style="width:22%;">Email</th>
<th style="width:12%;">Country</th>
<th style="width:10%;">Status</th>
<th style="width:7%;">Workspace</th>
<th style="width:5%;">Edit</th>
<th style="width:5%;">Delete</th>
</tr>
"""

    for row_number, (index, buyer) in enumerate(entries, start=1):
        html += f"""
<tr style="border-top:1px solid #E5E7EB;">
<td style="padding:14px;text-align:center;">{row_number}</td>
<td style="padding:14px;word-break:break-word;">{html_escape(buyer.get("name", ""))}</td>
<td style="padding:14px;word-break:break-word;">{html_escape(buyer.get("address", ""))}</td>
<td style="padding:14px;word-break:break-word;">{html_escape(buyer.get("email", ""))}</td>
<td style="padding:14px;text-align:center;word-break:break-word;">{html_escape(buyer.get("country", ""))}</td>
<td style="padding:14px;text-align:center;">{html_escape(buyer.get("status", "Lead") or "Lead")}</td>
<td style="text-align:center;"><a href="/buyer/{index}" style="color:#1D4ED8;font-weight:bold;text-decoration:none;">View</a></td>
<td style="text-align:center;">
<a href="/edit-buyer/{index}" style="color:#111827;font-weight:bold;text-decoration:none;">Edit</a>
</td>
<td style="text-align:center;">
<a href="/delete-buyer/{index}" style="color:#DC2626;font-weight:bold;text-decoration:none;">Delete</a>
</td>
</tr>
"""

    html += """
</table>
</div>
</div>
"""

    return HTMLResponse(html)


@router.get("/buyer-form")
def buyer_form(demo: int = 0):
    demo_values = {
        "name": "Sakura Retail Co.",
        "address": "Tokyo, Japan",
        "email": "buyer@example.jp",
        "country": "Japan",
        "status": "Lead",
    } if demo == 1 else {"name": "", "address": "", "email": "", "country": "", "status": "Lead"}
    demo_notice = (
        '<div class="demo-preview"><b>Demo Preview</b><br>'
        'Temporary values — nothing is saved until you press Save.</div>'
        if demo == 1 else ""
    )
    html = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Add Buyer</title>
<style>
body{font-family:Arial,sans-serif;background:#f3f4f6;padding:40px;}
.container{max-width:900px;margin:auto;background:white;padding:35px;border-radius:16px;}
h1{text-align:center;font-size:48px;margin-bottom:10px;}
.sub{text-align:center;color:#6B7280;margin-bottom:35px;}
.nav{display:flex;gap:12px;margin-bottom:25px;}
.card{border:1px solid #E5E7EB;border-radius:16px;padding:25px;margin-bottom:25px;background:#fff;}
input{width:100%;padding:14px;margin-bottom:14px;border:1px solid #D1D5DB;border-radius:10px;font-size:16px;box-sizing:border-box;}
select,textarea{width:100%;padding:14px;margin-bottom:14px;border:1px solid #D1D5DB;border-radius:10px;font-size:16px;box-sizing:border-box;}
button{padding:16px;background:#111827;color:white;border:none;border-radius:12px;font-size:18px;cursor:pointer;}
.small{padding:13px 22px;font-size:16px;border-radius:10px;}
.full{width:100%;}
.demo-preview{padding:16px;margin-bottom:20px;border:1px solid #BFDBFE;border-radius:12px;background:#EFF6FF;color:#1E3A8A;line-height:1.5;}
</style>
</head>
<body>
<div class="container">

<div class="nav">
<a href="/"><button type="button" class="small">← Dashboard</button></a>
<a href="/buyers"><button type="button" class="small">← Buyer List</button></a>
</div>

<h1>Add Buyer</h1>
<p class="sub">Register buyer master information</p>
__DEMO_NOTICE__

<div class="card">
<h2>Buyer Information</h2>

<form action="/save-buyer" method="post">
<input type="text" name="name" value="__DEMO_NAME__" placeholder="Buyer Name">
<input type="text" name="address" value="__DEMO_ADDRESS__" placeholder="Address">
<input type="text" name="email" value="__DEMO_EMAIL__" placeholder="Email">
<input type="text" name="country" value="__DEMO_COUNTRY__" placeholder="Country">
<select name="status" style="width:100%;padding:14px;margin-bottom:14px;border:1px solid #D1D5DB;border-radius:10px;font-size:16px;">__STATUS_OPTIONS__</select>
<h2>Frequently Used Terms</h2>
<label for="default_currency">Default Currency</label><input id="default_currency" type="text" name="default_currency" value="__DEFAULT_CURRENCY__" placeholder="e.g. USD">
<label for="default_trade_term">Default Trade Term</label><input id="default_trade_term" type="text" name="default_trade_term" value="__DEFAULT_TRADE_TERM__" placeholder="e.g. FOB">
<label for="default_payment_term">Default Payment Term</label><input id="default_payment_term" type="text" name="default_payment_term" value="__DEFAULT_PAYMENT_TERM__" placeholder="e.g. T/T 30 days">
<label for="preferred_carrier">Preferred Carrier</label><input id="preferred_carrier" type="text" name="preferred_carrier" value="__PREFERRED_CARRIER__">
<label for="preferred_loading_port">Preferred Loading Port</label><input id="preferred_loading_port" type="text" name="preferred_loading_port" value="__PREFERRED_LOADING_PORT__">
<label for="preferred_destination_port">Preferred Destination Port</label><input id="preferred_destination_port" type="text" name="preferred_destination_port" value="__PREFERRED_DESTINATION_PORT__">
<label for="default_remarks">Default Remarks</label><textarea id="default_remarks" name="default_remarks" rows="4">__DEFAULT_REMARKS__</textarea>

<button type="submit" class="full">Save Buyer</button>
</form>
</div>

</div>
</body>
</html>
"""

    html = (
        html.replace("__DEMO_NOTICE__", demo_notice)
        .replace("__DEMO_NAME__", demo_values["name"])
        .replace("__DEMO_ADDRESS__", demo_values["address"])
        .replace("__DEMO_EMAIL__", demo_values["email"])
        .replace("__DEMO_COUNTRY__", demo_values["country"])
        .replace("__STATUS_OPTIONS__", _status_options(demo_values["status"]))
        .replace("__DEFAULT_CURRENCY__", "")
        .replace("__DEFAULT_TRADE_TERM__", "")
        .replace("__DEFAULT_PAYMENT_TERM__", "")
        .replace("__PREFERRED_CARRIER__", "")
        .replace("__PREFERRED_LOADING_PORT__", "")
        .replace("__PREFERRED_DESTINATION_PORT__", "")
        .replace("__DEFAULT_REMARKS__", "")
    )
    return HTMLResponse(html)


@router.post("/save-buyer")
def save_buyer(
    request: Request,
    name: str = Form(""),
    address: str = Form(""),
    email: str = Form(""),
    country: str = Form(""),
    status: str = Form("Lead"),
    default_currency: str = Form(""),
    default_trade_term: str = Form(""),
    default_payment_term: str = Form(""),
    preferred_carrier: str = Form(""),
    preferred_loading_port: str = Form(""),
    preferred_destination_port: str = Form(""),
    default_remarks: str = Form(""),
):
    name = require_text("Buyer name", name)
    defaults = [default_currency, default_trade_term, default_payment_term, preferred_carrier, preferred_loading_port, preferred_destination_port, default_remarks]
    default_currency, default_trade_term, default_payment_term, preferred_carrier, preferred_loading_port, preferred_destination_port, default_remarks = [value if isinstance(value, str) else "" for value in defaults]
    buyer = {
        "account_id": _account_id(request),
        "name": name,
        "address": address,
        "email": email,
        "country": country,
        "status": status if status in CUSTOMER_STATUSES else "Lead",
        "default_currency": default_currency,
        "default_trade_term": default_trade_term,
        "default_payment_term": default_payment_term,
        "preferred_carrier": preferred_carrier,
        "preferred_loading_port": preferred_loading_port,
        "preferred_destination_port": preferred_destination_port,
        "default_remarks": default_remarks,
    }

    locked_json_mutation(BUYER_FILE, [], lambda buyers: buyers.append(buyer), list)
    from app.audit_log import record_request_audit
    record_request_audit(request, "Create", "Buyer", name, path=BUYER_FILE.with_name("audit_log.json"))

    return RedirectResponse(url="/buyers", status_code=303)


@router.get("/edit-buyer/{index}")
def edit_buyer(index: int, request: Request):
    buyer = _owned_buyer(index, _account_id(request))

    html = f"""
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Edit Buyer</title>
<style>
body{{font-family:Arial,sans-serif;background:#f3f4f6;padding:40px;}}
.container{{max-width:900px;margin:auto;background:white;padding:35px;border-radius:16px;}}
h1{{text-align:center;font-size:48px;margin-bottom:10px;}}
.sub{{text-align:center;color:#6B7280;margin-bottom:35px;}}
.nav{{display:flex;gap:12px;margin-bottom:25px;}}
.card{{border:1px solid #E5E7EB;border-radius:16px;padding:25px;margin-bottom:25px;background:#fff;}}
input{{width:100%;padding:14px;margin-bottom:14px;border:1px solid #D1D5DB;border-radius:10px;font-size:16px;box-sizing:border-box;}}
select,textarea{{width:100%;padding:14px;margin-bottom:14px;border:1px solid #D1D5DB;border-radius:10px;font-size:16px;box-sizing:border-box;}}
button{{padding:16px;background:#111827;color:white;border:none;border-radius:12px;font-size:18px;cursor:pointer;}}
.small{{padding:13px 22px;font-size:16px;border-radius:10px;}}
.full{{width:100%;}}
</style>
</head>
<body>
<div class="container">

<div class="nav">
<a href="/"><button type="button" class="small">← Dashboard</button></a>
<a href="/buyers"><button type="button" class="small">← Buyer List</button></a>
</div>

<h1>Edit Buyer</h1>
<p class="sub">Update buyer master information</p>

<div class="card">
<h2>Buyer Information</h2>

<form action="/update-buyer/{index}" method="post">
<input type="text" name="name" value="{html_escape(buyer.get('name', ''), attribute=True)}" placeholder="Buyer Name">
<input type="text" name="address" value="{html_escape(buyer.get('address', ''), attribute=True)}" placeholder="Address">
<input type="text" name="email" value="{html_escape(buyer.get('email', ''), attribute=True)}" placeholder="Email">
<input type="text" name="country" value="{html_escape(buyer.get('country', ''), attribute=True)}" placeholder="Country">
<select name="status" style="width:100%;padding:14px;margin-bottom:14px;border:1px solid #D1D5DB;border-radius:10px;font-size:16px;">{_status_options(buyer.get('status', 'Lead'))}</select>
<h2>Frequently Used Terms</h2>
<label for="default_currency">Default Currency</label><input id="default_currency" type="text" name="default_currency" value="{html_escape(buyer.get('default_currency', ''), attribute=True)}">
<label for="default_trade_term">Default Trade Term</label><input id="default_trade_term" type="text" name="default_trade_term" value="{html_escape(buyer.get('default_trade_term', ''), attribute=True)}">
<label for="default_payment_term">Default Payment Term</label><input id="default_payment_term" type="text" name="default_payment_term" value="{html_escape(buyer.get('default_payment_term', ''), attribute=True)}">
<label for="preferred_carrier">Preferred Carrier</label><input id="preferred_carrier" type="text" name="preferred_carrier" value="{html_escape(buyer.get('preferred_carrier', ''), attribute=True)}">
<label for="preferred_loading_port">Preferred Loading Port</label><input id="preferred_loading_port" type="text" name="preferred_loading_port" value="{html_escape(buyer.get('preferred_loading_port', ''), attribute=True)}">
<label for="preferred_destination_port">Preferred Destination Port</label><input id="preferred_destination_port" type="text" name="preferred_destination_port" value="{html_escape(buyer.get('preferred_destination_port', ''), attribute=True)}">
<label for="default_remarks">Default Remarks</label><textarea id="default_remarks" name="default_remarks" rows="4">{html_escape(buyer.get('default_remarks', ''))}</textarea>

<button type="submit" class="full">Update Buyer</button>
</form>
</div>

</div>
</body>
</html>
"""

    return HTMLResponse(html)


@router.post("/update-buyer/{index}")
def update_buyer(
    index: int,
    request: Request,
    name: str = Form(""),
    address: str = Form(""),
    email: str = Form(""),
    country: str = Form(""),
    status: str = Form("Lead"),
    default_currency: str = Form(""),
    default_trade_term: str = Form(""),
    default_payment_term: str = Form(""),
    preferred_carrier: str = Form(""),
    preferred_loading_port: str = Form(""),
    preferred_destination_port: str = Form(""),
    default_remarks: str = Form(""),
):
    name = require_text("Buyer name", name)
    defaults = [default_currency, default_trade_term, default_payment_term, preferred_carrier, preferred_loading_port, preferred_destination_port, default_remarks]
    default_currency, default_trade_term, default_payment_term, preferred_carrier, preferred_loading_port, preferred_destination_port, default_remarks = [value if isinstance(value, str) else "" for value in defaults]
    account_id = _account_id(request)
    def replace_buyer(buyers):
        if (
            not 0 <= index < len(buyers)
            or not isinstance(buyers[index], dict)
            or str(buyers[index].get("account_id", "") or "").strip() != account_id
        ):
            raise HTTPException(status_code=404, detail="Buyer not found")
        buyers[index] = {
            "account_id": account_id,
            "name": name,
            "address": address,
            "email": email,
            "country": country,
            "status": status if status in CUSTOMER_STATUSES else "Lead",
            "default_currency": default_currency,
            "default_trade_term": default_trade_term,
            "default_payment_term": default_payment_term,
            "preferred_carrier": preferred_carrier,
            "preferred_loading_port": preferred_loading_port,
            "preferred_destination_port": preferred_destination_port,
            "default_remarks": default_remarks,
        }
    locked_json_mutation(BUYER_FILE, [], replace_buyer, list)
    from app.audit_log import record_request_audit
    record_request_audit(request, "Update", "Buyer", name, path=BUYER_FILE.with_name("audit_log.json"))

    return RedirectResponse(url="/buyers", status_code=303)


@router.get("/buyer/{index}")
def buyer_workspace(index: int, request: Request):
    account_id = _account_id(request)
    buyer = _owned_buyer(index, account_id)
    metrics = buyer_workspace_metrics(buyer, account_id)
    shipment = metrics["latest_shipment"] or {}
    email = metrics["latest_email"] or {}
    shipment_no = str(shipment.get("shipment_no", "") or "")
    email_shipment = str(email.get("shipment_no", "") or "")
    latest_shipment = f'<a href="/shipment/{html_escape(shipment_no, attribute=True)}">{html_escape(shipment_no)}</a>' if shipment_no else "No shipments"
    latest_email = f'<a href="/shipment/{html_escape(email_shipment, attribute=True)}#email-history">{html_escape(email.get("document_no", ""))} · {html_escape(email.get("status", ""))}</a>' if email_shipment else (f'{html_escape(email.get("document_no", ""))} · {html_escape(email.get("status", ""))}' if email else "No emails")
    return HTMLResponse(f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{html_escape(buyer.get("name", ""))} Workspace</title><style>*{{box-sizing:border-box}}body{{margin:0;padding:36px;background:#F3F4F6;color:#111827;font-family:Arial}}main{{width:min(1050px,100%);margin:auto}}nav{{display:flex;gap:10px;margin-bottom:22px}}nav a,.action{{padding:11px 15px;border-radius:9px;background:#111827;color:#fff;text-decoration:none;font-weight:bold}}header,.card{{background:#fff;border:1px solid #E5E7EB;border-radius:16px;padding:24px}}header h1{{margin:0 0 8px}}.badge{{display:inline-block;padding:6px 10px;border-radius:999px;background:#DBEAFE;color:#1E3A8A;font-weight:bold}}.grid{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:15px;margin-top:20px}}.card span{{display:block;color:#64748B;font-size:13px;font-weight:bold}}.card strong{{display:block;margin-top:9px;font-size:22px}}.links{{display:grid;grid-template-columns:1fr 1fr;gap:15px;margin-top:15px}}.links a{{color:#1D4ED8;font-weight:bold}}@media(max-width:760px){{.grid,.links{{grid-template-columns:1fr}}}}</style></head><body><main><nav><a href="/buyers">Buyer List</a><a href="/edit-buyer/{index}">Edit Customer</a></nav><header><span class="badge">{html_escape(buyer.get("status", "Lead") or "Lead")}</span><h1>{html_escape(buyer.get("name", ""))}</h1><p>{html_escape(buyer.get("email", ""))} · {html_escape(buyer.get("country", ""))}</p><p>{html_escape(buyer.get("address", ""))}</p></header><section class="grid"><article class="card"><span>Last Transaction</span><strong>{html_escape(metrics["last_transaction_date"] or "-")}</strong></article><article class="card"><span>Transactions</span><strong>{metrics["transaction_count"]}</strong></article><article class="card"><span>Total Invoice Amount</span><strong>USD {metrics["total_invoice_amount"]:,.2f}</strong></article><article class="card"><span>Customer Status</span><strong>{html_escape(buyer.get("status", "Lead") or "Lead")}</strong></article></section><section class="links"><article class="card"><span>Latest Shipment</span><strong>{latest_shipment}</strong></article><article class="card"><span>Latest Email</span><strong>{latest_email}</strong></article></section></main></body></html>''')


@router.get("/delete-buyer/{index}")
def delete_buyer(index: int, request: Request):
    account_id = _account_id(request)
    buyer = _owned_buyer(index, account_id)
    name = str(buyer.get("name", "") or "").strip()
    return render_delete_page(
        "Buyer",
        name,
        f"/delete-buyer/{index}",
        "/buyers",
        warnings=find_soft_warnings("Buyer", name, account_id=account_id),
        expected_name=name,
    )

@router.post("/delete-buyer/{index}")
def confirm_delete_buyer(index: int, request: Request, expected_name: str = Form("")):
    account_id = _account_id(request)
    expected = str(expected_name or "").strip()

    def remove(buyers):
        if (
            not 0 <= index < len(buyers)
            or not isinstance(buyers[index], dict)
            or str(buyers[index].get("account_id", "") or "").strip() != account_id
            or str(buyers[index].get("name", "") or "").strip().casefold() != expected.casefold()
        ):
            raise HTTPException(status_code=404, detail="Buyer not found")
        buyers.pop(index)

    locked_json_mutation(BUYER_FILE, [], remove, list)
    return RedirectResponse("/buyers", status_code=303)
