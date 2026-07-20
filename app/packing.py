from io import BytesIO
from datetime import datetime
from typing import List
from fastapi import APIRouter, Body, Response, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.lib import colors
import html as html_lib
import json
from pathlib import Path

from app.storage import atomic_write_json, data_path, load_json_strict, locked_json_mutation, next_identifier
from app.validation import require_existing_reference, require_items, require_text
from app.referential_integrity import confirmed_identifier_delete, identifier_delete_confirmation
from app.shipment import link_direct_document
from app.ui import badge, button, form_footer, form_page, metadata, navigation_footer, page_shell, search_toolbar, section_card, table

COMPANY_FILE = data_path("company.json")
PACKING_FILE = data_path("packing_lists.json")
INVOICE_FILE = data_path("invoices.json")

router = APIRouter()


def load_company():
    return load_json_strict(COMPANY_FILE, {}, dict)


def load_packing_lists():
    return load_json_strict(PACKING_FILE, [], list)


def save_packing_lists(packing_lists):
    atomic_write_json(PACKING_FILE, packing_lists, list)


def load_invoice_records():
    return load_json_strict(INVOICE_FILE, [], list)


def next_packing_no(packing_lists):
    return next_identifier(packing_lists, "packing_no", "PK")
    existing_numbers = [
        int(p.get("packing_no", "PK-000").split("-")[1])
        for p in packing_lists
        if p.get("packing_no", "").startswith("PK-")
    ]

    next_no = max(existing_numbers, default=0) + 1
    return f"PK-{next_no:03d}"


@router.post("/packing-list")
def create_packing_list(payload: dict = Body(...)):
    record = dict(payload)
    shipment_no = str(record.pop("shipment_no", "") or "").strip()
    require_existing_reference("Invoice", record.get("invoice_no", ""), load_invoice_records(), "invoice_no", required=True)
    record["seller"] = require_text("Seller", record.get("seller", ""))
    record["buyer"] = require_text("Buyer", record.get("buyer", ""))
    require_items(record.get("items", []))
    def add_packing(records):
        record["packing_no"] = next_identifier(records, "packing_no", "PK")
        records.append(record)
    locked_json_mutation(PACKING_FILE, [], add_packing, list)
    link_direct_document(shipment_no, "packing_no", record["packing_no"])
    return record


@router.get("/packing-list")
def packing_list(search: str = ""):
    packing_lists = load_packing_lists()
    packing_lists = list(reversed(packing_lists))

    if search:
        search_lower = search.lower()
        packing_lists = [
            p for p in packing_lists
            if search_lower in str(p.get("packing_no", "")).lower()
            or search_lower in str(p.get("invoice_no", "")).lower()
            or search_lower in str(p.get("seller", "")).lower()
            or search_lower in str(p.get("buyer", "")).lower()
            or search_lower in str(p.get("items", "")).lower()
        ]

    rows = []
    for packing in packing_lists:
        if not packing.get("packing_no"):
            continue

        items = packing.get("items", [])

        escaped = lambda value: html_lib.escape(str(value or ""))
        packing_no = str(packing.get("packing_no", "") or "")
        rows.append([
            badge(packing_no), escaped(packing.get("invoice_no", "")), escaped(packing.get("seller", "")),
            escaped(packing.get("buyer", "")), "<br>".join(escaped(item.get("name", "")) for item in items),
            "<br>".join(escaped(item.get("quantity", "")) for item in items),
            "<br>".join(escaped(item.get("hs_code", "")) for item in items),
            "<br>".join(escaped(item.get("carton", "")) for item in items),
            "<br>".join(escaped(item.get("net_weight", "")) for item in items),
            "<br>".join(escaped(item.get("gross_weight", "")) for item in items),
            button("PDF", f"/packing-list-pdf/{packing_no}", "secondary"),
            button("Edit", f"/edit-packing/{packing_no}", "secondary"),
            button("Delete", f"/packing-delete/{packing_no}", "danger"),
        ])
    content = search_toolbar(button("+ New Packing", "/packing-page"), button("← Dashboard", "/", "secondary"), action="/packing-list", value=search, placeholder="Search packing, invoice, buyer, seller or item", reset_url="/packing-list", count_label=f"Total Packing Lists : {len(packing_lists)}")
    content += table(["Packing No", "Invoice No", "Seller", "Buyer", "Item", "Quantity", "HS Code", "Carton", "Net", "Gross", "PDF", "Edit", "Delete"], rows)
    return HTMLResponse(page_shell("Packing List", content, subtitle="Manage all packing documents"))

@router.post("/packing")
def save_packing(
    invoice_no: str = Form(""),
    seller: str = Form(""),
    buyer: str = Form(""),
    item_name: List[str] = Form([]),
    hs_code: List[str] = Form([]),
    carton: List[str] = Form([]),
    net_weight: List[str] = Form([]),
    gross_weight: List[str] = Form([]),
):
    require_existing_reference("Invoice", invoice_no, load_invoice_records(), "invoice_no", required=True)
    seller = require_text("Seller", seller)
    buyer = require_text("Buyer", buyer)
    items = []

    for i in range(len(item_name)):
        if not item_name[i].strip():
            continue

        items.append({
            "name": item_name[i],
            "hs_code": hs_code[i] if i < len(hs_code) else "",
            "carton": carton[i] if i < len(carton) else "",
            "net_weight": net_weight[i] if i < len(net_weight) else "",
            "gross_weight": gross_weight[i] if i < len(gross_weight) else "",
        })

    require_items(items)
    def add_packing(packing_lists):
        packing = {
        "packing_no": next_identifier(packing_lists, "packing_no", "PK"),
        "invoice_no": invoice_no,
        "seller": seller,
        "buyer": buyer,
        "items": items,
        }
        packing_lists.append(packing)
    locked_json_mutation(PACKING_FILE, [], add_packing, list)

    return RedirectResponse(url="/packing-list", status_code=303)


@router.get("/edit-packing/{packing_no}")
def edit_packing(packing_no: str):
    packing_lists = load_packing_lists()

    for packing in packing_lists:
        if packing.get("packing_no") == packing_no:
            items = packing.get("items", [])

            if not items:
                items = [{}]

            info = metadata([
                ("Packing No", f'<input type="text" value="{packing.get("packing_no", "")}" readonly>'),
                ("Invoice No", f'<input type="text" name="invoice_no" value="{packing.get("invoice_no", "")}">'),
                ("Seller", f'<input type="text" name="seller" value="{packing.get("seller", "")}">'),
                ("Buyer", f'<input type="text" name="buyer" value="{packing.get("buyer", "")}">'),
            ])
            html = f'<form action="/update-packing/{packing_no}" method="post">' + section_card("Packing Information", info)

            for item in items:
                html += f"""
<div class="item-row">

<p>Item Name</p>
<input type="text" name="item_name" value="{item.get('name','')}">

<p>HS Code</p>
<input type="text" name="hs_code" value="{item.get('hs_code','')}">

<p>Carton</p>
<input type="text" name="carton" value="{item.get('carton','')}">

<p>Net Weight</p>
<input type="text" name="net_weight" value="{item.get('net_weight','')}">

<p>Gross Weight</p>
<input type="text" name="gross_weight" value="{item.get('gross_weight','')}">

</div>
"""

            html += form_footer("/packing-list", "Update Packing") + "</form>"
            navigation = navigation_footer("/packing-list", "← Packing List", state="Editing")
            return HTMLResponse(form_page("Edit Packing List", html, subtitle="Update packing list information", navigation=navigation))

    return {"error": "Packing List not found"}

@router.post("/update-packing/{packing_no}")
def update_packing(
    packing_no: str,
    invoice_no: str = Form(""),
    seller: str = Form(""),
    buyer: str = Form(""),
    item_name: List[str] = Form([]),
    hs_code: List[str] = Form([]),
    carton: List[str] = Form([]),
    net_weight: List[str] = Form([]),
    gross_weight: List[str] = Form([]),
):
    require_existing_reference("Invoice", invoice_no, load_invoice_records(), "invoice_no", required=True)
    seller = require_text("Seller", seller)
    buyer = require_text("Buyer", buyer)
    items = []

    for i in range(len(item_name)):
        if not item_name[i].strip():
            continue

        items.append({
            "name": item_name[i],
            "hs_code": hs_code[i] if i < len(hs_code) else "",
            "carton": carton[i] if i < len(carton) else "",
            "net_weight": net_weight[i] if i < len(net_weight) else "",
            "gross_weight": gross_weight[i] if i < len(gross_weight) else "",
        })

    require_items(items)
    def replace_packing(packing_lists):
        for packing in packing_lists:
            if packing.get("packing_no") != packing_no:
                continue
            packing["invoice_no"] = invoice_no
            packing["seller"] = seller
            packing["buyer"] = buyer
            packing["items"] = items
            return
        raise HTTPException(status_code=404, detail="Packing List not found")
    locked_json_mutation(PACKING_FILE, [], replace_packing, list)

    return HTMLResponse("""
<script>
alert("Packing Updated");
window.location.href = "/packing-list";
</script>
""")


@router.get("/packing-delete/{packing_no}")
def delete_packing(packing_no: str):
    return identifier_delete_confirmation("Packing List", "Packing List", packing_no, PACKING_FILE, "packing_no", f"/packing-delete/{packing_no}", "/packing-list")

@router.post("/packing-delete/{packing_no}")
def confirm_delete_packing(packing_no: str):
    return confirmed_identifier_delete("Packing List", "Packing List", packing_no, PACKING_FILE, "packing_no", f"/packing-delete/{packing_no}", "/packing-list", "/packing-list")

@router.get("/packing-form")
def packing_form():
    return HTMLResponse(form_page("Packing Form", button("Go to New Packing Page", "/packing-page"), subtitle="Use /packing-page for the new Packing UI."))


@router.post("/packing-list/pdf")
def create_packing_list_pdf(payload: dict = Body(...)):
    company = load_company()

    packing_no = payload.get("packing_no") or "-"
    invoice_no = payload.get("invoice_no", "")
    today = datetime.now().strftime("%Y-%m-%d")

    buyer = payload.get("buyer", "")
    seller = payload.get("seller", "") or company.get("name", "")
    seller_address = company.get("address") or payload.get("seller_address", "")
    seller_email = company.get("email") or payload.get("seller_email", "")

    items = payload.get("items", [])

    buffer = BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4

    pdf.setTitle(f"Packing List {packing_no}")

    table_x = 45
    table_w = 505
    table_right = table_x + table_w
    table_header_h = 28
    row_h = 26
    row_min_bottom = 145
    summary_w = 225
    summary_h = 95
    summary_gap = 20

    def fit_text(text, max_width, font_name="Helvetica", font_size=8):
        text = str(text or "")
        if pdf.stringWidth(text, font_name, font_size) <= max_width:
            return text

        suffix = "..."
        while text and pdf.stringWidth(text + suffix, font_name, font_size) > max_width:
            text = text[:-1]
        return text + suffix if text else suffix

    def draw_document_header():
        pdf.setFillColor(colors.HexColor("#111827"))
        pdf.rect(0, height - 90, width, 90, fill=1, stroke=0)

        pdf.setFillColor(colors.white)
        pdf.setFont("Helvetica-Bold", 24)
        pdf.drawString(45, height - 55, "PACKING LIST")

        pdf.setFont("Helvetica", 9)
        pdf.drawRightString(width - 45, height - 38, "Trade Paper AI")
        pdf.drawRightString(width - 45, height - 55, "Automated Trade Document")

        pdf.setFillColor(colors.black)
        pdf.setFont("Helvetica-Bold", 11)
        pdf.drawString(45, height - 125, f"Packing No: {packing_no}")
        pdf.drawString(45, height - 143, f"Invoice No: {invoice_no}")
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
        pdf.drawString(325, height - 220, buyer)

    def draw_table_header():
        header_y = height - 315

        pdf.setFillColor(colors.HexColor("#E5E7EB"))
        pdf.rect(table_x, header_y, table_w, table_header_h, fill=1, stroke=0)

        pdf.setFillColor(colors.black)
        pdf.setFont("Helvetica-Bold", 8)
        pdf.drawString(52, header_y + 10, "No")
        pdf.drawString(80, header_y + 10, "Item")
        pdf.drawRightString(235, header_y + 10, "Quantity")
        pdf.drawString(270, header_y + 10, "HS Code")
        pdf.drawRightString(370, header_y + 10, "Carton")
        pdf.drawRightString(455, header_y + 10, "Net Weight")
        pdf.drawRightString(540, header_y + 10, "Gross Weight")

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

    total_carton = 0
    total_net_weight = 0.0
    total_gross_weight = 0.0

    item_count = len(items)

    for index, item in enumerate(items, start=1):
        quantity = item["quantity"] if "quantity" in item else ""
        carton = item.get("carton", "")
        net_weight = item.get("net_weight", "")
        gross_weight = item.get("gross_weight", "")

        try:
            total_carton += int(float(carton or 0))
        except:
            pass

        try:
            total_net_weight += float(net_weight or 0)
        except:
            pass

        try:
            total_gross_weight += float(gross_weight or 0)
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
        pdf.drawString(80, y + 9, fit_text(item.get("name", ""), 135))
        pdf.drawRightString(235, y + 9, str(quantity))
        pdf.drawString(270, y + 9, str(item.get("hs_code", "")))
        pdf.drawRightString(370, y + 9, str(carton))
        pdf.drawRightString(455, y + 9, str(net_weight))
        pdf.drawRightString(540, y + 9, str(gross_weight))

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
    text_y = summary_top - 28
    line_gap = 18
    pdf.drawString(text_x, text_y, f"Total Cartons: {total_carton}")
    pdf.drawString(text_x, text_y - line_gap, f"Total Net Weight: {total_net_weight:g}")
    pdf.drawString(text_x, text_y - line_gap * 2, f"Total Gross Weight: {total_gross_weight:g}")

    draw_signature_footer()

    pdf.showPage()
    pdf.save()
    buffer.seek(0)

    return Response(
        content=buffer.getvalue(),
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{packing_no}.pdf"'
        },
    )


@router.get("/packing-list-pdf/{packing_no}")
def packing_list_pdf(packing_no: str):
    packing_lists = load_packing_lists()

    for packing in packing_lists:
        if packing.get("packing_no") == packing_no:
            return create_packing_list_pdf(packing)

    return {"error": "Packing list not found"}
