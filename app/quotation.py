from typing import List
from pathlib import Path
from datetime import datetime
from io import BytesIO
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
import html as html_lib
import json

from fastapi import APIRouter, Form, Body, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, Response

router = APIRouter()

from app.storage import atomic_write_json, data_path, load_json_strict, locked_json_mutation, next_identifier
from app.validation import require_items, require_text
from app.referential_integrity import confirmed_identifier_delete, identifier_delete_confirmation
from app.ui import badge, button, form_css, form_footer, metadata, navigation_footer, page_shell, search_toolbar, section_card, table

QUOTATION_FILE = data_path("quotations.json")
COMPANY_FILE = data_path("company.json")

def load_company():
    return load_json_strict(COMPANY_FILE, {}, dict)

def load_quotations():
    return load_json_strict(QUOTATION_FILE, [], list)


def save_quotations(quotations):
    atomic_write_json(QUOTATION_FILE, quotations, list)


@router.get("/quotation-list")
def quotation_list(search: str = ""):
    quotations = load_quotations()
    quotations = list(reversed(quotations))

    if search:
        search_lower = search.lower()
        quotations = [
            q for q in quotations
            if search_lower in str(q.get("quotation_no", "")).lower()
            or search_lower in str(q.get("buyer_name", "")).lower()
            or search_lower in str(q.get("items", "")).lower()
        ]

    rows = []
    for quotation in quotations:
        total = 0
        for item in quotation.get("items", []):
            try:
                total += float(item.get("amount", 0) or 0)
            except:
                pass
        quotation_no = str(quotation.get("quotation_no", "") or "")
        rows.append([
            badge(quotation_no),
            html_lib.escape(str(quotation.get("buyer_name", "") or "")),
            html_lib.escape(str(quotation.get("seller", "") or "")),
            f'{html_lib.escape(str(quotation.get("currency", "USD") or "USD"))} {total:g}',
            button("Create PI", f"/proforma-form?quotation_no={quotation_no}", "secondary"),
            button("PDF", f"/quotation-pdf/{quotation_no}", "secondary"),
            button("Edit", f"/edit-quotation/{quotation_no}", "secondary"),
            button("Delete", f"/delete-quotation/{quotation_no}", "danger"),
        ])

    content = search_toolbar(
        button("+ New Quotation", "/quotation-form"),
        button("← Dashboard", "/", "secondary"),
        action="/quotation-list", value=search, placeholder="Search quotation, buyer or item",
        reset_url="/quotation-list", count_label=f"Total Quotations : {len(quotations)}",
    )
    content += table(
        ["Quotation No", "Buyer", "Seller", "Total", "Proforma", "PDF", "Edit", "Delete"],
        rows,
        empty_message="No quotations have been registered yet.",
    )
    return HTMLResponse(page_shell("Quotation List", content, subtitle="Manage all quotation documents"))

@router.get("/quotation-form")
def quotation_form():

    html = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Quotation</title>
<style>__FORM_CSS__</style>
</head>

<body>
<div class="container">

__NAVIGATION__

<h1>Quotation</h1>
<p class="sub">Create quotation from buyer and product master data</p>

<form action="/quotation" method="post">

__SELLER_SECTION__

<div class="card">
<h2>Buyer Information</h2>

<select id="buyer">
    <option value="">Select Buyer Master</option>
</select>

<input id="buyer_name" type="text" name="buyer_name" placeholder="Buyer Name">
<input id="buyer_address" type="text" name="buyer_address" placeholder="Address">
<input id="buyer_email" type="text" name="buyer_email" placeholder="Email">
</div>

<div class="card">
<h2>Quotation Information</h2>

<input id="valid_until" type="date" name="valid_until">

<select name="currency">
    <option>USD</option>
    <option>EUR</option>
    <option>KRW</option>
</select>
</div>

<div class="card">
<h2>Product Information</h2>

<div id="items_area">

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

</div>

<button class="add" type="button" onclick="addItem()">+ Add Item</button>
</div>

__FORM_FOOTER__
</form>

</div>


<script>
let companies = [];
let buyers = [];

async function loadCompanies() {
    const response = await fetch("/company-data");
    const data = await response.json();
    companies = Array.isArray(data) ? data : [data];

    const select = document.getElementById("sellerCompanySelect");
    select.innerHTML = '<option value="">Select Seller Company</option>';

    companies.forEach((company, index) => {
        if (company && (company.name || company.address || company.email || company.phone)) {
            const name = company.name || "Company";
            select.innerHTML += `<option value="${index}">${name}</option>`;
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

    const select = document.getElementById("buyer");

    buyers.forEach((buyer, index) => {
        select.innerHTML += `<option value="${index}">${buyer.name}</option>`;
    });
}

document.getElementById("buyer").addEventListener("change", function () {
    if (this.value === "") return;

    const buyer = buyers[this.value];

    document.getElementById("buyer_name").value = buyer.name || "";
    document.getElementById("buyer_address").value = buyer.address || "";
    document.getElementById("buyer_email").value = buyer.email || "";
});

let products = [];

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

    const product = products[index];

    row.querySelector('input[name="item_name"]').value = product.name || "";
    row.querySelector('input[name="hs_code"]').value = product.hs_code || "";

    if (row.querySelector('input[name="unit_price"]')) {
        row.querySelector('input[name="unit_price"]').value = product.unit_price || "";
    }

    calculateAmount(row);
}

loadBuyers();
function setDefaultValidUntil() {
    const input = document.getElementById("valid_until");
    const date = new Date();
    date.setDate(date.getDate() + 30);
    input.value = date.toISOString().slice(0, 10);
}

setDefaultValidUntil();
function calculateAmount(target) {

    const row = target.classList && target.classList.contains("item-row")
        ? target
        : target.closest(".item-row");

    const qty = parseFloat(row.querySelector('input[name="qty"]').value) || 0;

    const unitPrice = parseFloat(row.querySelector('input[name="unit_price"]').value) || 0;

    row.querySelector('input[name="amount"]').value = qty * unitPrice;
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
loadCompanies();
loadProducts();
</script>

</body>
</html>
"""

    html = html.replace("__FORM_CSS__", form_css())
    html = html.replace("__NAVIGATION__", navigation_footer("/quotation-list", "← Quotation List", state="New"))
    html = html.replace("__SELLER_SECTION__", section_card("Seller Information", metadata([
        ("Seller Company", '<select id="sellerCompanySelect" onchange="selectSellerCompany()"><option value="">Select Seller Company</option></select>'),
        ("Seller Name", '<input id="seller" type="text" name="seller" placeholder="Seller Name">'),
    ])))
    html = html.replace("__FORM_FOOTER__", form_footer("/quotation-list", "Save Quotation"))
    return HTMLResponse(html)

@router.get("/edit-quotation/{quotation_no}")
def edit_quotation(quotation_no: str):
    quotations = load_quotations()

    for quotation in quotations:
        if quotation.get("quotation_no") == quotation_no:
            items = quotation.get("items", [])

            html = f"""
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Edit Quotation</title>
<style>{form_css()}</style>
</head>

<body>
<div class="container">

{navigation_footer("/quotation-list", "← Quotation List", state="Editing")}

<h1>Edit Quotation</h1>
<p class="sub">Update quotation information</p>

<form action="/update-quotation/{quotation_no}" method="post">

<div class="card">
<h2>Buyer Information</h2>

<input type="text" name="buyer_name" value="{quotation.get('buyer_name','')}" placeholder="Buyer Name">
<input type="text" name="buyer_address" value="{quotation.get('buyer_address','')}" placeholder="Buyer Address">
<input type="text" name="buyer_email" value="{quotation.get('buyer_email','')}" placeholder="Buyer Email">
</div>

<div class="card">
<h2>Quotation Information</h2>

<input type="text" name="seller" value="{quotation.get('seller','')}" placeholder="Seller Name">
<input type="text" value="{quotation.get('valid_until','')}" placeholder="Valid Until" readonly>
<input type="text" name="currency" value="{quotation.get('currency','USD')}" placeholder="Currency">
</div>

<div class="card">
<h2>Product Information</h2>

<div id="items_area">
"""

            for item in items:
                html += f"""
<div class="item-row">

<input type="text" name="item_name" value="{item.get('name','')}" placeholder="Item Name">
<input type="text" name="hs_code" value="{item.get('hs_code','')}" placeholder="HS Code">
<input type="text" name="qty" value="{item.get('qty','')}" placeholder="Qty" oninput="calculateAmount(this)">
<input type="text" name="unit_price" value="{item.get('unit_price','')}" placeholder="Unit Price" oninput="calculateAmount(this)">
<input type="text" name="amount" value="{item.get('amount','')}" placeholder="Amount" readonly>

</div>
"""

            html += """
</div>
</div>

{form_footer("/quotation-list", "Update Quotation")}

</form>

<script>
function calculateAmount(target) {
    const row = target.classList && target.classList.contains("item-row")
        ? target
        : target.closest(".item-row");

    const qty = parseFloat(row.querySelector('input[name="qty"]').value) || 0;
    const unitPrice = parseFloat(row.querySelector('input[name="unit_price"]').value) || 0;

    row.querySelector('input[name="amount"]').value = qty * unitPrice;
}
</script>

</div>
</body>
</html>
"""

            return HTMLResponse(html)

    return HTMLResponse("Quotation Not Found")  

@router.post("/quotation")
def save_quotation(
    buyer_name: str = Form(""),
    buyer_address: str = Form(""),
    buyer_email: str = Form(""),
    seller: str = Form(""),
    valid_until: str = Form(""),
    currency: str = Form("USD"),
    item_name: List[str] = Form([]),
    hs_code: List[str] = Form([]),
    qty: List[str] = Form([]),
    unit_price: List[str] = Form([]),
    amount: List[str] = Form([]),
):
    buyer_name = require_text("Buyer name", buyer_name)
    seller = require_text("Seller", seller)
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

    require_items(items)
    def add_quotation(quotations):
        quotation = {
        "quotation_no": next_identifier(quotations, "quotation_no", "QT"),
        "buyer_name": buyer_name,
        "buyer_address": buyer_address,
        "buyer_email": buyer_email,
        "seller": seller,
        "valid_until": valid_until,
        "currency": currency,
        "items": items,
        }
        quotations.append(quotation)
    locked_json_mutation(QUOTATION_FILE, [], add_quotation, list)

    return RedirectResponse(url="/quotation-list", status_code=303)

@router.post("/update-quotation/{quotation_no}")
def update_quotation(
    quotation_no: str,
    buyer_name: str = Form(""),
    buyer_address: str = Form(""),
    buyer_email: str = Form(""),
    seller: str = Form(""),
    currency: str = Form("USD"),
    item_name: List[str] = Form([]),
    hs_code: List[str] = Form([]),
    qty: List[str] = Form([]),
    unit_price: List[str] = Form([]),
    amount: List[str] = Form([]),
):
    buyer_name = require_text("Buyer name", buyer_name)
    seller = require_text("Seller", seller)
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

    require_items(items)
    def replace_quotation(quotations):
        for quotation in quotations:
            if quotation.get("quotation_no") != quotation_no:
                continue
            quotation["buyer_name"] = buyer_name
            quotation["buyer_address"] = buyer_address
            quotation["buyer_email"] = buyer_email
            quotation["seller"] = seller
            quotation["currency"] = currency
            quotation["items"] = items
            return
        raise HTTPException(status_code=404, detail="Quotation not found")
    locked_json_mutation(QUOTATION_FILE, [], replace_quotation, list)

    return RedirectResponse(
        url="/quotation-list",
        status_code=303
    ) 

@router.get("/delete-quotation/{quotation_no}")
def delete_quotation(quotation_no: str):
    return identifier_delete_confirmation("Quotation", "Quotation", quotation_no, QUOTATION_FILE, "quotation_no", f"/delete-quotation/{quotation_no}", "/quotation-list")

@router.post("/delete-quotation/{quotation_no}")
def confirm_delete_quotation(quotation_no: str):
    return confirmed_identifier_delete("Quotation", "Quotation", quotation_no, QUOTATION_FILE, "quotation_no", f"/delete-quotation/{quotation_no}", "/quotation-list", "/quotation-list")

@router.post("/quotation/pdf")
def create_quotation_pdf(payload: dict = Body(...)):
    company = load_company()

    quotation_no = payload.get("quotation_no", "QT-001")
    valid_until = payload.get("valid_until", "")
    today = datetime.now().strftime("%Y-%m-%d")

    buyer_name = payload.get("buyer_name", "")
    buyer_address = payload.get("buyer_address", "")
    buyer_email = payload.get("buyer_email", "")
    seller = company.get("name") or payload.get("seller", "")
    seller_address = company.get("address") or payload.get("seller_address", "")
    seller_email = company.get("email") or payload.get("seller_email", "")

    items = payload.get("items", [])
    currency = payload.get("currency", "USD")
    total_amount = 0

    buffer = BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4

    pdf.setTitle(f"Quotation {quotation_no}")

    table_x = 45
    table_w = 505
    table_right = table_x + table_w
    table_header_h = 28
    row_h = 26
    row_min_bottom = 145
    summary_w = 225
    summary_h = 65
    summary_gap = 20

    def draw_document_header():
        pdf.setFillColor(colors.HexColor("#111827"))
        pdf.rect(0, height - 90, width, 90, fill=1, stroke=0)

        pdf.setFillColor(colors.white)
        pdf.setFont("Helvetica-Bold", 24)
        pdf.drawString(45, height - 55, "QUOTATION")

        pdf.setFont("Helvetica", 9)
        pdf.drawRightString(width - 45, height - 38, "Trade Paper AI")
        pdf.drawRightString(width - 45, height - 55, "Automated Trade Document")

        pdf.setFillColor(colors.black)
        pdf.setFont("Helvetica-Bold", 11)
        pdf.drawString(45, height - 125, f"Quotation No: {quotation_no}")
        pdf.drawString(45, height - 143, f"Valid Until: {valid_until}")
        pdf.drawString(45, height - 161, f"Date: {today}")

        pdf.setStrokeColor(colors.HexColor("#D1D5DB"))
        pdf.setFillColor(colors.HexColor("#F9FAFB"))
        pdf.roundRect(45, height - 260, 240, 80, 8, fill=1)
        pdf.roundRect(310, height - 260, 240, 80, 8, fill=1)

        pdf.setFillColor(colors.HexColor("#111827"))
        pdf.setFont("Helvetica-Bold", 11)
        pdf.drawString(60, height - 197, "SELLER")
        pdf.drawString(325, height - 197, "BUYER")

        pdf.setFont("Helvetica", 9)
        pdf.drawString(60, height - 220, seller)
        pdf.drawString(60, height - 235, seller_address)
        pdf.drawString(60, height - 250, seller_email)

        pdf.drawString(325, height - 220, buyer_name)
        pdf.drawString(325, height - 235, buyer_address)
        pdf.drawString(325, height - 250, buyer_email)

    def draw_table_header():
        header_y = height - 315

        pdf.setFillColor(colors.HexColor("#E5E7EB"))
        pdf.rect(table_x, header_y, table_w, table_header_h, fill=1, stroke=0)

        pdf.setFillColor(colors.black)
        pdf.setFont("Helvetica-Bold", 8)
        pdf.drawString(52, header_y + 10, "No")
        pdf.drawString(80, header_y + 10, "Item")
        pdf.drawString(205, header_y + 10, "HS Code")
        pdf.drawRightString(330, header_y + 10, "Qty")
        pdf.drawRightString(430, header_y + 10, "Unit Price")
        pdf.drawRightString(540, header_y + 10, "Amount")

        pdf.setFont("Helvetica", 8)
        pdf.setStrokeColor(colors.HexColor("#D1D5DB"))
        return header_y - table_header_h

    def start_table_page():
        draw_document_header()
        return draw_table_header()

    def draw_signature_footer():
        pdf.setFillColor(colors.black)
        pdf.setFont("Helvetica", 10)
        pdf.drawString(45, 115, "Authorized Signature:")
        pdf.line(170, 115, 330, 115)

        pdf.setFillColor(colors.HexColor("#6B7280"))
        pdf.setFont("Helvetica", 8)
        pdf.drawString(45, 60, "This document was generated by Trade Paper AI.")
        pdf.drawString(45, 45, "For trade documentation automation.")

    y = start_table_page()

    item_count = len(items)

    for index, item in enumerate(items, start=1):
        try:
            total_amount += float(item.get("amount", 0) or 0)
        except:
            pass

        is_last_row = index == item_count
        if is_last_row:
            required_bottom = row_min_bottom + summary_h + summary_gap + row_h
        else:
            required_bottom = row_min_bottom

        if y < required_bottom:
            pdf.showPage()
            y = start_table_page()

        pdf.rect(table_x, y, table_w, row_h, fill=0)
        pdf.drawString(52, y + 9, str(index))
        pdf.drawString(80, y + 9, str(item.get("name", ""))[:25])
        pdf.drawString(205, y + 9, str(item.get("hs_code", "")))
        pdf.drawRightString(330, y + 9, str(item.get("qty", "")))
        pdf.drawRightString(430, y + 9, str(item.get("unit_price", "")))
        pdf.drawRightString(540, y + 9, str(item.get("amount", "")))
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
    pdf.setFont("Helvetica-Bold", 10)
    text_x = summary_x + 15
    text_y = summary_top - 23
    line_gap = 18
    pdf.drawString(text_x, text_y, f"Total Amount: {currency} {total_amount:,.2f}")
    pdf.drawString(text_x, text_y - line_gap, f"Currency: {currency}")
    pdf.drawString(text_x, text_y - line_gap * 2, f"Valid Until: {valid_until}")

    draw_signature_footer()

    pdf.showPage()
    pdf.save()
    buffer.seek(0)

    return Response(
        content=buffer.getvalue(),
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{quotation_no}.pdf"'
        },
    )

@router.get("/quotation-pdf/{quotation_no}")
def quotation_pdf(quotation_no: str):
    quotations = load_quotations()

    for quotation in quotations:
        if quotation.get("quotation_no") == quotation_no:
            return create_quotation_pdf(quotation)

    return {"error": "Quotation not found"}     
