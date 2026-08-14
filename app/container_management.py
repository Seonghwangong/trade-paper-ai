from typing import Annotated, List, Optional
from copy import deepcopy
from datetime import datetime
from io import BytesIO
import html as html_lib

from fastapi import APIRouter, Body, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from app.pdf_fonts import TP_UNICODE, TP_UNICODE_BOLD, ensure_pdf_fonts, fit_pdf_text

router = APIRouter()

from app.storage import atomic_write_json, data_path, load_json_strict, locked_json_mutation, next_identifier
from app.validation import require_at_least_one_reference, require_consistent_reference, require_existing_reference, require_text
from app.referential_integrity import find_dependencies, render_delete_page
from app.account_container import ensure_legacy_container_ownership, public_container
from app.snapshot import assign_item_ids, fill_missing_snapshot_fields, set_submitted_snapshot_fields, snapshot_value
from app.export import set_pdf_export_record
from app.auth import USERS_FILE
from app import packing as packing_module
from app import invoice as invoice_module
from app import bill_of_lading as bill_of_lading_module
from app import shipping_instruction as shipping_instruction_module
from app import buyer as buyer_module
from app.account_company import load_account_company
from app.routers.company import ACCOUNT_COMPANIES_FILE
from app.shipment import shipment_detail_redirect_url

CONTAINER_FILE = data_path("containers.json")
SHIPMENT_FILE = data_path("shipments.json")
PACKING_FILE = data_path("packing_lists.json")
BL_FILE = data_path("bills_of_lading.json")
INVOICE_FILE = data_path("invoices.json")


def html_attr(value):
    return html_lib.escape(str(value or ""), quote=True)


def html_text(value):
    return html_lib.escape(str(value or ""))


def load_json(path, default):
    return load_json_strict(path, default, type(default) if isinstance(default, (list, dict)) else None)


def _account_id(request):
    user = request.scope.get("trade_paper_user") or {}
    return str(user.get("account_id", "") or "").strip()


def load_container_records():
    return ensure_legacy_container_ownership(CONTAINER_FILE, USERS_FILE)


def owned_container_records(account_id):
    owner = str(account_id or "").strip()
    return [
        record for record in load_container_records()
        if isinstance(record, dict)
        and str(record.get("account_id", "") or "").strip() == owner
        and not record.get("archived_at")
    ]


def load_containers(account_id):
    return [public_container(record) for record in owned_container_records(account_id)]


def _owned_container(container_record_no, account_id):
    target = str(container_record_no or "").strip()
    record = next(
        (record for record in owned_container_records(account_id)
         if str(record.get("container_record_no", "") or "").strip() == target),
        None,
    )
    if record is None:
        raise HTTPException(status_code=404, detail="Container record not found")
    return record


def save_containers(records):
    atomic_write_json(CONTAINER_FILE, records, list)


def load_shipments(account_id):
    from app import shipment as shipment_module
    return shipment_module.load_shipments(account_id)


def load_packing_lists(account_id):
    return packing_module.load_packing_lists(account_id)


def load_bills_of_lading(account_id):
    return bill_of_lading_module.load_bills_of_lading(account_id)


def load_shipping_instructions(account_id):
    return shipping_instruction_module.load_shipping_instructions(account_id)


def validate_container_links(shipment_no, packing_no, invoice_no, bl_no, account_id):
    shipments = load_shipments(account_id)
    packings = load_packing_lists(account_id)
    require_at_least_one_reference(
        ("Shipment", shipment_no, shipments, "shipment_no"),
        ("Packing List", packing_no, packings, "packing_no"),
    )
    shipment = require_existing_reference("Shipment", shipment_no, shipments, "shipment_no")
    packing = require_existing_reference("Packing List", packing_no, packings, "packing_no")
    bill = require_existing_reference("Bill of Lading", bl_no, load_bills_of_lading(account_id), "bl_no")
    require_existing_reference("Invoice", invoice_no, invoice_module.load_invoices(account_id), "invoice_no")
    if shipment:
        require_consistent_reference("Packing List", packing_no, shipment.get("packing_no", ""), "selected Shipment")
        require_consistent_reference("Invoice", invoice_no, shipment.get("invoice_no", ""), "selected Shipment")
        require_consistent_reference("Bill of Lading", bl_no, shipment.get("bl_no", ""), "selected Shipment")
    if packing:
        require_consistent_reference("Invoice", invoice_no, packing.get("invoice_no", ""), "selected Packing List")
    if bill:
        require_consistent_reference("Packing List", packing_no, bill.get("packing_no", ""), "selected Bill of Lading")


def next_container_record_no(records):
    return next_identifier(records, "container_record_no", "CON")


def numeric_total(items, field):
    total = 0.0
    for item in items:
        try:
            total += float(item.get(field, 0) or 0)
        except (TypeError, ValueError):
            pass
    return total


def format_number(value):
    try:
        number = float(value or 0)
        return f"{number:g}"
    except (TypeError, ValueError):
        return str(value or "")


def find_record(records, key, value):
    if not value:
        return None
    for record in records:
        if record.get(key) == value:
            return record
    return None


def shipment_exists(shipment_no, account_id):
    return bool(find_record(load_shipments(account_id), "shipment_no", shipment_no))


def packing_exists(packing_no, account_id):
    return bool(find_record(load_packing_lists(account_id), "packing_no", packing_no))


def bl_exists(bl_no, account_id):
    return bool(find_record(load_bills_of_lading(account_id), "bl_no", bl_no))


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


def blank_payload():
    return {
        "container_record_no": "",
        "container_date": datetime.now().strftime("%Y-%m-%d"),
        "shipment_no": "",
        "packing_no": "",
        "bl_no": "",
        "invoice_no": "",
        "booking_no": "",
        "container_no": "",
        "exporter_name": "",
        "exporter_address": "",
        "exporter_email": "",
        "exporter_phone": "",
        "consignee_name": "",
        "consignee_address": "",
        "consignee_email": "",
        "country_of_origin": "",
        "destination_country": "",
        "seal_no": "",
        "container_type": "",
        "carrier": "",
        "vessel": "",
        "voyage_no": "",
        "etd": "",
        "eta": "",
        "port_of_loading": "",
        "port_of_discharge": "",
        "place_of_delivery": "",
        "loading_place": "",
        "remarks": "",
        "items": [],
        "total_carton": "",
        "total_net_weight": "",
        "total_gross_weight": "",
    }


def resolve_container_snapshot(record, account_id, shipment=None, instruction=None, packing=None, invoice=None, preserve_empty=None):
    """Resolve a read-only Container snapshot from account-owned upstream records."""
    resolved = deepcopy(record or {})
    if preserve_empty is None:
        preserve_empty = bool(resolved.get("container_record_no"))

    shipment_no = str(resolved.get("shipment_no", "") or "").strip()
    if shipment is None:
        shipment = find_record(load_shipments(account_id), "shipment_no", shipment_no)
    shipment = shipment or {}

    si_no = str(shipment.get("si_no", "") or "").strip()
    if instruction is None:
        instruction = (
            find_record(load_shipping_instructions(account_id), "si_no", si_no)
            if si_no else None
        )
    instruction = instruction or {}

    packing_no = str(snapshot_value(
        resolved, "packing_no",
        (shipment.get("packing_no"), instruction.get("packing_no")),
        preserve_empty=preserve_empty,
    ) or "").strip()
    if packing is None:
        packing = find_record(load_packing_lists(account_id), "packing_no", packing_no)
    packing = packing or {}

    invoice_no = str(snapshot_value(
        resolved, "invoice_no",
        (shipment.get("invoice_no"), instruction.get("invoice_no"), packing.get("invoice_no")),
        preserve_empty=preserve_empty,
    ) or "").strip()
    if invoice is None:
        invoice = find_record(invoice_module.load_invoices(account_id), "invoice_no", invoice_no)
    invoice = invoice or {}

    company = load_account_company(account_id, ACCOUNT_COMPANIES_FILE)
    consignee_lookup = (
        resolved.get("consignee_name") or shipment.get("consignee")
        or instruction.get("consignee_name") or instruction.get("consignee")
        or packing.get("buyer") or invoice.get("buyer") or ""
    )
    buyer = next(
        (candidate for candidate in buyer_module.load_buyers(account_id)
         if str(candidate.get("name", "") or "").strip().casefold()
         == str(consignee_lookup or "").strip().casefold()),
        {},
    )
    fallbacks = {
        "exporter_name": (shipment.get("shipper"), instruction.get("exporter_name"), instruction.get("shipper"), packing.get("seller"), invoice.get("seller"), company.get("name")),
        "exporter_address": (shipment.get("shipper_address"), instruction.get("exporter_address"), packing.get("seller_address"), invoice.get("seller_address"), company.get("address")),
        "exporter_email": (shipment.get("shipper_email"), instruction.get("exporter_email"), packing.get("seller_email"), invoice.get("seller_email"), company.get("email")),
        "exporter_phone": (shipment.get("shipper_phone"), instruction.get("exporter_phone"), packing.get("seller_phone"), invoice.get("seller_phone"), company.get("phone")),
        "consignee_name": (shipment.get("consignee"), instruction.get("consignee_name"), instruction.get("consignee"), packing.get("buyer"), invoice.get("buyer"), buyer.get("name")),
        "consignee_address": (shipment.get("consignee_address"), instruction.get("consignee_address"), packing.get("buyer_address"), invoice.get("buyer_address"), buyer.get("address")),
        "consignee_email": (shipment.get("consignee_email"), instruction.get("consignee_email"), packing.get("buyer_email"), invoice.get("buyer_email"), buyer.get("email")),
        "booking_no": (shipment.get("booking_no"), instruction.get("booking_no"), packing.get("booking_no"), invoice.get("booking_no")),
        "country_of_origin": (shipment.get("country_of_origin"), shipment.get("origin_country"), instruction.get("country_of_origin"), packing.get("country_of_origin"), invoice.get("country_of_origin"), company.get("country")),
        "destination_country": (shipment.get("destination_country"), instruction.get("destination_country"), packing.get("destination_country"), invoice.get("destination_country"), buyer.get("country")),
    }
    fill_missing_snapshot_fields(resolved, fallbacks, preserve_empty=preserve_empty)

    if "items" not in resolved or (not preserve_empty and not resolved["items"]):
        resolved["items"] = deepcopy(
            shipment.get("items") or instruction.get("items")
            or packing.get("items") or invoice.get("items") or []
        )
    for total, item_field in (
        ("total_carton", "carton"),
        ("total_net_weight", "net_weight"),
        ("total_gross_weight", "gross_weight"),
    ):
        if total not in resolved or (not preserve_empty and not resolved[total]):
            resolved[total] = (
                shipment.get(total) or instruction.get(total) or packing.get(total)
                or format_number(numeric_total(resolved.get("items", []), item_field))
            )
    resolved.update({
        "shipment_no": shipment_no,
        "packing_no": packing_no,
        "invoice_no": invoice_no,
    })
    return resolved


def copy_packing_payload(payload, packing_no, account_id):
    packing = find_record(load_packing_lists(account_id), "packing_no", packing_no)
    if not packing:
        return payload
    items = packing.get("items", [])
    payload.update({
        "packing_no": packing.get("packing_no", ""),
        "invoice_no": packing.get("invoice_no", ""),
        "items": items,
        "total_carton": format_number(numeric_total(items, "carton")),
        "total_net_weight": format_number(numeric_total(items, "net_weight")),
        "total_gross_weight": format_number(numeric_total(items, "gross_weight")),
    })
    return payload


def copy_bl_payload(payload, bl_no, account_id):
    bill = find_record(load_bills_of_lading(account_id), "bl_no", bl_no)
    if not bill:
        return payload
    payload.update({
        "bl_no": bill.get("bl_no", ""),
        "packing_no": payload.get("packing_no") or bill.get("packing_no", ""),
        "invoice_no": payload.get("invoice_no") or bill.get("invoice_no", ""),
        "vessel": bill.get("vessel", ""),
        "voyage_no": bill.get("voyage_no", ""),
        "port_of_loading": bill.get("port_of_loading", ""),
        "port_of_discharge": bill.get("port_of_discharge", ""),
        "place_of_delivery": bill.get("place_of_delivery", ""),
    })
    if not payload.get("items") and bill.get("items"):
        items = bill.get("items", [])
        payload.update({
            "items": items,
            "total_carton": format_number(numeric_total(items, "carton")),
            "total_net_weight": format_number(numeric_total(items, "net_weight")),
            "total_gross_weight": format_number(numeric_total(items, "gross_weight")),
        })
    return payload


def payload_from_sources(shipment_no="", packing_no="", bl_no="", account_id=""):
    payload = blank_payload()
    if shipment_no and shipment_exists(shipment_no, account_id):
        payload["shipment_no"] = shipment_no
    if packing_no:
        payload = copy_packing_payload(payload, packing_no, account_id)
    if bl_no:
        payload = copy_bl_payload(payload, bl_no, account_id)
    return payload


def build_items(item_name, hs_code, quantity, carton, net_weight, gross_weight):
    items = []
    for i, name in enumerate(item_name):
        if not str(name or "").strip():
            continue
        items.append({
            "name": name,
            "hs_code": hs_code[i] if i < len(hs_code) else "",
            "quantity": quantity[i] if i < len(quantity) else "",
            "carton": carton[i] if i < len(carton) else "",
            "net_weight": net_weight[i] if i < len(net_weight) else "",
            "gross_weight": gross_weight[i] if i < len(gross_weight) else "",
        })
    return items


def build_record(
    container_record_no, container_date, shipment_no, packing_no, bl_no, invoice_no,
    container_no, seal_no, container_type, carrier, vessel, voyage_no, etd, eta,
    port_of_loading, port_of_discharge, place_of_delivery, loading_place, remarks,
    item_name, hs_code, quantity, carton, net_weight, gross_weight,
    total_carton, total_net_weight, total_gross_weight,
):
    items = build_items(item_name, hs_code, quantity, carton, net_weight, gross_weight)
    if not total_carton:
        total_carton = format_number(numeric_total(items, "carton"))
    if not total_net_weight:
        total_net_weight = format_number(numeric_total(items, "net_weight"))
    if not total_gross_weight:
        total_gross_weight = format_number(numeric_total(items, "gross_weight"))
    return {
        "container_record_no": container_record_no,
        "container_date": container_date,
        "shipment_no": shipment_no,
        "packing_no": packing_no,
        "bl_no": bl_no,
        "invoice_no": invoice_no,
        "container_no": container_no,
        "seal_no": seal_no,
        "container_type": container_type,
        "carrier": carrier,
        "vessel": vessel,
        "voyage_no": voyage_no,
        "etd": etd,
        "eta": eta,
        "port_of_loading": port_of_loading,
        "port_of_discharge": port_of_discharge,
        "place_of_delivery": place_of_delivery,
        "loading_place": loading_place,
        "remarks": remarks,
        "items": items,
        "total_carton": total_carton,
        "total_net_weight": total_net_weight,
        "total_gross_weight": total_gross_weight,
    }


def build_item_rows(items):
    if not items:
        items = [{}]
    rows = ""
    for item in items:
        rows += f"""
<div class="item-row">
<input type="hidden" name="item_id" value="{html_attr(item.get('item_id', ''))}">
<input type="text" name="item_name" value="{html_attr(item.get('name', ''))}" placeholder="Item">
<input type="text" name="hs_code" value="{html_attr(item.get('hs_code', ''))}" placeholder="HS Code">
<input type="text" name="quantity" value="{html_attr(item.get('quantity', ''))}" placeholder="Quantity">
<input type="text" name="carton" value="{html_attr(item.get('carton', ''))}" placeholder="Carton" oninput="calculateTotals()">
<input type="text" name="net_weight" value="{html_attr(item.get('net_weight', ''))}" placeholder="Net Weight" oninput="calculateTotals()">
<input type="text" name="gross_weight" value="{html_attr(item.get('gross_weight', ''))}" placeholder="Gross Weight" oninput="calculateTotals()">
<button class="remove" type="button" onclick="removeItem(this)">Remove Item</button>
</div>
"""
    return rows


def render_form(record, action, title, button_text, show_no=False, account_id=""):
    rows = build_item_rows(record.get("items", []))
    no_input = ""
    if show_no:
        no_input = f'<div class="field"><label>Container Record No</label><input type="text" name="container_record_no" value="{html_attr(record.get("container_record_no", ""))}" placeholder="Container Record No" readonly></div>'
    shipment_select = select_html("shipment_no", record.get("shipment_no", ""), doc_options(load_shipments(account_id), "shipment_no"), "Select Shipment")
    packing_select = select_html("packing_no", record.get("packing_no", ""), doc_options(load_packing_lists(account_id), "packing_no"), "Select Packing List")
    bl_select = select_html("bl_no", record.get("bl_no", ""), doc_options(load_bills_of_lading(account_id), "bl_no"), "Select B/L")

    html = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Container Management</title>
<style>
body{font-family:Arial,sans-serif;background:#f3f4f6;padding:40px;color:#111827;}
.container{max-width:1080px;margin:auto;background:white;padding:35px;border-radius:16px;box-shadow:0 12px 35px rgba(15,23,42,.08);}
h1{text-align:center;font-size:46px;margin:8px 0 10px;}
.sub{text-align:center;color:#6B7280;margin-bottom:35px;}
.nav-row{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:25px;}
.card{border:1px solid #E5E7EB;border-radius:16px;padding:25px;margin-bottom:25px;background:#fff;}
.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px;}
.record-links{grid-template-columns:repeat(2,minmax(0,1fr));column-gap:20px;row-gap:18px;align-items:start;}
.record-links .field{display:flex;flex-direction:column;gap:8px;min-width:0;}
.record-links label{margin:0;line-height:1.2;}
.record-links input,.record-links select{height:48px;padding:0 14px;}
.item-row{display:grid;grid-template-columns:1.35fr 1fr .8fr .8fr .9fr .9fr;gap:12px;border:1px solid #E5E7EB;border-radius:14px;padding:18px;margin-bottom:16px;background:#F9FAFB;}
label{display:block;font-weight:bold;margin-bottom:7px;color:#374151;}
input,select,textarea{width:100%;padding:14px;border:1px solid #D1D5DB;border-radius:10px;font-size:16px;box-sizing:border-box;background:white;}
textarea{min-height:100px;resize:vertical;}
button{padding:15px 18px;background:#111827;color:white;border:none;border-radius:12px;font-size:16px;cursor:pointer;}
.small{min-width:170px;}
.full{width:100%;margin-top:10px;font-size:18px;}
.add{width:100%;background:#374151;margin-bottom:20px;}
.remove{grid-column:1/-1;width:100%;background:#991B1B;}
.totals{display:flex;gap:18px;flex-wrap:wrap;font-size:17px;font-weight:bold;color:#111827;margin:8px 0 20px;}
@media(max-width:860px){body{padding:18px}.grid,.item-row{grid-template-columns:1fr}h1{font-size:34px}}
</style>
</head>
<body>
<div class="container">
<div class="nav-row">
<a href="/"><button class="small" type="button">Dashboard</button></a>
<a href="/container-list"><button class="small" type="button">Container List</button></a>
</div>
<h1>__TITLE__</h1>
<p class="sub">Manage container loading records linked to shipments, packing lists, and B/Ls</p>
<form action="__ACTION__" method="post">

<div class="card">
<h2>Record Links</h2>
<div class="grid record-links">
__NO_INPUT__
<div class="field"><label>Container Date</label><input type="date" name="container_date" value="__CONTAINER_DATE__"></div>
<div class="field"><label>Shipment</label>__SHIPMENT_SELECT__</div>
<div class="field"><label>Packing List</label>__PACKING_SELECT__</div>
<div class="field"><label>Bill of Lading</label>__BL_SELECT__</div>
<div class="field"><label>Invoice No</label><input type="text" name="invoice_no" value="__INVOICE_NO__" placeholder="Invoice No"></div>
<div class="field"><label>Booking No</label><input type="text" name="booking_no" value="__BOOKING_NO__" placeholder="Booking No"></div>
</div>
</div>

<div class="card">
<h2>Exporter / Consignee Snapshot</h2>
<div class="grid">
<div><label>Exporter</label><input type="text" name="exporter_name" value="__EXPORTER_NAME__" placeholder="Exporter Name"></div>
<div><label>Exporter Address</label><input type="text" name="exporter_address" value="__EXPORTER_ADDRESS__" placeholder="Exporter Address"></div>
<div><label>Exporter Email</label><input type="email" name="exporter_email" value="__EXPORTER_EMAIL__" placeholder="Exporter Email"></div>
<div><label>Exporter Phone</label><input type="text" name="exporter_phone" value="__EXPORTER_PHONE__" placeholder="Exporter Phone"></div>
<div><label>Consignee</label><input type="text" name="consignee_name" value="__CONSIGNEE_NAME__" placeholder="Consignee Name"></div>
<div><label>Consignee Address</label><input type="text" name="consignee_address" value="__CONSIGNEE_ADDRESS__" placeholder="Consignee Address"></div>
<div><label>Consignee Email</label><input type="email" name="consignee_email" value="__CONSIGNEE_EMAIL__" placeholder="Consignee Email"></div>
<div><label>Country of Origin</label><input type="text" name="country_of_origin" value="__COUNTRY_OF_ORIGIN__" placeholder="Country of Origin"></div>
<div><label>Destination Country</label><input type="text" name="destination_country" value="__DESTINATION_COUNTRY__" placeholder="Destination Country"></div>
</div>
</div>

<div class="card">
<h2>Container Details</h2>
<div class="grid">
<div><label>Container No</label><input type="text" name="container_no" value="__CONTAINER_NO__" placeholder="Container No"></div>
<div><label>Seal No</label><input type="text" name="seal_no" value="__SEAL_NO__" placeholder="Seal No"></div>
<div><label>Container Type</label><input type="text" name="container_type" value="__CONTAINER_TYPE__" placeholder="20GP / 40HC"></div>
<div><label>Loading Place</label><input type="text" name="loading_place" value="__LOADING_PLACE__" placeholder="Loading Place"></div>
</div>
</div>

<div class="card">
<h2>Transport Details</h2>
<div class="grid">
<div><label>Carrier</label><input type="text" name="carrier" value="__CARRIER__" placeholder="Carrier"></div>
<div><label>Vessel</label><input type="text" name="vessel" value="__VESSEL__" placeholder="Vessel"></div>
<div><label>Voyage No</label><input type="text" name="voyage_no" value="__VOYAGE_NO__" placeholder="Voyage No"></div>
<div><label>ETD</label><input type="date" name="etd" value="__ETD__"></div>
<div><label>ETA</label><input type="date" name="eta" value="__ETA__"></div>
<div><label>Port of Loading</label><input type="text" name="port_of_loading" value="__PORT_OF_LOADING__" placeholder="Port of Loading"></div>
<div><label>Port of Discharge</label><input type="text" name="port_of_discharge" value="__PORT_OF_DISCHARGE__" placeholder="Port of Discharge"></div>
<div><label>Place of Delivery</label><input type="text" name="place_of_delivery" value="__PLACE_OF_DELIVERY__" placeholder="Place of Delivery"></div>
</div>
</div>

<div class="card">
<h2>Cargo Loaded</h2>
<div id="items">__ITEM_ROWS__</div>
<button class="add" type="button" onclick="addItem()">+ Add Item</button>
<input type="hidden" id="total_carton" name="total_carton" value="__TOTAL_CARTON__">
<input type="hidden" id="total_net_weight" name="total_net_weight" value="__TOTAL_NET_WEIGHT__">
<input type="hidden" id="total_gross_weight" name="total_gross_weight" value="__TOTAL_GROSS_WEIGHT__">
<div class="totals">
<span>Total Cartons: <span id="cartonText">__TOTAL_CARTON__</span></span>
<span>Total Net Weight: <span id="netText">__TOTAL_NET_WEIGHT__</span></span>
<span>Total Gross Weight: <span id="grossText">__TOTAL_GROSS_WEIGHT__</span></span>
</div>
</div>

<div class="card">
<h2>Remarks</h2>
<textarea name="remarks" placeholder="Remarks">__REMARKS__</textarea>
</div>

<button class="full" type="submit">__BUTTON_TEXT__</button>
</form>
</div>
<script>
function addItem(){
  const div = document.createElement("div");
  div.className = "item-row";
  div.innerHTML = `
    <input type="text" name="item_name" placeholder="Item">
    <input type="text" name="hs_code" placeholder="HS Code">
    <input type="text" name="quantity" placeholder="Quantity">
    <input type="text" name="carton" placeholder="Carton" oninput="calculateTotals()">
    <input type="text" name="net_weight" placeholder="Net Weight" oninput="calculateTotals()">
    <input type="text" name="gross_weight" placeholder="Gross Weight" oninput="calculateTotals()">
    <button class="remove" type="button" onclick="removeItem(this)">Remove Item</button>`;
  document.getElementById("items").appendChild(div);
}
function removeItem(btn){btn.closest(".item-row").remove();calculateTotals();}
function sumByName(name){return Array.from(document.querySelectorAll(`[name="${name}"]`)).reduce((sum,input)=>sum+(parseFloat(input.value)||0),0);}
function formatNumber(value){return Number.isInteger(value)?String(value):String(Number(value.toFixed(3)));}
function calculateTotals(){
  const carton=sumByName("carton"), net=sumByName("net_weight"), gross=sumByName("gross_weight");
  document.getElementById("total_carton").value=formatNumber(carton);
  document.getElementById("total_net_weight").value=formatNumber(net);
  document.getElementById("total_gross_weight").value=formatNumber(gross);
  document.getElementById("cartonText").textContent=formatNumber(carton);
  document.getElementById("netText").textContent=formatNumber(net);
  document.getElementById("grossText").textContent=formatNumber(gross);
}
calculateTotals();
</script>
</body>
</html>
"""
    replacements = {
        "__TITLE__": html_text(title),
        "__ACTION__": html_attr(action),
        "__NO_INPUT__": no_input,
        "__CONTAINER_DATE__": html_attr(record.get("container_date", "")),
        "__SHIPMENT_SELECT__": shipment_select,
        "__PACKING_SELECT__": packing_select,
        "__BL_SELECT__": bl_select,
        "__INVOICE_NO__": html_attr(record.get("invoice_no", "")),
        "__BOOKING_NO__": html_attr(record.get("booking_no", "")),
        "__EXPORTER_NAME__": html_attr(record.get("exporter_name", "")),
        "__EXPORTER_ADDRESS__": html_attr(record.get("exporter_address", "")),
        "__EXPORTER_EMAIL__": html_attr(record.get("exporter_email", "")),
        "__EXPORTER_PHONE__": html_attr(record.get("exporter_phone", "")),
        "__CONSIGNEE_NAME__": html_attr(record.get("consignee_name", "")),
        "__CONSIGNEE_ADDRESS__": html_attr(record.get("consignee_address", "")),
        "__CONSIGNEE_EMAIL__": html_attr(record.get("consignee_email", "")),
        "__COUNTRY_OF_ORIGIN__": html_attr(record.get("country_of_origin", "")),
        "__DESTINATION_COUNTRY__": html_attr(record.get("destination_country", "")),
        "__CONTAINER_NO__": html_attr(record.get("container_no", "")),
        "__SEAL_NO__": html_attr(record.get("seal_no", "")),
        "__CONTAINER_TYPE__": html_attr(record.get("container_type", "")),
        "__LOADING_PLACE__": html_attr(record.get("loading_place", "")),
        "__CARRIER__": html_attr(record.get("carrier", "")),
        "__VESSEL__": html_attr(record.get("vessel", "")),
        "__VOYAGE_NO__": html_attr(record.get("voyage_no", "")),
        "__ETD__": html_attr(record.get("etd", "")),
        "__ETA__": html_attr(record.get("eta", "")),
        "__PORT_OF_LOADING__": html_attr(record.get("port_of_loading", "")),
        "__PORT_OF_DISCHARGE__": html_attr(record.get("port_of_discharge", "")),
        "__PLACE_OF_DELIVERY__": html_attr(record.get("place_of_delivery", "")),
        "__ITEM_ROWS__": rows,
        "__TOTAL_CARTON__": html_attr(record.get("total_carton", "")),
        "__TOTAL_NET_WEIGHT__": html_attr(record.get("total_net_weight", "")),
        "__TOTAL_GROSS_WEIGHT__": html_attr(record.get("total_gross_weight", "")),
        "__REMARKS__": html_text(record.get("remarks", "")),
        "__BUTTON_TEXT__": html_text(button_text),
    }
    for key, value in replacements.items():
        html = html.replace(key, value)
    return HTMLResponse(html)


@router.get("/container-list", response_class=HTMLResponse)
def container_list(request: Request, search: str = ""):
    records = sorted(load_containers(_account_id(request)), key=lambda r: r.get("container_record_no", ""), reverse=True)
    if search:
        term = search.lower()
        records = [
            r for r in records
            if term in str(r.get("container_record_no", "")).lower()
            or term in str(r.get("container_no", "")).lower()
            or term in str(r.get("seal_no", "")).lower()
            or term in str(r.get("shipment_no", "")).lower()
            or term in str(r.get("packing_no", "")).lower()
            or term in str(r.get("bl_no", "")).lower()
        ]

    rows = ""
    for r in records:
        no = r.get("container_record_no", "")
        rows += f"""
<tr>
<td>{html_text(no)}</td><td>{html_text(r.get('container_no',''))}</td><td>{html_text(r.get('seal_no',''))}</td>
<td>{html_text(r.get('container_type',''))}</td><td>{html_text(r.get('shipment_no',''))}</td>
<td>{html_text(r.get('packing_no',''))}</td><td>{html_text(r.get('bl_no',''))}</td>
<td>{html_text(r.get('etd',''))}</td><td>{html_text(r.get('eta',''))}</td>
<td><a class="link" href="/container/{html_attr(no)}">View</a></td>
<td><a class="link" href="/container-pdf/{html_attr(no)}">PDF</a></td>
<td><a class="link" href="/edit-container/{html_attr(no)}">Edit</a></td>
<td><a class="danger" href="/delete-container/{html_attr(no)}">Delete</a></td>
</tr>
"""
    html = f"""
<!DOCTYPE html><html><head><meta charset="UTF-8"><title>Container Management</title>
<style>
body{{font-family:Arial,sans-serif;background:#f3f4f6;padding:40px;color:#111827;}}.container{{width:94%;margin:auto;}}
h1{{text-align:center;font-size:46px;margin:8px 0 10px;}}.sub{{text-align:center;color:#6B7280;margin-bottom:28px;}}
.toolbar{{display:flex;justify-content:space-between;gap:12px;align-items:center;flex-wrap:wrap;margin-bottom:20px;}}.nav,.search{{display:flex;gap:12px;flex-wrap:wrap;}}
button,.btn{{display:inline-block;padding:13px 18px;background:#111827;color:white;border:none;border-radius:12px;text-decoration:none;font-size:15px;cursor:pointer;}}.reset{{background:#6B7280;}}
input{{padding:13px;border:1px solid #D1D5DB;border-radius:10px;font-size:15px;min-width:260px;}}.count{{font-weight:bold;color:#374151;margin-bottom:14px;}}
.table-wrap{{background:white;border:1px solid #E5E7EB;border-radius:16px;overflow:auto;box-shadow:0 12px 35px rgba(15,23,42,.08);}}table{{width:100%;border-collapse:collapse;min-width:1180px;}}
th{{background:#111827;color:white;text-align:left;padding:14px;font-size:14px;}}td{{padding:14px;border-bottom:1px solid #E5E7EB;font-size:14px;}}
.link{{background:#111827;color:white;padding:8px 11px;border-radius:9px;text-decoration:none;}}.danger{{background:#991B1B;color:white;padding:8px 11px;border-radius:9px;text-decoration:none;}}
</style></head><body><div class="container">
<h1>Container Management</h1><p class="sub">Manage container loading records linked to shipments, packing lists, and B/Ls</p>
<div class="toolbar"><div class="nav"><a class="btn" href="/">Dashboard</a><a class="btn" href="/container-form">+ New Container</a></div>
<form class="search" action="/container-list" method="get"><input type="text" name="search" value="{html_attr(search)}" placeholder="Search container, seal, shipment, packing, B/L"><button type="submit">Search</button><a class="btn reset" href="/container-list">Reset</a></form></div>
<div class="count">Total Container Records: {len(records)}</div><div class="table-wrap"><table><thead><tr>
<th>Container Record No</th><th>Container No</th><th>Seal No</th><th>Type</th><th>Shipment No</th><th>Packing No</th><th>B/L No</th><th>ETD</th><th>ETA</th><th>View</th><th>PDF</th><th>Edit</th><th>Delete</th>
</tr></thead><tbody>{rows}</tbody></table></div></div></body></html>
"""
    return HTMLResponse(html)


@router.get("/container-form", response_class=HTMLResponse)
def container_form(request: Request, shipment_no: str = "", packing_no: str = "", bl_no: str = ""):
    account_id = _account_id(request)
    record = payload_from_sources(shipment_no, packing_no, bl_no, account_id)
    record["container_record_no"] = next_container_record_no(load_container_records())
    record = resolve_container_snapshot(record, account_id, preserve_empty=False)
    return render_form(record, "/container", "New Container Load Plan", "Save Container Record", show_no=True, account_id=account_id)


@router.post("/container")
def save_container(
    request: Request,
    container_date: str = Form(""), shipment_no: str = Form(""), packing_no: str = Form(""),
    bl_no: str = Form(""), invoice_no: str = Form(""), container_no: str = Form(""),
    seal_no: str = Form(""), container_type: str = Form(""), carrier: str = Form(""),
    vessel: str = Form(""), voyage_no: str = Form(""), etd: str = Form(""), eta: str = Form(""),
    port_of_loading: str = Form(""), port_of_discharge: str = Form(""),
    place_of_delivery: str = Form(""), loading_place: str = Form(""), remarks: str = Form(""),
    item_name: List[str] = Form([]), hs_code: List[str] = Form([]), quantity: List[str] = Form([]),
    item_id: List[str] = Form([]),
    carton: List[str] = Form([]), net_weight: List[str] = Form([]), gross_weight: List[str] = Form([]),
    total_carton: str = Form(""), total_net_weight: str = Form(""), total_gross_weight: str = Form(""),
    booking_no: Annotated[Optional[str], Form()] = None,
    exporter_name: Annotated[Optional[str], Form()] = None,
    exporter_address: Annotated[Optional[str], Form()] = None,
    exporter_email: Annotated[Optional[str], Form()] = None,
    exporter_phone: Annotated[Optional[str], Form()] = None,
    consignee_name: Annotated[Optional[str], Form()] = None,
    consignee_address: Annotated[Optional[str], Form()] = None,
    consignee_email: Annotated[Optional[str], Form()] = None,
    country_of_origin: Annotated[Optional[str], Form()] = None,
    destination_country: Annotated[Optional[str], Form()] = None,
):
    container_no = require_text("Container number", container_no)
    account_id = _account_id(request)
    validate_container_links(shipment_no, packing_no, invoice_no, bl_no, account_id)
    def add_container(records):
        record = build_record(
        next_identifier(records, "container_record_no", "CON"), container_date, shipment_no, packing_no, bl_no,
        invoice_no, container_no, seal_no, container_type, carrier, vessel, voyage_no,
        etd, eta, port_of_loading, port_of_discharge, place_of_delivery, loading_place,
        remarks, item_name, hs_code, quantity, carton, net_weight, gross_weight,
        total_carton, total_net_weight, total_gross_weight,
        )
        assign_item_ids(record["items"], item_id)
        set_submitted_snapshot_fields(record, {
            "booking_no": booking_no,
            "exporter_name": exporter_name,
            "exporter_address": exporter_address,
            "exporter_email": exporter_email,
            "exporter_phone": exporter_phone,
            "consignee_name": consignee_name,
            "consignee_address": consignee_address,
            "consignee_email": consignee_email,
            "country_of_origin": country_of_origin,
            "destination_country": destination_country,
        })
        record = resolve_container_snapshot(record, account_id)
        record["account_id"] = account_id
        records.append(record)
    locked_json_mutation(CONTAINER_FILE, [], add_container, list)
    return RedirectResponse(
        shipment_detail_redirect_url(shipment_no, account_id, "/container-list"), status_code=303,
    )


@router.get("/edit-container/{container_record_no}", response_class=HTMLResponse)
def edit_container(container_record_no: str, request: Request):
    account_id = _account_id(request)
    record = resolve_container_snapshot(
        public_container(_owned_container(container_record_no, account_id)), account_id,
    )
    return render_form(record, f"/update-container/{html_attr(container_record_no)}", "Edit Container Load Plan", "Update Container Record", show_no=True, account_id=account_id)


@router.post("/update-container/{container_record_no}")
def update_container(
    container_record_no: str,
    request: Request,
    container_date: str = Form(""), shipment_no: str = Form(""), packing_no: str = Form(""),
    bl_no: str = Form(""), invoice_no: str = Form(""), container_no: str = Form(""),
    seal_no: str = Form(""), container_type: str = Form(""), carrier: str = Form(""),
    vessel: str = Form(""), voyage_no: str = Form(""), etd: str = Form(""), eta: str = Form(""),
    port_of_loading: str = Form(""), port_of_discharge: str = Form(""),
    place_of_delivery: str = Form(""), loading_place: str = Form(""), remarks: str = Form(""),
    item_name: Annotated[Optional[List[str]], Form()] = None,
    item_id: Annotated[Optional[List[str]], Form()] = None,
    hs_code: Annotated[Optional[List[str]], Form()] = None,
    quantity: Annotated[Optional[List[str]], Form()] = None,
    carton: Annotated[Optional[List[str]], Form()] = None,
    net_weight: Annotated[Optional[List[str]], Form()] = None,
    gross_weight: Annotated[Optional[List[str]], Form()] = None,
    total_carton: Annotated[Optional[str], Form()] = None,
    total_net_weight: Annotated[Optional[str], Form()] = None,
    total_gross_weight: Annotated[Optional[str], Form()] = None,
    booking_no: Annotated[Optional[str], Form()] = None,
    exporter_name: Annotated[Optional[str], Form()] = None,
    exporter_address: Annotated[Optional[str], Form()] = None,
    exporter_email: Annotated[Optional[str], Form()] = None,
    exporter_phone: Annotated[Optional[str], Form()] = None,
    consignee_name: Annotated[Optional[str], Form()] = None,
    consignee_address: Annotated[Optional[str], Form()] = None,
    consignee_email: Annotated[Optional[str], Form()] = None,
    country_of_origin: Annotated[Optional[str], Form()] = None,
    destination_country: Annotated[Optional[str], Form()] = None,
):
    container_no = require_text("Container number", container_no)
    account_id = _account_id(request)
    current = public_container(_owned_container(container_record_no, account_id))
    validate_container_links(shipment_no, packing_no, invoice_no, bl_no, account_id)
    current_items = current.get("items", [])

    def item_values(field, submitted):
        if submitted is not None:
            return submitted
        return [item.get(field, "") for item in current_items if isinstance(item, dict)]

    item_name = item_values("name", item_name)
    hs_code = item_values("hs_code", hs_code)
    quantity = item_values("quantity", quantity)
    carton = item_values("carton", carton)
    net_weight = item_values("net_weight", net_weight)
    gross_weight = item_values("gross_weight", gross_weight)
    total_carton = current.get("total_carton", "") if total_carton is None else total_carton
    total_net_weight = current.get("total_net_weight", "") if total_net_weight is None else total_net_weight
    total_gross_weight = current.get("total_gross_weight", "") if total_gross_weight is None else total_gross_weight
    def replace_container(records):
        for index, record in enumerate(records):
            if (record.get("container_record_no") != container_record_no
                    or str(record.get("account_id", "") or "").strip() != account_id):
                continue
            updated = build_record(
                container_record_no, container_date, shipment_no, packing_no, bl_no,
                invoice_no, container_no, seal_no, container_type, carrier, vessel,
                voyage_no, etd, eta, port_of_loading, port_of_discharge, place_of_delivery,
                loading_place, remarks, item_name, hs_code, quantity, carton, net_weight,
                gross_weight, total_carton, total_net_weight, total_gross_weight,
            )
            assign_item_ids(updated["items"], item_id, current_items)
            submitted_snapshot = {
                "booking_no": booking_no,
                "exporter_name": exporter_name,
                "exporter_address": exporter_address,
                "exporter_email": exporter_email,
                "exporter_phone": exporter_phone,
                "consignee_name": consignee_name,
                "consignee_address": consignee_address,
                "consignee_email": consignee_email,
                "country_of_origin": country_of_origin,
                "destination_country": destination_country,
            }
            for field, value in submitted_snapshot.items():
                if value is not None:
                    updated[field] = value
                elif field in current:
                    updated[field] = deepcopy(current[field])
            updated = resolve_container_snapshot(updated, account_id)
            updated["account_id"] = account_id
            records[index] = updated
            return
        raise HTTPException(status_code=404, detail="Container record not found")
    locked_json_mutation(CONTAINER_FILE, [], replace_container, list)
    return RedirectResponse(
        shipment_detail_redirect_url(shipment_no, account_id, "/container-list"), status_code=303,
    )


@router.get("/delete-container/{container_record_no}")
def delete_container(container_record_no: str, request: Request):
    _owned_container(container_record_no, _account_id(request))
    from app.archive import render_archive_page
    return render_archive_page("Container Management", container_record_no, f"/delete-container/{container_record_no}", "/container-list")

@router.post("/delete-container/{container_record_no}")
def confirm_delete_container(container_record_no: str, request: Request):
    account_id = _account_id(request)
    from app.archive import archive_document
    return archive_document(request, "container", container_record_no, "/container-list")
    _owned_container(container_record_no, account_id)
    dependencies = find_dependencies("Container Management", container_record_no, account_id)
    if dependencies:
        return render_delete_page("Container Management", container_record_no, f"/delete-container/{container_record_no}", "/container-list", dependencies, status_code=409)
    def remove(records):
        index = next((index for index, record in enumerate(records)
                      if isinstance(record, dict)
                      and str(record.get("container_record_no", "") or "").strip() == container_record_no
                      and str(record.get("account_id", "") or "").strip() == account_id), None)
        if index is None:
            raise HTTPException(status_code=404, detail="Container record not found")
        records.pop(index)
    locked_json_mutation(CONTAINER_FILE, [], remove, list)
    return RedirectResponse("/container-list", status_code=303)


@router.get("/container-data/{container_record_no}")
def container_data(container_record_no: str, request: Request):
    account_id = _account_id(request)
    return resolve_container_snapshot(
        public_container(_owned_container(container_record_no, account_id)), account_id,
    )


def status_card(label, value, exists, pdf="", edit="", detail=""):
    if value and exists:
        links = ""
        if detail:
            links += f'<a href="{html_attr(detail)}">View</a>'
        if pdf:
            links += f'<a href="{html_attr(pdf)}">PDF</a>'
        if edit:
            links += f'<a href="{html_attr(edit)}">Edit</a>'
        return f'<div class="mini"><b>{html_text(label)}</b><span>{html_text(value)}</span><em class="ok">Linked</em><div class="actions">{links}</div></div>'
    return f'<div class="mini"><b>{html_text(label)}</b><span>-</span><em class="bad">Missing</em></div>'


@router.get("/container/{container_record_no}", response_class=HTMLResponse)
def container_detail(container_record_no: str, request: Request):
    account_id = _account_id(request)
    record = resolve_container_snapshot(
        public_container(_owned_container(container_record_no, account_id)), account_id,
    )
    shipment_no = record.get("shipment_no", "")
    packing_no = record.get("packing_no", "")
    bl_no = record.get("bl_no", "")
    rows = "".join(
        f"<tr><td>{i}</td><td>{html_text(item.get('name',''))}</td><td>{html_text(item.get('hs_code',''))}</td><td>{html_text(item.get('quantity',''))}</td><td>{html_text(item.get('carton',''))}</td><td>{html_text(item.get('net_weight',''))}</td><td>{html_text(item.get('gross_weight',''))}</td></tr>"
        for i, item in enumerate(record.get("items", []), 1)
    )
    cards = (
        status_card("Shipment", shipment_no, shipment_exists(shipment_no, account_id), detail=f"/shipment/{shipment_no}" if shipment_no else "")
        + status_card("Packing List", packing_no, packing_exists(packing_no, account_id), pdf=f"/packing-list-pdf/{packing_no}" if packing_no else "", edit=f"/edit-packing/{packing_no}" if packing_no else "")
        + status_card("Bill of Lading", bl_no, bl_exists(bl_no, _account_id(request)), pdf=f"/bl-pdf/{bl_no}" if bl_no else "", edit=f"/edit-bl/{bl_no}" if bl_no else "")
    )
    html = f"""
<!DOCTYPE html><html><head><meta charset="UTF-8"><title>{html_text(container_record_no)}</title><style>
body{{font-family:Arial,sans-serif;background:#f3f4f6;padding:40px;color:#111827;}}.container{{width:94%;max-width:1180px;margin:auto;}}.nav-row{{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:25px;}}
.btn{{display:inline-block;padding:13px 18px;background:#111827;color:white;border:none;border-radius:12px;text-decoration:none;font-size:15px;}}.header{{background:#111827;color:white;border-radius:16px;padding:30px;box-shadow:0 12px 35px rgba(15,23,42,.12);}}
.header h1{{font-size:42px;margin:0 0 8px 0;}}.meta{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px;margin-top:22px;}}.meta div,.remarks{{background:#1F2937;border-radius:12px;padding:14px;}}.label{{color:#CBD5E1;font-size:13px;margin-bottom:5px;}}.value{{font-weight:bold;}}
.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:16px;margin:24px 0;}}.mini{{background:white;border:1px solid #E5E7EB;border-radius:16px;padding:20px;box-shadow:0 10px 25px rgba(15,23,42,.07);}}.mini b,.mini span{{display:block;margin-bottom:10px;}}.ok{{color:#166534;background:#DCFCE7;padding:7px 10px;border-radius:999px;font-style:normal;font-weight:bold;}}.bad{{color:#991B1B;background:#FEE2E2;padding:7px 10px;border-radius:999px;font-style:normal;font-weight:bold;}}.actions{{display:flex;gap:8px;margin-top:15px;}}.actions a{{background:#111827;color:white;text-decoration:none;padding:9px 11px;border-radius:9px;font-weight:bold;}}
.table-wrap{{background:white;border-radius:16px;overflow:auto;border:1px solid #E5E7EB;}}table{{width:100%;border-collapse:collapse;min-width:760px;}}th{{background:#111827;color:white;text-align:left;padding:13px;}}td{{padding:13px;border-bottom:1px solid #E5E7EB;}}@media(max-width:780px){{body{{padding:18px}}.meta{{grid-template-columns:1fr}}}}
</style></head><body><div class="container"><div class="nav-row"><a class="btn" href="/">Dashboard</a><a class="btn" href="/container-list">Container List</a><a class="btn" href="/edit-container/{html_attr(container_record_no)}">Edit</a><a class="btn" href="/container-pdf/{html_attr(container_record_no)}">PDF</a></div>
<div class="header"><h1>{html_text(container_record_no)}</h1><div>{html_text(record.get("container_no",""))} / Seal {html_text(record.get("seal_no",""))}</div><div class="meta">
<div><div class="label">Date</div><div class="value">{html_text(record.get("container_date",""))}</div></div><div><div class="label">Type</div><div class="value">{html_text(record.get("container_type",""))}</div></div><div><div class="label">ETD</div><div class="value">{html_text(record.get("etd",""))}</div></div><div><div class="label">ETA</div><div class="value">{html_text(record.get("eta",""))}</div></div>
<div><div class="label">Carrier</div><div class="value">{html_text(record.get("carrier",""))}</div></div><div><div class="label">Vessel</div><div class="value">{html_text(record.get("vessel",""))}</div></div><div><div class="label">POL</div><div class="value">{html_text(record.get("port_of_loading",""))}</div></div><div><div class="label">POD</div><div class="value">{html_text(record.get("port_of_discharge",""))}</div></div>
<div><div class="label">Exporter</div><div class="value">{html_text(record.get("exporter_name",""))}</div></div><div><div class="label">Exporter Contact</div><div class="value">{html_text(record.get("exporter_email",""))}</div></div><div><div class="label">Consignee</div><div class="value">{html_text(record.get("consignee_name",""))}</div></div><div><div class="label">Destination</div><div class="value">{html_text(record.get("destination_country",""))}</div></div>
<div><div class="label">Exporter Address</div><div class="value">{html_text(record.get("exporter_address",""))}</div></div><div><div class="label">Exporter Phone</div><div class="value">{html_text(record.get("exporter_phone",""))}</div></div><div><div class="label">Consignee Address</div><div class="value">{html_text(record.get("consignee_address",""))}</div></div><div><div class="label">Consignee Email</div><div class="value">{html_text(record.get("consignee_email",""))}</div></div>
<div><div class="label">Booking No</div><div class="value">{html_text(record.get("booking_no",""))}</div></div><div><div class="label">Invoice No</div><div class="value">{html_text(record.get("invoice_no",""))}</div></div><div><div class="label">Country of Origin</div><div class="value">{html_text(record.get("country_of_origin",""))}</div></div>
</div><div class="remarks"><div class="label">Remarks</div><div>{html_text(record.get("remarks",""))}</div></div></div>
<div class="cards">{cards}</div>
<div class="table-wrap"><table><thead><tr><th>No</th><th>Item</th><th>HS Code</th><th>Qty</th><th>Carton</th><th>Net Weight</th><th>Gross Weight</th></tr></thead><tbody>{rows}</tbody></table></div>
<div class="cards"><div class="mini"><b>Total Cartons</b><span>{html_text(record.get("total_carton",""))}</span></div><div class="mini"><b>Total Net Weight</b><span>{html_text(record.get("total_net_weight",""))}</span></div><div class="mini"><b>Total Gross Weight</b><span>{html_text(record.get("total_gross_weight",""))}</span></div></div>
</div></body></html>"""
    return HTMLResponse(html)


def draw_text_fit(pdf, text, x, y, max_width, font=TP_UNICODE, size=8, min_size=6):
    text = str(text or "")
    current_size = size
    while current_size > min_size and pdf.stringWidth(text, font, current_size) > max_width:
        current_size -= 0.5
    pdf.setFont(font, current_size)
    pdf.drawString(x, y, fit_pdf_text(pdf, text, max_width, font, current_size))


def create_container_pdf_buffer(payload):
    buffer = BytesIO()
    ensure_pdf_fonts()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4
    navy = colors.HexColor("#111827")
    border = colors.HexColor("#D1D5DB")
    muted = colors.HexColor("#6B7280")

    def footer():
        pdf.setFont(TP_UNICODE, 8)
        pdf.setFillColor(muted)
        pdf.drawCentredString(width / 2, 30, "Generated by Trade Paper AI")
        pdf.setFillColor(colors.black)

    def header():
        pdf.setFillColor(navy)
        pdf.roundRect(40, height - 92, width - 80, 56, 8, stroke=0, fill=1)
        pdf.setFillColor(colors.white)
        pdf.setFont(TP_UNICODE_BOLD, 22)
        pdf.drawCentredString(width / 2, height - 70, "CONTAINER LOAD PLAN")
        pdf.setFillColor(colors.black)
        pdf.setFont(TP_UNICODE, 9)
        info = [
            ("Record No", payload.get("container_record_no", "")), ("Date", payload.get("container_date", "")),
            ("Container No", payload.get("container_no", "")), ("Seal No", payload.get("seal_no", "")),
            ("Type", payload.get("container_type", "")), ("Shipment No", payload.get("shipment_no", "")),
            ("Packing No", payload.get("packing_no", "")), ("B/L No", payload.get("bl_no", "")),
            ("ETD", payload.get("etd", "")), ("ETA", payload.get("eta", "")),
            ("Exporter", payload.get("exporter_name", "")), ("Consignee", payload.get("consignee_name", "")),
            ("Exporter Email", payload.get("exporter_email", "")), ("Consignee Email", payload.get("consignee_email", "")),
        ]
        y = height - 122
        for idx, (label, value) in enumerate(info):
            x = 48 if idx % 2 == 0 else 318
            if idx and idx % 2 == 0:
                y -= 16
            pdf.drawString(x, y, f"{label}:")
            draw_text_fit(pdf, value, x + 78, y, 155, size=9)
        y2 = height - 250
        transport = [
            ("Carrier", payload.get("carrier", "")), ("Vessel", payload.get("vessel", "")),
            ("Voyage No", payload.get("voyage_no", "")), ("POL", payload.get("port_of_loading", "")),
            ("POD", payload.get("port_of_discharge", "")), ("Delivery", payload.get("place_of_delivery", "")),
            ("Loading Place", payload.get("loading_place", "")),
        ]
        for idx, (label, value) in enumerate(transport):
            x = 48 if idx % 2 == 0 else 318
            if idx and idx % 2 == 0:
                y2 -= 16
            pdf.drawString(x, y2, f"{label}:")
            draw_text_fit(pdf, value, x + 78, y2, 155, size=9)

    def table_header(y):
        pdf.setFillColor(navy)
        pdf.rect(40, y, width - 80, 24, stroke=0, fill=1)
        pdf.setFillColor(colors.white)
        pdf.setFont(TP_UNICODE_BOLD, 8)
        for x, label in [(48, "No"), (74, "Item"), (218, "HS Code"), (300, "Qty"), (354, "Carton"), (412, "Net Weight"), (492, "Gross Weight")]:
            pdf.drawString(x, y + 8, label)
        pdf.setFillColor(colors.black)

    header()
    table_start_y = height - 370
    y = table_start_y
    table_header(y)
    y -= 20
    for idx, item in enumerate(payload.get("items", []), 1):
        if y < 130:
            footer()
            pdf.showPage()
            header()
            y = table_start_y
            table_header(y)
            y -= 20
        pdf.setStrokeColor(border)
        pdf.line(40, y - 5, width - 40, y - 5)
        draw_text_fit(pdf, idx, 48, y + 4, 20, size=8)
        draw_text_fit(pdf, item.get("name", ""), 74, y + 4, 132, size=8)
        draw_text_fit(pdf, item.get("hs_code", ""), 218, y + 4, 68, size=8)
        draw_text_fit(pdf, item.get("quantity", ""), 300, y + 4, 42, size=8)
        draw_text_fit(pdf, item.get("carton", ""), 354, y + 4, 44, size=8)
        draw_text_fit(pdf, item.get("net_weight", ""), 412, y + 4, 66, size=8)
        draw_text_fit(pdf, item.get("gross_weight", ""), 492, y + 4, 58, size=8)
        y -= 22
    if y < 170:
        footer()
        pdf.showPage()
        header()
        y = table_start_y
    summary_y = y - 90
    pdf.setFillColor(navy)
    pdf.roundRect(335, summary_y, 220, 78, 8, stroke=0, fill=1)
    pdf.setFillColor(colors.white)
    pdf.setFont(TP_UNICODE_BOLD, 10)
    pdf.drawString(350, summary_y + 52, f"Total Cartons: {payload.get('total_carton', '')}")
    pdf.drawString(350, summary_y + 32, f"Total Net Weight: {payload.get('total_net_weight', '')}")
    pdf.drawString(350, summary_y + 12, f"Total Gross Weight: {payload.get('total_gross_weight', '')}")
    pdf.setFillColor(colors.black)
    if payload.get("remarks"):
        pdf.setFont(TP_UNICODE_BOLD, 10)
        pdf.drawString(40, summary_y + 52, "Remarks")
        draw_text_fit(pdf, payload.get("remarks", ""), 40, summary_y + 34, 260, size=9)
    pdf.setStrokeColor(colors.black)
    pdf.line(380, max(90, summary_y - 42), 555, max(90, summary_y - 42))
    pdf.setFont(TP_UNICODE, 9)
    pdf.drawString(415, max(75, summary_y - 57), "Authorized Signature")
    footer()
    pdf.save()
    buffer.seek(0)
    return buffer


@router.post("/container/pdf")
def create_container_pdf(request: Request, payload: dict = Body(...)):
    account_id = _account_id(request)
    require_existing_reference("Packing List", payload.get("packing_no", ""), load_packing_lists(account_id), "packing_no")
    payload = public_container(payload)
    payload = resolve_container_snapshot(payload, account_id)
    pdf_buffer = create_container_pdf_buffer(payload)
    filename = f"{payload.get('container_record_no', 'container')}.pdf"
    return Response(pdf_buffer.getvalue(), media_type="application/pdf", headers={"Content-Disposition": f"attachment; filename={filename}"})


@router.get("/container-pdf/{container_record_no}")
def container_pdf(container_record_no: str, request: Request):
    account_id = _account_id(request)
    record = resolve_container_snapshot(
        public_container(_owned_container(container_record_no, account_id)), account_id,
    )
    set_pdf_export_record(request, record)
    pdf_buffer = create_container_pdf_buffer(record)
    return Response(pdf_buffer.getvalue(), media_type="application/pdf", headers={"Content-Disposition": f"attachment; filename={container_record_no}.pdf"})
