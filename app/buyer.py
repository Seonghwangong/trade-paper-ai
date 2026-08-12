from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from app.account_buyer import ensure_legacy_buyer_ownership, public_buyer
from app.auth import USERS_FILE
from app.storage import data_path, locked_json_mutation
from app.validation import require_text
from app.referential_integrity import find_soft_warnings, render_delete_page
from app.ui import html_escape

router = APIRouter()

BUYER_FILE = data_path("buyers.json")


def _account_id(request):
    user = request.scope.get("trade_paper_user") or {}
    return str(user.get("account_id", "") or "").strip()


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


@router.get("/buyer-data")
def buyer_data(request: Request):
    return load_buyers(_account_id(request))


@router.get("/buyers")
def buyer_list(request: Request):
    entries = owned_buyer_entries(_account_id(request))
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

<div style="background:white;border:1px solid #E5E7EB;border-radius:16px;overflow:hidden;">

<table style="width:100%;border-collapse:collapse;table-layout:fixed;">
<tr style="background:#F9FAFB;">
<th style="padding:14px;width:8%;">No</th>
<th style="width:18%;">Name</th>
<th style="width:30%;">Address</th>
<th style="width:22%;">Email</th>
<th style="width:12%;">Country</th>
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
    } if demo == 1 else {"name": "", "address": "", "email": "", "country": ""}
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
    )
    return HTMLResponse(html)


@router.post("/save-buyer")
def save_buyer(
    request: Request,
    name: str = Form(""),
    address: str = Form(""),
    email: str = Form(""),
    country: str = Form(""),
):
    name = require_text("Buyer name", name)
    buyer = {
        "account_id": _account_id(request),
        "name": name,
        "address": address,
        "email": email,
        "country": country
    }

    locked_json_mutation(BUYER_FILE, [], lambda buyers: buyers.append(buyer), list)

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
):
    name = require_text("Buyer name", name)
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
            "country": country
        }
    locked_json_mutation(BUYER_FILE, [], replace_buyer, list)

    return RedirectResponse(url="/buyers", status_code=303)


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
