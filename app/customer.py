from fastapi import APIRouter, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from app.storage import atomic_write_json, data_path, load_json_strict, locked_json_mutation
from app.validation import ensure_unique_name_casefold, require_text
from app.referential_integrity import confirmed_indexed_delete, indexed_delete_confirmation

router = APIRouter()

CUSTOMER_FILE = data_path("customers.json")


def load_customers():
    return load_json_strict(CUSTOMER_FILE, [], list)


def save_customers(customers):
    atomic_write_json(CUSTOMER_FILE, customers, list)


@router.get("/customer", response_class=HTMLResponse)
def customer_page():
    customers = load_customers()

    html = """
<h1 style="font-family:Arial;text-align:center;font-size:48px;margin-bottom:10px;">
Customer Management
</h1>

<p style="font-family:Arial;text-align:center;font-size:16px;color:#6B7280;letter-spacing:0.5px;margin-top:0;margin-bottom:35px;">
Manage trading partners and customer information
</p>

<div style="font-family:Arial;width:80%;margin:auto;">

<div style="background:#ffffff;border:1px solid #E5E7EB;border-radius:16px;padding:30px;margin-bottom:30px;">
<h2 style="margin-top:0;">Add Customer</h2>

<form action="/save-customer" method="post">
    <p>Company</p>
    <input type="text" name="company" style="width:100%;padding:14px;border:1px solid #D1D5DB;border-radius:10px;">

    <p>Country</p>
    <input type="text" name="country" style="width:100%;padding:14px;border:1px solid #D1D5DB;border-radius:10px;">

    <p>Address</p>
    <input type="text" name="address" style="width:100%;padding:14px;border:1px solid #D1D5DB;border-radius:10px;">

    <p>Email</p>
    <input type="text" name="email" style="width:100%;padding:14px;border:1px solid #D1D5DB;border-radius:10px;">

    <p>Phone</p>
    <input type="text" name="phone" style="width:100%;padding:14px;border:1px solid #D1D5DB;border-radius:10px;">

    <p>PIC</p>
    <input type="text" name="pic" style="width:100%;padding:14px;border:1px solid #D1D5DB;border-radius:10px;">

    <br><br>
    <button type="submit" style="width:100%;padding:16px;background:#111827;color:white;border:none;border-radius:12px;font-size:18px;">
        Save Customer
    </button>
</form>
</div>

<h2>Customer List</h2>
"""
    if not customers:
        html += """
<div style="background:white;border:1px solid #E5E7EB;border-radius:16px;padding:30px;text-align:center;color:#6B7280;">
No customer has been registered yet.
</div>
"""
    else:
        for index, customer in enumerate(customers):
            html += f"""
<div style="background:white;border:1px solid #E5E7EB;border-radius:16px;padding:24px;margin-bottom:16px;">
    <h2 style="margin-top:0;">{customer.get("company", "")}</h2>
    <p><b>Country:</b> {customer.get("country", "")}</p>
    <p><b>Address:</b> {customer.get("address", "")}</p>
    <p><b>Email:</b> {customer.get("email", "")}</p>
    <p><b>Phone:</b> {customer.get("phone", "")}</p>
    <p><b>PIC:</b> {customer.get("pic", "")}</p>
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
    company: str = Form(""),
    country: str = Form(""),
    address: str = Form(""),
    email: str = Form(""),
    phone: str = Form(""),
    pic: str = Form(""),
):
    company = require_text("Customer company", company)
    def add_customer(customers):
        ensure_unique_name_casefold(customers, "company", company)
        customers.append({
        "company": company,
        "country": country,
        "address": address,
        "email": email,
        "phone": phone,
        "pic": pic,
        })
    locked_json_mutation(CUSTOMER_FILE, [], add_customer, list)

    return RedirectResponse(url="/customer", status_code=303)


@router.get("/delete-customer/{index}")
def delete_customer(index: int):
    return indexed_delete_confirmation("Customer", "Customer", index, CUSTOMER_FILE, "company", f"/delete-customer/{index}", "/customer")

@router.post("/delete-customer/{index}")
def confirm_delete_customer(index: int, expected_name: str = Form("")):
    return confirmed_indexed_delete("Customer", "Customer", index, expected_name, CUSTOMER_FILE, "company", f"/delete-customer/{index}", "/customer", "/customer")


@router.get("/customer-data")
def customer_data():
    return load_customers()
