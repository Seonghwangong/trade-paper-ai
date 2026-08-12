from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from app.account_customer import ensure_legacy_customer_ownership, public_customer
from app.auth import USERS_FILE
from app.storage import data_path, locked_json_mutation
from app.validation import ensure_unique_name_casefold, require_text
from app.referential_integrity import find_soft_warnings, render_delete_page

router = APIRouter()

CUSTOMER_FILE = data_path("customers.json")


def _account_id(request):
    user = request.scope.get("trade_paper_user") or {}
    return str(user.get("account_id", "") or "").strip()


def load_customer_records():
    return ensure_legacy_customer_ownership(CUSTOMER_FILE, USERS_FILE)


def owned_customer_entries(account_id):
    owner = str(account_id or "").strip()
    return [
        (index, record)
        for index, record in enumerate(load_customer_records())
        if isinstance(record, dict) and str(record.get("account_id", "") or "").strip() == owner
    ]


def load_customers(account_id):
    return [public_customer(record) for _, record in owned_customer_entries(account_id)]


def _owned_customer(index, account_id):
    records = load_customer_records()
    if (
        index < 0
        or index >= len(records)
        or not isinstance(records[index], dict)
        or str(records[index].get("account_id", "") or "").strip() != str(account_id or "").strip()
    ):
        raise HTTPException(status_code=404, detail="Customer not found")
    return records[index]


@router.get("/customer", response_class=HTMLResponse)
def customer_page(request: Request, search: str = "", edit: Optional[int] = None):
    account_id = _account_id(request)
    entries = owned_customer_entries(account_id)
    if search:
        needle = search.casefold()
        entries = [
            (index, customer) for index, customer in entries
            if any(needle in str(customer.get(field, "") or "").casefold()
                   for field in ("company", "country", "address", "email", "phone", "pic"))
        ]
    editing = _owned_customer(edit, account_id) if edit is not None else {}
    form_title = "Edit Customer" if edit is not None else "Add Customer"
    submit_label = "Update Customer" if edit is not None else "Save Customer"
    hidden_index = f'<input type="hidden" name="index" value="{edit}">' if edit is not None else ""

    html = """
<h1 style="font-family:Arial;text-align:center;font-size:48px;margin-bottom:10px;">
Customer Management
</h1>

<p style="font-family:Arial;text-align:center;font-size:16px;color:#6B7280;letter-spacing:0.5px;margin-top:0;margin-bottom:35px;">
Manage trading partners and customer information
</p>

<div style="font-family:Arial;width:80%;margin:auto;">

<div style="background:#ffffff;border:1px solid #E5E7EB;border-radius:16px;padding:30px;margin-bottom:30px;">
<h2 style="margin-top:0;">__FORM_TITLE__</h2>

<form action="/save-customer" method="post">
    __HIDDEN_INDEX__
    <p>Company</p>
    <input type="text" name="company" value="__COMPANY__" style="width:100%;padding:14px;border:1px solid #D1D5DB;border-radius:10px;">

    <p>Country</p>
    <input type="text" name="country" value="__COUNTRY__" style="width:100%;padding:14px;border:1px solid #D1D5DB;border-radius:10px;">

    <p>Address</p>
    <input type="text" name="address" value="__ADDRESS__" style="width:100%;padding:14px;border:1px solid #D1D5DB;border-radius:10px;">

    <p>Email</p>
    <input type="text" name="email" value="__EMAIL__" style="width:100%;padding:14px;border:1px solid #D1D5DB;border-radius:10px;">

    <p>Phone</p>
    <input type="text" name="phone" value="__PHONE__" style="width:100%;padding:14px;border:1px solid #D1D5DB;border-radius:10px;">

    <p>PIC</p>
    <input type="text" name="pic" value="__PIC__" style="width:100%;padding:14px;border:1px solid #D1D5DB;border-radius:10px;">

    <br><br>
    <button type="submit" style="width:100%;padding:16px;background:#111827;color:white;border:none;border-radius:12px;font-size:18px;">
        __SUBMIT_LABEL__
    </button>
</form>
</div>

<h2>Customer List</h2>
<form action="/customer" method="get" style="display:flex;gap:10px;margin-bottom:20px;">
<input type="text" name="search" value="__SEARCH__" placeholder="Search customer" style="flex:1;padding:13px;border:1px solid #D1D5DB;border-radius:10px;">
<button type="submit" style="padding:13px 22px;background:#111827;color:white;border:none;border-radius:10px;">Search</button>
</form>
"""
    for token, value in {
        "__FORM_TITLE__": form_title, "__HIDDEN_INDEX__": hidden_index,
        "__COMPANY__": editing.get("company", ""), "__COUNTRY__": editing.get("country", ""),
        "__ADDRESS__": editing.get("address", ""), "__EMAIL__": editing.get("email", ""),
        "__PHONE__": editing.get("phone", ""), "__PIC__": editing.get("pic", ""),
        "__SUBMIT_LABEL__": submit_label, "__SEARCH__": search,
    }.items():
        html = html.replace(token, str(value))
    if not entries:
        html += """
<div style="background:white;border:1px solid #E5E7EB;border-radius:16px;padding:30px;text-align:center;color:#6B7280;">
No customer has been registered yet.
</div>
"""
    else:
        for index, customer in entries:
            html += f"""
<div style="background:white;border:1px solid #E5E7EB;border-radius:16px;padding:24px;margin-bottom:16px;">
    <h2 style="margin-top:0;">{customer.get("company", "")}</h2>
    <p><b>Country:</b> {customer.get("country", "")}</p>
    <p><b>Address:</b> {customer.get("address", "")}</p>
    <p><b>Email:</b> {customer.get("email", "")}</p>
    <p><b>Phone:</b> {customer.get("phone", "")}</p>
    <p><b>PIC:</b> {customer.get("pic", "")}</p>
    <a href="/customer?edit={index}" style="color:#111827;margin-right:14px;">Edit</a>
    <a href="/delete-customer/{index}" style="color:#DC2626;">Delete</a>
</div>
"""

    html += """
<br>
<a href="/">
    <button style="width:220px;padding:15px;background:#111827;color:white;border:none;border-radius:10px;font-size:18px;">
        ← Dashboard
    </button>
</a>
</div>
"""

    return HTMLResponse(html)


@router.post("/save-customer")
def save_customer(
    request: Request,
    index: str = Form(""),
    company: str = Form(""),
    country: str = Form(""),
    address: str = Form(""),
    email: str = Form(""),
    phone: str = Form(""),
    pic: str = Form(""),
):
    company = require_text("Customer company", company)
    account_id = _account_id(request)
    submitted = {
        "account_id": account_id,
        "company": company,
        "country": country,
        "address": address,
        "email": email,
        "phone": phone,
        "pic": pic,
    }
    edit_index = None
    if str(index or "").strip():
        try:
            edit_index = int(index)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail="Customer not found") from exc

    def mutate(customers):
        owned = [
            record for record in customers
            if isinstance(record, dict) and str(record.get("account_id", "") or "").strip() == account_id
        ]
        if edit_index is None:
            ensure_unique_name_casefold(owned, "company", company)
            customers.append(submitted)
            return
        if (
            not 0 <= edit_index < len(customers)
            or not isinstance(customers[edit_index], dict)
            or str(customers[edit_index].get("account_id", "") or "").strip() != account_id
        ):
            raise HTTPException(status_code=404, detail="Customer not found")
        other_owned = [record for record in owned if record is not customers[edit_index]]
        ensure_unique_name_casefold(other_owned, "company", company)
        customers[edit_index] = submitted

    locked_json_mutation(CUSTOMER_FILE, [], mutate, list)

    return RedirectResponse(url="/customer", status_code=303)


@router.get("/delete-customer/{index}")
def delete_customer(index: int, request: Request):
    account_id = _account_id(request)
    customer = _owned_customer(index, account_id)
    name = str(customer.get("company", "") or "").strip()
    return render_delete_page(
        "Customer", name, f"/delete-customer/{index}", "/customer",
        warnings=find_soft_warnings("Customer", name, account_id=account_id),
        expected_name=name,
    )

@router.post("/delete-customer/{index}")
def confirm_delete_customer(index: int, request: Request, expected_name: str = Form("")):
    account_id = _account_id(request)
    expected = str(expected_name or "").strip()

    def remove(customers):
        if (
            not 0 <= index < len(customers)
            or not isinstance(customers[index], dict)
            or str(customers[index].get("account_id", "") or "").strip() != account_id
            or str(customers[index].get("company", "") or "").strip().casefold() != expected.casefold()
        ):
            raise HTTPException(status_code=404, detail="Customer not found")
        customers.pop(index)

    locked_json_mutation(CUSTOMER_FILE, [], remove, list)
    return RedirectResponse("/customer", status_code=303)


@router.get("/customer-data")
def customer_data(request: Request):
    return load_customers(_account_id(request))
