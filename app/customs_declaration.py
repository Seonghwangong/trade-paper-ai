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

CUSTOMS_FILE = data_path("customs_declarations.json")
SHIPMENT_FILE = data_path("shipments.json")
BOOKING_FILE = data_path("booking_confirmations.json")
INVOICE_FILE = data_path("invoices.json")
PACKING_FILE = data_path("packing_lists.json")
CONTAINER_FILE = data_path("containers.json")
BL_FILE = data_path("bills_of_lading.json")
PRODUCT_FILE = data_path("products.json")


def html_attr(value):
    return html_lib.escape(str(value or ""), quote=True)


def html_text(value):
    return html_lib.escape(str(value or ""))


def load_json(path, default):
    return load_json_strict(path, default, type(default) if isinstance(default, (list, dict)) else None)


def load_customs():
    return load_json(CUSTOMS_FILE, [])


def save_customs(records):
    atomic_write_json(CUSTOMS_FILE, records, list)


def load_shipments():
    return load_json(SHIPMENT_FILE, [])


def load_bookings():
    return load_json(BOOKING_FILE, [])


def load_invoices():
    return load_json(INVOICE_FILE, [])


def load_packing_lists():
    return load_json(PACKING_FILE, [])


def load_containers():
    return load_json(CONTAINER_FILE, [])


def load_bills_of_lading():
    return load_json(BL_FILE, [])


def load_products():
    return load_json(PRODUCT_FILE, [])


def validate_customs_links(shipment_no, booking_record_no, invoice_no, packing_no, container_record_no, bl_no):
    shipment = require_existing_reference("Shipment", shipment_no, load_shipments(), "shipment_no", required=True)
    booking = require_existing_reference("Booking", booking_record_no, load_bookings(), "booking_record_no")
    require_existing_reference("Invoice", invoice_no, load_invoices(), "invoice_no")
    packing = require_existing_reference("Packing List", packing_no, load_packing_lists(), "packing_no")
    container = require_existing_reference("Container", container_record_no, load_containers(), "container_record_no")
    bill = require_existing_reference("Bill of Lading", bl_no, load_bills_of_lading(), "bl_no")
    for field, actual, shipment_field in [
        ("Invoice", invoice_no, "invoice_no"), ("Packing List", packing_no, "packing_no"),
        ("Bill of Lading", bl_no, "bl_no"),
    ]:
        require_consistent_reference(field, actual, shipment.get(shipment_field, ""), "selected Shipment")
    for source, label in [(booking, "Booking"), (container, "Container")]:
        if source:
            require_consistent_reference("Shipment", source.get("shipment_no", ""), shipment_no, f"selected {label}")
    if packing:
        require_consistent_reference("Invoice", invoice_no, packing.get("invoice_no", ""), "selected Packing List")
    if bill:
        require_consistent_reference("Packing List", packing_no, bill.get("packing_no", ""), "selected Bill of Lading")


def next_customs_record_no(records):
    return next_identifier(records, "customs_record_no", "CD")
    numbers = [
        int(record.get("customs_record_no", "CD-000").split("-")[1])
        for record in records
        if record.get("customs_record_no", "").startswith("CD-")
    ]
    return f"CD-{max(numbers, default=0) + 1:03d}"


def find_record(records, key, value):
    if not value:
        return None
    for record in records:
        if record.get(key) == value:
            return record
    return None


def exists(path_records, key, value):
    return bool(find_record(path_records, key, value))


def doc_options(records, key):
    values = [str(record.get(key, "") or "") for record in records]
    return sorted({value for value in values if value}, reverse=True)


def select_html(name, selected, options, placeholder):
    parts = [f'<select name="{html_attr(name)}">']
    parts.append(f'<option value="">{html_text(placeholder)}</option>')
    for value in options:
        checked = " selected" if value == selected else ""
        parts.append(f'<option value="{html_attr(value)}"{checked}>{html_text(value)}</option>')
    parts.append("</select>")
    return "".join(parts)


def to_float(value):
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def format_number(value):
    try:
        number = float(value or 0)
        return f"{number:g}"
    except (TypeError, ValueError):
        return str(value or "")


def numeric_total(items, field):
    return sum(to_float(item.get(field, 0)) for item in items)


def item_key(item):
    return str(item.get("name", "") or "").strip().lower()


def hs_key(item):
    return str(item.get("hs_code", "") or "").strip().lower()


def match_item(target, candidates):
    name = item_key(target)
    hs_code = hs_key(target)
    for candidate in candidates:
        if name and item_key(candidate) == name:
            return candidate
    for candidate in candidates:
        if hs_code and hs_key(candidate) == hs_code:
            return candidate
    return None


def blank_payload():
    return {
        "customs_record_no": "",
        "customs_date": datetime.now().strftime("%Y-%m-%d"),
        "declaration_no": "",
        "shipment_no": "",
        "booking_record_no": "",
        "invoice_no": "",
        "packing_no": "",
        "container_record_no": "",
        "bl_no": "",
        "exporter": "",
        "consignee": "",
        "country_of_origin": "",
        "destination_country": "",
        "port_of_loading": "",
        "port_of_discharge": "",
        "vessel": "",
        "voyage_no": "",
        "container_no": "",
        "seal_no": "",
        "customs_office": "",
        "declaration_type": "Export",
        "incoterms": "",
        "currency": "",
        "total_invoice_value": "",
        "remarks": "",
        "items": [],
        "total_quantity": "",
        "total_net_weight": "",
        "total_gross_weight": "",
        "total_amount": "",
    }


def enrich_product_origins(payload):
    products = load_products()
    origins = []
    for item in payload.get("items", []):
        product = match_item(item, products)
        if product and not item.get("origin"):
            item["origin"] = product.get("origin", "")
        if item.get("origin"):
            origins.append(str(item.get("origin")).strip())
    unique = {origin for origin in origins if origin}
    if len(unique) == 1 and not payload.get("country_of_origin"):
        payload["country_of_origin"] = next(iter(unique))
    return payload


def recalc_totals(payload):
    items = payload.get("items", [])
    payload["total_quantity"] = format_number(numeric_total(items, "quantity"))
    payload["total_net_weight"] = payload.get("total_net_weight") or format_number(numeric_total(items, "net_weight"))
    payload["total_gross_weight"] = payload.get("total_gross_weight") or format_number(numeric_total(items, "gross_weight"))
    amount_total = numeric_total(items, "amount")
    payload["total_amount"] = payload.get("total_amount") or format_number(amount_total)
    payload["total_invoice_value"] = payload.get("total_invoice_value") or payload.get("total_amount", "")
    return payload


def copy_invoice_payload(payload, invoice_no):
    invoice = find_record(load_invoices(), "invoice_no", invoice_no)
    if not invoice:
        return payload
    items = []
    for item in invoice.get("items", []):
        qty = item.get("quantity", "")
        unit_price = item.get("unit_price", "")
        amount = item.get("amount", "")
        if amount == "":
            amount = format_number(to_float(qty) * to_float(unit_price))
        items.append({
            "name": item.get("name", ""),
            "hs_code": item.get("hs_code", ""),
            "quantity": qty,
            "unit_price": unit_price,
            "amount": amount,
            "origin": "",
            "net_weight": "",
            "gross_weight": "",
        })
    payload.update({
        "invoice_no": invoice.get("invoice_no", ""),
        "exporter": invoice.get("seller", ""),
        "consignee": invoice.get("buyer", ""),
        "currency": invoice.get("currency", ""),
        "items": items,
        "total_amount": invoice.get("total_amount", "") or format_number(numeric_total(items, "amount")),
        "total_invoice_value": invoice.get("total_amount", "") or format_number(numeric_total(items, "amount")),
    })
    return recalc_totals(enrich_product_origins(payload))


def copy_packing_payload(payload, packing_no):
    packing = find_record(load_packing_lists(), "packing_no", packing_no)
    if not packing:
        return payload
    payload["packing_no"] = packing.get("packing_no", "")
    payload["invoice_no"] = payload.get("invoice_no") or packing.get("invoice_no", "")
    packing_items = packing.get("items", [])
    if not payload.get("items"):
        payload["items"] = [
            {
                "name": item.get("name", ""),
                "hs_code": item.get("hs_code", ""),
                "quantity": item.get("quantity", ""),
                "unit_price": "",
                "amount": "",
                "origin": "",
                "net_weight": item.get("net_weight", ""),
                "gross_weight": item.get("gross_weight", ""),
            }
            for item in packing_items
        ]
    else:
        for item in payload["items"]:
            matched = match_item(item, packing_items)
            if matched:
                item["net_weight"] = item.get("net_weight") or matched.get("net_weight", "")
                item["gross_weight"] = item.get("gross_weight") or matched.get("gross_weight", "")
    payload["total_net_weight"] = packing.get("total_net_weight") or format_number(numeric_total(packing_items, "net_weight"))
    payload["total_gross_weight"] = packing.get("total_gross_weight") or format_number(numeric_total(packing_items, "gross_weight"))
    return recalc_totals(enrich_product_origins(payload))


def copy_booking_payload(payload, booking_record_no):
    booking = find_record(load_bookings(), "booking_record_no", booking_record_no)
    if not booking:
        return payload
    payload.update({
        "booking_record_no": booking.get("booking_record_no", ""),
        "shipment_no": payload.get("shipment_no") or booking.get("shipment_no", ""),
        "vessel": booking.get("vessel", ""),
        "voyage_no": booking.get("voyage_no", ""),
        "port_of_loading": booking.get("port_of_loading", ""),
        "port_of_discharge": booking.get("port_of_discharge", ""),
        "destination_country": payload.get("destination_country") or booking.get("place_of_delivery", ""),
    })
    return payload


def copy_container_payload(payload, container_record_no):
    container = find_record(load_containers(), "container_record_no", container_record_no)
    if not container:
        return payload
    payload.update({
        "container_record_no": container.get("container_record_no", ""),
        "shipment_no": payload.get("shipment_no") or container.get("shipment_no", ""),
        "packing_no": payload.get("packing_no") or container.get("packing_no", ""),
        "bl_no": payload.get("bl_no") or container.get("bl_no", ""),
        "container_no": container.get("container_no", ""),
        "seal_no": container.get("seal_no", ""),
        "vessel": container.get("vessel", ""),
        "voyage_no": container.get("voyage_no", ""),
        "port_of_loading": container.get("port_of_loading", ""),
        "port_of_discharge": container.get("port_of_discharge", ""),
    })
    return payload


def copy_bl_payload(payload, bl_no):
    bill = find_record(load_bills_of_lading(), "bl_no", bl_no)
    if not bill:
        return payload
    payload.update({
        "bl_no": bill.get("bl_no", ""),
        "exporter": payload.get("exporter") or bill.get("shipper", ""),
        "consignee": payload.get("consignee") or bill.get("consignee", ""),
        "vessel": bill.get("vessel", ""),
        "voyage_no": bill.get("voyage_no", ""),
        "port_of_loading": bill.get("port_of_loading", ""),
        "port_of_discharge": bill.get("port_of_discharge", ""),
        "destination_country": payload.get("destination_country") or bill.get("place_of_delivery", ""),
    })
    return payload


def payload_from_sources(shipment_no="", invoice_no="", packing_no="", booking_record_no="", container_record_no="", bl_no=""):
    payload = blank_payload()
    if shipment_no and exists(load_shipments(), "shipment_no", shipment_no):
        payload["shipment_no"] = shipment_no
    if invoice_no:
        payload = copy_invoice_payload(payload, invoice_no)
    if packing_no:
        payload = copy_packing_payload(payload, packing_no)
    if booking_record_no:
        payload = copy_booking_payload(payload, booking_record_no)
    if container_record_no:
        payload = copy_container_payload(payload, container_record_no)
    if bl_no:
        payload = copy_bl_payload(payload, bl_no)
    return recalc_totals(enrich_product_origins(payload))


def build_items(item_name, hs_code, quantity, unit_price, amount, origin, net_weight, gross_weight):
    items = []
    for i, name in enumerate(item_name):
        if not str(name or "").strip():
            continue
        qty = quantity[i] if i < len(quantity) else ""
        price = unit_price[i] if i < len(unit_price) else ""
        line_amount = amount[i] if i < len(amount) else ""
        if line_amount == "":
            line_amount = format_number(to_float(qty) * to_float(price))
        items.append({
            "name": name,
            "hs_code": hs_code[i] if i < len(hs_code) else "",
            "quantity": qty,
            "unit_price": price,
            "amount": line_amount,
            "origin": origin[i] if i < len(origin) else "",
            "net_weight": net_weight[i] if i < len(net_weight) else "",
            "gross_weight": gross_weight[i] if i < len(gross_weight) else "",
        })
    return items


def build_record(
    customs_record_no, customs_date, declaration_no, shipment_no, booking_record_no,
    invoice_no, packing_no, container_record_no, bl_no, exporter, consignee,
    country_of_origin, destination_country, port_of_loading, port_of_discharge,
    vessel, voyage_no, container_no, seal_no, customs_office, declaration_type,
    incoterms, currency, total_invoice_value, remarks, item_name, hs_code,
    quantity, unit_price, amount, origin, net_weight, gross_weight, total_quantity,
    total_net_weight, total_gross_weight, total_amount,
):
    items = build_items(item_name, hs_code, quantity, unit_price, amount, origin, net_weight, gross_weight)
    record = {
        "customs_record_no": customs_record_no,
        "customs_date": customs_date,
        "declaration_no": declaration_no,
        "shipment_no": shipment_no,
        "booking_record_no": booking_record_no,
        "invoice_no": invoice_no,
        "packing_no": packing_no,
        "container_record_no": container_record_no,
        "bl_no": bl_no,
        "exporter": exporter,
        "consignee": consignee,
        "country_of_origin": country_of_origin,
        "destination_country": destination_country,
        "port_of_loading": port_of_loading,
        "port_of_discharge": port_of_discharge,
        "vessel": vessel,
        "voyage_no": voyage_no,
        "container_no": container_no,
        "seal_no": seal_no,
        "customs_office": customs_office,
        "declaration_type": declaration_type,
        "incoterms": incoterms,
        "currency": currency,
        "total_invoice_value": total_invoice_value,
        "remarks": remarks,
        "items": items,
        "total_quantity": total_quantity,
        "total_net_weight": total_net_weight,
        "total_gross_weight": total_gross_weight,
        "total_amount": total_amount,
    }
    return recalc_totals(enrich_product_origins(record))


def build_item_rows(items):
    if not items:
        items = [{}]
    rows = ""
    for item in items:
        rows += f"""
<div class="item-row">
<input type="text" name="item_name" value="{html_attr(item.get('name', ''))}" placeholder="Item">
<input type="text" name="hs_code" value="{html_attr(item.get('hs_code', ''))}" placeholder="HS Code">
<input type="text" name="quantity" value="{html_attr(item.get('quantity', ''))}" placeholder="Qty" oninput="calculateTotals()">
<input type="text" name="unit_price" value="{html_attr(item.get('unit_price', ''))}" placeholder="Unit Price" oninput="calculateTotals()">
<input type="text" name="amount" value="{html_attr(item.get('amount', ''))}" placeholder="Amount" oninput="calculateTotals()">
<input type="text" name="origin" value="{html_attr(item.get('origin', ''))}" placeholder="Origin">
<input type="text" name="net_weight" value="{html_attr(item.get('net_weight', ''))}" placeholder="Net Weight" oninput="calculateTotals()">
<input type="text" name="gross_weight" value="{html_attr(item.get('gross_weight', ''))}" placeholder="Gross Weight" oninput="calculateTotals()">
<button class="remove" type="button" onclick="removeItem(this)">Remove Item</button>
</div>
"""
    return rows


def render_form(record, action, title, button_text, show_no=False):
    no_input = ""
    if show_no:
        no_input = f'<div class="field"><label>Customs Record No</label><input type="text" name="customs_record_no" value="{html_attr(record.get("customs_record_no", ""))}" readonly></div>'
    shipment_select = select_html("shipment_no", record.get("shipment_no", ""), doc_options(load_shipments(), "shipment_no"), "Select Shipment")
    booking_select = select_html("booking_record_no", record.get("booking_record_no", ""), doc_options(load_bookings(), "booking_record_no"), "Select Booking")
    invoice_select = select_html("invoice_no", record.get("invoice_no", ""), doc_options(load_invoices(), "invoice_no"), "Select Invoice")
    packing_select = select_html("packing_no", record.get("packing_no", ""), doc_options(load_packing_lists(), "packing_no"), "Select Packing")
    container_select = select_html("container_record_no", record.get("container_record_no", ""), doc_options(load_containers(), "container_record_no"), "Select Container")
    bl_select = select_html("bl_no", record.get("bl_no", ""), doc_options(load_bills_of_lading(), "bl_no"), "Select B/L")
    rows = build_item_rows(record.get("items", []))
    product_master_json = json.dumps(load_products(), ensure_ascii=False)

    html = """
<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><title>Customs Declaration</title><style>
body{font-family:Arial,sans-serif;background:#f3f4f6;padding:40px;color:#111827;}.container{max-width:1120px;margin:auto;background:white;padding:35px;border-radius:16px;box-shadow:0 12px 35px rgba(15,23,42,.08);}h1{text-align:center;font-size:46px;margin:8px 0 10px;}.sub{text-align:center;color:#6B7280;margin-bottom:35px;}.nav-row{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:25px;}.card{border:1px solid #E5E7EB;border-radius:16px;padding:25px;margin-bottom:25px;background:#fff;}.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px;}.record-links{column-gap:20px;row-gap:18px;align-items:start;}.field{display:flex;flex-direction:column;gap:8px;min-width:0;}.item-row{display:grid;grid-template-columns:1.25fr .9fr .65fr .8fr .8fr .8fr .85fr .85fr;gap:12px;border:1px solid #E5E7EB;border-radius:14px;padding:18px;margin-bottom:16px;background:#F9FAFB;}label{display:block;font-weight:bold;margin:0 0 7px;color:#374151;}.field label{margin:0;line-height:1.2;}input,select,textarea{width:100%;padding:14px;border:1px solid #D1D5DB;border-radius:10px;font-size:16px;box-sizing:border-box;background:white;}.record-links input,.record-links select{height:48px;padding:0 14px;}textarea{min-height:100px;resize:vertical;}button{padding:15px 18px;background:#111827;color:white;border:none;border-radius:12px;font-size:16px;cursor:pointer;}.small{min-width:170px;}.full{width:100%;margin-top:10px;font-size:18px;}.add{width:100%;background:#374151;margin-bottom:20px;}.remove{grid-column:1/-1;width:100%;background:#991B1B;}.totals{display:flex;gap:18px;flex-wrap:wrap;font-size:17px;font-weight:bold;color:#111827;margin:8px 0 20px;}@media(max-width:900px){body{padding:18px}.grid,.item-row{grid-template-columns:1fr}h1{font-size:34px}}
</style></head><body><div class="container"><div class="nav-row"><a href="/"><button class="small" type="button">Dashboard</button></a><a href="/customs-list"><button class="small" type="button">Customs List</button></a></div><h1>__TITLE__</h1><p class="sub">Prepare export customs declarations from invoice, packing, booking, container, and B/L data</p><form action="__ACTION__" method="post">
<div class="card"><h2>Record Links</h2><div class="grid record-links">__NO_INPUT__<div class="field"><label>Customs Date</label><input type="date" name="customs_date" value="__CUSTOMS_DATE__"></div><div class="field"><label>Shipment</label>__SHIPMENT_SELECT__</div><div class="field"><label>Booking</label>__BOOKING_SELECT__</div><div class="field"><label>Commercial Invoice</label>__INVOICE_SELECT__</div><div class="field"><label>Packing List</label>__PACKING_SELECT__</div><div class="field"><label>Container</label>__CONTAINER_SELECT__</div><div class="field"><label>Bill of Lading</label>__BL_SELECT__</div></div></div>
<div class="card"><h2>Customs Declaration Information</h2><div class="grid"><div><label>Declaration No</label><input type="text" name="declaration_no" value="__DECLARATION_NO__"></div><div><label>Declaration Type</label><input type="text" name="declaration_type" value="__DECLARATION_TYPE__"></div><div><label>Customs Office</label><input type="text" name="customs_office" value="__CUSTOMS_OFFICE__"></div><div><label>Incoterms</label><input type="text" name="incoterms" value="__INCOTERMS__"></div><div><label>Currency</label><input type="text" name="currency" value="__CURRENCY__"></div><div><label>Total Invoice Value</label><input type="text" name="total_invoice_value" value="__TOTAL_INVOICE_VALUE__"></div></div></div>
<div class="card"><h2>Exporter / Consignee</h2><div class="grid"><div><label>Exporter</label><input type="text" name="exporter" value="__EXPORTER__"></div><div><label>Consignee</label><input type="text" name="consignee" value="__CONSIGNEE__"></div><div><label>Country of Origin</label><input type="text" name="country_of_origin" value="__COUNTRY_OF_ORIGIN__"></div><div><label>Destination Country</label><input type="text" name="destination_country" value="__DESTINATION_COUNTRY__"></div></div></div>
<div class="card"><h2>Transport / Container Information</h2><div class="grid"><div><label>Vessel</label><input type="text" name="vessel" value="__VESSEL__"></div><div><label>Voyage No</label><input type="text" name="voyage_no" value="__VOYAGE_NO__"></div><div><label>Port of Loading</label><input type="text" name="port_of_loading" value="__PORT_OF_LOADING__"></div><div><label>Port of Discharge</label><input type="text" name="port_of_discharge" value="__PORT_OF_DISCHARGE__"></div><div><label>Container No</label><input type="text" name="container_no" value="__CONTAINER_NO__"></div><div><label>Seal No</label><input type="text" name="seal_no" value="__SEAL_NO__"></div></div></div>
<div class="card"><h2>Goods Declaration</h2><div id="items">__ITEM_ROWS__</div><button class="add" type="button" onclick="addItem()">+ Add Goods Item</button><input type="hidden" id="total_quantity" name="total_quantity" value="__TOTAL_QUANTITY__"><input type="hidden" id="total_net_weight" name="total_net_weight" value="__TOTAL_NET_WEIGHT__"><input type="hidden" id="total_gross_weight" name="total_gross_weight" value="__TOTAL_GROSS_WEIGHT__"><input type="hidden" id="total_amount" name="total_amount" value="__TOTAL_AMOUNT__"><div class="totals"><span>Total Qty: <span id="qtyText">__TOTAL_QUANTITY__</span></span><span>Total Net: <span id="netText">__TOTAL_NET_WEIGHT__</span></span><span>Total Gross: <span id="grossText">__TOTAL_GROSS_WEIGHT__</span></span><span>Total Amount: <span id="amountText">__TOTAL_AMOUNT__</span></span></div></div>
<div class="card"><h2>Remarks</h2><textarea name="remarks" placeholder="Remarks">__REMARKS__</textarea></div><button class="full" type="submit">__BUTTON_TEXT__</button></form></div>
<script>
const PRODUCT_MASTER = __PRODUCT_MASTER__;
function itemRowHtml(item = {}){
  const amount = item.amount || ((parseFloat(item.quantity) || 0) * (parseFloat(item.unit_price) || 0) || "");
  return `<div class="item-row">
    <input type="text" name="item_name" value="${escapeAttr(item.name || "")}" placeholder="Item">
    <input type="text" name="hs_code" value="${escapeAttr(item.hs_code || "")}" placeholder="HS Code">
    <input type="text" name="quantity" value="${escapeAttr(item.quantity || "")}" placeholder="Qty" oninput="calculateTotals()">
    <input type="text" name="unit_price" value="${escapeAttr(item.unit_price || "")}" placeholder="Unit Price" oninput="calculateTotals()">
    <input type="text" name="amount" value="${escapeAttr(amount)}" placeholder="Amount" oninput="calculateTotals()">
    <input type="text" name="origin" value="${escapeAttr(item.origin || "")}" placeholder="Origin">
    <input type="text" name="net_weight" value="${escapeAttr(item.net_weight || "")}" placeholder="Net Weight" oninput="calculateTotals()">
    <input type="text" name="gross_weight" value="${escapeAttr(item.gross_weight || "")}" placeholder="Gross Weight" oninput="calculateTotals()">
    <button class="remove" type="button" onclick="removeItem(this)">Remove Item</button>
  </div>`;
}
function escapeAttr(value){
  return String(value ?? "").replaceAll("&","&amp;").replaceAll('"',"&quot;").replaceAll("<","&lt;").replaceAll(">","&gt;");
}
function addItem(){
  document.getElementById("items").insertAdjacentHTML("beforeend", itemRowHtml({}));
}
function removeItem(btn){
  btn.closest(".item-row").remove();
  calculateTotals();
}
function values(name){
  return Array.from(document.querySelectorAll(`[name="${name}"]`));
}
function setFirst(name, value){
  const el = document.querySelector(`[name="${name}"]`);
  if(el) el.value = value || "";
}
function sum(name){
  return values(name).reduce((s,i)=>s+(parseFloat(i.value)||0),0);
}
function fmt(v){
  return Number.isInteger(v) ? String(v) : String(Number(v.toFixed(3)));
}
function rowData(row){
  return {
    name: row.querySelector('[name="item_name"]')?.value || "",
    hs_code: row.querySelector('[name="hs_code"]')?.value || "",
    quantity: row.querySelector('[name="quantity"]')?.value || "",
    unit_price: row.querySelector('[name="unit_price"]')?.value || "",
    amount: row.querySelector('[name="amount"]')?.value || "",
    origin: row.querySelector('[name="origin"]')?.value || "",
    net_weight: row.querySelector('[name="net_weight"]')?.value || "",
    gross_weight: row.querySelector('[name="gross_weight"]')?.value || ""
  };
}
function currentItems(){
  return Array.from(document.querySelectorAll("#items .item-row")).map(rowData);
}
function itemNameKey(item){
  return String(item.name || "").trim().toLowerCase();
}
function hsKey(item){
  return String(item.hs_code || "").trim().toLowerCase();
}
function productOriginFor(item){
  const name = itemNameKey(item);
  const hs = hsKey(item);
  const product = PRODUCT_MASTER.find(product => name && itemNameKey(product) === name)
    || PRODUCT_MASTER.find(product => hs && hsKey(product) === hs);
  return product?.origin || "";
}
function enrichItemsWithOrigin(items){
  return items.map(item => {
    const origin = item.origin || productOriginFor(item);
    return {...item, origin};
  });
}
function updateCountryOfOriginFromRows(){
  const origins = values("origin").map(input => input.value.trim()).filter(Boolean);
  const unique = Array.from(new Set(origins));
  const country = document.querySelector('[name="country_of_origin"]');
  if(!country) return;
  country.value = unique.length === 1 ? unique[0] : "";
}
function matchItem(target, candidates){
  const name = itemNameKey(target);
  const hs = hsKey(target);
  return candidates.find(item => name && itemNameKey(item) === name)
      || candidates.find(item => hs && hsKey(item) === hs);
}
function renderItems(items){
  const enrichedItems = enrichItemsWithOrigin(items.length ? items : [{}]);
  document.getElementById("items").innerHTML = enrichedItems.map(itemRowHtml).join("");
  updateCountryOfOriginFromRows();
  calculateTotals();
}
function calculateTotals(){
  values("amount").forEach((a,i)=>{
    if(!a.value){
      const q=parseFloat(values("quantity")[i]?.value)||0;
      const p=parseFloat(values("unit_price")[i]?.value)||0;
      if(q||p) a.value=fmt(q*p);
    }
  });
  const qty=sum("quantity"), net=sum("net_weight"), gross=sum("gross_weight"), amt=sum("amount");
  document.getElementById("total_quantity").value=fmt(qty);
  document.getElementById("total_net_weight").value=fmt(net);
  document.getElementById("total_gross_weight").value=fmt(gross);
  document.getElementById("total_amount").value=fmt(amt);
  document.getElementById("qtyText").textContent=fmt(qty);
  document.getElementById("netText").textContent=fmt(net);
  document.getElementById("grossText").textContent=fmt(gross);
  document.getElementById("amountText").textContent=fmt(amt);
}
async function fetchSource(url){
  const response = await fetch(url);
  if(!response.ok) throw new Error(`${response.status} ${response.statusText}`);
  return response.json();
}
async function loadInvoicePrefill(invoiceNo){
  if(!invoiceNo) return;
  try{
    const invoice = await fetchSource(`/customs-source/invoice/${encodeURIComponent(invoiceNo)}`);
    const existingItems = currentItems();
    setFirst("exporter", invoice.seller || "");
    setFirst("consignee", invoice.buyer || "");
    if(invoice.currency) setFirst("currency", invoice.currency);
    const invoiceItems = (invoice.items || []).map(item => {
      const existing = matchItem(item, existingItems) || {};
      return {
        name: item.name || "",
        hs_code: item.hs_code || "",
        quantity: item.quantity || "",
        unit_price: item.unit_price || "",
        amount: item.amount || ((parseFloat(item.quantity) || 0) * (parseFloat(item.unit_price) || 0) || ""),
        origin: productOriginFor(item),
        net_weight: existing.net_weight || "",
        gross_weight: existing.gross_weight || ""
      };
    });
    renderItems(invoiceItems);
    const totalAmount = invoice.total_amount || document.getElementById("total_amount").value;
    setFirst("total_invoice_value", totalAmount);
    document.getElementById("total_amount").value = totalAmount;
    document.getElementById("amountText").textContent = totalAmount;
  }catch(error){
    console.error(`Customs invoice prefill failed for ${invoiceNo}:`, error);
  }
}
async function loadPackingEnrichment(packingNo){
  if(!packingNo) return;
  try{
    const packing = await fetchSource(`/customs-source/packing/${encodeURIComponent(packingNo)}`);
    if(packing.invoice_no) setFirst("invoice_no", packing.invoice_no);
    const packingItems = packing.items || [];
    const merged = currentItems();
    if(merged.length === 0 || (merged.length === 1 && !merged[0].name && !merged[0].hs_code)){
      renderItems(packingItems.map(item => ({
        name: item.name || "",
        hs_code: item.hs_code || "",
        quantity: item.quantity || "",
        unit_price: "",
        amount: "",
        origin: productOriginFor(item),
        net_weight: item.net_weight || "",
        gross_weight: item.gross_weight || ""
      })));
    }else{
      merged.forEach(item => {
        const match = matchItem(item, packingItems);
        if(match){
          item.net_weight = item.net_weight || match.net_weight || "";
          item.gross_weight = item.gross_weight || match.gross_weight || "";
        }
      });
      renderItems(merged);
    }
    if(packing.total_net_weight) document.getElementById("total_net_weight").value = packing.total_net_weight;
    if(packing.total_gross_weight) document.getElementById("total_gross_weight").value = packing.total_gross_weight;
    if(packing.total_net_weight) document.getElementById("netText").textContent = packing.total_net_weight;
    if(packing.total_gross_weight) document.getElementById("grossText").textContent = packing.total_gross_weight;
  }catch(error){
    console.error(`Customs packing enrichment failed for ${packingNo}:`, error);
  }
}
function setIfPresent(name, value){
  if(value) setFirst(name, value);
}
function setLinkedSelect(name, value){
  if(!value) return;
  const select = document.querySelector(`select[name="${name}"]`);
  if(!select) return;
  const hasOption = Array.from(select.options).some(option => option.value === value);
  if(hasOption) select.value = value;
}
async function loadBookingEnrichment(bookingRecordNo){
  if(!bookingRecordNo) return;
  try{
    const booking = await fetchSource(`/customs-source/booking/${encodeURIComponent(bookingRecordNo)}`);
    setLinkedSelect("shipment_no", booking.shipment_no || "");
    setLinkedSelect("packing_no", booking.packing_no || "");
    setLinkedSelect("bl_no", booking.bl_no || "");
    setIfPresent("vessel", booking.vessel || "");
    setIfPresent("voyage_no", booking.voyage_no || "");
    setIfPresent("port_of_loading", booking.port_of_loading || "");
    setIfPresent("port_of_discharge", booking.port_of_discharge || "");
    setIfPresent("destination_country", booking.place_of_delivery || "");
  }catch(error){
    console.error(`Customs booking enrichment failed for ${bookingRecordNo}:`, error);
  }
}
async function loadContainerEnrichment(containerRecordNo){
  if(!containerRecordNo) return;
  try{
    const container = await fetchSource(`/customs-source/container/${encodeURIComponent(containerRecordNo)}`);
    setLinkedSelect("shipment_no", container.shipment_no || "");
    setLinkedSelect("packing_no", container.packing_no || "");
    setLinkedSelect("bl_no", container.bl_no || "");
    setIfPresent("container_no", container.container_no || "");
    setIfPresent("seal_no", container.seal_no || "");
    setIfPresent("vessel", container.vessel || "");
    setIfPresent("voyage_no", container.voyage_no || "");
    setIfPresent("port_of_loading", container.port_of_loading || "");
    setIfPresent("port_of_discharge", container.port_of_discharge || "");
    setIfPresent("destination_country", container.place_of_delivery || "");
  }catch(error){
    console.error(`Customs container enrichment failed for ${containerRecordNo}:`, error);
  }
}
document.querySelector('[name="invoice_no"]')?.addEventListener("change", event => {
  loadInvoicePrefill(event.target.value);
});
document.querySelector('[name="packing_no"]')?.addEventListener("change", event => {
  loadPackingEnrichment(event.target.value);
});
document.querySelector('[name="booking_record_no"]')?.addEventListener("change", event => {
  loadBookingEnrichment(event.target.value);
});
document.querySelector('[name="container_record_no"]')?.addEventListener("change", event => {
  loadContainerEnrichment(event.target.value);
});
calculateTotals();
</script></body></html>
"""
    replacements = {
        "__TITLE__": html_text(title), "__ACTION__": html_attr(action), "__NO_INPUT__": no_input,
        "__CUSTOMS_DATE__": html_attr(record.get("customs_date", "")), "__SHIPMENT_SELECT__": shipment_select,
        "__BOOKING_SELECT__": booking_select, "__INVOICE_SELECT__": invoice_select, "__PACKING_SELECT__": packing_select,
        "__CONTAINER_SELECT__": container_select, "__BL_SELECT__": bl_select, "__DECLARATION_NO__": html_attr(record.get("declaration_no", "")),
        "__DECLARATION_TYPE__": html_attr(record.get("declaration_type", "")), "__CUSTOMS_OFFICE__": html_attr(record.get("customs_office", "")),
        "__INCOTERMS__": html_attr(record.get("incoterms", "")), "__CURRENCY__": html_attr(record.get("currency", "")),
        "__TOTAL_INVOICE_VALUE__": html_attr(record.get("total_invoice_value", "")), "__EXPORTER__": html_attr(record.get("exporter", "")),
        "__CONSIGNEE__": html_attr(record.get("consignee", "")), "__COUNTRY_OF_ORIGIN__": html_attr(record.get("country_of_origin", "")),
        "__DESTINATION_COUNTRY__": html_attr(record.get("destination_country", "")), "__VESSEL__": html_attr(record.get("vessel", "")),
        "__VOYAGE_NO__": html_attr(record.get("voyage_no", "")), "__PORT_OF_LOADING__": html_attr(record.get("port_of_loading", "")),
        "__PORT_OF_DISCHARGE__": html_attr(record.get("port_of_discharge", "")), "__CONTAINER_NO__": html_attr(record.get("container_no", "")),
        "__SEAL_NO__": html_attr(record.get("seal_no", "")), "__ITEM_ROWS__": rows,
        "__TOTAL_QUANTITY__": html_attr(record.get("total_quantity", "")), "__TOTAL_NET_WEIGHT__": html_attr(record.get("total_net_weight", "")),
        "__TOTAL_GROSS_WEIGHT__": html_attr(record.get("total_gross_weight", "")), "__TOTAL_AMOUNT__": html_attr(record.get("total_amount", "")),
        "__REMARKS__": html_text(record.get("remarks", "")), "__BUTTON_TEXT__": html_text(button_text),
        "__PRODUCT_MASTER__": product_master_json,
    }
    for key, value in replacements.items():
        html = html.replace(key, value)
    return HTMLResponse(html)


@router.get("/customs-list", response_class=HTMLResponse)
def customs_list(search: str = ""):
    records = sorted(load_customs(), key=lambda r: r.get("customs_record_no", ""), reverse=True)
    if search:
        term = search.lower()
        records = [r for r in records if any(term in str(r.get(k, "")).lower() for k in ["customs_record_no", "declaration_no", "shipment_no", "invoice_no", "packing_no", "container_record_no", "bl_no", "destination_country"])]
    rows = ""
    for r in records:
        no = r.get("customs_record_no", "")
        rows += f"""<tr><td>{html_text(no)}</td><td>{html_text(r.get('declaration_no',''))}</td><td>{html_text(r.get('customs_date',''))}</td><td>{html_text(r.get('shipment_no',''))}</td><td>{html_text(r.get('invoice_no',''))}</td><td>{html_text(r.get('packing_no',''))}</td><td>{html_text(r.get('container_record_no',''))}</td><td>{html_text(r.get('bl_no',''))}</td><td>{html_text(r.get('destination_country',''))}</td><td><a class="link" href="/customs/{html_attr(no)}">View</a></td><td><a class="link" href="/customs-pdf/{html_attr(no)}">PDF</a></td><td><a class="link" href="/edit-customs/{html_attr(no)}">Edit</a></td><td><a class="danger" href="/delete-customs/{html_attr(no)}">Delete</a></td></tr>"""
    html = f"""<!DOCTYPE html><html><head><meta charset="UTF-8"><title>Customs Declarations</title><style>body{{font-family:Arial,sans-serif;background:#f3f4f6;padding:40px;color:#111827;}}.container{{width:94%;margin:auto;}}h1{{text-align:center;font-size:46px;margin:8px 0 10px;}}.sub{{text-align:center;color:#6B7280;margin-bottom:28px;}}.toolbar{{display:flex;justify-content:space-between;gap:12px;align-items:center;flex-wrap:wrap;margin-bottom:20px;}}.nav,.search{{display:flex;gap:12px;flex-wrap:wrap;}}button,.btn{{display:inline-block;padding:13px 18px;background:#111827;color:white;border:none;border-radius:12px;text-decoration:none;font-size:15px;cursor:pointer;}}.reset{{background:#6B7280;}}input{{padding:13px;border:1px solid #D1D5DB;border-radius:10px;font-size:15px;min-width:260px;}}.count{{font-weight:bold;color:#374151;margin-bottom:14px;}}.table-wrap{{background:white;border:1px solid #E5E7EB;border-radius:16px;overflow:auto;box-shadow:0 12px 35px rgba(15,23,42,.08);}}table{{width:100%;border-collapse:collapse;min-width:1240px;}}th{{background:#111827;color:white;text-align:left;padding:14px;font-size:14px;}}td{{padding:14px;border-bottom:1px solid #E5E7EB;font-size:14px;}}.link{{background:#111827;color:white;padding:8px 11px;border-radius:9px;text-decoration:none;}}.danger{{background:#991B1B;color:white;padding:8px 11px;border-radius:9px;text-decoration:none;}}</style></head><body><div class="container"><h1>Customs Declarations</h1><p class="sub">Manage export customs declarations</p><div class="toolbar"><div class="nav"><a class="btn" href="/">Dashboard</a><a class="btn" href="/customs-form">+ New Customs</a></div><form class="search" action="/customs-list" method="get"><input type="text" name="search" value="{html_attr(search)}" placeholder="Search declaration, shipment, invoice, packing"><button type="submit">Search</button><a class="btn reset" href="/customs-list">Reset</a></form></div><div class="count">Total Customs Declarations: {len(records)}</div><div class="table-wrap"><table><thead><tr><th>Customs Record No</th><th>Declaration No</th><th>Customs Date</th><th>Shipment No</th><th>Invoice No</th><th>Packing No</th><th>Container No</th><th>B/L No</th><th>Destination Country</th><th>View</th><th>PDF</th><th>Edit</th><th>Delete</th></tr></thead><tbody>{rows}</tbody></table></div></div></body></html>"""
    return HTMLResponse(html)


@router.get("/customs-form", response_class=HTMLResponse)
def customs_form(shipment_no: str = "", invoice_no: str = "", packing_no: str = "", booking_record_no: str = "", container_record_no: str = "", bl_no: str = ""):
    record = payload_from_sources(shipment_no, invoice_no, packing_no, booking_record_no, container_record_no, bl_no)
    record["customs_record_no"] = next_customs_record_no(load_customs())
    return render_form(record, "/customs", "New Customs Declaration", "Save Customs Declaration", show_no=True)


@router.get("/customs-source/invoice/{invoice_no}")
def customs_source_invoice(invoice_no: str):
    invoice = find_record(load_invoices(), "invoice_no", invoice_no)
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")
    return invoice


@router.get("/customs-source/packing/{packing_no}")
def customs_source_packing(packing_no: str):
    packing = find_record(load_packing_lists(), "packing_no", packing_no)
    if not packing:
        raise HTTPException(status_code=404, detail="Packing list not found")
    return packing


@router.get("/customs-source/booking/{booking_record_no}")
def customs_source_booking(booking_record_no: str):
    booking = find_record(load_bookings(), "booking_record_no", booking_record_no)
    if not booking:
        raise HTTPException(status_code=404, detail="Booking confirmation not found")
    return booking


@router.get("/customs-source/container/{container_record_no}")
def customs_source_container(container_record_no: str):
    container = find_record(load_containers(), "container_record_no", container_record_no)
    if not container:
        raise HTTPException(status_code=404, detail="Container record not found")
    return container


@router.post("/customs")
def save_customs_record(
    customs_date: str = Form(""), declaration_no: str = Form(""), shipment_no: str = Form(""), booking_record_no: str = Form(""), invoice_no: str = Form(""), packing_no: str = Form(""), container_record_no: str = Form(""), bl_no: str = Form(""), exporter: str = Form(""), consignee: str = Form(""), country_of_origin: str = Form(""), destination_country: str = Form(""), port_of_loading: str = Form(""), port_of_discharge: str = Form(""), vessel: str = Form(""), voyage_no: str = Form(""), container_no: str = Form(""), seal_no: str = Form(""), customs_office: str = Form(""), declaration_type: str = Form("Export"), incoterms: str = Form(""), currency: str = Form(""), total_invoice_value: str = Form(""), remarks: str = Form(""), item_name: List[str] = Form([]), hs_code: List[str] = Form([]), quantity: List[str] = Form([]), unit_price: List[str] = Form([]), amount: List[str] = Form([]), origin: List[str] = Form([]), net_weight: List[str] = Form([]), gross_weight: List[str] = Form([]), total_quantity: str = Form(""), total_net_weight: str = Form(""), total_gross_weight: str = Form(""), total_amount: str = Form("")
):
    validate_customs_links(shipment_no, booking_record_no, invoice_no, packing_no, container_record_no, bl_no)
    declaration_no = require_text("Declaration number", declaration_no)
    exporter = require_text("Exporter", exporter)
    consignee = require_text("Consignee", consignee)
    def add_customs(records):
        record = build_record(next_identifier(records, "customs_record_no", "CD"), customs_date, declaration_no, shipment_no, booking_record_no, invoice_no, packing_no, container_record_no, bl_no, exporter, consignee, country_of_origin, destination_country, port_of_loading, port_of_discharge, vessel, voyage_no, container_no, seal_no, customs_office, declaration_type, incoterms, currency, total_invoice_value, remarks, item_name, hs_code, quantity, unit_price, amount, origin, net_weight, gross_weight, total_quantity, total_net_weight, total_gross_weight, total_amount)
        records.append(record)
    locked_json_mutation(CUSTOMS_FILE, [], add_customs, list)
    return RedirectResponse("/customs-list", status_code=303)


@router.get("/edit-customs/{customs_record_no}", response_class=HTMLResponse)
def edit_customs(customs_record_no: str):
    record = find_record(load_customs(), "customs_record_no", customs_record_no)
    if not record:
        raise HTTPException(status_code=404, detail="Customs declaration not found")
    return render_form(record, f"/update-customs/{html_attr(customs_record_no)}", "Edit Customs Declaration", "Update Customs Declaration", show_no=True)


@router.post("/update-customs/{customs_record_no}")
def update_customs(
    customs_record_no: str, customs_date: str = Form(""), declaration_no: str = Form(""), shipment_no: str = Form(""), booking_record_no: str = Form(""), invoice_no: str = Form(""), packing_no: str = Form(""), container_record_no: str = Form(""), bl_no: str = Form(""), exporter: str = Form(""), consignee: str = Form(""), country_of_origin: str = Form(""), destination_country: str = Form(""), port_of_loading: str = Form(""), port_of_discharge: str = Form(""), vessel: str = Form(""), voyage_no: str = Form(""), container_no: str = Form(""), seal_no: str = Form(""), customs_office: str = Form(""), declaration_type: str = Form("Export"), incoterms: str = Form(""), currency: str = Form(""), total_invoice_value: str = Form(""), remarks: str = Form(""), item_name: List[str] = Form([]), hs_code: List[str] = Form([]), quantity: List[str] = Form([]), unit_price: List[str] = Form([]), amount: List[str] = Form([]), origin: List[str] = Form([]), net_weight: List[str] = Form([]), gross_weight: List[str] = Form([]), total_quantity: str = Form(""), total_net_weight: str = Form(""), total_gross_weight: str = Form(""), total_amount: str = Form("")
):
    validate_customs_links(shipment_no, booking_record_no, invoice_no, packing_no, container_record_no, bl_no)
    declaration_no = require_text("Declaration number", declaration_no)
    exporter = require_text("Exporter", exporter)
    consignee = require_text("Consignee", consignee)
    def replace_customs(records):
        for index, record in enumerate(records):
            if record.get("customs_record_no") != customs_record_no:
                continue
            records[index] = build_record(customs_record_no, customs_date, declaration_no, shipment_no, booking_record_no, invoice_no, packing_no, container_record_no, bl_no, exporter, consignee, country_of_origin, destination_country, port_of_loading, port_of_discharge, vessel, voyage_no, container_no, seal_no, customs_office, declaration_type, incoterms, currency, total_invoice_value, remarks, item_name, hs_code, quantity, unit_price, amount, origin, net_weight, gross_weight, total_quantity, total_net_weight, total_gross_weight, total_amount)
            return
        raise HTTPException(status_code=404, detail="Customs declaration not found")
    locked_json_mutation(CUSTOMS_FILE, [], replace_customs, list)
    return RedirectResponse("/customs-list", status_code=303)


@router.get("/delete-customs/{customs_record_no}")
def delete_customs(customs_record_no: str):
    return identifier_delete_confirmation("Customs Declaration", "Customs Declaration", customs_record_no, CUSTOMS_FILE, "customs_record_no", f"/delete-customs/{customs_record_no}", "/customs-list")

@router.post("/delete-customs/{customs_record_no}")
def confirm_delete_customs(customs_record_no: str):
    return confirmed_identifier_delete("Customs Declaration", "Customs Declaration", customs_record_no, CUSTOMS_FILE, "customs_record_no", f"/delete-customs/{customs_record_no}", "/customs-list", "/customs-list")


@router.get("/customs-data/{customs_record_no}")
def customs_data(customs_record_no: str):
    record = find_record(load_customs(), "customs_record_no", customs_record_no)
    if not record:
        raise HTTPException(status_code=404, detail="Customs declaration not found")
    return record


def status_card(label, value, exists_value, pdf="", edit="", detail=""):
    if value and exists_value:
        links = ""
        if detail:
            links += f'<a href="{html_attr(detail)}">View</a>'
        if pdf:
            links += f'<a href="{html_attr(pdf)}">PDF</a>'
        if edit:
            links += f'<a href="{html_attr(edit)}">Edit</a>'
        return f'<div class="mini"><b>{html_text(label)}</b><span>{html_text(value)}</span><em class="ok">Linked</em><div class="actions">{links}</div></div>'
    return f'<div class="mini"><b>{html_text(label)}</b><span>-</span><em class="bad">Missing</em></div>'


@router.get("/customs/{customs_record_no}", response_class=HTMLResponse)
def customs_detail(customs_record_no: str):
    record = find_record(load_customs(), "customs_record_no", customs_record_no)
    if not record:
        raise HTTPException(status_code=404, detail="Customs declaration not found")
    rows = "".join(f"<tr><td>{i}</td><td>{html_text(item.get('name',''))}</td><td>{html_text(item.get('hs_code',''))}</td><td>{html_text(item.get('quantity',''))}</td><td>{html_text(item.get('unit_price',''))}</td><td>{html_text(item.get('amount',''))}</td><td>{html_text(item.get('origin',''))}</td><td>{html_text(item.get('net_weight',''))}</td><td>{html_text(item.get('gross_weight',''))}</td></tr>" for i, item in enumerate(record.get("items", []), 1))
    cards = (
        status_card("Shipment", record.get("shipment_no", ""), exists(load_shipments(), "shipment_no", record.get("shipment_no", "")), detail=f"/shipment/{record.get('shipment_no','')}" if record.get("shipment_no") else "")
        + status_card("Booking", record.get("booking_record_no", ""), exists(load_bookings(), "booking_record_no", record.get("booking_record_no", "")), detail=f"/booking/{record.get('booking_record_no','')}" if record.get("booking_record_no") else "", pdf=f"/booking-pdf/{record.get('booking_record_no','')}" if record.get("booking_record_no") else "")
        + status_card("Invoice", record.get("invoice_no", ""), exists(load_invoices(), "invoice_no", record.get("invoice_no", "")), pdf=f"/invoice-pdf/{record.get('invoice_no','')}" if record.get("invoice_no") else "", edit=f"/edit-invoice/{record.get('invoice_no','')}" if record.get("invoice_no") else "")
        + status_card("Packing", record.get("packing_no", ""), exists(load_packing_lists(), "packing_no", record.get("packing_no", "")), pdf=f"/packing-list-pdf/{record.get('packing_no','')}" if record.get("packing_no") else "", edit=f"/edit-packing/{record.get('packing_no','')}" if record.get("packing_no") else "")
        + status_card("Container", record.get("container_record_no", ""), exists(load_containers(), "container_record_no", record.get("container_record_no", "")), detail=f"/container/{record.get('container_record_no','')}" if record.get("container_record_no") else "", pdf=f"/container-pdf/{record.get('container_record_no','')}" if record.get("container_record_no") else "")
        + status_card("B/L", record.get("bl_no", ""), exists(load_bills_of_lading(), "bl_no", record.get("bl_no", "")), pdf=f"/bl-pdf/{record.get('bl_no','')}" if record.get("bl_no") else "", edit=f"/edit-bl/{record.get('bl_no','')}" if record.get("bl_no") else "")
    )
    html = f"""<!DOCTYPE html><html><head><meta charset="UTF-8"><title>{html_text(customs_record_no)}</title><style>body{{font-family:Arial,sans-serif;background:#f3f4f6;padding:40px;color:#111827;}}.container{{width:94%;max-width:1180px;margin:auto;}}.nav-row{{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:25px;}}.btn{{display:inline-block;padding:13px 18px;background:#111827;color:white;border:none;border-radius:12px;text-decoration:none;font-size:15px;}}.header{{background:#111827;color:white;border-radius:16px;padding:30px;box-shadow:0 12px 35px rgba(15,23,42,.12);}}.header h1{{font-size:42px;margin:0 0 8px 0;}}.meta{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px;margin-top:22px;}}.meta div,.remarks{{background:#1F2937;border-radius:12px;padding:14px;}}.label{{color:#CBD5E1;font-size:13px;margin-bottom:5px;}}.value{{font-weight:bold;}}.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:16px;margin:24px 0;}}.mini{{background:white;border:1px solid #E5E7EB;border-radius:16px;padding:20px;box-shadow:0 10px 25px rgba(15,23,42,.07);}}.mini b,.mini span{{display:block;margin-bottom:10px;}}.ok{{color:#166534;background:#DCFCE7;padding:7px 10px;border-radius:999px;font-style:normal;font-weight:bold;}}.bad{{color:#991B1B;background:#FEE2E2;padding:7px 10px;border-radius:999px;font-style:normal;font-weight:bold;}}.actions{{display:flex;gap:8px;margin-top:15px;flex-wrap:wrap;}}.actions a{{background:#111827;color:white;text-decoration:none;padding:9px 11px;border-radius:9px;font-weight:bold;}}.table-wrap{{background:white;border-radius:16px;overflow:auto;border:1px solid #E5E7EB;}}table{{width:100%;border-collapse:collapse;min-width:920px;}}th{{background:#111827;color:white;text-align:left;padding:13px;}}td{{padding:13px;border-bottom:1px solid #E5E7EB;}}@media(max-width:780px){{body{{padding:18px}}.meta{{grid-template-columns:1fr}}}}</style></head><body><div class="container"><div class="nav-row"><a class="btn" href="/">Dashboard</a><a class="btn" href="/customs-list">Customs List</a><a class="btn" href="/edit-customs/{html_attr(customs_record_no)}">Edit</a><a class="btn" href="/customs-pdf/{html_attr(customs_record_no)}">PDF</a></div><div class="header"><h1>{html_text(customs_record_no)}</h1><div>Declaration No: {html_text(record.get("declaration_no",""))}</div><div class="meta"><div><div class="label">Customs Date</div><div class="value">{html_text(record.get("customs_date",""))}</div></div><div><div class="label">Type</div><div class="value">{html_text(record.get("declaration_type",""))}</div></div><div><div class="label">Destination</div><div class="value">{html_text(record.get("destination_country",""))}</div></div><div><div class="label">Total Value</div><div class="value">{html_text(record.get("total_invoice_value",""))}</div></div><div><div class="label">Exporter</div><div class="value">{html_text(record.get("exporter",""))}</div></div><div><div class="label">Consignee</div><div class="value">{html_text(record.get("consignee",""))}</div></div><div><div class="label">Vessel / Voyage</div><div class="value">{html_text(record.get("vessel",""))} / {html_text(record.get("voyage_no",""))}</div></div><div><div class="label">Container / Seal</div><div class="value">{html_text(record.get("container_no",""))} / {html_text(record.get("seal_no",""))}</div></div></div><div class="remarks"><div class="label">Remarks</div><div>{html_text(record.get("remarks",""))}</div></div></div><div class="cards">{cards}</div><div class="table-wrap"><table><thead><tr><th>No</th><th>Item</th><th>HS Code</th><th>Qty</th><th>Unit Price</th><th>Amount</th><th>Origin</th><th>Net</th><th>Gross</th></tr></thead><tbody>{rows}</tbody></table></div><div class="cards"><div class="mini"><b>Total Quantity</b><span>{html_text(record.get("total_quantity",""))}</span></div><div class="mini"><b>Total Net Weight</b><span>{html_text(record.get("total_net_weight",""))}</span></div><div class="mini"><b>Total Gross Weight</b><span>{html_text(record.get("total_gross_weight",""))}</span></div><div class="mini"><b>Total Amount</b><span>{html_text(record.get("total_amount",""))}</span></div></div></div></body></html>"""
    return HTMLResponse(html)


def draw_text_fit(pdf, text, x, y, max_width, font="Helvetica", size=8, min_size=6):
    text = str(text or "")
    current_size = size
    while current_size > min_size and pdf.stringWidth(text, font, current_size) > max_width:
        current_size -= 0.5
    pdf.setFont(font, current_size)
    pdf.drawString(x, y, text)


def create_customs_pdf_buffer(payload):
    buffer = BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4
    navy = colors.HexColor("#111827")
    border = colors.HexColor("#D1D5DB")
    muted = colors.HexColor("#6B7280")

    def footer():
        pdf.setFont("Helvetica", 8)
        pdf.setFillColor(muted)
        pdf.drawCentredString(width / 2, 30, "Generated by Trade Paper AI")
        pdf.setFillColor(colors.black)

    def header():
        pdf.setFillColor(navy)
        pdf.roundRect(40, height - 92, width - 80, 56, 8, stroke=0, fill=1)
        pdf.setFillColor(colors.white)
        pdf.setFont("Helvetica-Bold", 22)
        pdf.drawCentredString(width / 2, height - 70, "CUSTOMS DECLARATION")
        pdf.setFillColor(colors.black)
        pdf.setFont("Helvetica", 8)
        info = [("Record No", payload.get("customs_record_no", "")), ("Declaration No", payload.get("declaration_no", "")), ("Date", payload.get("customs_date", "")), ("Type", payload.get("declaration_type", "")), ("Invoice", payload.get("invoice_no", "")), ("Packing", payload.get("packing_no", "")), ("Container", payload.get("container_no", "")), ("B/L", payload.get("bl_no", "")), ("Exporter", payload.get("exporter", "")), ("Consignee", payload.get("consignee", "")), ("Vessel", payload.get("vessel", "")), ("Voyage", payload.get("voyage_no", "")), ("POL", payload.get("port_of_loading", "")), ("POD", payload.get("port_of_discharge", "")), ("Origin", payload.get("country_of_origin", "")), ("Destination", payload.get("destination_country", ""))]
        y = height - 122
        for idx, (label, value) in enumerate(info):
            x = 48 if idx % 2 == 0 else 318
            if idx and idx % 2 == 0:
                y -= 14
            pdf.drawString(x, y, f"{label}:")
            draw_text_fit(pdf, value, x + 76, y, 160, size=8)

    def table_header(y):
        pdf.setFillColor(navy)
        pdf.rect(40, y, width - 80, 24, stroke=0, fill=1)
        pdf.setFillColor(colors.white)
        pdf.setFont("Helvetica-Bold", 7)
        for x, label in [(46, "No"), (66, "Item"), (176, "HS Code"), (242, "Qty"), (288, "Price"), (340, "Amount"), (398, "Origin"), (460, "Net"), (512, "Gross")]:
            pdf.drawString(x, y + 8, label)
        pdf.setFillColor(colors.black)

    header()
    table_start_y = height - 350
    y = table_start_y
    table_header(y)
    y -= 18
    for idx, item in enumerate(payload.get("items", []), 1):
        if y < 130:
            footer()
            pdf.showPage()
            header()
            y = table_start_y
            table_header(y)
            y -= 18
        pdf.setStrokeColor(border)
        pdf.line(40, y - 5, width - 40, y - 5)
        draw_text_fit(pdf, idx, 46, y + 4, 16, size=7)
        draw_text_fit(pdf, item.get("name", ""), 66, y + 4, 104, size=7)
        draw_text_fit(pdf, item.get("hs_code", ""), 176, y + 4, 58, size=7)
        draw_text_fit(pdf, item.get("quantity", ""), 242, y + 4, 38, size=7)
        draw_text_fit(pdf, item.get("unit_price", ""), 288, y + 4, 46, size=7)
        draw_text_fit(pdf, item.get("amount", ""), 340, y + 4, 52, size=7)
        draw_text_fit(pdf, item.get("origin", ""), 398, y + 4, 56, size=7)
        draw_text_fit(pdf, item.get("net_weight", ""), 460, y + 4, 46, size=7)
        draw_text_fit(pdf, item.get("gross_weight", ""), 512, y + 4, 42, size=7)
        y -= 20
    if y < 170:
        footer()
        pdf.showPage()
        header()
        y = table_start_y
    summary_y = y - 92
    pdf.setFillColor(navy)
    pdf.roundRect(335, summary_y, 220, 84, 8, stroke=0, fill=1)
    pdf.setFillColor(colors.white)
    pdf.setFont("Helvetica-Bold", 9)
    pdf.drawString(350, summary_y + 60, f"Total Quantity: {payload.get('total_quantity', '')}")
    pdf.drawString(350, summary_y + 42, f"Total Net Weight: {payload.get('total_net_weight', '')}")
    pdf.drawString(350, summary_y + 24, f"Total Gross Weight: {payload.get('total_gross_weight', '')}")
    pdf.drawString(350, summary_y + 6, f"Total Amount: {payload.get('total_amount', '')}")
    pdf.setFillColor(colors.black)
    if payload.get("remarks"):
        pdf.setFont("Helvetica-Bold", 10)
        pdf.drawString(40, summary_y + 60, "Remarks")
        draw_text_fit(pdf, payload.get("remarks", ""), 40, summary_y + 42, 260, size=8)
    signature_y = max(90, summary_y - 42)
    pdf.setStrokeColor(colors.black)
    pdf.line(380, signature_y, 555, signature_y)
    pdf.setFont("Helvetica", 9)
    pdf.drawString(415, signature_y - 15, "Authorized Signature")
    footer()
    pdf.save()
    buffer.seek(0)
    return buffer


@router.post("/customs/pdf")
def create_customs_pdf(payload: dict = Body(...)):
    pdf_buffer = create_customs_pdf_buffer(payload)
    filename = f"{payload.get('customs_record_no', 'customs')}.pdf"
    return Response(pdf_buffer.getvalue(), media_type="application/pdf", headers={"Content-Disposition": f"attachment; filename={filename}"})


@router.get("/customs-pdf/{customs_record_no}")
def customs_pdf(customs_record_no: str):
    record = find_record(load_customs(), "customs_record_no", customs_record_no)
    if not record:
        raise HTTPException(status_code=404, detail="Customs declaration not found")
    pdf_buffer = create_customs_pdf_buffer(record)
    return Response(pdf_buffer.getvalue(), media_type="application/pdf", headers={"Content-Disposition": f"attachment; filename={customs_record_no}.pdf"})
