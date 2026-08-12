from io import BytesIO
from datetime import datetime
from fastapi import APIRouter, Body, Response, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.lib import colors
from app.pdf_fonts import TP_UNICODE, TP_UNICODE_BOLD, ensure_pdf_fonts, fit_pdf_text
import html as html_lib
import os
from typing import Annotated, Optional

from app.storage import data_path, load_json_strict, locked_json_mutation, next_identifier
from app.validation import DataValidationError, require_existing_reference, require_items, require_text
from app.referential_integrity import find_dependencies, render_delete_page
from app.shipment import link_direct_document
from app.ui import badge, button, form_css, form_footer, metadata, navigation_footer, page_shell, search_toolbar, section_card, table
from app.account_invoice import ensure_legacy_invoice_ownership, public_invoice
from app.auth import USERS_FILE
from app.account_company import load_account_company
from app.routers.company import ACCOUNT_COMPANIES_FILE
from app import proforma as proforma_module
from app.export import set_pdf_export_record

COMPANY_FILE = data_path("company.json")
INVOICE_FILE = data_path("invoices.json")
PROFORMA_FILE = data_path("proformas.json")

router = APIRouter()

def _account_id(request):
    user = request.scope.get("trade_paper_user") or {}
    return str(user.get("account_id", "") or "").strip()


def load_invoice_records():
    return ensure_legacy_invoice_ownership(INVOICE_FILE, USERS_FILE)


def owned_invoice_records(account_id):
    owner = str(account_id or "").strip()
    return [
        record
        for record in load_invoice_records()
        if isinstance(record, dict) and str(record.get("account_id", "") or "").strip() == owner
    ]


def load_invoices(account_id):
    return [public_invoice(record) for record in owned_invoice_records(account_id)]


def _owned_invoice(invoice_no, account_id):
    target = str(invoice_no or "").strip()
    owner = str(account_id or "").strip()
    record = next(
        (
            invoice
            for invoice in load_invoice_records()
            if isinstance(invoice, dict)
            and str(invoice.get("invoice_no", "") or "").strip() == target
            and str(invoice.get("account_id", "") or "").strip() == owner
        ),
        None,
    )
    if record is None:
        raise HTTPException(status_code=404, detail="Invoice not found")
    return record


def load_proformas(account_id):
    return proforma_module.load_proformas(account_id)


@router.post("/invoice")
def create_invoice(request: Request, payload: dict = Body(...)):
    record = dict(payload)
    record.pop("account_id", None)
    record["account_id"] = _account_id(request)
    shipment_no = str(record.pop("shipment_no", "") or "").strip()
    record["seller"] = require_text("Seller", record.get("seller", ""))
    record["buyer"] = require_text("Buyer", record.get("buyer", ""))
    require_items(record.get("items", []))
    require_existing_reference("Proforma Invoice", record.get("pi_no", ""), load_proformas(_account_id(request)), "pi_no")
    def add_invoice(invoices):
        record["invoice_no"] = next_identifier(invoices, "invoice_no", "INV")
        invoices.append(record)
    locked_json_mutation(INVOICE_FILE, [], add_invoice, list)
    link_direct_document(shipment_no, "invoice_no", record["invoice_no"])
    return public_invoice(record)

@router.get("/invoice-data")
def invoice_data(request: Request):
    return load_invoices(_account_id(request))

@router.get("/invoice-pdf/{invoice_no}")
def invoice_pdf(invoice_no: str, request: Request):
    account_id = _account_id(request)
    company = load_account_company(account_id, ACCOUNT_COMPANIES_FILE)
    record = public_invoice(_owned_invoice(invoice_no, account_id))
    set_pdf_export_record(request, record)
    return create_invoice_pdf(
        record,
        company,
    )

@router.post("/invoice/pdf")
def preview_invoice_pdf(request: Request, payload: dict = Body(...)):
    company = load_account_company(_account_id(request), ACCOUNT_COMPANIES_FILE)
    return create_invoice_pdf(payload, company)


preview_invoice_pdf.__name__ = "create_invoice_pdf"


def create_invoice_pdf(payload, company=None):
    payload = public_invoice(payload)
    company = company if isinstance(company, dict) else load_json_strict(COMPANY_FILE, {}, dict)

    invoice_no = payload.get("invoice_no", "INV-001")
    today = datetime.now().strftime("%Y-%m-%d")

    buyer = payload.get("buyer", "Unknown Buyer")
    buyer_address = payload.get("buyer_address", "Dubai, UAE")
    buyer_email = payload.get("buyer_email", "sales@abctrading.com")

    seller = payload.get("seller") or company.get("name") or "Unknown Seller"
    seller_address = payload.get("seller_address") or company.get("address") or "Seoul, Korea"
    seller_email = payload.get("seller_email") or company.get("email") or "contact@tradepaper.ai"
    seller_phone = payload.get("seller_phone") or company.get("phone") or ""

    items = require_items(payload.get("items", []))
    if any(not isinstance(item, dict) for item in items):
        raise DataValidationError(
            "Items", "Each cargo item must contain structured item details.",
            "Add the cargo item again, then retry the PDF.",
        )

    def pdf_number(field, value):
        try:
            number = float(value or 0)
        except (TypeError, ValueError) as exc:
            raise DataValidationError(
                field, f"{field} must be a number.",
                f"Enter a numeric {field.lower()}, then retry the PDF.",
            ) from exc
        return int(number) if number.is_integer() else number

    total = sum(
        pdf_number("Quantity", item.get("quantity", 0))
        * pdf_number("Unit price", item.get("unit_price", 0))
        for item in items
    )

    buffer = BytesIO()
    ensure_pdf_fonts()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4

    pdf.setTitle(f"Invoice {invoice_no}")

    table_x = 45
    table_w = 505
    table_right = table_x + table_w
    table_header_h = 28
    row_h = 30
    row_min_bottom = 145
    total_w = 225
    total_h = 45
    total_gap = 20

    def draw_document_header():
        pdf.setFillColor(colors.HexColor("#111827"))
        pdf.rect(0, height - 90, width, 90, fill=1, stroke=0)

        pdf.setFillColor(colors.white)
        pdf.setFont(TP_UNICODE_BOLD, 24)
        pdf.drawString(45, height - 55, "COMMERCIAL INVOICE")

        pdf.setFont(TP_UNICODE, 9)
        pdf.drawRightString(width - 45, height - 38, "Trade Paper AI")
        pdf.drawRightString(width - 45, height - 55, "Automated Trade Document")

        pdf.setFillColor(colors.black)
        pdf.setFont(TP_UNICODE_BOLD, 11)
        pdf.drawString(45, height - 125, f"Invoice No: {invoice_no}")
        pdf.drawString(45, height - 145, f"Date: {today}")

        pdf.setStrokeColor(colors.HexColor("#D1D5DB"))
        pdf.setFillColor(colors.HexColor("#F9FAFB"))
        pdf.roundRect(45, height - 260, 240, 80, 8, fill=1)
        pdf.roundRect(310, height - 260, 240, 80, 8, fill=1)

        pdf.setFillColor(colors.HexColor("#111827"))
        pdf.setFont(TP_UNICODE_BOLD, 11)
        pdf.drawString(60, height - 197, "SELLER")
        pdf.drawString(325, height - 197, "BUYER")

        pdf.setFont(TP_UNICODE, 9)
        pdf.drawString(60, height - 220, fit_pdf_text(pdf, seller, 210, TP_UNICODE, 9))
        pdf.drawString(60, height - 235, fit_pdf_text(pdf, seller_address, 210, TP_UNICODE, 9))
        seller_contact = " · ".join(value for value in (seller_email, seller_phone) if value)
        pdf.drawString(60, height - 250, fit_pdf_text(pdf, seller_contact, 210, TP_UNICODE, 9))

        pdf.drawString(325, height - 220, fit_pdf_text(pdf, buyer, 210, TP_UNICODE, 9))
        pdf.drawString(325, height - 235, fit_pdf_text(pdf, buyer_address, 210, TP_UNICODE, 9))
        pdf.drawString(325, height - 250, fit_pdf_text(pdf, buyer_email, 210, TP_UNICODE, 9))

    def draw_table_header():
        header_y = height - 315

        pdf.setFillColor(colors.HexColor("#E5E7EB"))
        pdf.rect(table_x, header_y, table_w, table_header_h, fill=1, stroke=0)

        pdf.setFillColor(colors.black)
        pdf.setFont(TP_UNICODE_BOLD, 10)
        pdf.drawString(60, header_y + 10, "Item")
        pdf.drawString(245, header_y + 10, "HS Code")
        pdf.drawRightString(350, header_y + 10, "Qty")
        pdf.drawRightString(445, header_y + 10, "Unit Price")
        pdf.drawRightString(540, header_y + 10, "Total")

        pdf.setStrokeColor(colors.HexColor("#D1D5DB"))
        pdf.setFont(TP_UNICODE, 10)
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
        quantity = pdf_number("Quantity", item.get("quantity", 0))
        unit_price = pdf_number("Unit price", item.get("unit_price", 0))
        line_total = quantity * unit_price

        is_last_row = index == item_count
        if is_last_row:
            required_bottom = row_min_bottom + total_h + total_gap + row_h
        else:
            required_bottom = row_min_bottom

        if y < required_bottom:
            pdf.showPage()
            y = start_table_page()

        pdf.rect(table_x, y, table_w, row_h, fill=0)
        pdf.drawString(60, y + 11, fit_pdf_text(pdf, item["name"], 170, TP_UNICODE, 10))
        pdf.drawString(245, y + 11, str(item.get("hs_code", "")))
        pdf.drawRightString(350, y + 11, str(quantity))
        pdf.drawRightString(445, y + 11, f"USD {unit_price:,.2f}")
        pdf.drawRightString(540, y + 11, f"USD {line_total:,.2f}")
        y -= row_h

    total_x = table_right - total_w
    total_top = y - total_gap
    total_bottom = total_top - total_h

    if total_bottom < row_min_bottom:
        pdf.showPage()
        y = start_table_page()
        total_top = y - total_gap
        total_bottom = total_top - total_h

    pdf.setFillColor(colors.HexColor("#111827"))
    pdf.roundRect(total_x, total_bottom, total_w, total_h, 8, fill=1, stroke=0)

    pdf.setFillColor(colors.white)
    pdf.setFont(TP_UNICODE_BOLD, 13)
    pdf.drawRightString(total_x + total_w - 20, total_bottom + 17, f"TOTAL: USD {total:,.2f}")

    draw_signature_footer()

    pdf.showPage()
    pdf.save()
    buffer.seek(0)

    return Response(
        content=buffer.getvalue(),
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{invoice_no}.pdf"'
        }
    )
@router.get("/edit-invoice/{invoice_no}")
def edit_invoice(invoice_no: str, request: Request):
    account_id = _account_id(request)
    inv = public_invoice(_owned_invoice(invoice_no, account_id))
    company = load_account_company(account_id, ACCOUNT_COMPANIES_FILE)
    if inv:
            items = inv.get("items", [])
            item = items[0] if items else {}
            item_name = item.get("name", "")
            hs_code = item.get("hs_code", "")
            quantity = item.get("quantity", "")
            unit_price = item.get("unit_price", "")

            html = f"""
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Edit Invoice</title>
<style>{form_css()}</style>
</head>

<body>
<div class="container">

{navigation_footer("/invoice-list", "← Invoice List", state="Editing")}

<h1>Edit Invoice</h1>
<p class="sub">Update invoice information</p>

<form action="/update-invoice/{invoice_no}" method="post">

{section_card("Invoice Information", metadata([
    ("Currency", f'<input type="text" name="currency" value="{inv.get("currency", "USD")}" placeholder="Currency">'),
    ("Seller", f'<input type="text" name="seller" value="{inv.get("seller", "")}" placeholder="Seller Name">'),
    ("Seller Address", f'<input type="text" name="seller_address" value="{inv.get("seller_address") or company.get("address", "")}" placeholder="Seller Address">'),
    ("Seller Email", f'<input type="text" name="seller_email" value="{inv.get("seller_email") or company.get("email", "")}" placeholder="Seller Email">'),
    ("Seller Phone", f'<input type="text" name="seller_phone" value="{inv.get("seller_phone") or company.get("phone", "")}" placeholder="Seller Phone">'),
    ("Buyer", f'<input type="text" name="buyer" value="{inv.get("buyer", "")}" placeholder="Buyer Name">'),
    ("Buyer Address", f'<input type="text" name="buyer_address" value="{inv.get("buyer_address", "")}" placeholder="Buyer Address">'),
    ("Buyer Email", f'<input type="text" name="buyer_email" value="{inv.get("buyer_email", "")}" placeholder="Buyer Email">'),
]))}

<div class="card">
<h2>Product Information</h2>

<select id="product1" onchange="selectProduct(1)">
<option value="">Select Product Master</option>
</select>

<input type="text" name="item_name" value="{item_name}" placeholder="Item Name">
<input type="text" name="hs_code" id="hs1" value="{hs_code}" placeholder="HS Code">
<input type="number" name="quantity" id="qty1" value="{quantity}" placeholder="Quantity" oninput="calculateTotal()">
<input type="number" name="unit_price" id="price1" value="{unit_price}" placeholder="Unit Price" oninput="calculateTotal()">
</div>

<div class="total" id="total">Total: USD 0</div>

{form_footer("/invoice-list", "Update Invoice")}

</form>

</div>
<script>
let products = [];

async function loadProducts(){{
    const response = await fetch("/product-data");
    products = await response.json();

    const select = document.getElementById("product1");
    select.innerHTML = '<option value="">Select Product Master</option>';

    products.forEach((product, index) => {{
        const option = document.createElement("option");
        option.value = index;
        option.textContent = product.name;
        select.appendChild(option);
    }});

    const currentName = document.querySelector('input[name="item_name"]').value.toLowerCase();
    const selectedIndex = products.findIndex(product => (product.name || "").toLowerCase() === currentName);
    if(selectedIndex >= 0){{
        select.value = selectedIndex;
    }}
}}

function selectProduct(number){{
    const index = document.getElementById("product" + number).value;
    if(index === "") return;

    const product = products[index];

    document.querySelector('input[name="item_name"]').value = product.name || "";
    document.getElementById("price" + number).value = product.unit_price || 0;
    document.getElementById("hs" + number).value = product.hs_code || "";
    calculateTotal();
}}

function calculateTotal(){{
    const quantity = Number(document.getElementById("qty1").value || 0);
    const unitPrice = Number(document.getElementById("price1").value || 0);
    document.getElementById("total").innerHTML = "Total: USD " + (quantity * unitPrice);
}}

window.onload = function(){{
    loadProducts();
    calculateTotal();
}};
</script>
</body>
</html>
            """

            return HTMLResponse(html)
@router.post("/update-invoice/{invoice_no}")
def update_invoice(
    invoice_no: str,
    request: Request,
    seller: str = Form(""),
    currency: str = Form(""),
    buyer: str = Form(""),
    buyer_address: str = Form(""),
    buyer_email: str = Form(""),
    item_name: str = Form(""),
    hs_code: str = Form(""),
    quantity: str = Form(""),
    unit_price: str = Form(""),
    seller_address: Annotated[Optional[str], Form()] = None,
    seller_email: Annotated[Optional[str], Form()] = None,
    seller_phone: Annotated[Optional[str], Form()] = None,
):
    seller = require_text("Seller", seller)
    buyer = require_text("Buyer", buyer)
    require_items([item_name])
    account_id = _account_id(request)
    def replace_invoice(invoices):
        for inv in invoices:
            if (
                inv.get("invoice_no") != invoice_no
                or str(inv.get("account_id", "") or "").strip() != account_id
            ):
                continue
            try:
                quantity_value = int(quantity)
            except:
                quantity_value = quantity

            try:
                unit_price_value = float(unit_price)
            except:
                unit_price_value = unit_price

            inv["seller"] = seller
            if seller_address is not None:
                inv["seller_address"] = seller_address
            if seller_email is not None:
                inv["seller_email"] = seller_email
            if seller_phone is not None:
                inv["seller_phone"] = seller_phone
            inv["currency"] = currency
            inv["buyer"] = buyer
            inv["buyer_address"] = buyer_address
            inv["buyer_email"] = buyer_email
            inv["items"] = [{
                "name": item_name,
                "hs_code": hs_code,
                "quantity": quantity_value,
                "unit_price": unit_price_value
            }]
            return
        raise HTTPException(status_code=404, detail="Invoice not found")
    locked_json_mutation(INVOICE_FILE, [], replace_invoice, list)

    return RedirectResponse(url="/invoice-list", status_code=303)     
@router.get("/delete-invoice/{invoice_no}")

def delete_invoice(invoice_no: str, request: Request):
    _owned_invoice(invoice_no, _account_id(request))
    return render_delete_page(
        "Commercial Invoice",
        invoice_no,
        f"/delete-invoice/{invoice_no}",
        "/invoice-list",
        find_dependencies("Commercial Invoice", invoice_no, _account_id(request)),
    )

@router.post("/delete-invoice/{invoice_no}")
def confirm_delete_invoice(invoice_no: str, request: Request):
    account_id = _account_id(request)
    dependencies = find_dependencies("Commercial Invoice", invoice_no, account_id)
    if dependencies:
        return render_delete_page(
            "Commercial Invoice",
            invoice_no,
            f"/delete-invoice/{invoice_no}",
            "/invoice-list",
            dependencies,
            status_code=409,
        )

    def remove(invoices):
        index = next(
            (
                index
                for index, invoice in enumerate(invoices)
                if isinstance(invoice, dict)
                and str(invoice.get("invoice_no", "") or "").strip() == invoice_no
                and str(invoice.get("account_id", "") or "").strip() == account_id
            ),
            None,
        )
        if index is None:
            raise HTTPException(status_code=404, detail="Invoice not found")
        invoices.pop(index)

    locked_json_mutation(INVOICE_FILE, [], remove, list)
    return RedirectResponse("/invoice-list", status_code=303)
@router.get("/invoice-list")
def invoice_list(request: Request, search: str = ""):

    if not os.path.exists(INVOICE_FILE):
        return HTMLResponse("<h1>No Invoices</h1>")

    account_id = _account_id(request)
    invoices = load_invoices(account_id)
    from app import packing as packing_module
    packing_by_invoice = {}
    for packing in packing_module.load_packing_lists(account_id):
        invoice_no = str(packing.get("invoice_no", "") or "").strip()
        packing_no = str(packing.get("packing_no", "") or "").strip()
        if invoice_no and packing_no:
            packing_by_invoice.setdefault(invoice_no, packing_no)

    valid_invoices = [
        inv for inv in invoices
        if inv.get("invoice_no")
    ]

    valid_invoices = sorted(
        valid_invoices,
        key=lambda inv: inv.get("invoice_no", ""),
        reverse=True
    )

    if search:
        search_lower = search.lower()

        valid_invoices = [
            inv for inv in valid_invoices
            if search_lower in inv.get("invoice_no", "").lower()
            or search_lower in inv.get("buyer", "").lower()
            or search_lower in inv.get("seller", "").lower()
            or search_lower in str(inv.get("items", "")).lower()
        ]

    rows = []
    for inv in valid_invoices:
        packing_no = packing_by_invoice.get(str(inv.get("invoice_no", "") or "").strip(), "")
        packing_exists = bool(packing_no)
        packing_href = f"/edit-packing/{packing_no}" if packing_exists else f"/packing-page?invoice_no={inv.get('invoice_no', '')}"
        items = inv.get("items", [])
        item_names = "<br>".join(html_lib.escape(str(item.get("name", "") or "")) for item in items)

        total = sum(
            item.get("quantity", 0) * item.get("unit_price", 0)
            for item in items
        )

        invoice_no = str(inv.get("invoice_no", "") or "")
        rows.append([
            badge(invoice_no), html_lib.escape(str(inv.get("seller", "") or "")),
            html_lib.escape(str(inv.get("buyer", "") or "")), item_names, f"USD {total:g}",
            button("PDF", f"/invoice-pdf/{invoice_no}", "secondary"),
            button("Created" if packing_exists else "Packing", packing_href, "secondary"),
            button("Edit", f"/edit-invoice/{invoice_no}", "secondary"),
            button("Delete", f"/delete-invoice/{invoice_no}", "danger"),
        ])
    content = search_toolbar(button("+ New Invoice", "/invoice"), button("← Dashboard", "/", "secondary"), action="/invoice-list", value=search, placeholder="Search invoice, buyer, seller or item", reset_url="/invoice-list", count_label=f"Total Invoices : {len(valid_invoices)}")
    content += table(["Invoice No", "Seller", "Buyer", "Product", "Total", "PDF", "Packing", "Edit", "Delete"], rows)
    return HTMLResponse(page_shell("Invoice List", content, subtitle="Manage all invoice documents"))
