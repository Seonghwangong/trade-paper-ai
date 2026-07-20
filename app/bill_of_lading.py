from typing import List
from pathlib import Path
from datetime import datetime
from io import BytesIO
import html as html_lib
import json

from fastapi import APIRouter, Body, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

router = APIRouter()

from app.storage import atomic_write_json, data_path, load_json_strict, locked_json_mutation, next_identifier
from app.validation import require_consistent_reference, require_existing_reference, require_text
from app.referential_integrity import confirmed_identifier_delete, identifier_delete_confirmation
from app.shipment import shipment_context_redirect_url
from app.ui import badge, button, form_css, form_footer, metadata, navigation_footer, page_shell, search_toolbar, section_card, table

BL_FILE = data_path("bills_of_lading.json")
PACKING_FILE = data_path("packing_lists.json")
INVOICE_FILE = data_path("invoices.json")


def load_bills_of_lading():
    return load_json_strict(BL_FILE, [], list)


def save_bills_of_lading(records):
    atomic_write_json(BL_FILE, records, list)


def load_packing_lists():
    return load_json_strict(PACKING_FILE, [], list)


def validate_bl_links(packing_no, invoice_no):
    packing = require_existing_reference("Packing List", packing_no, load_packing_lists(), "packing_no", required=True)
    require_consistent_reference("Invoice", invoice_no, packing.get("invoice_no", ""), "selected Packing List")
    require_existing_reference("Invoice", invoice_no or packing.get("invoice_no", ""), load_json_strict(INVOICE_FILE, [], list), "invoice_no", required=True)


def next_bl_no(records):
    return next_identifier(records, "bl_no", "BL")
    numbers = [
        int(record.get("bl_no", "BL-000").split("-")[1])
        for record in records
        if record.get("bl_no", "").startswith("BL-")
    ]
    return f"BL-{max(numbers, default=0) + 1:03d}"


def html_attr(value):
    return html_lib.escape(str(value or ""), quote=True)


def numeric_total(items, field):
    total = 0.0
    for item in items:
        try:
            total += float(item.get(field, 0) or 0)
        except:
            pass
    return total


def format_number(value):
    try:
        number = float(value or 0)
        return f"{number:g}"
    except:
        return str(value or "")


def build_items(name, quantity, hs_code, carton, net_weight, gross_weight):
    items = []
    for i in range(len(name)):
        if not name[i].strip():
            continue
        items.append({
            "name": name[i],
            "quantity": quantity[i] if i < len(quantity) else "",
            "hs_code": hs_code[i] if i < len(hs_code) else "",
            "carton": carton[i] if i < len(carton) else "",
            "net_weight": net_weight[i] if i < len(net_weight) else "",
            "gross_weight": gross_weight[i] if i < len(gross_weight) else "",
        })
    return items


def build_item_rows(items):
    if not items:
        items = [{}]

    rows = ""
    for item in items:
        rows += f"""
<div class="item-row">
<input type="text" name="item_name" value="{html_attr(item.get('name', ''))}" placeholder="Item Name">
<input type="text" name="quantity" value="{html_attr(item.get('quantity', ''))}" placeholder="Quantity" oninput="calculateTotals()">
<input type="text" name="hs_code" value="{html_attr(item.get('hs_code', ''))}" placeholder="HS Code">
<input type="text" name="carton" value="{html_attr(item.get('carton', ''))}" placeholder="Carton" oninput="calculateTotals()">
<input type="text" name="net_weight" value="{html_attr(item.get('net_weight', ''))}" placeholder="Net Weight" oninput="calculateTotals()">
<input type="text" name="gross_weight" value="{html_attr(item.get('gross_weight', ''))}" placeholder="Gross Weight" oninput="calculateTotals()">
<button class="remove" type="button" onclick="removeItem(this)">Remove Item</button>
</div>
"""
    return rows


def blank_payload():
    return {
        "packing_no": "",
        "invoice_no": "",
        "shipper": "",
        "consignee": "",
        "notify_party": "",
        "vessel": "",
        "voyage_no": "",
        "port_of_loading": "",
        "port_of_discharge": "",
        "place_of_delivery": "",
        "bl_date": datetime.now().strftime("%Y-%m-%d"),
        "items": [],
        "total_carton": "",
        "total_net_weight": "",
        "total_gross_weight": "",
    }


def payload_from_packing(packing_no):
    payload = blank_payload()
    if not packing_no:
        return payload

    for packing in load_packing_lists():
        if packing.get("packing_no") == packing_no:
            items = packing.get("items", [])
            payload.update({
                "packing_no": packing.get("packing_no", ""),
                "invoice_no": packing.get("invoice_no", ""),
                "shipper": packing.get("seller", ""),
                "consignee": packing.get("buyer", ""),
                "items": items,
                "total_carton": format_number(numeric_total(items, "carton")),
                "total_net_weight": format_number(numeric_total(items, "net_weight")),
                "total_gross_weight": format_number(numeric_total(items, "gross_weight")),
            })
            break
    return payload


def build_record(
    bl_no, packing_no, invoice_no, shipper, consignee, notify_party, vessel,
    voyage_no, port_of_loading, port_of_discharge, place_of_delivery, bl_date,
    item_name, quantity, hs_code, carton, net_weight, gross_weight,
    total_carton, total_net_weight, total_gross_weight,
):
    items = build_items(item_name, quantity, hs_code, carton, net_weight, gross_weight)
    return {
        "bl_no": bl_no,
        "packing_no": packing_no,
        "invoice_no": invoice_no,
        "shipper": shipper,
        "consignee": consignee,
        "notify_party": notify_party,
        "vessel": vessel,
        "voyage_no": voyage_no,
        "port_of_loading": port_of_loading,
        "port_of_discharge": port_of_discharge,
        "place_of_delivery": place_of_delivery,
        "bl_date": bl_date,
        "items": items,
        "total_carton": total_carton,
        "total_net_weight": total_net_weight,
        "total_gross_weight": total_gross_weight,
    }


def render_form(record, action, title, button_text, show_bl_no=False, shipment_no=""):
    rows = build_item_rows(record.get("items", []))
    bl_no_input = ""
    if show_bl_no:
        bl_no_input = f'<input type="text" value="{html_attr(record.get("bl_no", ""))}" placeholder="B/L No" readonly>'

    html = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Bill of Lading</title>
<style>__FORM_CSS__</style>
</head>
<body>
<div class="container">
__NAVIGATION__
<h1>__TITLE__</h1>
<p class="sub">Create shipping document from packing cargo data</p>

<form action="__ACTION__" method="post">
__SHIPMENT_CONTEXT__
__DOCUMENT_SECTION__
__PARTY_SECTION__
__TRANSPORT_SECTION__
__CARGO_SECTION__
__FORM_FOOTER__
</form>
</div>

<script>
function addItem(){
    const area = document.getElementById("items_area");
    const first = document.querySelector(".item-row");
    const row = first.cloneNode(true);
    row.querySelectorAll("input").forEach(input => input.value = "");
    area.appendChild(row);
    calculateTotals();
}

function removeItem(button){
    const rows = document.querySelectorAll(".item-row");
    if(rows.length <= 1) return;
    button.closest(".item-row").remove();
    calculateTotals();
}

function sumField(name){
    let total = 0;
    document.querySelectorAll('input[name="' + name + '"]').forEach(input => {
        total += parseFloat(input.value) || 0;
    });
    return total;
}

function cleanNumber(value){
    return Number.isInteger(value) ? String(value) : String(Number(value.toFixed(4)));
}

function calculateTotals(){
    const carton = sumField("carton");
    const net = sumField("net_weight");
    const gross = sumField("gross_weight");
    document.getElementById("total_carton").value = cleanNumber(carton);
    document.getElementById("total_net_weight").value = cleanNumber(net);
    document.getElementById("total_gross_weight").value = cleanNumber(gross);
    document.getElementById("totals_text").innerHTML =
        "Total Cartons: " + cleanNumber(carton) +
        " | Total Net Weight: " + cleanNumber(net) +
        " | Total Gross Weight: " + cleanNumber(gross);
}

calculateTotals();
</script>
</body>
</html>
"""
    replacements = {
        "__TITLE__": html_attr(title),
        "__ACTION__": html_attr(action),
        "__BL_NO_INPUT__": bl_no_input,
        "__PACKING_NO__": html_attr(record.get("packing_no", "")),
        "__INVOICE_NO__": html_attr(record.get("invoice_no", "")),
        "__BL_DATE__": html_attr(record.get("bl_date", "")),
        "__SHIPPER__": html_attr(record.get("shipper", "")),
        "__CONSIGNEE__": html_attr(record.get("consignee", "")),
        "__NOTIFY_PARTY__": html_attr(record.get("notify_party", "")),
        "__VESSEL__": html_attr(record.get("vessel", "")),
        "__VOYAGE_NO__": html_attr(record.get("voyage_no", "")),
        "__PORT_OF_LOADING__": html_attr(record.get("port_of_loading", "")),
        "__PORT_OF_DISCHARGE__": html_attr(record.get("port_of_discharge", "")),
        "__PLACE_OF_DELIVERY__": html_attr(record.get("place_of_delivery", "")),
        "__ITEM_ROWS__": rows,
        "__TOTAL_CARTON__": html_attr(record.get("total_carton", "")),
        "__TOTAL_NET_WEIGHT__": html_attr(record.get("total_net_weight", "")),
        "__TOTAL_GROSS_WEIGHT__": html_attr(record.get("total_gross_weight", "")),
        "__BUTTON_TEXT__": html_attr(button_text),
        "__FORM_CSS__": form_css(max_width=960),
        "__NAVIGATION__": navigation_footer("/bl-list", "B/L List", state="Editing" if show_bl_no else "New"),
        "__DOCUMENT_SECTION__": section_card("Document Information", metadata([
            ("B/L No", bl_no_input),
            ("Packing No", f'<input type="text" name="packing_no" value="{html_attr(record.get("packing_no", ""))}" placeholder="Packing No">'),
            ("Invoice No", f'<input type="text" name="invoice_no" value="{html_attr(record.get("invoice_no", ""))}" placeholder="Invoice No">'),
            ("B/L Date", f'<input type="date" name="bl_date" value="{html_attr(record.get("bl_date", ""))}">'),
        ])),
        "__PARTY_SECTION__": section_card("Party Information", f'<input type="text" name="shipper" value="{html_attr(record.get("shipper", ""))}" placeholder="Shipper"><input type="text" name="consignee" value="{html_attr(record.get("consignee", ""))}" placeholder="Consignee"><input type="text" name="notify_party" value="{html_attr(record.get("notify_party", ""))}" placeholder="Notify Party">'),
        "__TRANSPORT_SECTION__": section_card("Transport Information", f'<input type="text" name="vessel" value="{html_attr(record.get("vessel", ""))}" placeholder="Vessel"><input type="text" name="voyage_no" value="{html_attr(record.get("voyage_no", ""))}" placeholder="Voyage No"><input type="text" name="port_of_loading" value="{html_attr(record.get("port_of_loading", ""))}" placeholder="Port of Loading"><input type="text" name="port_of_discharge" value="{html_attr(record.get("port_of_discharge", ""))}" placeholder="Port of Discharge"><input type="text" name="place_of_delivery" value="{html_attr(record.get("place_of_delivery", ""))}" placeholder="Place of Delivery">'),
        "__CARGO_SECTION__": section_card("Cargo Information", f'<div id="items_area">{rows}</div><button class="add" type="button" onclick="addItem()">+ Add Cargo Item</button><input id="total_carton" type="hidden" name="total_carton" value="{html_attr(record.get("total_carton", ""))}"><input id="total_net_weight" type="hidden" name="total_net_weight" value="{html_attr(record.get("total_net_weight", ""))}"><input id="total_gross_weight" type="hidden" name="total_gross_weight" value="{html_attr(record.get("total_gross_weight", ""))}"><div class="totals" id="totals_text"></div>'),
        "__FORM_FOOTER__": form_footer("/bl-list", button_text),
        "__SHIPMENT_CONTEXT__": f'<input type="hidden" name="shipment_no" value="{html_attr(shipment_no)}">' if shipment_no else "",
    }
    for key, value in replacements.items():
        html = html.replace(key, value)
    return HTMLResponse(html)


@router.get("/bl-list")
def bl_list(search: str = ""):
    records = list(reversed(load_bills_of_lading()))
    if search:
        q = search.lower()
        records = [
            record for record in records
            if q in str(record.get("bl_no", "")).lower()
            or q in str(record.get("packing_no", "")).lower()
            or q in str(record.get("invoice_no", "")).lower()
            or q in str(record.get("shipper", "")).lower()
            or q in str(record.get("consignee", "")).lower()
            or q in str(record.get("items", "")).lower()
        ]

    rows = []
    for record in records:
        bl_no = str(record.get("bl_no", "") or "")
        rows.append([
            badge(bl_no), html_attr(record.get("packing_no", "")), html_attr(record.get("invoice_no", "")),
            html_attr(record.get("shipper", "")), html_attr(record.get("consignee", "")),
            button("PDF", f"/bl-pdf/{bl_no}", "secondary"),
            button("Edit", f"/edit-bl/{bl_no}", "secondary"),
            button("Delete", f"/delete-bl/{bl_no}", "danger"),
        ])
    content = search_toolbar(button("+ New B/L", "/bl-form"), button("Dashboard", "/", "secondary"), action="/bl-list", value=search, placeholder="Search B/L, packing, invoice, shipper, consignee or item", reset_url="/bl-list", count_label=f"Total Bills of Lading : {len(records)}")
    content += table(["B/L No", "Packing", "Invoice", "Shipper", "Consignee", "PDF", "Edit", "Delete"], rows, empty_message="No Bills of Lading have been registered yet.")
    return HTMLResponse(page_shell("Bill of Lading List", content, subtitle="Manage all Bill of Lading documents"))


@router.get("/bl-form")
def bl_form(packing_no: str = "", shipment_no: str = ""):
    record = payload_from_packing(packing_no)
    return render_form(record, "/bl", "Bill of Lading", "Save Bill of Lading", shipment_no=shipment_no)


@router.post("/bl")
def save_bl(
    shipment_no: str = Form(""),
    packing_no: str = Form(""),
    invoice_no: str = Form(""),
    shipper: str = Form(""),
    consignee: str = Form(""),
    notify_party: str = Form(""),
    vessel: str = Form(""),
    voyage_no: str = Form(""),
    port_of_loading: str = Form(""),
    port_of_discharge: str = Form(""),
    place_of_delivery: str = Form(""),
    bl_date: str = Form(""),
    item_name: List[str] = Form([]),
    quantity: List[str] = Form([]),
    hs_code: List[str] = Form([]),
    carton: List[str] = Form([]),
    net_weight: List[str] = Form([]),
    gross_weight: List[str] = Form([]),
    total_carton: str = Form(""),
    total_net_weight: str = Form(""),
    total_gross_weight: str = Form(""),
):
    validate_bl_links(packing_no, invoice_no)
    shipper = require_text("Shipper", shipper)
    consignee = require_text("Consignee", consignee)
    saved = {}
    def add_bl(records):
        bl_number = next_identifier(records, "bl_no", "BL")
        record = build_record(
        bl_number, packing_no, invoice_no, shipper, consignee,
        notify_party, vessel, voyage_no, port_of_loading, port_of_discharge,
        place_of_delivery, bl_date, item_name, quantity, hs_code, carton,
        net_weight, gross_weight, total_carton, total_net_weight,
        total_gross_weight,
        )
        records.append(record)
        saved["bl_no"] = bl_number
    locked_json_mutation(BL_FILE, [], add_bl, list)
    return RedirectResponse(url=shipment_context_redirect_url(shipment_no, "bl_no", saved["bl_no"], "/bl-list"), status_code=303)


@router.get("/edit-bl/{bl_no}")
def edit_bl(bl_no: str):
    for record in load_bills_of_lading():
        if record.get("bl_no") == bl_no:
            return render_form(record, f"/update-bl/{bl_no}", "Edit Bill of Lading", "Update Bill of Lading", True)
    return HTMLResponse("Bill of Lading Not Found", status_code=404)


@router.post("/update-bl/{bl_no}")
def update_bl(
    bl_no: str,
    packing_no: str = Form(""),
    invoice_no: str = Form(""),
    shipper: str = Form(""),
    consignee: str = Form(""),
    notify_party: str = Form(""),
    vessel: str = Form(""),
    voyage_no: str = Form(""),
    port_of_loading: str = Form(""),
    port_of_discharge: str = Form(""),
    place_of_delivery: str = Form(""),
    bl_date: str = Form(""),
    item_name: List[str] = Form([]),
    quantity: List[str] = Form([]),
    hs_code: List[str] = Form([]),
    carton: List[str] = Form([]),
    net_weight: List[str] = Form([]),
    gross_weight: List[str] = Form([]),
    total_carton: str = Form(""),
    total_net_weight: str = Form(""),
    total_gross_weight: str = Form(""),
):
    validate_bl_links(packing_no, invoice_no)
    shipper = require_text("Shipper", shipper)
    consignee = require_text("Consignee", consignee)
    updated = build_record(
        bl_no, packing_no, invoice_no, shipper, consignee, notify_party,
        vessel, voyage_no, port_of_loading, port_of_discharge,
        place_of_delivery, bl_date, item_name, quantity, hs_code, carton,
        net_weight, gross_weight, total_carton, total_net_weight,
        total_gross_weight,
    )
    def replace_bl(records):
        for index, record in enumerate(records):
            if record.get("bl_no") == bl_no:
                records[index] = updated
                return
        raise HTTPException(status_code=404, detail="Bill of Lading not found")
    locked_json_mutation(BL_FILE, [], replace_bl, list)
    return RedirectResponse(url="/bl-list", status_code=303)


@router.get("/delete-bl/{bl_no}")
def delete_bl(bl_no: str):
    return identifier_delete_confirmation("Bill of Lading", "Bill of Lading", bl_no, BL_FILE, "bl_no", f"/delete-bl/{bl_no}", "/bl-list")

@router.post("/delete-bl/{bl_no}")
def confirm_delete_bl(bl_no: str):
    return confirmed_identifier_delete("Bill of Lading", "Bill of Lading", bl_no, BL_FILE, "bl_no", f"/delete-bl/{bl_no}", "/bl-list", "/bl-list")


@router.get("/bl-data/{bl_no}")
def bl_data(bl_no: str):
    for record in load_bills_of_lading():
        if record.get("bl_no") == bl_no:
            return record
    raise HTTPException(status_code=404, detail="Bill of Lading not found")


@router.post("/bl/pdf")
def create_bl_pdf(payload: dict = Body(...)):
    bl_no = payload.get("bl_no") or "-"
    packing_no = payload.get("packing_no", "")
    invoice_no = payload.get("invoice_no", "")
    bl_date = payload.get("bl_date") or datetime.now().strftime("%Y-%m-%d")
    shipper = payload.get("shipper", "")
    consignee = payload.get("consignee", "")
    notify_party = payload.get("notify_party", "")
    vessel = payload.get("vessel", "")
    voyage_no = payload.get("voyage_no", "")
    port_of_loading = payload.get("port_of_loading", "")
    port_of_discharge = payload.get("port_of_discharge", "")
    place_of_delivery = payload.get("place_of_delivery", "")
    items = payload.get("items", [])

    total_carton = payload.get("total_carton") or format_number(numeric_total(items, "carton"))
    total_net_weight = payload.get("total_net_weight") or format_number(numeric_total(items, "net_weight"))
    total_gross_weight = payload.get("total_gross_weight") or format_number(numeric_total(items, "gross_weight"))

    buffer = BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4
    pdf.setTitle(f"Bill of Lading {bl_no}")

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

    def draw_header():
        pdf.setFillColor(colors.HexColor("#111827"))
        pdf.rect(0, height - 90, width, 90, fill=1, stroke=0)
        pdf.setFillColor(colors.white)
        pdf.setFont("Helvetica-Bold", 24)
        pdf.drawString(45, height - 55, "BILL OF LADING")
        pdf.setFont("Helvetica", 9)
        pdf.drawRightString(width - 45, height - 38, "Trade Paper AI")
        pdf.drawRightString(width - 45, height - 55, "Automated Trade Document")

        pdf.setFillColor(colors.black)
        pdf.setFont("Helvetica-Bold", 10)
        pdf.drawString(45, height - 118, f"B/L No: {bl_no}")
        pdf.drawString(45, height - 135, f"B/L Date: {bl_date}")
        pdf.drawString(45, height - 152, f"Packing No: {packing_no}")
        pdf.drawString(45, height - 169, f"Invoice No: {invoice_no}")

        pdf.setStrokeColor(colors.HexColor("#D1D5DB"))
        pdf.setFillColor(colors.HexColor("#F9FAFB"))
        pdf.roundRect(45, height - 265, 155, 78, 8, fill=1)
        pdf.roundRect(220, height - 265, 155, 78, 8, fill=1)
        pdf.roundRect(395, height - 265, 155, 78, 8, fill=1)

        pdf.setFillColor(colors.HexColor("#111827"))
        pdf.setFont("Helvetica-Bold", 9)
        pdf.drawString(58, height - 205, "SHIPPER")
        pdf.drawString(233, height - 205, "CONSIGNEE")
        pdf.drawString(408, height - 205, "NOTIFY PARTY")
        pdf.setFont("Helvetica", 8)
        pdf.drawString(58, height - 228, fit_text(shipper, 120))
        pdf.drawString(233, height - 228, fit_text(consignee, 120))
        pdf.drawString(408, height - 228, fit_text(notify_party, 120))

        pdf.setFillColor(colors.black)
        pdf.setFont("Helvetica-Bold", 8)
        pdf.drawString(45, height - 292, f"Vessel: {vessel}")
        pdf.drawString(185, height - 292, f"Voyage No: {voyage_no}")
        pdf.drawString(325, height - 292, f"Port of Loading: {port_of_loading}")
        pdf.drawString(45, height - 309, f"Port of Discharge: {port_of_discharge}")
        pdf.drawString(325, height - 309, f"Place of Delivery: {place_of_delivery}")

    def draw_table_header():
        header_y = height - 345
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

    def start_page():
        draw_header()
        return draw_table_header()

    def draw_footer():
        pdf.setFillColor(colors.black)
        pdf.setFont("Helvetica", 10)
        pdf.drawString(45, 115, "Authorized Signature:")
        pdf.line(170, 115, 330, 115)
        pdf.setFillColor(colors.HexColor("#6B7280"))
        pdf.setFont("Helvetica", 8)
        pdf.drawString(45, 60, "This document was generated by Trade Paper AI.")
        pdf.drawString(45, 45, "For trade documentation automation.")

    y = start_page()
    item_count = len(items)
    for index, item in enumerate(items, start=1):
        required_bottom = row_min_bottom + summary_h + summary_gap + row_h if index == item_count else row_min_bottom
        if y < required_bottom:
            pdf.showPage()
            y = start_page()
        pdf.rect(table_x, y, table_w, row_h, fill=0)
        pdf.drawString(52, y + 9, str(index))
        pdf.drawString(80, y + 9, fit_text(item.get("name", ""), 135))
        pdf.drawRightString(235, y + 9, str(item.get("quantity", "")))
        pdf.drawString(270, y + 9, str(item.get("hs_code", "")))
        pdf.drawRightString(370, y + 9, str(item.get("carton", "")))
        pdf.drawRightString(455, y + 9, str(item.get("net_weight", "")))
        pdf.drawRightString(540, y + 9, str(item.get("gross_weight", "")))
        y -= row_h

    summary_x = table_right - summary_w
    summary_top = y - summary_gap
    summary_bottom = summary_top - summary_h
    if summary_bottom < row_min_bottom:
        pdf.showPage()
        y = start_page()
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
    pdf.drawString(text_x, text_y - line_gap, f"Total Net Weight: {total_net_weight}")
    pdf.drawString(text_x, text_y - line_gap * 2, f"Total Gross Weight: {total_gross_weight}")

    draw_footer()
    pdf.showPage()
    pdf.save()
    buffer.seek(0)
    return Response(
        content=buffer.getvalue(),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{bl_no}.pdf"'},
    )


@router.get("/bl-pdf/{bl_no}")
def bl_pdf(bl_no: str):
    for record in load_bills_of_lading():
        if record.get("bl_no") == bl_no:
            return create_bl_pdf(record)
    return {"error": "Bill of Lading not found"}
