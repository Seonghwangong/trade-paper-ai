from typing import Annotated, List, Optional
from copy import deepcopy
from datetime import datetime
from io import BytesIO
import html as html_lib
from urllib.parse import quote

from fastapi import APIRouter, Body, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from app.pdf_fonts import TP_UNICODE, TP_UNICODE_BOLD, ensure_pdf_fonts, fit_pdf_text

router = APIRouter()

from app.storage import atomic_write_json, data_path, load_json_strict, locked_json_mutation, next_identifier
from app.validation import require_consistent_reference, require_existing_reference, require_text
from app.referential_integrity import find_dependencies, render_delete_page
from app.account_booking import ensure_legacy_booking_ownership, public_booking
from app.snapshot import assign_item_ids, fill_missing_snapshot_fields, find_by_identifier, preserve_omitted_item_fields, resolve_source_chain
from app.export import set_pdf_export_record
from app.auth import USERS_FILE
from app import invoice as invoice_module
from app import packing as packing_module
from app import bill_of_lading as bill_of_lading_module
from app import shipping_instruction as shipping_instruction_module
from app import shipment as shipment_module
from app import buyer as buyer_module
from app.account_company import load_account_company
from app.routers.company import ACCOUNT_COMPANIES_FILE
from app.ui import imported_section_css, render_imported_section
from app.shipment import shipment_detail_redirect_url

BOOKING_FILE = data_path("booking_confirmations.json")
SHIPMENT_FILE = data_path("shipments.json")
SI_FILE = data_path("shipping_instructions.json")
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


def load_booking_records():
    return ensure_legacy_booking_ownership(BOOKING_FILE, USERS_FILE)


def owned_booking_records(account_id):
    owner = str(account_id or "").strip()
    return [
        record for record in load_booking_records()
        if isinstance(record, dict)
        and str(record.get("account_id", "") or "").strip() == owner
        and not record.get("archived_at")
    ]


def load_bookings(account_id):
    return [public_booking(record) for record in owned_booking_records(account_id)]


def _owned_booking(booking_record_no, account_id):
    target = str(booking_record_no or "").strip()
    record = find_by_identifier(
        owned_booking_records(account_id), "booking_record_no", target, normalize=True,
    )
    if record is None:
        raise HTTPException(status_code=404, detail="Booking not found")
    return record


def save_bookings(records):
    atomic_write_json(BOOKING_FILE, records, list)


def load_shipments(account_id):
    return shipment_module.owned_shipment_records(account_id)


def load_shipping_instructions(account_id):
    return shipping_instruction_module.owned_shipping_instruction_records(account_id)


def load_packing_lists(account_id):
    return packing_module.owned_packing_records(account_id)


def load_bills_of_lading(account_id):
    return bill_of_lading_module.load_bills_of_lading(account_id)


def validate_booking_links(shipment_no, si_no, packing_no, bl_no, invoice_no, account_id):
    shipment = require_existing_reference("Shipment", shipment_no, load_shipments(account_id), "shipment_no", required=True)
    instruction = require_existing_reference("Shipping Instruction", si_no, load_shipping_instructions(account_id), "si_no")
    packing = require_existing_reference("Packing List", packing_no, load_packing_lists(account_id), "packing_no")
    bill = require_existing_reference("Bill of Lading", bl_no, load_bills_of_lading(account_id), "bl_no")
    require_existing_reference("Invoice", invoice_no, invoice_module.owned_invoice_records(account_id), "invoice_no")
    for field, actual, shipment_field in [
        ("Shipping Instruction", si_no, "si_no"), ("Packing List", packing_no, "packing_no"),
        ("Bill of Lading", bl_no, "bl_no"), ("Invoice", invoice_no, "invoice_no"),
    ]:
        require_consistent_reference(field, actual, shipment.get(shipment_field, ""), "selected Shipment")
    if instruction:
        require_consistent_reference("Packing List", packing_no, instruction.get("packing_no", ""), "selected Shipping Instruction")
    if packing:
        require_consistent_reference("Invoice", invoice_no, packing.get("invoice_no", ""), "selected Packing List")
    if bill:
        require_consistent_reference("Packing List", packing_no, bill.get("packing_no", ""), "selected Bill of Lading")


def next_booking_record_no(records):
    return next_identifier(records, "booking_record_no", "BK")


def shipment_exists(shipment_no, account_id):
    return bool(find_by_identifier(load_shipments(account_id), "shipment_no", shipment_no))


def si_exists(si_no, account_id):
    return bool(find_by_identifier(load_shipping_instructions(account_id), "si_no", si_no))


def packing_exists(packing_no, account_id):
    return bool(find_by_identifier(load_packing_lists(account_id), "packing_no", packing_no))


def bl_exists(bl_no, account_id):
    return bool(find_by_identifier(load_bills_of_lading(account_id), "bl_no", bl_no))


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


def blank_payload():
    return {
        "booking_record_no": "",
        "booking_date": datetime.now().strftime("%Y-%m-%d"),
        "shipment_no": "",
        "si_no": "",
        "packing_no": "",
        "bl_no": "",
        "invoice_no": "",
        "booking_no": "",
        "booking_reference": "",
        "exporter": "", "consignee": "",
        "exporter_name": "", "exporter_address": "", "exporter_email": "", "exporter_phone": "",
        "consignee_name": "", "consignee_address": "", "consignee_email": "",
        "country_of_origin": "", "destination_country": "",
        "carrier": "",
        "vessel": "",
        "voyage_no": "",
        "container_type": "",
        "container_count": "",
        "etd": "",
        "eta": "",
        "port_of_loading": "",
        "port_of_discharge": "",
        "place_of_delivery": "",
        "cut_off_date": "",
        "loading_place": "",
        "remarks": "",
        "items": [],
        "total_carton": "",
        "total_net_weight": "",
        "total_gross_weight": "",
    }


def resolve_booking_snapshot(record, account_id, shipment=None, bill=None, packing=None, invoice=None):
    """Resolve Booking Confirmation snapshot from account-owned sources."""
    context = resolve_source_chain(
        record, account_id, document_id_field="booking_record_no",
        load_shipments=load_shipments, load_bills=load_bills_of_lading,
        load_packings=load_packing_lists, load_invoices=invoice_module.owned_invoice_records,
        load_company=lambda owner: load_account_company(owner, ACCOUNT_COMPANIES_FILE),
        load_buyers=buyer_module.load_buyers, shipment=shipment, bill=bill,
        packing=packing, invoice=invoice,
    )
    resolved, preserve_empty = context.resolved, context.preserve_empty
    shipment, bill, packing, invoice = context.shipment, context.bill, context.packing, context.invoice
    company, buyer = context.company, context.buyer
    shipment_no, bl_no, packing_no, invoice_no = context.shipment_no, context.bl_no, context.packing_no, context.invoice_no
    party_sources = {
        "exporter_name": (shipment.get("shipper"), bill.get("shipper"), packing.get("seller"), invoice.get("seller"), company.get("name")),
        "exporter_address": (shipment.get("shipper_address"), bill.get("shipper_address"), packing.get("seller_address"), invoice.get("seller_address"), company.get("address")),
        "exporter_email": (shipment.get("shipper_email"), bill.get("shipper_email"), packing.get("seller_email"), invoice.get("seller_email"), company.get("email")),
        "exporter_phone": (shipment.get("shipper_phone"), bill.get("shipper_phone"), packing.get("seller_phone"), invoice.get("seller_phone"), company.get("phone")),
        "consignee_name": (shipment.get("consignee"), bill.get("consignee"), packing.get("buyer"), invoice.get("buyer"), buyer.get("name")),
        "consignee_address": (shipment.get("consignee_address"), bill.get("consignee_address"), packing.get("buyer_address"), invoice.get("buyer_address"), buyer.get("address")),
        "consignee_email": (shipment.get("consignee_email"), bill.get("consignee_email"), packing.get("buyer_email"), invoice.get("buyer_email"), buyer.get("email")),
    }
    fill_missing_snapshot_fields(resolved, party_sources, preserve_empty=preserve_empty)
    resolved["exporter"] = resolved.get("exporter_name", resolved.get("exporter", ""))
    resolved["consignee"] = resolved.get("consignee_name", resolved.get("consignee", ""))
    if "items" not in resolved or (not preserve_empty and not resolved["items"]):
        resolved["items"] = deepcopy(shipment.get("items") or bill.get("items") or packing.get("items") or invoice.get("items") or [])
    scalar_sources = {
        "country_of_origin": (shipment.get("country_of_origin"), shipment.get("origin_country")),
        "destination_country": (shipment.get("destination_country"), bill.get("place_of_delivery"), bill.get("port_of_discharge")),
        "port_of_loading": (shipment.get("port_of_loading"), bill.get("port_of_loading")),
        "port_of_discharge": (shipment.get("port_of_discharge"), bill.get("port_of_discharge")),
        "place_of_delivery": (shipment.get("place_of_delivery"), bill.get("place_of_delivery")),
    }
    fill_missing_snapshot_fields(resolved, scalar_sources, preserve_empty=preserve_empty)
    origins = [item.get("origin") for item in resolved.get("items", []) if isinstance(item, dict) and item.get("origin")]
    if ("country_of_origin" not in resolved or (not preserve_empty and not resolved["country_of_origin"])) and origins and len(set(origins)) == 1:
        resolved["country_of_origin"] = origins[0]
    si_no = str(resolved.get("si_no", "") or shipment.get("si_no", "") or "").strip()
    resolved.update({"shipment_no": shipment_no, "si_no": si_no, "bl_no": bl_no, "packing_no": packing_no, "invoice_no": invoice_no})
    for total, field in (("total_carton", "carton"), ("total_net_weight", "net_weight"), ("total_gross_weight", "gross_weight")):
        if total not in resolved or (not preserve_empty and not resolved[total]):
            resolved[total] = format_number(numeric_total(resolved.get("items", []), field))
    from app import product as product_module
    product_module.enrich_items_from_products(resolved.get("items", []), account_id)
    return resolved


def copy_items_and_totals(payload, source):
    items = source.get("items", [])
    if items and not payload.get("items"):
        payload["items"] = items
        payload["total_carton"] = source.get("total_carton") or format_number(numeric_total(items, "carton"))
        payload["total_net_weight"] = source.get("total_net_weight") or format_number(numeric_total(items, "net_weight"))
        payload["total_gross_weight"] = source.get("total_gross_weight") or format_number(numeric_total(items, "gross_weight"))
    return payload


def copy_si_payload(payload, si_no, account_id):
    si = find_by_identifier(load_shipping_instructions(account_id), "si_no", si_no)
    if not si:
        return payload
    payload.update({
        "si_no": si.get("si_no", ""),
        "packing_no": si.get("packing_no", ""),
        "invoice_no": si.get("invoice_no", ""),
        "shipment_no": payload.get("shipment_no") or si.get("shipment_no", ""),
        "carrier": si.get("carrier", ""),
        "vessel": si.get("vessel", ""),
        "voyage_no": si.get("voyage_no", ""),
        "port_of_loading": si.get("port_of_loading", ""),
        "port_of_discharge": si.get("port_of_discharge", ""),
        "place_of_delivery": si.get("place_of_delivery", ""),
    })
    return copy_items_and_totals(payload, si)


def copy_packing_payload(payload, packing_no, account_id):
    packing = find_by_identifier(load_packing_lists(account_id), "packing_no", packing_no)
    if not packing:
        return payload
    payload.update({
        "packing_no": packing.get("packing_no", ""),
        "invoice_no": payload.get("invoice_no") or packing.get("invoice_no", ""),
    })
    return copy_items_and_totals(payload, packing)


def copy_bl_payload(payload, bl_no, account_id):
    bill = find_by_identifier(load_bills_of_lading(account_id), "bl_no", bl_no)
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
    if not payload.get("items"):
        payload = copy_items_and_totals(payload, bill)
    return payload


def payload_from_sources(shipment_no="", si_no="", packing_no="", bl_no="", account_id=""):
    payload = blank_payload()
    if shipment_no and shipment_exists(shipment_no, account_id):
        payload["shipment_no"] = shipment_no
        payload = resolve_booking_snapshot(payload, account_id)
    if si_no:
        payload = copy_si_payload(payload, si_no, account_id)
    if packing_no:
        payload = copy_packing_payload(payload, packing_no, account_id)
    if bl_no:
        payload = copy_bl_payload(payload, bl_no, account_id)
    return resolve_booking_snapshot(payload, account_id)


def build_items(item_name, hs_code, quantity, carton, net_weight, gross_weight, origin=None):
    origin = origin if isinstance(origin, (list, tuple)) else []
    carton = carton if isinstance(carton, (list, tuple)) else []
    net_weight = net_weight if isinstance(net_weight, (list, tuple)) else []
    gross_weight = gross_weight if isinstance(gross_weight, (list, tuple)) else []
    items = []
    for i, name in enumerate(item_name):
        if not str(name or "").strip():
            continue
        items.append({
            "name": name,
            "hs_code": hs_code[i] if i < len(hs_code) else "",
            "quantity": quantity[i] if i < len(quantity) else "",
            "origin": origin[i] if i < len(origin) else "",
            "carton": carton[i] if i < len(carton) else "",
            "net_weight": net_weight[i] if i < len(net_weight) else "",
            "gross_weight": gross_weight[i] if i < len(gross_weight) else "",
        })
    return items


def build_record(
    booking_record_no, booking_date, shipment_no, si_no, packing_no, bl_no,
    invoice_no, booking_no, carrier, vessel, voyage_no, container_type,
    container_count, etd, eta, port_of_loading, port_of_discharge,
    place_of_delivery, cut_off_date, loading_place, remarks,
    item_name, hs_code, quantity, carton, net_weight, gross_weight,
    total_carton, total_net_weight, total_gross_weight, origin=None,
    exporter_name="", exporter_address="", exporter_email="", exporter_phone="",
    consignee_name="", consignee_address="", consignee_email="",
    country_of_origin="", destination_country="",
):
    items = build_items(item_name, hs_code, quantity, carton, net_weight, gross_weight, origin)
    if not total_carton:
        total_carton = format_number(numeric_total(items, "carton"))
    if not total_net_weight:
        total_net_weight = format_number(numeric_total(items, "net_weight"))
    if not total_gross_weight:
        total_gross_weight = format_number(numeric_total(items, "gross_weight"))
    return {
        "booking_record_no": booking_record_no,
        "booking_date": booking_date,
        "shipment_no": shipment_no,
        "si_no": si_no,
        "packing_no": packing_no,
        "bl_no": bl_no,
        "invoice_no": invoice_no,
        "booking_no": booking_no,
        "exporter": exporter_name, "consignee": consignee_name,
        "exporter_name": exporter_name, "exporter_address": exporter_address,
        "exporter_email": exporter_email, "exporter_phone": exporter_phone,
        "consignee_name": consignee_name, "consignee_address": consignee_address,
        "consignee_email": consignee_email, "country_of_origin": country_of_origin,
        "destination_country": destination_country,
        "carrier": carrier,
        "vessel": vessel,
        "voyage_no": voyage_no,
        "container_type": container_type,
        "container_count": container_count,
        "etd": etd,
        "eta": eta,
        "port_of_loading": port_of_loading,
        "port_of_discharge": port_of_discharge,
        "place_of_delivery": place_of_delivery,
        "cut_off_date": cut_off_date,
        "loading_place": loading_place,
        "remarks": remarks,
        "items": items,
        "total_carton": total_carton,
        "total_net_weight": total_net_weight,
        "total_gross_weight": total_gross_weight,
    }


def build_item_rows(items, readonly=False):
    if not items:
        items = [{}]
    rows = ""
    readonly_attr = " readonly" if readonly else ""
    for item in items:
        rows += f"""
<div class="item-row">
<input type="hidden" name="item_id" value="{html_attr(item.get('item_id', ''))}">
<input type="text" name="item_name" value="{html_attr(item.get('name', ''))}" placeholder="Item"{readonly_attr}>
<input type="text" name="hs_code" value="{html_attr(item.get('hs_code', ''))}" placeholder="HS Code"{readonly_attr}>
<input type="text" name="quantity" value="{html_attr(item.get('quantity', ''))}" placeholder="Quantity"{readonly_attr}>
<input type="text" name="origin" value="{html_attr(item.get('origin', ''))}" placeholder="Origin"{readonly_attr}>
<input type="text" name="carton" value="{html_attr(item.get('carton', ''))}" placeholder="Carton"{readonly_attr}>
<input type="text" name="net_weight" value="{html_attr(item.get('net_weight', ''))}" placeholder="Net Weight"{readonly_attr}>
<input type="text" name="gross_weight" value="{html_attr(item.get('gross_weight', ''))}" placeholder="Gross Weight"{readonly_attr}>
</div>
"""
    return rows


def render_form(record, action, title, button_text, show_no=False, account_id="", create_mode=False):
    rows = build_item_rows(record.get("items", []), readonly=True)
    no_input = ""
    if show_no:
        no_input = f'<div class="field"><label>Booking Record No</label><input type="text" name="booking_record_no" value="{html_attr(record.get("booking_record_no", ""))}" placeholder="Booking Record No" readonly></div>'
    shipment_select = select_html("shipment_no", record.get("shipment_no", ""), doc_options(load_shipments(account_id), "shipment_no"), "Select Shipment")
    if create_mode:
        shipment_select = shipment_select.replace("<select ", '<select required aria-required="true" onchange="selectShipment(this.value)" ', 1)
    else:
        shipment_select = f'<input type="hidden" name="shipment_no" value="{html_attr(record.get("shipment_no", ""))}"><input type="text" value="{html_attr(record.get("shipment_no", ""))}" readonly>'

    def readonly_reference(name, value):
        return f'<input type="hidden" name="{html_attr(name)}" value="{html_attr(value)}"><input type="text" value="{html_attr(value)}" readonly>'

    si_select = readonly_reference("si_no", record.get("si_no", ""))
    packing_select = readonly_reference("packing_no", record.get("packing_no", ""))
    bl_select = readonly_reference("bl_no", record.get("bl_no", ""))

    html = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Booking Confirmation</title>
<style>
body{font-family:Arial,sans-serif;background:#f3f4f6;padding:40px;color:#111827;}
.container{max-width:1080px;margin:auto;background:white;padding:35px;border-radius:16px;box-shadow:0 12px 35px rgba(15,23,42,.08);}
h1{text-align:center;font-size:46px;margin:8px 0 10px;}
.sub{text-align:center;color:#6B7280;margin-bottom:35px;}
.nav-row{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:25px;}
.card{border:1px solid #E5E7EB;border-radius:16px;padding:25px;margin-bottom:25px;background:#fff;}
.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px;}
.record-links{column-gap:20px;row-gap:18px;align-items:start;}
.field{display:flex;flex-direction:column;gap:8px;min-width:0;}
.item-row{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;border:1px solid #E5E7EB;border-radius:14px;padding:18px;margin-bottom:16px;background:#F9FAFB;}
label{display:block;font-weight:bold;margin:0 0 7px;color:#374151;}
.field label{margin:0;line-height:1.2;}
input,select,textarea{width:100%;padding:14px;border:1px solid #D1D5DB;border-radius:10px;font-size:16px;box-sizing:border-box;background:white;}
input[readonly]{background:#F3F4F6;color:#475569;}
.record-links input,.record-links select{height:48px;padding:0 14px;}
textarea{min-height:100px;resize:vertical;}
button{padding:15px 18px;background:#111827;color:white;border:none;border-radius:12px;font-size:16px;cursor:pointer;}
.small{min-width:170px;}
.full{width:100%;margin-top:10px;font-size:18px;}
.add{width:100%;background:#374151;margin-bottom:20px;}
.remove{grid-column:1/-1;width:100%;background:#991B1B;}
.totals{display:flex;gap:18px;flex-wrap:wrap;font-size:17px;font-weight:bold;color:#111827;margin:8px 0 20px;}
__IMPORTED_CSS__
@media(max-width:860px){body{padding:18px}.grid,.item-row{grid-template-columns:1fr}h1{font-size:34px}}
</style>
</head>
<body>
<div class="container">
<div class="nav-row">
<a href="/"><button class="small" type="button">Dashboard</button></a>
<a href="/booking-list"><button class="small" type="button">Booking List</button></a>
</div>
<h1>__TITLE__</h1>
<p class="sub">Confirm carrier bookings from shipping instructions and packing cargo data</p>
<form action="__ACTION__" method="post">
<div class="card">
<h2>Record Links</h2>
<div class="grid record-links">
<div class="field"><label>Shipment *</label>__SHIPMENT_SELECT__</div>
__NO_INPUT__
<div class="field"><label>Booking Date</label><input type="date" name="booking_date" value="__BOOKING_DATE__"></div>
<div class="field"><label>Shipping Instruction</label>__SI_SELECT__</div>
<div class="field"><label>Packing List</label>__PACKING_SELECT__</div>
<div class="field"><label>Bill of Lading</label>__BL_SELECT__</div>
<div class="field"><label>Commercial Invoice</label><input type="hidden" name="invoice_no" value="__INVOICE_NO__"><input type="text" value="__INVOICE_NO__" readonly></div>
</div>
</div>
<div class="card">
<h2>Booking Information</h2>
<div class="grid">
<div><label>Booking No</label><input type="text" name="booking_no" value="__BOOKING_NO__" readonly></div>
<div><label>Booking Reference</label><input type="text" name="booking_reference" value="__BOOKING_REFERENCE__" placeholder="Carrier Booking Reference"></div>
<div><label>Shipping Line</label><input type="text" name="carrier" value="__CARRIER__" placeholder="Shipping Line"></div>
<div><label>Container Type</label><input type="text" name="container_type" value="__CONTAINER_TYPE__" placeholder="40HC / 20GP"></div>
<div><label>Container Count</label><input type="text" name="container_count" value="__CONTAINER_COUNT__" placeholder="Container Count"></div>
</div>
</div>
__IMPORTED_PARTY_START__
<h2>Exporter / Consignee</h2><div class="grid">
<input type="text" name="exporter_name" value="__EXPORTER__" placeholder="Exporter Name" readonly>
<input type="text" name="exporter_address" value="__EXPORTER_ADDRESS__" placeholder="Exporter Address" readonly>
<input type="email" name="exporter_email" value="__EXPORTER_EMAIL__" placeholder="Exporter Email" readonly>
<input type="text" name="exporter_phone" value="__EXPORTER_PHONE__" placeholder="Exporter Phone" readonly>
<input type="text" name="consignee_name" value="__CONSIGNEE__" placeholder="Consignee Name" readonly>
<input type="text" name="consignee_address" value="__CONSIGNEE_ADDRESS__" placeholder="Consignee Address" readonly>
<input type="email" name="consignee_email" value="__CONSIGNEE_EMAIL__" placeholder="Consignee Email" readonly>
<input type="text" name="country_of_origin" value="__COUNTRY_OF_ORIGIN__" placeholder="Country of Origin" readonly>
<input type="text" name="destination_country" value="__DESTINATION_COUNTRY__" placeholder="Destination Country" readonly>
</div>
__IMPORTED_PARTY_END__
<div class="card">
<h2>Transport Schedule</h2>
<div class="grid">
<div><label>Vessel</label><input type="text" name="vessel" value="__VESSEL__" placeholder="Vessel"></div>
<div><label>Voyage No</label><input type="text" name="voyage_no" value="__VOYAGE_NO__" placeholder="Voyage No"></div>
<div><label>ETD</label><input type="date" name="etd" value="__ETD__"></div>
<div><label>ETA</label><input type="date" name="eta" value="__ETA__"></div>
<div><label>Cut-off Date</label><input type="date" name="cut_off_date" value="__CUT_OFF_DATE__"></div>
<div><label>Loading Place</label><input type="text" name="loading_place" value="__LOADING_PLACE__" placeholder="Loading Place"></div>
<div><label>Port of Loading</label><input type="text" name="port_of_loading" value="__PORT_OF_LOADING__" placeholder="Port of Loading"></div>
<div><label>Port of Discharge</label><input type="text" name="port_of_discharge" value="__PORT_OF_DISCHARGE__" placeholder="Port of Discharge"></div>
<div><label>Place of Delivery</label><input type="text" name="place_of_delivery" value="__PLACE_OF_DELIVERY__" placeholder="Place of Delivery"></div>
</div>
</div>
__IMPORTED_CARGO_START__
<h2>Cargo Summary</h2>
<div id="items">__ITEM_ROWS__</div>
<input type="hidden" id="total_carton" name="total_carton" value="__TOTAL_CARTON__">
<input type="hidden" id="total_net_weight" name="total_net_weight" value="__TOTAL_NET_WEIGHT__">
<input type="hidden" id="total_gross_weight" name="total_gross_weight" value="__TOTAL_GROSS_WEIGHT__">
<div class="totals">
<span>Total Cartons: <span id="cartonText">__TOTAL_CARTON__</span></span>
<span>Total Net Weight: <span id="netText">__TOTAL_NET_WEIGHT__</span></span>
<span>Total Gross Weight: <span id="grossText">__TOTAL_GROSS_WEIGHT__</span></span>
</div>
__IMPORTED_CARGO_END__
<div class="card">
<h2>Remarks</h2>
<textarea name="remarks" placeholder="Remarks">__REMARKS__</textarea>
</div>
<button class="full" type="submit">__BUTTON_TEXT__</button>
</form>
</div>
<script>
function selectShipment(value){
  const url=new URL('/booking-form',window.location.origin);
  if(value)url.searchParams.set('shipment_no',value);
  window.location.assign(url.toString());
}
function addItem(){
  const div = document.createElement("div");
  div.className = "item-row";
  div.innerHTML = `
    <input type="text" name="item_name" placeholder="Item">
    <input type="text" name="hs_code" placeholder="HS Code">
    <input type="text" name="quantity" placeholder="Quantity">
    <input type="text" name="origin" placeholder="Origin">
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
    for section_type in ("party", "cargo"):
        start_marker = f"__IMPORTED_{section_type.upper()}_START__"
        end_marker = f"__IMPORTED_{section_type.upper()}_END__"
        before, marker, remainder = html.partition(start_marker)
        content, closing_marker, after = remainder.partition(end_marker)
        if marker and closing_marker:
            html = before + render_imported_section(section_type, content) + after
    replacements = {
        "__IMPORTED_CSS__": imported_section_css(),
        "__TITLE__": html_text(title),
        "__ACTION__": html_attr(action),
        "__NO_INPUT__": no_input,
        "__BOOKING_DATE__": html_attr(record.get("booking_date", "")),
        "__SHIPMENT_SELECT__": shipment_select,
        "__SI_SELECT__": si_select,
        "__PACKING_SELECT__": packing_select,
        "__BL_SELECT__": bl_select,
        "__INVOICE_NO__": html_attr(record.get("invoice_no", "")),
        "__BOOKING_NO__": html_attr(record.get("booking_no", "")),
        "__BOOKING_REFERENCE__": html_attr(record.get("booking_reference", "")),
        "__EXPORTER__": html_attr(record.get("exporter", "")),
        "__EXPORTER_ADDRESS__": html_attr(record.get("exporter_address", "")),
        "__EXPORTER_EMAIL__": html_attr(record.get("exporter_email", "")),
        "__EXPORTER_PHONE__": html_attr(record.get("exporter_phone", "")),
        "__CONSIGNEE__": html_attr(record.get("consignee", "")),
        "__CONSIGNEE_ADDRESS__": html_attr(record.get("consignee_address", "")),
        "__CONSIGNEE_EMAIL__": html_attr(record.get("consignee_email", "")),
        "__COUNTRY_OF_ORIGIN__": html_attr(record.get("country_of_origin", "")),
        "__DESTINATION_COUNTRY__": html_attr(record.get("destination_country", "")),
        "__CARRIER__": html_attr(record.get("carrier", "")),
        "__CONTAINER_TYPE__": html_attr(record.get("container_type", "")),
        "__CONTAINER_COUNT__": html_attr(record.get("container_count", "")),
        "__VESSEL__": html_attr(record.get("vessel", "")),
        "__VOYAGE_NO__": html_attr(record.get("voyage_no", "")),
        "__ETD__": html_attr(record.get("etd", "")),
        "__ETA__": html_attr(record.get("eta", "")),
        "__CUT_OFF_DATE__": html_attr(record.get("cut_off_date", "")),
        "__LOADING_PLACE__": html_attr(record.get("loading_place", "")),
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


@router.get("/booking-list", response_class=HTMLResponse)
def booking_list(request: Request, search: str = ""):
    records = sorted(load_bookings(_account_id(request)), key=lambda r: r.get("booking_record_no", ""), reverse=True)
    if search:
        term = search.lower()
        records = [
            r for r in records
            if term in str(r.get("booking_record_no", "")).lower()
            or term in str(r.get("booking_no", "")).lower()
            or term in str(r.get("booking_reference", "")).lower()
            or term in str(r.get("carrier", "")).lower()
            or term in str(r.get("vessel", "")).lower()
            or term in str(r.get("shipment_no", "")).lower()
            or term in str(r.get("si_no", "")).lower()
            or term in str(r.get("packing_no", "")).lower()
        ]
    rows = ""
    for r in records:
        no = r.get("booking_record_no", "")
        rows += f"""
<tr><td>{html_text(no)}</td><td>{html_text(r.get('booking_no',''))}</td><td>{html_text(r.get('booking_reference',''))}</td><td>{html_text(r.get('carrier',''))}</td>
<td>{html_text(r.get('vessel',''))}</td><td>{html_text(r.get('voyage_no',''))}</td><td>{html_text(r.get('shipment_no',''))}</td>
<td>{html_text(r.get('si_no',''))}</td><td>{html_text(r.get('packing_no',''))}</td><td>{html_text(r.get('etd',''))}</td><td>{html_text(r.get('eta',''))}</td>
<td><a class="link" href="/booking/{html_attr(no)}">View</a></td><td><a class="link" href="/booking-pdf/{html_attr(no)}">PDF</a></td>
<td><a class="link" href="/edit-booking/{html_attr(no)}">Edit</a></td><td><a class="danger" href="/delete-booking/{html_attr(no)}">Delete</a></td></tr>
"""
    html = f"""
<!DOCTYPE html><html><head><meta charset="UTF-8"><title>Booking Confirmations</title><style>
body{{font-family:Arial,sans-serif;background:#f3f4f6;padding:40px;color:#111827;}}.container{{width:94%;margin:auto;}}h1{{text-align:center;font-size:46px;margin:8px 0 10px;}}.sub{{text-align:center;color:#6B7280;margin-bottom:28px;}}
.toolbar{{display:flex;justify-content:space-between;gap:12px;align-items:center;flex-wrap:wrap;margin-bottom:20px;}}.nav,.search{{display:flex;gap:12px;flex-wrap:wrap;}}button,.btn{{display:inline-block;padding:13px 18px;background:#111827;color:white;border:none;border-radius:12px;text-decoration:none;font-size:15px;cursor:pointer;}}.reset{{background:#6B7280;}}input{{padding:13px;border:1px solid #D1D5DB;border-radius:10px;font-size:15px;min-width:260px;}}.count{{font-weight:bold;color:#374151;margin-bottom:14px;}}.table-wrap{{background:white;border:1px solid #E5E7EB;border-radius:16px;overflow:auto;box-shadow:0 12px 35px rgba(15,23,42,.08);}}table{{width:100%;border-collapse:collapse;min-width:1260px;}}th{{background:#111827;color:white;text-align:left;padding:14px;font-size:14px;}}td{{padding:14px;border-bottom:1px solid #E5E7EB;font-size:14px;}}.link{{background:#111827;color:white;padding:8px 11px;border-radius:9px;text-decoration:none;}}.danger{{background:#991B1B;color:white;padding:8px 11px;border-radius:9px;text-decoration:none;}}
</style></head><body><div class="container"><h1>Booking Confirmations</h1><p class="sub">Manage carrier booking confirmations</p>
<div class="toolbar"><div class="nav"><a class="btn" href="/">Dashboard</a><a class="btn" href="/booking-form">+ New Booking</a></div>
<form class="search" action="/booking-list" method="get"><input type="text" name="search" value="{html_attr(search)}" placeholder="Search booking, carrier, vessel, shipment, S/I"><button type="submit">Search</button><a class="btn reset" href="/booking-list">Reset</a></form></div>
<div class="count">Total Bookings: {len(records)}</div><div class="table-wrap"><table><thead><tr>
<th>Booking Record No</th><th>Booking No</th><th>Booking Reference</th><th>Shipping Line</th><th>Vessel</th><th>Voyage No</th><th>Shipment No</th><th>S/I No</th><th>Packing No</th><th>ETD</th><th>ETA</th><th>View</th><th>PDF</th><th>Edit</th><th>Delete</th>
</tr></thead><tbody>{rows}</tbody></table></div></div></body></html>"""
    return HTMLResponse(html)


@router.get("/booking-form", response_class=HTMLResponse)
def booking_form(request: Request, shipment_no: str = "", si_no: str = "", packing_no: str = "", bl_no: str = ""):
    account_id = _account_id(request)
    record = payload_from_sources(shipment_no, si_no, packing_no, bl_no, account_id)
    next_no = next_booking_record_no(load_booking_records())
    record["booking_record_no"] = next_no
    record["booking_no"] = next_no
    return render_form(record, "/booking", "New Booking Confirmation", "Save Booking", show_no=True, account_id=account_id, create_mode=True)


def booking_success_response(record, shipment_url):
    shipment_no = str(record.get("shipment_no", "") or "")
    packing_no = str(record.get("packing_no", "") or "")
    booking_record_no = str(record.get("booking_record_no", "") or "")
    bl_url = html_attr(f'/bl-form?booking_record_no={quote(booking_record_no, safe="")}&packing_no={quote(packing_no, safe="")}&shipment_no={quote(shipment_no, safe="")}')
    return HTMLResponse(f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Booking Saved</title><style>*{{box-sizing:border-box}}body{{margin:0;background:#F3F4F6;color:#111827;font-family:Arial,sans-serif}}main{{min-height:100vh;display:grid;place-items:center;padding:24px}}.card{{width:min(620px,100%);padding:34px;border:1px solid #E5E7EB;border-radius:18px;background:#fff;text-align:center;box-shadow:0 14px 34px rgba(15,23,42,.09)}}h1{{margin:0 0 10px}}p{{color:#475569}}.actions{{display:flex;justify-content:center;gap:10px;flex-wrap:wrap;margin-top:20px}}a{{display:inline-flex;min-height:46px;align-items:center;padding:11px 16px;border-radius:10px;background:#E5E7EB;color:#111827;text-decoration:none;font-weight:800}}a.primary{{background:#111827;color:#fff}}</style></head><body><main><section class="card"><h1>Booking Saved</h1><p>✓ {html_text(booking_record_no)} was created successfully.</p><div class="actions"><a class="primary" href="{bl_url}">Continue to Bill of Lading →</a><a href="/booking/{html_attr(booking_record_no)}">View Booking</a><a href="{html_attr(shipment_url)}">View Shipment</a></div></section></main></body></html>''')


def form_fields():
    return {
        "booking_date": Form(""), "shipment_no": Form(""), "si_no": Form(""),
        "packing_no": Form(""), "bl_no": Form(""), "invoice_no": Form(""),
        "booking_no": Form(""), "carrier": Form(""), "vessel": Form(""),
        "voyage_no": Form(""), "container_type": Form(""), "container_count": Form(""),
        "etd": Form(""), "eta": Form(""), "port_of_loading": Form(""),
        "port_of_discharge": Form(""), "place_of_delivery": Form(""),
        "cut_off_date": Form(""), "loading_place": Form(""), "remarks": Form(""),
    }


@router.post("/booking")
def save_booking(
    request: Request,
    booking_date: str = Form(""), shipment_no: str = Form(""), si_no: str = Form(""),
    packing_no: str = Form(""), bl_no: str = Form(""), invoice_no: str = Form(""),
    booking_no: str = Form(""), booking_reference: str = Form(""), carrier: str = Form(""), vessel: str = Form(""),
    voyage_no: str = Form(""), container_type: str = Form(""), container_count: str = Form(""),
    etd: str = Form(""), eta: str = Form(""), port_of_loading: str = Form(""),
    port_of_discharge: str = Form(""), place_of_delivery: str = Form(""),
    cut_off_date: str = Form(""), loading_place: str = Form(""), remarks: str = Form(""),
    item_name: List[str] = Form([]), hs_code: List[str] = Form([]), quantity: List[str] = Form([]),
    item_id: List[str] = Form([]),
    carton: List[str] = Form([]), net_weight: List[str] = Form([]), gross_weight: List[str] = Form([]),
    total_carton: str = Form(""), total_net_weight: str = Form(""), total_gross_weight: str = Form(""),
    origin: Annotated[Optional[List[str]], Form()] = None,
    exporter_name: Annotated[Optional[str], Form()] = None, exporter_address: Annotated[Optional[str], Form()] = None,
    exporter_email: Annotated[Optional[str], Form()] = None, exporter_phone: Annotated[Optional[str], Form()] = None,
    consignee_name: Annotated[Optional[str], Form()] = None, consignee_address: Annotated[Optional[str], Form()] = None,
    consignee_email: Annotated[Optional[str], Form()] = None, country_of_origin: Annotated[Optional[str], Form()] = None,
    destination_country: Annotated[Optional[str], Form()] = None,
):
    account_id = _account_id(request)
    booking_reference = booking_reference if isinstance(booking_reference, str) else ""
    validate_booking_links(shipment_no, si_no, packing_no, bl_no, invoice_no, account_id)
    saved = {}
    def add_booking(records):
        generated_no = next_identifier(records, "booking_record_no", "BK")
        snapshot = resolve_booking_snapshot({"shipment_no": shipment_no, "bl_no": bl_no, "packing_no": packing_no, "invoice_no": invoice_no}, account_id)
        record = build_record(generated_no, booking_date, shipment_no, si_no, packing_no, bl_no, invoice_no, generated_no, carrier, vessel, voyage_no, container_type, container_count, etd, eta, port_of_loading, port_of_discharge, place_of_delivery, cut_off_date, loading_place, remarks, item_name, hs_code, quantity, carton, net_weight, gross_weight, total_carton, total_net_weight, total_gross_weight, origin, snapshot.get("exporter_name", "") if exporter_name is None else exporter_name, snapshot.get("exporter_address", "") if exporter_address is None else exporter_address, snapshot.get("exporter_email", "") if exporter_email is None else exporter_email, snapshot.get("exporter_phone", "") if exporter_phone is None else exporter_phone, snapshot.get("consignee_name", "") if consignee_name is None else consignee_name, snapshot.get("consignee_address", "") if consignee_address is None else consignee_address, snapshot.get("consignee_email", "") if consignee_email is None else consignee_email, snapshot.get("country_of_origin", "") if country_of_origin is None else country_of_origin, snapshot.get("destination_country", "") if destination_country is None else destination_country)
        record["booking_reference"] = booking_reference
        assign_item_ids(record["items"], item_id)
        record = resolve_booking_snapshot(record, account_id)
        record["account_id"] = account_id
        records.append(record)
        saved.update(record)
    locked_json_mutation(BOOKING_FILE, [], add_booking, list)
    from app.audit_log import record_request_audit
    record_request_audit(request, "Create", "Booking Confirmation", saved["booking_record_no"], path=BOOKING_FILE.with_name("audit_log.json"))
    shipment_url = shipment_detail_redirect_url(shipment_no, account_id, "/booking-list")
    return booking_success_response(saved, shipment_url)


@router.get("/edit-booking/{booking_record_no}", response_class=HTMLResponse)
def edit_booking(booking_record_no: str, request: Request):
    account_id = _account_id(request)
    record = resolve_booking_snapshot(public_booking(_owned_booking(booking_record_no, account_id)), account_id)
    return render_form(record, f"/update-booking/{html_attr(booking_record_no)}", "Edit Booking Confirmation", "Update Booking", show_no=True, account_id=account_id)


@router.post("/update-booking/{booking_record_no}")
def update_booking(
    booking_record_no: str,
    request: Request,
    booking_date: str = Form(""), shipment_no: str = Form(""), si_no: str = Form(""),
    packing_no: str = Form(""), bl_no: str = Form(""), invoice_no: str = Form(""),
    booking_no: str = Form(""), booking_reference: str = Form(""), carrier: str = Form(""), vessel: str = Form(""),
    voyage_no: str = Form(""), container_type: str = Form(""), container_count: str = Form(""),
    etd: str = Form(""), eta: str = Form(""), port_of_loading: str = Form(""),
    port_of_discharge: str = Form(""), place_of_delivery: str = Form(""),
    cut_off_date: str = Form(""), loading_place: str = Form(""), remarks: str = Form(""),
    item_name: List[str] = Form([]), hs_code: List[str] = Form([]), quantity: List[str] = Form([]),
    item_id: List[str] = Form([]),
    carton: Annotated[Optional[List[str]], Form()] = None, net_weight: Annotated[Optional[List[str]], Form()] = None, gross_weight: Annotated[Optional[List[str]], Form()] = None,
    total_carton: str = Form(""), total_net_weight: str = Form(""), total_gross_weight: str = Form(""),
    origin: Annotated[Optional[List[str]], Form()] = None,
    exporter_name: Annotated[Optional[str], Form()] = None, exporter_address: Annotated[Optional[str], Form()] = None,
    exporter_email: Annotated[Optional[str], Form()] = None, exporter_phone: Annotated[Optional[str], Form()] = None,
    consignee_name: Annotated[Optional[str], Form()] = None, consignee_address: Annotated[Optional[str], Form()] = None,
    consignee_email: Annotated[Optional[str], Form()] = None, country_of_origin: Annotated[Optional[str], Form()] = None,
    destination_country: Annotated[Optional[str], Form()] = None,
):
    account_id = _account_id(request)
    current = _owned_booking(booking_record_no, account_id)
    booking_reference = booking_reference if isinstance(booking_reference, str) else str(current.get("booking_reference", "") or "")
    validate_booking_links(shipment_no, si_no, packing_no, bl_no, invoice_no, account_id)
    booking_no = current.get("booking_no", "") or current.get("booking_record_no", "")
    def replace_booking(records):
        for index, record in enumerate(records):
            if (record.get("booking_record_no") != booking_record_no
                    or str(record.get("account_id", "") or "").strip() != account_id):
                continue
            updated = build_record(booking_record_no, booking_date, shipment_no, si_no, packing_no, bl_no, invoice_no, booking_no, carrier, vessel, voyage_no, container_type, container_count, etd, eta, port_of_loading, port_of_discharge, place_of_delivery, cut_off_date, loading_place, remarks, item_name, hs_code, quantity, carton, net_weight, gross_weight, total_carton, total_net_weight, total_gross_weight, origin, exporter_name or current.get("exporter_name", ""), current.get("exporter_address", "") if exporter_address is None else exporter_address, current.get("exporter_email", "") if exporter_email is None else exporter_email, current.get("exporter_phone", "") if exporter_phone is None else exporter_phone, consignee_name or current.get("consignee_name", ""), current.get("consignee_address", "") if consignee_address is None else consignee_address, current.get("consignee_email", "") if consignee_email is None else consignee_email, current.get("country_of_origin", "") if country_of_origin is None else country_of_origin, current.get("destination_country", "") if destination_country is None else destination_country)
            updated["booking_reference"] = booking_reference
            assign_item_ids(updated["items"], item_id, current.get("items", []))
            preserve_omitted_item_fields(
                updated["items"], current.get("items", []),
                [field for values, field in ((origin, "origin"), (carton, "carton"), (net_weight, "net_weight"), (gross_weight, "gross_weight")) if values is None],
            )
            updated = resolve_booking_snapshot(updated, account_id)
            updated["account_id"] = account_id
            records[index] = updated
            return
        raise HTTPException(status_code=404, detail="Booking not found")
    locked_json_mutation(BOOKING_FILE, [], replace_booking, list)
    from app.audit_log import record_request_audit
    record_request_audit(request, "Update", "Booking Confirmation", booking_record_no, path=BOOKING_FILE.with_name("audit_log.json"))
    return RedirectResponse(
        shipment_detail_redirect_url(shipment_no, account_id, "/booking-list"), status_code=303,
    )


@router.get("/delete-booking/{booking_record_no}")
def delete_booking(booking_record_no: str, request: Request):
    _owned_booking(booking_record_no, _account_id(request))
    from app.archive import render_archive_page
    return render_archive_page("Booking Confirmation", booking_record_no, f"/delete-booking/{booking_record_no}", "/booking-list")

@router.post("/delete-booking/{booking_record_no}")
def confirm_delete_booking(booking_record_no: str, request: Request):
    account_id = _account_id(request)
    from app.archive import archive_document
    return archive_document(request, "booking", booking_record_no, "/booking-list")
    _owned_booking(booking_record_no, account_id)
    dependencies = find_dependencies("Booking Confirmation", booking_record_no, account_id)
    if dependencies:
        return render_delete_page("Booking Confirmation", booking_record_no, f"/delete-booking/{booking_record_no}", "/booking-list", dependencies, status_code=409)
    def remove(records):
        index = next((index for index, record in enumerate(records)
                      if isinstance(record, dict)
                      and str(record.get("booking_record_no", "") or "").strip() == booking_record_no
                      and str(record.get("account_id", "") or "").strip() == account_id), None)
        if index is None:
            raise HTTPException(status_code=404, detail="Booking not found")
        records.pop(index)
    locked_json_mutation(BOOKING_FILE, [], remove, list)
    return RedirectResponse("/booking-list", status_code=303)


@router.get("/booking-data/{booking_record_no}")
def booking_data(booking_record_no: str, request: Request):
    account_id = _account_id(request)
    return resolve_booking_snapshot(public_booking(_owned_booking(booking_record_no, account_id)), account_id)


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


@router.get("/booking/{booking_record_no}", response_class=HTMLResponse)
def booking_detail(booking_record_no: str, request: Request):
    account_id = _account_id(request)
    record = resolve_booking_snapshot(public_booking(_owned_booking(booking_record_no, account_id)), account_id)
    shipment_no = record.get("shipment_no", "")
    si_no = record.get("si_no", "")
    packing_no = record.get("packing_no", "")
    bl_no = record.get("bl_no", "")
    rows = "".join(
        f"<tr><td>{i}</td><td>{html_text(item.get('name',''))}</td><td>{html_text(item.get('hs_code',''))}</td><td>{html_text(item.get('quantity',''))}</td><td>{html_text(item.get('carton',''))}</td><td>{html_text(item.get('net_weight',''))}</td><td>{html_text(item.get('gross_weight',''))}</td></tr>"
        for i, item in enumerate(record.get("items", []), 1)
    )
    cards = (
        status_card("Shipment", shipment_no, shipment_exists(shipment_no, account_id), detail=f"/shipment/{shipment_no}" if shipment_no else "")
        + status_card("Shipping Instruction", si_no, si_exists(si_no, account_id), pdf=f"/si-pdf/{si_no}" if si_no else "", edit=f"/edit-si/{si_no}" if si_no else "")
        + status_card("Packing List", packing_no, packing_exists(packing_no, account_id), pdf=f"/packing-list-pdf/{packing_no}" if packing_no else "", edit=f"/edit-packing/{packing_no}" if packing_no else "")
        + status_card("Bill of Lading", bl_no, bl_exists(bl_no, _account_id(request)), pdf=f"/bl-pdf/{bl_no}" if bl_no else "", edit=f"/edit-bl/{bl_no}" if bl_no else "")
    )
    html = f"""
<!DOCTYPE html><html><head><meta charset="UTF-8"><title>{html_text(booking_record_no)}</title><style>
body{{font-family:Arial,sans-serif;background:#f3f4f6;padding:40px;color:#111827;}}.container{{width:94%;max-width:1180px;margin:auto;}}.nav-row{{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:25px;}}.btn{{display:inline-block;padding:13px 18px;background:#111827;color:white;border:none;border-radius:12px;text-decoration:none;font-size:15px;}}.header{{background:#111827;color:white;border-radius:16px;padding:30px;box-shadow:0 12px 35px rgba(15,23,42,.12);}}.header h1{{font-size:42px;margin:0 0 8px 0;}}.meta{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px;margin-top:22px;}}.meta div,.remarks{{background:#1F2937;border-radius:12px;padding:14px;}}.label{{color:#CBD5E1;font-size:13px;margin-bottom:5px;}}.value{{font-weight:bold;}}.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:16px;margin:24px 0;}}.mini{{background:white;border:1px solid #E5E7EB;border-radius:16px;padding:20px;box-shadow:0 10px 25px rgba(15,23,42,.07);}}.mini b,.mini span{{display:block;margin-bottom:10px;}}.ok{{color:#166534;background:#DCFCE7;padding:7px 10px;border-radius:999px;font-style:normal;font-weight:bold;}}.bad{{color:#991B1B;background:#FEE2E2;padding:7px 10px;border-radius:999px;font-style:normal;font-weight:bold;}}.actions{{display:flex;gap:8px;margin-top:15px;}}.actions a{{background:#111827;color:white;text-decoration:none;padding:9px 11px;border-radius:9px;font-weight:bold;}}.table-wrap{{background:white;border-radius:16px;overflow:auto;border:1px solid #E5E7EB;}}table{{width:100%;border-collapse:collapse;min-width:760px;}}th{{background:#111827;color:white;text-align:left;padding:13px;}}td{{padding:13px;border-bottom:1px solid #E5E7EB;}}@media(max-width:780px){{body{{padding:18px}}.meta{{grid-template-columns:1fr}}}}
</style></head><body><div class="container"><div class="nav-row"><a class="btn" href="/">Dashboard</a><a class="btn" href="/booking-list">Booking List</a><a class="btn" href="/edit-booking/{html_attr(booking_record_no)}">Edit</a><a class="btn" href="/booking-pdf/{html_attr(booking_record_no)}">PDF</a><a class="btn" href="/send-email/booking/{html_attr(booking_record_no)}">Send Email</a></div>
<div class="header"><h1>{html_text(booking_record_no)}</h1><div>Booking No: {html_text(record.get("booking_no",""))} · Reference: {html_text(record.get("booking_reference",""))}</div><div class="meta">
<div><div class="label">Carrier</div><div class="value">{html_text(record.get("carrier",""))}</div></div><div><div class="label">Vessel / Voyage</div><div class="value">{html_text(record.get("vessel",""))} / {html_text(record.get("voyage_no",""))}</div></div><div><div class="label">ETD</div><div class="value">{html_text(record.get("etd",""))}</div></div><div><div class="label">ETA</div><div class="value">{html_text(record.get("eta",""))}</div></div>
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


def create_booking_pdf_buffer(payload):
    payload = public_booking(payload)
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
        pdf.drawCentredString(width / 2, height - 70, "BOOKING CONFIRMATION")
        pdf.setFillColor(colors.black)
        pdf.setFont(TP_UNICODE, 9)
        info = [
            ("Record No", payload.get("booking_record_no", "")), ("Booking No", payload.get("booking_no", "")),
            ("Booking Reference", payload.get("booking_reference", "")), ("Shipping Line", payload.get("carrier", "")),
            ("Booking Date", payload.get("booking_date", "")), ("Shipment No", payload.get("shipment_no", "")),
            ("S/I No", payload.get("si_no", "")), ("Packing No", payload.get("packing_no", "")),
            ("B/L No", payload.get("bl_no", "")), ("Invoice No", payload.get("invoice_no", "")),
            ("Exporter", payload.get("exporter", "")), ("Consignee", payload.get("consignee", "")),
            ("Exporter Addr", payload.get("exporter_address", "")), ("Consignee Addr", payload.get("consignee_address", "")),
            ("Exporter Contact", " / ".join(v for v in (payload.get("exporter_email", ""), payload.get("exporter_phone", "")) if v)), ("Consignee Email", payload.get("consignee_email", "")),
        ]
        y = height - 122
        for idx, (label, value) in enumerate(info):
            x = 48 if idx % 2 == 0 else 318
            if idx and idx % 2 == 0:
                y -= 16
            pdf.drawString(x, y, f"{label}:")
            draw_text_fit(pdf, value, x + 82, y, 150, size=9)
        transport = [
            ("Carrier", payload.get("carrier", "")), ("Vessel", payload.get("vessel", "")),
            ("Voyage No", payload.get("voyage_no", "")), ("Container", f"{payload.get('container_count','')} x {payload.get('container_type','')}".strip()),
            ("ETD", payload.get("etd", "")), ("ETA", payload.get("eta", "")),
            ("Cut-off", payload.get("cut_off_date", "")), ("Loading Place", payload.get("loading_place", "")),
            ("POL", payload.get("port_of_loading", "")), ("POD", payload.get("port_of_discharge", "")),
            ("Delivery", payload.get("place_of_delivery", "")),
        ]
        y2 = height - 210
        for idx, (label, value) in enumerate(transport):
            x = 48 if idx % 2 == 0 else 318
            if idx and idx % 2 == 0:
                y2 -= 16
            pdf.drawString(x, y2, f"{label}:")
            draw_text_fit(pdf, value, x + 82, y2, 150, size=9)

    def table_header(y):
        pdf.setFillColor(navy)
        pdf.rect(40, y, width - 80, 24, stroke=0, fill=1)
        pdf.setFillColor(colors.white)
        pdf.setFont(TP_UNICODE_BOLD, 8)
        for x, label in [(48, "No"), (74, "Item"), (218, "HS Code"), (300, "Qty"), (354, "Carton"), (412, "Net Weight"), (492, "Gross Weight")]:
            pdf.drawString(x, y + 8, label)
        pdf.setFillColor(colors.black)

    header()
    table_start_y = height - 360
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
    signature_y = max(90, summary_y - 42)
    pdf.setStrokeColor(colors.black)
    pdf.line(380, signature_y, 555, signature_y)
    pdf.setFont(TP_UNICODE, 9)
    pdf.drawString(415, signature_y - 15, "Authorized Signature")
    footer()
    pdf.save()
    buffer.seek(0)
    return buffer


@router.post("/booking/pdf")
def create_booking_pdf(request: Request, payload: dict = Body(...)):
    account_id = _account_id(request)
    validate_booking_links(payload.get("shipment_no", ""), payload.get("si_no", ""), payload.get("packing_no", ""), payload.get("bl_no", ""), payload.get("invoice_no", ""), account_id)
    payload = public_booking(payload)
    payload = resolve_booking_snapshot(payload, account_id)
    pdf_buffer = create_booking_pdf_buffer(payload)
    filename = f"{payload.get('booking_record_no', 'booking')}.pdf"
    return Response(pdf_buffer.getvalue(), media_type="application/pdf", headers={"Content-Disposition": f"attachment; filename={filename}"})


@router.get("/booking-pdf/{booking_record_no}")
def booking_pdf(booking_record_no: str, request: Request):
    account_id = _account_id(request)
    record = resolve_booking_snapshot(public_booking(_owned_booking(booking_record_no, account_id)), account_id)
    set_pdf_export_record(request, record)
    pdf_buffer = create_booking_pdf_buffer(record)
    return Response(pdf_buffer.getvalue(), media_type="application/pdf", headers={"Content-Disposition": f"attachment; filename={booking_record_no}.pdf"})
