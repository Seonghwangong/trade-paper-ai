from typing import List
from datetime import datetime
from io import BytesIO
import html as html_lib

from fastapi import APIRouter, Body, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

router = APIRouter()

from app.storage import atomic_write_json, data_path, locked_json_mutation, next_identifier
from app.validation import DataValidationError, require_consistent_reference, require_items, require_text
from app.referential_integrity import find_dependencies, render_delete_page
from app.account_proforma import ensure_legacy_proforma_ownership, public_proforma
from app.export import set_pdf_export_record
from app.pdf_fonts import TP_UNICODE, TP_UNICODE_BOLD, ensure_pdf_fonts, fit_pdf_text
from app.account_company import load_account_company
from app.auth import USERS_FILE
from app.routers.company import ACCOUNT_COMPANIES_FILE
from app.ui import badge, button, form_css, form_footer, metadata, navigation_footer, page_shell, search_toolbar, section_card, table
from app import quotation as quotation_module
from app import buyer as buyer_module
from app import product as product_module

PROFORMA_FILE = data_path("proformas.json")
COMPANY_FILE = data_path("company.json")
QUOTATION_FILE = data_path("quotations.json")


def _account_id(request):
    user = request.scope.get("trade_paper_user") or {}
    return str(user.get("account_id", "") or "").strip()

def load_company(account_id):
    return load_account_company(account_id, ACCOUNT_COMPANIES_FILE)


def load_proforma_records():
    return ensure_legacy_proforma_ownership(PROFORMA_FILE, USERS_FILE)


def owned_proforma_records(account_id):
    owner = str(account_id or "").strip()
    return [record for record in load_proforma_records()
            if isinstance(record, dict) and str(record.get("account_id", "") or "").strip() == owner]


def load_proformas(account_id):
    return [public_proforma(record) for record in owned_proforma_records(account_id)]


def _owned_proforma(pi_no, account_id):
    record = next((record for record in owned_proforma_records(account_id)
                   if str(record.get("pi_no", "") or "").strip() == str(pi_no or "").strip()), None)
    if record is None:
        raise HTTPException(status_code=404, detail="Proforma invoice not found")
    return record


def validate_proforma_sources(account_id, seller, buyer, buyer_address, buyer_email, items):
    company = load_company(account_id)
    require_consistent_reference("Seller", seller, company.get("name", ""), "Company Master")
    buyer_record = next((record for record in buyer_module.load_buyers(account_id)
                         if str(record.get("name", "") or "").strip() == str(buyer or "").strip()), None)
    if buyer_record is None:
        raise DataValidationError("Buyer", "The selected Buyer is no longer available.", "Select a Buyer from Buyer Master, then save again.")
    require_consistent_reference("Buyer address", buyer_address, buyer_record.get("address", ""), "Buyer Master")
    require_consistent_reference("Buyer email", buyer_email, buyer_record.get("email", ""), "Buyer Master")
    products = product_module.load_products(account_id)
    enriched = []
    for submitted in items:
        product = next((record for record in products
                        if str(record.get("name", "") or "").strip() == str(submitted.get("name", "") or "").strip()), None)
        if product is None:
            raise DataValidationError("Product", "The selected Product is no longer available.", "Select a Product from Product Master, then save again.")
        item = dict(submitted)
        require_consistent_reference("HS Code", item.get("hs_code", ""), product.get("hs_code", ""), "Product Master")
        item["hs_code"] = item.get("hs_code", "") or product.get("hs_code", "")
        item["unit_price"] = item.get("unit_price", "") or product.get("unit_price", "")
        enriched.append(item)
    return enriched


def load_quotations(account_id):
    return quotation_module.load_quotations(account_id)


def save_proformas(proformas):
    atomic_write_json(PROFORMA_FILE, proformas, list)


def build_items(item_name, hs_code, qty, unit_price, amount):
    items = []

    for i in range(len(item_name)):
        if not item_name[i].strip():
            continue

        items.append({
            "name": item_name[i],
            "hs_code": hs_code[i] if i < len(hs_code) else "",
            "qty": qty[i] if i < len(qty) else "",
            "unit_price": unit_price[i] if i < len(unit_price) else "",
            "amount": amount[i] if i < len(amount) else "",
        })

    return items


def calculate_total(items):
    total = 0.0
    for item in items:
        try:
            total += float(item.get("amount", 0) or 0)
        except:
            pass
    return total


def html_attr(value):
    return html_lib.escape(str(value or ""), quote=True)


def build_proforma_item_rows(items):
    if not items:
        items = [{}]

    rows = ""
    for item in items:
        rows += f"""
<div class="item-row">
<select onchange="selectProduct(this)">
    <option value="">Select Product Master</option>
</select>

<input type="text" name="item_name" value="{html_attr(item.get('name', ''))}" placeholder="Item Name">
<input type="text" name="hs_code" value="{html_attr(item.get('hs_code', ''))}" placeholder="HS Code">
<input type="text" name="qty" value="{html_attr(item.get('qty', ''))}" placeholder="Qty" oninput="calculateAmount(this)">
<input type="text" name="unit_price" value="{html_attr(item.get('unit_price', ''))}" placeholder="Unit Price" oninput="calculateAmount(this)">
<input type="text" name="amount" value="{html_attr(item.get('amount', ''))}" placeholder="Amount" readonly>
</div>
"""

    return rows


@router.get("/proforma-list")
def proforma_list(request: Request, search: str = ""):
    proformas = list(reversed(load_proformas(_account_id(request))))

    if search:
        search_lower = search.lower()
        proformas = [
            p for p in proformas
            if search_lower in str(p.get("pi_no", "")).lower()
            or search_lower in str(p.get("buyer", "")).lower()
            or search_lower in str(p.get("seller", "")).lower()
            or search_lower in str(p.get("items", "")).lower()
        ]

    rows = []
    for proforma in proformas:
        total = proforma.get("total_amount", calculate_total(proforma.get("items", [])))
        try:
            total = float(total or 0)
        except:
            total = 0
        pi_no = str(proforma.get("pi_no", "") or "")
        rows.append([
            badge(pi_no), html_attr(proforma.get("buyer", "")), html_attr(proforma.get("seller", "")),
            f'{html_attr(proforma.get("currency", "USD"))} {total:g}',
            button("Create Invoice", f"/invoice?pi_no={pi_no}", "secondary"),
            button("PDF", f"/proforma-pdf/{pi_no}", "secondary"),
            button("Edit", f"/edit-proforma/{pi_no}", "secondary"),
            button("Delete", f"/delete-proforma/{pi_no}", "danger"),
        ])
    content = search_toolbar(button("+ New Proforma Invoice", "/proforma-form"), button("Dashboard", "/", "secondary"), action="/proforma-list", value=search, placeholder="Search PI, buyer, seller or item", reset_url="/proforma-list", count_label=f"Total Proforma Invoices : {len(proformas)}")
    content += table(["PI No", "Buyer", "Seller", "Total", "Invoice", "PDF", "Edit", "Delete"], rows, empty_message="No proforma invoices have been registered yet.")
    return HTMLResponse(page_shell("Proforma Invoice List", content, subtitle="Manage all proforma invoice documents"))


@router.get("/proforma-data/{pi_no}")
def proforma_data(pi_no: str, request: Request):
    return public_proforma(_owned_proforma(pi_no, _account_id(request)))


@router.get("/proforma-form")
def proforma_form(request: Request, quotation_no: str = ""):
    source_quotation = {}
    if quotation_no:
        for quotation in load_quotations(request.scope["trade_paper_user"]["account_id"]):
            if quotation.get("quotation_no") == quotation_no:
                source_quotation = quotation
                break

    prefill_items = source_quotation.get("items", [])
    prefill_total = source_quotation.get("total_amount", calculate_total(prefill_items))
    try:
        prefill_total = float(prefill_total or 0)
    except:
        prefill_total = calculate_total(prefill_items)

    currency = source_quotation.get("currency", "USD")
    currency_options = "\n".join(
        f'<option{" selected" if option == currency else ""}>{option}</option>'
        for option in ["USD", "EUR", "KRW"]
    )

    html = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Proforma Invoice</title>
<style>__FORM_CSS__</style>
</head>

<body>
<div class="container">

__NAVIGATION__

<h1>Proforma Invoice</h1>
<p class="sub">Create proforma invoice from company, buyer and product master data</p>

<form action="/proforma" method="post">

<div class="card">
<h2>Seller Information</h2>

<select id="sellerCompanySelect" onchange="selectSellerCompany()">
    <option value="">Select Seller Company</option>
</select>

<input id="seller" type="text" name="seller" value="__SELLER__" placeholder="Seller Name">
</div>

<div class="card">
<h2>Buyer Information</h2>

<select id="buyerSelect">
    <option value="">Select Buyer Master</option>
</select>

<input id="buyer" type="text" name="buyer" value="__BUYER__" placeholder="Buyer Name">
<input id="buyer_address" type="text" name="buyer_address" value="__BUYER_ADDRESS__" placeholder="Buyer Address">
<input id="buyer_email" type="text" name="buyer_email" value="__BUYER_EMAIL__" placeholder="Buyer Email">
</div>

__METADATA_SECTION__

<div class="card">
<h2>Product Information</h2>

<div id="items_area">
__ITEM_ROWS__
</div>

<button class="add" type="button" onclick="addItem()">+ Add Item</button>
</div>

<input type="hidden" id="total_amount_input" name="total_amount" value="__TOTAL_AMOUNT__">
<div class="total" id="total_amount_display">Total: __CURRENCY__ __TOTAL_AMOUNT__</div>

__FORM_FOOTER__
</form>
</div>

<script>
let companies = [];
let buyers = [];
let products = [];

async function loadCompanies() {
    const response = await fetch("/company-data");
    const data = await response.json();
    companies = Array.isArray(data) ? data : [data];

    const select = document.getElementById("sellerCompanySelect");
    select.innerHTML = '<option value="">Select Seller Company</option>';

    companies.forEach((company, index) => {
        if (company && (company.name || company.address || company.email || company.phone)) {
            select.innerHTML += `<option value="${index}">${company.name || "Company"}</option>`;
        }
    });
}

function selectSellerCompany() {
    const index = document.getElementById("sellerCompanySelect").value;
    if (index === "") return;

    const company = companies[index] || {};
    document.getElementById("seller").value = company.name || "";
}

async function loadBuyers() {
    const response = await fetch("/buyer-data");
    buyers = await response.json();

    const select = document.getElementById("buyerSelect");
    select.innerHTML = '<option value="">Select Buyer Master</option>';

    buyers.forEach((buyer, index) => {
        select.innerHTML += `<option value="${index}">${buyer.name}</option>`;
    });
}

document.getElementById("buyerSelect").addEventListener("change", function () {
    if (this.value === "") return;

    const buyer = buyers[this.value] || {};
    document.getElementById("buyer").value = buyer.name || "";
    document.getElementById("buyer_address").value = buyer.address || "";
    document.getElementById("buyer_email").value = buyer.email || "";
});

async function loadProducts() {
    const response = await fetch("/product-data");
    products = await response.json();
    fillProductSelects();
}

function fillProductSelects() {
    document.querySelectorAll(".item-row select").forEach(select => {
        if (select.options.length > 1) return;

        products.forEach((product, index) => {
            select.innerHTML += `<option value="${index}">${product.name}</option>`;
        });
    });
}

function selectProduct(select) {
    const index = select.value;
    if (index === "") return;

    const row = select.closest(".item-row");
    const product = products[index] || {};

    row.querySelector('input[name="item_name"]').value = product.name || "";
    row.querySelector('input[name="hs_code"]').value = product.hs_code || "";
    row.querySelector('input[name="unit_price"]').value = product.unit_price || "";
    calculateAmount(row);
}

function calculateAmount(target) {
    const row = target.classList && target.classList.contains("item-row")
        ? target
        : target.closest(".item-row");

    const qty = parseFloat(row.querySelector('input[name="qty"]').value) || 0;
    const unitPrice = parseFloat(row.querySelector('input[name="unit_price"]').value) || 0;
    row.querySelector('input[name="amount"]').value = qty * unitPrice;
    calculateAllAmounts();
}

function calculateAllAmounts() {
    let total = 0;
    document.querySelectorAll('input[name="amount"]').forEach(input => {
        total += parseFloat(input.value) || 0;
    });

    const currency = document.getElementById("currency").value || "USD";
    document.getElementById("total_amount_input").value = total;
    document.getElementById("total_amount_display").innerHTML = "Total: " + currency + " " + total;
}

function addItem() {
    const area = document.getElementById("items_area");
    const firstRow = document.querySelector(".item-row");
    const newRow = firstRow.cloneNode(true);

    newRow.querySelectorAll("input").forEach(input => {
        input.value = "";
    });

    newRow.querySelector("select").selectedIndex = 0;
    area.appendChild(newRow);
    fillProductSelects();
}

function setDefaultPiDate() {
    const input = document.getElementById("pi_date");
    if (!input || input.value) return;

    const today = new Date();
    input.value = today.toISOString().slice(0, 10);
}

loadCompanies();
loadBuyers();
loadProducts();
setDefaultPiDate();
calculateAllAmounts();
</script>

</body>
</html>
"""
    html = html.replace("__SELLER__", html_attr(source_quotation.get("seller", "")))
    html = html.replace("__BUYER__", html_attr(source_quotation.get("buyer_name", source_quotation.get("buyer", ""))))
    html = html.replace("__BUYER_ADDRESS__", html_attr(source_quotation.get("buyer_address", "")))
    html = html.replace("__BUYER_EMAIL__", html_attr(source_quotation.get("buyer_email", "")))
    html = html.replace("__CURRENCY_OPTIONS__", currency_options)
    html = html.replace("__ITEM_ROWS__", build_proforma_item_rows(prefill_items))
    html = html.replace("__TOTAL_AMOUNT__", html_attr(f"{prefill_total:g}"))
    html = html.replace("__CURRENCY__", html_attr(currency))
    html = html.replace("__FORM_CSS__", form_css())
    html = html.replace("__NAVIGATION__", navigation_footer("/proforma-list", "Proforma List", state="New"))
    html = html.replace("__METADATA_SECTION__", section_card("Proforma Information", metadata([
        ("PI Date", '<input id="pi_date" type="date" name="pi_date">'),
        ("Currency", f'<select name="currency" id="currency" onchange="calculateAllAmounts()">{currency_options}</select>'),
    ])))
    html = html.replace("__FORM_FOOTER__", form_footer("/proforma-list", "Save Proforma Invoice"))
    return HTMLResponse(html)


@router.get("/edit-proforma/{pi_no}")
def edit_proforma(pi_no: str, request: Request):
    proforma = public_proforma(_owned_proforma(pi_no, _account_id(request)))
    if proforma:
            rows = ""
            for item in proforma.get("items", []):
                rows += f"""
<div class="item-row">
<select onchange="selectProduct(this)">
    <option value="">Select Product Master</option>
</select>
<input type="text" name="item_name" value="{item.get('name','')}" placeholder="Item Name">
<input type="text" name="hs_code" value="{item.get('hs_code','')}" placeholder="HS Code">
<input type="text" name="qty" value="{item.get('qty','')}" placeholder="Qty" oninput="calculateAmount(this)">
<input type="text" name="unit_price" value="{item.get('unit_price','')}" placeholder="Unit Price" oninput="calculateAmount(this)">
<input type="text" name="amount" value="{item.get('amount','')}" placeholder="Amount" readonly>
</div>
"""

            if not rows:
                rows = """
<div class="item-row">
<select onchange="selectProduct(this)">
    <option value="">Select Product Master</option>
</select>
<input type="text" name="item_name" placeholder="Item Name">
<input type="text" name="hs_code" placeholder="HS Code">
<input type="text" name="qty" placeholder="Qty" oninput="calculateAmount(this)">
<input type="text" name="unit_price" placeholder="Unit Price" oninput="calculateAmount(this)">
<input type="text" name="amount" placeholder="Amount" readonly>
</div>
"""

            html = f"""
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Edit Proforma Invoice</title>
<style>{form_css()}</style>
</head>

<body>
<div class="container">

{navigation_footer("/proforma-list", "Proforma List", state="Editing")}

<h1>Edit Proforma Invoice</h1>
<p class="sub">Update proforma invoice information</p>

<form action="/update-proforma/{pi_no}" method="post">

<div class="card">
<h2>Proforma Information</h2>
<input type="text" value="{proforma.get('pi_no','')}" placeholder="PI No" readonly>
<input type="date" name="pi_date" value="{proforma.get('pi_date','')}">
<select name="currency" id="currency" onchange="calculateAllAmounts()">
    <option {"selected" if proforma.get("currency", "USD") == "USD" else ""}>USD</option>
    <option {"selected" if proforma.get("currency") == "EUR" else ""}>EUR</option>
    <option {"selected" if proforma.get("currency") == "KRW" else ""}>KRW</option>
</select>
</div>

<div class="card">
<h2>Seller Information</h2>
<input type="text" name="seller" value="{proforma.get('seller','')}" placeholder="Seller Name">
</div>

<div class="card">
<h2>Buyer Information</h2>
<input type="text" name="buyer" value="{proforma.get('buyer','')}" placeholder="Buyer Name">
<input type="text" name="buyer_address" value="{proforma.get('buyer_address','')}" placeholder="Buyer Address">
<input type="text" name="buyer_email" value="{proforma.get('buyer_email','')}" placeholder="Buyer Email">
</div>

<div class="card">
<h2>Product Information</h2>
<div id="items_area">
{rows}
</div>
<button class="add" type="button" onclick="addItem()">+ Add Item</button>
</div>

<input type="hidden" id="total_amount_input" name="total_amount" value="{proforma.get('total_amount', 0)}">
<div class="total" id="total_amount_display">Total: {proforma.get('currency','USD')} {proforma.get('total_amount', 0)}</div>

{form_footer("/proforma-list", "Update Proforma Invoice")}
</form>
</div>

<script>
let products = [];

async function loadProducts() {{
    const response = await fetch("/product-data");
    products = await response.json();
    fillProductSelects();
}}

function fillProductSelects() {{
    document.querySelectorAll(".item-row select").forEach(select => {{
        if (select.options.length > 1) return;

        products.forEach((product, index) => {{
            select.innerHTML += `<option value="${{index}}">${{product.name}}</option>`;
        }});
    }});
}}

function selectProduct(select) {{
    const index = select.value;
    if (index === "") return;

    const row = select.closest(".item-row");
    const product = products[index] || {{}};

    row.querySelector('input[name="item_name"]').value = product.name || "";
    row.querySelector('input[name="hs_code"]').value = product.hs_code || "";
    row.querySelector('input[name="unit_price"]').value = product.unit_price || "";
    calculateAmount(row);
}}

function calculateAmount(target) {{
    const row = target.classList && target.classList.contains("item-row")
        ? target
        : target.closest(".item-row");

    const qty = parseFloat(row.querySelector('input[name="qty"]').value) || 0;
    const unitPrice = parseFloat(row.querySelector('input[name="unit_price"]').value) || 0;
    row.querySelector('input[name="amount"]').value = qty * unitPrice;
    calculateAllAmounts();
}}

function calculateAllAmounts() {{
    let total = 0;
    document.querySelectorAll('input[name="amount"]').forEach(input => {{
        total += parseFloat(input.value) || 0;
    }});

    const currency = document.getElementById("currency").value || "USD";
    document.getElementById("total_amount_input").value = total;
    document.getElementById("total_amount_display").innerHTML = "Total: " + currency + " " + total;
}}

function addItem() {{
    const area = document.getElementById("items_area");
    const firstRow = document.querySelector(".item-row");
    const newRow = firstRow.cloneNode(true);

    newRow.querySelectorAll("input").forEach(input => {{
        input.value = "";
    }});

    newRow.querySelector("select").selectedIndex = 0;
    area.appendChild(newRow);
    fillProductSelects();
}}

loadProducts();
calculateAllAmounts();
</script>

</body>
</html>
"""
            return HTMLResponse(html)


@router.post("/proforma")
def save_proforma(
    request: Request,
    seller: str = Form(""),
    buyer: str = Form(""),
    buyer_address: str = Form(""),
    buyer_email: str = Form(""),
    pi_date: str = Form(""),
    currency: str = Form("USD"),
    total_amount: str = Form("0"),
    item_name: List[str] = Form([]),
    hs_code: List[str] = Form([]),
    qty: List[str] = Form([]),
    unit_price: List[str] = Form([]),
    amount: List[str] = Form([]),
):
    seller = require_text("Seller", seller)
    buyer = require_text("Buyer", buyer)
    items = build_items(item_name, hs_code, qty, unit_price, amount)
    account_id = _account_id(request)
    items = validate_proforma_sources(account_id, seller, buyer, buyer_address, buyer_email, require_items(items))
    def add_proforma(proformas):
        proforma = {
        "pi_no": next_identifier(proformas, "pi_no", "PI"),
        "seller": seller,
        "buyer": buyer,
        "buyer_address": buyer_address,
        "buyer_email": buyer_email,
        "pi_date": pi_date,
        "currency": currency,
        "items": items,
        "total_amount": total_amount,
        "account_id": account_id,
        }
        proformas.append(proforma)
    locked_json_mutation(PROFORMA_FILE, [], add_proforma, list)

    return RedirectResponse(url="/proforma-list", status_code=303)


@router.post("/update-proforma/{pi_no}")
def update_proforma(
    pi_no: str,
    request: Request,
    seller: str = Form(""),
    buyer: str = Form(""),
    buyer_address: str = Form(""),
    buyer_email: str = Form(""),
    pi_date: str = Form(""),
    currency: str = Form("USD"),
    total_amount: str = Form("0"),
    item_name: List[str] = Form([]),
    hs_code: List[str] = Form([]),
    qty: List[str] = Form([]),
    unit_price: List[str] = Form([]),
    amount: List[str] = Form([]),
):
    seller = require_text("Seller", seller)
    buyer = require_text("Buyer", buyer)
    items = build_items(item_name, hs_code, qty, unit_price, amount)
    account_id = _account_id(request)
    _owned_proforma(pi_no, account_id)
    items = validate_proforma_sources(account_id, seller, buyer, buyer_address, buyer_email, require_items(items))
    def replace_proforma(proformas):
        for proforma in proformas:
            if proforma.get("pi_no") != pi_no or proforma.get("account_id") != account_id:
                continue
            proforma["seller"] = seller
            proforma["buyer"] = buyer
            proforma["buyer_address"] = buyer_address
            proforma["buyer_email"] = buyer_email
            proforma["pi_date"] = pi_date
            proforma["currency"] = currency
            proforma["items"] = items
            proforma["total_amount"] = total_amount
            return
        raise HTTPException(status_code=404, detail="Proforma invoice not found")
    locked_json_mutation(PROFORMA_FILE, [], replace_proforma, list)

    return RedirectResponse(url="/proforma-list", status_code=303)


@router.get("/delete-proforma/{pi_no}")
def delete_proforma(pi_no: str, request: Request):
    _owned_proforma(pi_no, _account_id(request))
    return render_delete_page("Proforma Invoice", pi_no, f"/delete-proforma/{pi_no}", "/proforma-list", find_dependencies("Proforma Invoice", pi_no, _account_id(request)))

@router.post("/delete-proforma/{pi_no}")
def confirm_delete_proforma(pi_no: str, request: Request):
    account_id = _account_id(request)
    _owned_proforma(pi_no, account_id)
    dependencies = find_dependencies("Proforma Invoice", pi_no, account_id)
    if dependencies:
        return render_delete_page("Proforma Invoice", pi_no, f"/delete-proforma/{pi_no}", "/proforma-list", dependencies, status_code=409)
    def remove(records):
        index = next((i for i, record in enumerate(records)
                      if record.get("pi_no") == pi_no and record.get("account_id") == account_id), None)
        if index is None:
            raise HTTPException(status_code=404, detail="Proforma invoice not found")
        records.pop(index)
    locked_json_mutation(PROFORMA_FILE, [], remove, list)
    return RedirectResponse("/proforma-list", status_code=303)


@router.post("/proforma/pdf")
def create_proforma_pdf(request: Request, payload: dict = Body(...), validate_sources: bool = True):
    account_id = _account_id(request)
    payload = public_proforma(payload)
    if validate_sources:
        payload["items"] = validate_proforma_sources(
            account_id, payload.get("seller", ""), payload.get("buyer", ""),
            payload.get("buyer_address", ""), payload.get("buyer_email", ""),
            require_items(payload.get("items", [])),
        )
    company = load_company(account_id)

    pi_no = payload.get("pi_no") or "-"
    today = datetime.now().strftime("%Y-%m-%d")
    pi_date = payload.get("pi_date") or today

    seller = payload.get("seller", "") or company.get("name", "")
    seller_address = company.get("address") or payload.get("seller_address", "")
    seller_email = company.get("email") or payload.get("seller_email", "")
    buyer = payload.get("buyer", "")
    buyer_address = payload.get("buyer_address", "")
    buyer_email = payload.get("buyer_email", "")

    items = payload.get("items", [])
    currency = payload.get("currency", "USD")
    total_amount = payload.get("total_amount", calculate_total(items))

    try:
        total_amount = float(total_amount or 0)
    except:
        total_amount = calculate_total(items)

    buffer = BytesIO()
    ensure_pdf_fonts()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4

    pdf.setTitle(f"Proforma Invoice {pi_no}")

    table_x = 45
    table_w = 505
    table_right = table_x + table_w
    table_header_h = 28
    row_h = 26
    row_min_bottom = 145
    summary_w = 225
    summary_h = 65
    summary_gap = 20

    def fit_text(text, max_width, font_name=TP_UNICODE, font_size=8):
        return fit_pdf_text(pdf, text, max_width, font_name, font_size)

    def draw_document_header():
        pdf.setFillColor(colors.HexColor("#111827"))
        pdf.rect(0, height - 90, width, 90, fill=1, stroke=0)

        pdf.setFillColor(colors.white)
        pdf.setFont(TP_UNICODE_BOLD, 24)
        pdf.drawString(45, height - 55, "PROFORMA INVOICE")

        pdf.setFont(TP_UNICODE, 9)
        pdf.drawRightString(width - 45, height - 38, "Trade Paper AI")
        pdf.drawRightString(width - 45, height - 55, "Automated Trade Document")

        pdf.setFillColor(colors.black)
        pdf.setFont(TP_UNICODE_BOLD, 11)
        pdf.drawString(45, height - 125, fit_text(f"PI No: {pi_no}", 505, TP_UNICODE_BOLD, 11))
        pdf.drawString(45, height - 145, fit_text(f"PI Date: {pi_date}", 505, TP_UNICODE_BOLD, 11))
        pdf.drawString(45, height - 163, f"Date: {today}")

        pdf.setStrokeColor(colors.HexColor("#D1D5DB"))
        pdf.setFillColor(colors.HexColor("#F9FAFB"))
        pdf.roundRect(45, height - 260, 240, 80, 8, fill=1)
        pdf.roundRect(310, height - 260, 240, 80, 8, fill=1)

        pdf.setFillColor(colors.HexColor("#111827"))
        pdf.setFont(TP_UNICODE_BOLD, 11)
        pdf.drawString(60, height - 197, "SELLER")
        pdf.drawString(325, height - 197, "BUYER")

        pdf.setFont(TP_UNICODE, 9)
        pdf.drawString(60, height - 220, fit_text(seller, 210, font_size=9))
        pdf.drawString(60, height - 235, fit_text(seller_address, 210, font_size=9))
        pdf.drawString(60, height - 250, fit_text(seller_email, 210, font_size=9))

        pdf.drawString(325, height - 220, fit_text(buyer, 210, font_size=9))
        pdf.drawString(325, height - 235, fit_text(buyer_address, 210, font_size=9))
        pdf.drawString(325, height - 250, fit_text(buyer_email, 210, font_size=9))

    def draw_table_header():
        header_y = height - 315

        pdf.setFillColor(colors.HexColor("#E5E7EB"))
        pdf.rect(table_x, header_y, table_w, table_header_h, fill=1, stroke=0)

        pdf.setFillColor(colors.black)
        pdf.setFont(TP_UNICODE_BOLD, 8)
        pdf.drawString(52, header_y + 10, "No")
        pdf.drawString(80, header_y + 10, "Item")
        pdf.drawString(205, header_y + 10, "HS Code")
        pdf.drawRightString(330, header_y + 10, "Qty")
        pdf.drawRightString(430, header_y + 10, "Unit Price")
        pdf.drawRightString(540, header_y + 10, "Amount")

        pdf.setFont(TP_UNICODE, 8)
        pdf.setStrokeColor(colors.HexColor("#D1D5DB"))
        return header_y - table_header_h

    def start_table_page():
        draw_document_header()
        return draw_table_header()

    def draw_signature_footer():
        pdf.setFillColor(colors.black)
        pdf.setFont(TP_UNICODE, 10)
        pdf.drawString(45, 115, "Authorized Signature:")
        pdf.line(170, 115, 330, 115)

        pdf.setFillColor(colors.HexColor("#6B7280"))
        pdf.setFont(TP_UNICODE, 8)
        pdf.drawString(45, 60, "This document was generated by Trade Paper AI.")
        pdf.drawString(45, 45, "For trade documentation automation.")

    y = start_table_page()
    item_count = len(items)

    for index, item in enumerate(items, start=1):
        is_last_row = index == item_count
        required_bottom = row_min_bottom + summary_h + summary_gap + row_h if is_last_row else row_min_bottom

        if y < required_bottom:
            pdf.showPage()
            y = start_table_page()

        pdf.rect(table_x, y, table_w, row_h, fill=0)
        pdf.drawString(52, y + 9, str(index))
        pdf.drawString(80, y + 9, fit_text(item.get("name", ""), 110))
        pdf.drawString(205, y + 9, fit_text(item.get("hs_code", ""), 75))
        pdf.drawRightString(330, y + 9, fit_text(item.get("qty", ""), 55))
        pdf.drawRightString(430, y + 9, fit_text(item.get("unit_price", ""), 85))
        pdf.drawRightString(540, y + 9, fit_text(item.get("amount", ""), 85))
        y -= row_h

    summary_x = table_right - summary_w
    summary_top = y - summary_gap
    summary_bottom = summary_top - summary_h

    if summary_bottom < row_min_bottom:
        pdf.showPage()
        y = start_table_page()
        summary_top = y - summary_gap
        summary_bottom = summary_top - summary_h

    pdf.setFillColor(colors.HexColor("#111827"))
    pdf.roundRect(summary_x, summary_bottom, summary_w, summary_h, 8, fill=1, stroke=0)

    pdf.setFillColor(colors.white)
    pdf.setFont(TP_UNICODE_BOLD, 10)
    text_x = summary_x + 15
    text_y = summary_top - 23
    line_gap = 18
    summary_text_w = summary_w - 30
    pdf.drawString(text_x, text_y, fit_text(f"Total Amount: {currency} {total_amount:,.2f}", summary_text_w, TP_UNICODE_BOLD, 10))
    pdf.drawString(text_x, text_y - line_gap, fit_text(f"Currency: {currency}", summary_text_w, TP_UNICODE_BOLD, 10))

    draw_signature_footer()

    pdf.showPage()
    pdf.save()
    buffer.seek(0)

    return Response(
        content=buffer.getvalue(),
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{pi_no}.pdf"'
        },
    )


@router.get("/proforma-pdf/{pi_no}")
def proforma_pdf(pi_no: str, request: Request):
    record = public_proforma(_owned_proforma(pi_no, _account_id(request)))
    set_pdf_export_record(request, record)
    return create_proforma_pdf(request, record, validate_sources=False)
