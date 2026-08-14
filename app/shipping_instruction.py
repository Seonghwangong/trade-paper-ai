from typing import Annotated, List, Optional
from copy import deepcopy
from datetime import datetime
from io import BytesIO
import html as html_lib
import json
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
from app.shipment import link_direct_document, shipment_context_redirect_url, shipment_detail_redirect_url
from app.account_shipping_instruction import ensure_legacy_shipping_instruction_ownership, public_shipping_instruction
from app.snapshot import assign_item_ids, fill_missing_snapshot_fields, set_submitted_snapshot_fields, snapshot_value
from app.export import set_pdf_export_record
from app.auth import USERS_FILE
from app import packing as packing_module
from app import invoice as invoice_module
from app import shipment as shipment_module
from app import buyer as buyer_module
from app.account_company import load_account_company
from app.routers.company import ACCOUNT_COMPANIES_FILE
from app.ui import imported_section_css, render_imported_section

SI_FILE = data_path("shipping_instructions.json")
PACKING_FILE = data_path("packing_lists.json")
INVOICE_FILE = data_path("invoices.json")
SHIPMENT_FILE = data_path("shipments.json")


def _account_id(request):
    user = request.scope.get("trade_paper_user") or {}
    return str(user.get("account_id", "") or "").strip()


def load_shipping_instruction_records():
    return ensure_legacy_shipping_instruction_ownership(SI_FILE, USERS_FILE)


def owned_shipping_instruction_records(account_id):
    owner = str(account_id or "").strip()
    return [
        record for record in load_shipping_instruction_records()
        if isinstance(record, dict)
        and str(record.get("account_id", "") or "").strip() == owner
        and not record.get("archived_at")
    ]


def load_shipping_instructions(account_id):
    return [public_shipping_instruction(record) for record in owned_shipping_instruction_records(account_id)]


def _owned_shipping_instruction(si_no, account_id):
    target = str(si_no or "").strip()
    record = next(
        (record for record in owned_shipping_instruction_records(account_id)
         if str(record.get("si_no", "") or "").strip() == target),
        None,
    )
    if record is None:
        raise HTTPException(status_code=404, detail="Shipping instruction not found")
    return record


def save_shipping_instructions(records):
    atomic_write_json(SI_FILE, records, list)


def load_packing_lists(account_id):
    return packing_module.owned_packing_records(account_id)


def load_invoices(account_id):
    return invoice_module.load_invoices(account_id)


def load_shipments(account_id):
    return shipment_module.load_shipments(account_id)


def validate_si_links(packing_no, invoice_no, account_id, shipment_no=""):
    packing = require_existing_reference("Packing List", packing_no, load_packing_lists(account_id), "packing_no", required=True)
    require_consistent_reference("Invoice", invoice_no, packing.get("invoice_no", ""), "selected Packing List")
    require_existing_reference("Shipment", shipment_no, load_shipments(account_id), "shipment_no")


def next_si_no(records):
    return next_identifier(records, "si_no", "SI")


def html_attr(value):
    return html_lib.escape(str(value or ""), quote=True)


def html_text(value):
    return html_lib.escape(str(value or ""))


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


def draw_text_fit(pdf, text, x, y, max_width, font=TP_UNICODE, size=8, min_size=6):
    text = str(text or "")
    current_size = size
    while current_size > min_size and pdf.stringWidth(text, font, current_size) > max_width:
        current_size -= 0.5
    pdf.setFont(font, current_size)
    pdf.drawString(x, y, fit_pdf_text(pdf, text, max_width, font, current_size))


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


def build_item_rows(items):
    if not items:
        items = [{}]

    rows = ""
    for item in items:
        rows += f"""
<div class="item-row">
<input type="hidden" name="item_id" value="{html_attr(item.get('item_id', ''))}">
<input type="text" name="item_name" value="{html_attr(item.get('name', ''))}" placeholder="Item Name">
<input type="text" name="hs_code" value="{html_attr(item.get('hs_code', ''))}" placeholder="HS Code">
<input type="text" name="quantity" value="{html_attr(item.get('quantity', ''))}" placeholder="Quantity">
<input type="text" name="carton" value="{html_attr(item.get('carton', ''))}" placeholder="Carton" oninput="calculateTotals()">
<input type="text" name="net_weight" value="{html_attr(item.get('net_weight', ''))}" placeholder="Net Weight" oninput="calculateTotals()">
<input type="text" name="gross_weight" value="{html_attr(item.get('gross_weight', ''))}" placeholder="Gross Weight" oninput="calculateTotals()">
<button class="remove" type="button" onclick="removeItem(this)">Remove Item</button>
</div>
"""
    return rows


def blank_payload():
    return {
        "si_no": "",
        "si_date": datetime.now().strftime("%Y-%m-%d"),
        "packing_no": "",
        "invoice_no": "",
        "shipment_no": "",
        "booking_no": "",
        "shipper": "",
        "consignee": "",
        "exporter_name": "",
        "exporter_address": "",
        "exporter_email": "",
        "exporter_phone": "",
        "consignee_name": "",
        "consignee_address": "",
        "consignee_email": "",
        "country_of_origin": "",
        "destination_country": "",
        "notify_party": "",
        "carrier": "",
        "vessel": "",
        "voyage_no": "",
        "port_of_loading": "",
        "port_of_discharge": "",
        "place_of_delivery": "",
        "shipping_marks": "",
        "freight_terms": "",
        "special_instructions": "",
        "items": [],
        "total_carton": "",
        "total_net_weight": "",
        "total_gross_weight": "",
    }


def _first_record(records, field, value):
    target = str(value or "").strip()
    if not target:
        return {}
    return next(
        (record for record in records
         if str(record.get(field, "") or "").strip() == target),
        {},
    )


def resolve_si_snapshot(record, account_id, shipment=None, packing=None, invoice=None, preserve_empty=None):
    """Resolve a read-only Shipping Instruction snapshot from account-owned sources."""
    resolved = deepcopy(record or {})
    if preserve_empty is None:
        preserve_empty = bool(resolved.get("si_no"))

    shipment_no = str(resolved.get("shipment_no", "") or "").strip()
    if shipment is None:
        shipment = _first_record(load_shipments(account_id), "shipment_no", shipment_no)
    shipment = shipment or {}

    packing_no = str(snapshot_value(
        resolved, "packing_no", (shipment.get("packing_no"),), preserve_empty=preserve_empty,
    ) or "").strip()
    if packing is None:
        packing = _first_record(load_packing_lists(account_id), "packing_no", packing_no)
    packing = packing or {}

    invoice_no = str(snapshot_value(
        resolved, "invoice_no",
        (shipment.get("invoice_no"), packing.get("invoice_no")),
        preserve_empty=preserve_empty,
    ) or "").strip()
    if invoice is None:
        invoice = _first_record(load_invoices(account_id), "invoice_no", invoice_no)
    invoice = invoice or {}

    company = load_account_company(account_id, ACCOUNT_COMPANIES_FILE)
    consignee_lookup = (
        resolved.get("consignee_name") or resolved.get("consignee")
        or shipment.get("consignee") or packing.get("buyer") or invoice.get("buyer") or ""
    )
    buyer = next(
        (candidate for candidate in buyer_module.load_buyers(account_id)
         if str(candidate.get("name", "") or "").strip().casefold()
         == str(consignee_lookup or "").strip().casefold()),
        {},
    )

    fallbacks = {
        "exporter_name": (resolved.get("shipper"), shipment.get("shipper"), packing.get("seller"), invoice.get("seller"), company.get("name")),
        "exporter_address": (shipment.get("shipper_address"), packing.get("seller_address"), invoice.get("seller_address"), company.get("address")),
        "exporter_email": (shipment.get("shipper_email"), packing.get("seller_email"), invoice.get("seller_email"), company.get("email")),
        "exporter_phone": (shipment.get("shipper_phone"), packing.get("seller_phone"), invoice.get("seller_phone"), company.get("phone")),
        "consignee_name": (resolved.get("consignee"), shipment.get("consignee"), packing.get("buyer"), invoice.get("buyer"), buyer.get("name")),
        "consignee_address": (shipment.get("consignee_address"), packing.get("buyer_address"), invoice.get("buyer_address"), buyer.get("address")),
        "consignee_email": (shipment.get("consignee_email"), packing.get("buyer_email"), invoice.get("buyer_email"), buyer.get("email")),
        "booking_no": (shipment.get("booking_no"), packing.get("booking_no"), invoice.get("booking_no")),
        "country_of_origin": (shipment.get("country_of_origin"), shipment.get("origin_country"), packing.get("country_of_origin"), invoice.get("country_of_origin"), company.get("country")),
        "destination_country": (shipment.get("destination_country"), packing.get("destination_country"), invoice.get("destination_country"), buyer.get("country")),
    }
    fill_missing_snapshot_fields(resolved, fallbacks, preserve_empty=preserve_empty)
    resolved["shipper"] = resolved.get("exporter_name", resolved.get("shipper", ""))
    resolved["consignee"] = resolved.get("consignee_name", resolved.get("consignee", ""))

    if "items" not in resolved or (not preserve_empty and not resolved["items"]):
        resolved["items"] = deepcopy(
            shipment.get("items") or packing.get("items") or invoice.get("items") or []
        )
    for total, item_field in (
        ("total_carton", "carton"),
        ("total_net_weight", "net_weight"),
        ("total_gross_weight", "gross_weight"),
    ):
        if total not in resolved or (not preserve_empty and not resolved[total]):
            resolved[total] = (
                shipment.get(total) or packing.get(total)
                or format_number(numeric_total(resolved.get("items", []), item_field))
            )
    resolved.update({
        "shipment_no": shipment_no,
        "packing_no": packing_no,
        "invoice_no": invoice_no,
    })
    from app import product as product_module
    product_module.enrich_items_from_products(resolved.get("items", []), account_id)
    return resolved


def payload_from_packing(packing_no, account_id):
    packing = _first_record(load_packing_lists(account_id), "packing_no", packing_no)
    if not packing:
        return blank_payload()
    payload = blank_payload()
    payload["packing_no"] = str(packing.get("packing_no", "") or "")
    return resolve_si_snapshot(
        payload, account_id, packing=packing, preserve_empty=False,
    )


def build_record(
    si_no, si_date, packing_no, invoice_no, shipper, consignee, notify_party,
    carrier, vessel, voyage_no, port_of_loading, port_of_discharge,
    place_of_delivery, shipping_marks, freight_terms, special_instructions,
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
        "si_no": si_no,
        "si_date": si_date,
        "packing_no": packing_no,
        "invoice_no": invoice_no,
        "shipper": shipper,
        "consignee": consignee,
        "notify_party": notify_party,
        "carrier": carrier,
        "vessel": vessel,
        "voyage_no": voyage_no,
        "port_of_loading": port_of_loading,
        "port_of_discharge": port_of_discharge,
        "place_of_delivery": place_of_delivery,
        "shipping_marks": shipping_marks,
        "freight_terms": freight_terms,
        "special_instructions": special_instructions,
        "items": items,
        "total_carton": total_carton,
        "total_net_weight": total_net_weight,
        "total_gross_weight": total_gross_weight,
    }


def valid_shipment_context(shipment_no):
    normalized = str(shipment_no or "").strip()
    if not normalized:
        return ""
    return normalized if any(
        isinstance(record, dict) and str(record.get("shipment_no", "") or "").strip() == normalized
        for record in load_json_strict(SHIPMENT_FILE, [], list)
    ) else ""


def shipment_return_response(shipment_no, si_no):
    url = f'/shipment/{quote(shipment_no, safe="")}'
    return HTMLResponse(f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Shipping Instruction Saved</title><style>*{{box-sizing:border-box}}body{{margin:0;background:#F3F4F6;color:#111827;font-family:Arial,sans-serif}}main{{min-height:100vh;display:grid;place-items:center;padding:24px}}.card{{width:min(560px,100%);background:white;border:1px solid #E5E7EB;border-radius:18px;padding:34px;text-align:center;box-shadow:0 14px 34px rgba(15,23,42,.09)}}h1{{margin:0 0 10px}}p{{color:#475569}}a{{display:inline-block;margin-top:14px;padding:12px 18px;background:#111827;color:white;text-decoration:none;border-radius:10px;font-weight:800}}a:focus-visible{{outline:3px solid #2563EB;outline-offset:3px}}</style></head><body><main><section class="card"><h1>Shipping Instruction Saved</h1><p>✓ Shipping Instruction saved successfully.</p><p>{html_text(si_no)}</p><a href="{html_attr(url)}">Return to Shipment</a></section></main></body></html>""")


def _safe_json_for_html(value):
    return json.dumps(value, ensure_ascii=False).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


def render_form(record, action, title, button_text, show_si_no=False, shipment_no="", account_id=""):
    rows = build_item_rows(record.get("items", []))
    si_no_input = ""
    if show_si_no:
        si_no_input = f'<input type="text" name="si_no" value="{html_attr(record.get("si_no", ""))}" placeholder="S/I No" readonly>'
    packings = load_packing_lists(account_id)
    selected_packing = str(record.get("packing_no", "") or "")
    packing_options = '<option value="">Select Packing List</option>' + "".join(
        f'<option value="{html_attr(packing.get("packing_no", ""))}"'
        f'{" selected" if str(packing.get("packing_no", "") or "") == selected_packing else ""}>'
        f'{html_text(packing.get("packing_no", ""))}</option>'
        for packing in packings
        if str(packing.get("packing_no", "") or "").strip()
    )
    packing_sources = {
        str(packing.get("packing_no", "") or ""): resolve_si_snapshot(
            {"packing_no": packing.get("packing_no", "")},
            account_id,
            packing=packing,
            preserve_empty=False,
        )
        for packing in packings
        if str(packing.get("packing_no", "") or "").strip()
    }

    html = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Shipping Instruction</title>
<style>
body{font-family:Arial,sans-serif;background:#f3f4f6;padding:40px;color:#111827;}
.container{max-width:1080px;margin:auto;background:white;padding:35px;border-radius:16px;box-shadow:0 12px 35px rgba(15,23,42,.08);}
h1{text-align:center;font-size:46px;margin:8px 0 10px;}
.sub{text-align:center;color:#6B7280;margin-bottom:35px;}
.nav-row{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:25px;}
.card{border:1px solid #E5E7EB;border-radius:16px;padding:25px;margin-bottom:25px;background:#fff;}
.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px;}
.item-row{display:grid;grid-template-columns:1.35fr 1fr .8fr .8fr .9fr .9fr;gap:12px;border:1px solid #E5E7EB;border-radius:14px;padding:18px;margin-bottom:16px;background:#F9FAFB;}
input,select,textarea{width:100%;padding:14px;border:1px solid #D1D5DB;border-radius:10px;font-size:16px;box-sizing:border-box;background:white;}
textarea{min-height:90px;resize:vertical;}
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
<a href="/si-list"><button class="small" type="button">S/I List</button></a>
</div>
<h1>__TITLE__</h1>
<p class="sub">Prepare shipping instructions from saved packing cargo data</p>

<form action="__ACTION__" method="post">
__SHIPMENT_CONTEXT__
<div class="card">
<h2>Document Information</h2>
<div class="grid">
__SI_NO_INPUT__
<input type="date" name="si_date" value="__SI_DATE__">
<select id="packing_no" name="packing_no" aria-label="Packing No">__PACKING_OPTIONS__</select>
<input type="text" name="invoice_no" value="__INVOICE_NO__" placeholder="Invoice No">
<input type="text" name="booking_no" value="__BOOKING_NO__" placeholder="Booking No">
</div>
</div>

__IMPORTED_PARTY_START__
<h2>Party Information</h2><div class="grid">
<input type="text" name="shipper" value="__SHIPPER__" placeholder="Shipper">
<input type="text" name="exporter_address" value="__EXPORTER_ADDRESS__" placeholder="Exporter Address">
<input type="email" name="exporter_email" value="__EXPORTER_EMAIL__" placeholder="Exporter Email">
<input type="text" name="exporter_phone" value="__EXPORTER_PHONE__" placeholder="Exporter Phone">
<input type="text" name="consignee" value="__CONSIGNEE__" placeholder="Consignee">
<input type="text" name="consignee_address" value="__CONSIGNEE_ADDRESS__" placeholder="Consignee Address">
<input type="email" name="consignee_email" value="__CONSIGNEE_EMAIL__" placeholder="Consignee Email">
<input type="text" name="notify_party" value="__NOTIFY_PARTY__" placeholder="Notify Party">
</div>
__IMPORTED_PARTY_END__

<div class="card">
<h2>Transport Information</h2>
<div class="grid">
<input type="text" name="carrier" value="__CARRIER__" placeholder="Carrier">
<input type="text" name="vessel" value="__VESSEL__" placeholder="Vessel">
<input type="text" name="voyage_no" value="__VOYAGE_NO__" placeholder="Voyage No">
<input type="text" name="port_of_loading" value="__PORT_OF_LOADING__" placeholder="Port of Loading">
<input type="text" name="port_of_discharge" value="__PORT_OF_DISCHARGE__" placeholder="Port of Discharge">
<input type="text" name="place_of_delivery" value="__PLACE_OF_DELIVERY__" placeholder="Place of Delivery">
<input type="text" name="country_of_origin" value="__COUNTRY_OF_ORIGIN__" placeholder="Country of Origin">
<input type="text" name="destination_country" value="__DESTINATION_COUNTRY__" placeholder="Destination Country">
</div>
</div>

<div class="card">
<h2>Shipping Details</h2>
<div class="grid">
<textarea name="shipping_marks" placeholder="Shipping Marks">__SHIPPING_MARKS__</textarea>
<textarea name="freight_terms" placeholder="Freight Terms">__FREIGHT_TERMS__</textarea>
</div>
<br>
<textarea name="special_instructions" placeholder="Special Instructions">__SPECIAL_INSTRUCTIONS__</textarea>
</div>

__IMPORTED_CARGO_START__
<h2>Cargo Information</h2>
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
__IMPORTED_CARGO_END__

<button class="full" type="submit">__BUTTON_TEXT__</button>
</form>
</div>

<script>
function addItem(){
  const div = document.createElement("div");
  div.className = "item-row";
  div.innerHTML = `
    <input type="text" name="item_name" placeholder="Item Name">
    <input type="text" name="hs_code" placeholder="HS Code">
    <input type="text" name="quantity" placeholder="Quantity">
    <input type="text" name="carton" placeholder="Carton" oninput="calculateTotals()">
    <input type="text" name="net_weight" placeholder="Net Weight" oninput="calculateTotals()">
    <input type="text" name="gross_weight" placeholder="Gross Weight" oninput="calculateTotals()">
    <button class="remove" type="button" onclick="removeItem(this)">Remove Item</button>`;
  document.getElementById("items").appendChild(div);
}
function removeItem(btn){
  btn.closest(".item-row").remove();
  calculateTotals();
}
function sumByName(name){
  return Array.from(document.querySelectorAll(`[name="${name}"]`)).reduce((sum, input) => sum + (parseFloat(input.value) || 0), 0);
}
function formatNumber(value){
  return Number.isInteger(value) ? String(value) : String(Number(value.toFixed(3)));
}
function calculateTotals(){
  const carton = sumByName("carton");
  const net = sumByName("net_weight");
  const gross = sumByName("gross_weight");
  document.getElementById("total_carton").value = formatNumber(carton);
  document.getElementById("total_net_weight").value = formatNumber(net);
  document.getElementById("total_gross_weight").value = formatNumber(gross);
  document.getElementById("cartonText").textContent = formatNumber(carton);
  document.getElementById("netText").textContent = formatNumber(net);
  document.getElementById("grossText").textContent = formatNumber(gross);
}
calculateTotals();
const packingSources=__PACKING_SOURCES__;
function replaceCargoItems(items){
  const container=document.getElementById("items");container.replaceChildren();
  (items&&items.length?items:[{}]).forEach(function(item){
    const row=document.createElement("div");row.className="item-row";
    [["item_id","hidden"],["item_name","text"],["hs_code","text"],["quantity","text"],["carton","text"],["net_weight","text"],["gross_weight","text"]].forEach(function(definition){
      const input=document.createElement("input");input.name=definition[0];input.type=definition[1];
      const field=definition[0]==="item_name"?"name":definition[0];input.value=String(item[field]||"");
      if(definition[1]!=="hidden")input.placeholder={item_name:"Item Name",hs_code:"HS Code",quantity:"Quantity",carton:"Carton",net_weight:"Net Weight",gross_weight:"Gross Weight"}[definition[0]]||definition[0];
      if(["carton","net_weight","gross_weight"].includes(definition[0]))input.addEventListener("input",calculateTotals);
      row.appendChild(input);
    });
    const remove=document.createElement("button");remove.className="remove";remove.type="button";remove.textContent="Remove Item";remove.addEventListener("click",function(){removeItem(remove);});row.appendChild(remove);container.appendChild(row);
  });calculateTotals();
}
document.getElementById("packing_no").addEventListener("change",function(event){
  const source=packingSources[event.target.value];if(!source)return;
  [["invoice_no","invoice_no"],["shipper","shipper"],["consignee","consignee"],["exporter_address","exporter_address"],["exporter_email","exporter_email"],["exporter_phone","exporter_phone"],["consignee_address","consignee_address"],["consignee_email","consignee_email"],["country_of_origin","country_of_origin"],["destination_country","destination_country"]].forEach(function(fields){const input=document.querySelector('[name="'+fields[0]+'"]');if(input)input.value=String(source[fields[1]]||"");});
  replaceCargoItems(source.items||[]);
});
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
        "__SI_NO_INPUT__": si_no_input,
        "__SHIPMENT_CONTEXT__": f'<input type="hidden" name="shipment_no" value="{html_attr(shipment_no)}">' if shipment_no else "",
        "__SI_DATE__": html_attr(record.get("si_date", "")),
        "__PACKING_OPTIONS__": packing_options,
        "__PACKING_SOURCES__": _safe_json_for_html(packing_sources),
        "__INVOICE_NO__": html_attr(record.get("invoice_no", "")),
        "__BOOKING_NO__": html_attr(record.get("booking_no", "")),
        "__SHIPPER__": html_attr(record.get("shipper", "")),
        "__EXPORTER_ADDRESS__": html_attr(record.get("exporter_address", "")),
        "__EXPORTER_EMAIL__": html_attr(record.get("exporter_email", "")),
        "__EXPORTER_PHONE__": html_attr(record.get("exporter_phone", "")),
        "__CONSIGNEE__": html_attr(record.get("consignee", "")),
        "__CONSIGNEE_ADDRESS__": html_attr(record.get("consignee_address", "")),
        "__CONSIGNEE_EMAIL__": html_attr(record.get("consignee_email", "")),
        "__NOTIFY_PARTY__": html_attr(record.get("notify_party", "")),
        "__CARRIER__": html_attr(record.get("carrier", "")),
        "__VESSEL__": html_attr(record.get("vessel", "")),
        "__VOYAGE_NO__": html_attr(record.get("voyage_no", "")),
        "__PORT_OF_LOADING__": html_attr(record.get("port_of_loading", "")),
        "__PORT_OF_DISCHARGE__": html_attr(record.get("port_of_discharge", "")),
        "__PLACE_OF_DELIVERY__": html_attr(record.get("place_of_delivery", "")),
        "__COUNTRY_OF_ORIGIN__": html_attr(record.get("country_of_origin", "")),
        "__DESTINATION_COUNTRY__": html_attr(record.get("destination_country", "")),
        "__SHIPPING_MARKS__": html_text(record.get("shipping_marks", "")),
        "__FREIGHT_TERMS__": html_text(record.get("freight_terms", "")),
        "__SPECIAL_INSTRUCTIONS__": html_text(record.get("special_instructions", "")),
        "__ITEM_ROWS__": rows,
        "__TOTAL_CARTON__": html_attr(record.get("total_carton", "")),
        "__TOTAL_NET_WEIGHT__": html_attr(record.get("total_net_weight", "")),
        "__TOTAL_GROSS_WEIGHT__": html_attr(record.get("total_gross_weight", "")),
        "__BUTTON_TEXT__": html_text(button_text),
    }
    for key, value in replacements.items():
        html = html.replace(key, value)
    return HTMLResponse(html)


@router.get("/si-list", response_class=HTMLResponse)
def si_list(request: Request, search: str = ""):
    records = sorted(load_shipping_instructions(_account_id(request)), key=lambda record: record.get("si_no", ""), reverse=True)
    if search:
        term = search.lower()
        records = [
            record for record in records
            if term in str(record.get("si_no", "")).lower()
            or term in str(record.get("packing_no", "")).lower()
            or term in str(record.get("invoice_no", "")).lower()
            or term in str(record.get("shipper", "")).lower()
            or term in str(record.get("consignee", "")).lower()
        ]

    rows = ""
    for record in records:
        si_no = record.get("si_no", "")
        rows += f"""
<tr>
<td>{html_text(si_no)}</td>
<td>{html_text(record.get('si_date', ''))}</td>
<td>{html_text(record.get('packing_no', ''))}</td>
<td>{html_text(record.get('invoice_no', ''))}</td>
<td>{html_text(record.get('shipper', ''))}</td>
<td>{html_text(record.get('consignee', ''))}</td>
<td>{html_text(record.get('total_carton', ''))}</td>
<td><a class="link" href="/si-pdf/{html_attr(si_no)}">PDF</a></td>
<td><a class="link" href="/send-email/shipping-instruction/{html_attr(si_no)}">Send Email</a></td>
<td><a class="link" href="/edit-si/{html_attr(si_no)}">Edit</a></td>
<td><a class="danger" href="/delete-si/{html_attr(si_no)}">Delete</a></td>
</tr>
"""

    html = f"""
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Shipping Instructions</title>
<style>
body{{font-family:Arial,sans-serif;background:#f3f4f6;padding:40px;color:#111827;}}
.container{{width:94%;margin:auto;}}
h1{{text-align:center;font-size:46px;margin:8px 0 10px;}}
.sub{{text-align:center;color:#6B7280;margin-bottom:28px;}}
.toolbar{{display:flex;justify-content:space-between;gap:12px;align-items:center;flex-wrap:wrap;margin-bottom:20px;}}
.nav{{display:flex;gap:12px;flex-wrap:wrap;}}
button,.btn{{display:inline-block;padding:13px 18px;background:#111827;color:white;border:none;border-radius:12px;text-decoration:none;font-size:15px;cursor:pointer;}}
.reset{{background:#6B7280;}}
.search{{display:flex;gap:10px;flex-wrap:wrap;}}
input{{padding:13px;border:1px solid #D1D5DB;border-radius:10px;font-size:15px;min-width:260px;}}
.count{{font-weight:bold;color:#374151;margin-bottom:14px;}}
.table-wrap{{background:white;border:1px solid #E5E7EB;border-radius:16px;overflow:hidden;box-shadow:0 12px 35px rgba(15,23,42,.08);}}
table{{width:100%;border-collapse:collapse;}}
th{{background:#111827;color:white;text-align:left;padding:14px;font-size:14px;}}
td{{padding:14px;border-bottom:1px solid #E5E7EB;font-size:14px;}}
.link{{background:#111827;color:white;padding:8px 11px;border-radius:9px;text-decoration:none;}}
.danger{{background:#991B1B;color:white;padding:8px 11px;border-radius:9px;text-decoration:none;}}
</style>
</head>
<body>
<div class="container">
<h1>Shipping Instructions</h1>
<p class="sub">Manage shipping instructions and generate PDFs</p>
<div class="toolbar">
<div class="nav">
<a class="btn" href="/">Dashboard</a>
<a class="btn" href="/si-form">+ New S/I</a>
</div>
<form class="search" action="/si-list" method="get">
<input type="text" name="search" value="{html_attr(search)}" placeholder="Search S/I, packing, invoice, shipper, consignee">
<button type="submit">Search</button>
<a class="btn reset" href="/si-list">Reset</a>
</form>
</div>
<div class="count">Total Shipping Instructions: {len(records)}</div>
<div class="table-wrap">
<table>
<thead>
<tr>
<th>S/I No</th><th>Date</th><th>Packing No</th><th>Invoice No</th><th>Shipper</th><th>Consignee</th><th>Total Cartons</th><th>PDF</th><th>Email</th><th>Edit</th><th>Delete</th>
</tr>
</thead>
<tbody>{rows}</tbody>
</table>
</div>
</div>
</body>
</html>
"""
    return HTMLResponse(html)


@router.get("/si-form", response_class=HTMLResponse)
def si_form(request: Request, packing_no: str = "", shipment_no: str = ""):
    account_id = _account_id(request)
    record = payload_from_packing(packing_no, account_id) if packing_no else blank_payload()
    record["si_no"] = next_si_no(load_shipping_instruction_records())
    shipment_context = valid_shipment_context(shipment_no)
    record["shipment_no"] = shipment_context
    record = resolve_si_snapshot(record, account_id, preserve_empty=False)
    return render_form(record, "/si", "New Shipping Instruction", "Save Shipping Instruction", show_si_no=True, shipment_no=shipment_context, account_id=account_id)


@router.post("/si")
def save_si(
    request: Request,
    shipment_no: str = Form(""),
    si_date: str = Form(""),
    packing_no: str = Form(""),
    invoice_no: str = Form(""),
    shipper: str = Form(""),
    consignee: str = Form(""),
    notify_party: str = Form(""),
    carrier: str = Form(""),
    vessel: str = Form(""),
    voyage_no: str = Form(""),
    port_of_loading: str = Form(""),
    port_of_discharge: str = Form(""),
    place_of_delivery: str = Form(""),
    shipping_marks: str = Form(""),
    freight_terms: str = Form(""),
    special_instructions: str = Form(""),
    item_name: List[str] = Form([]),
    item_id: List[str] = Form([]),
    hs_code: List[str] = Form([]),
    quantity: List[str] = Form([]),
    carton: List[str] = Form([]),
    net_weight: List[str] = Form([]),
    gross_weight: List[str] = Form([]),
    total_carton: str = Form(""),
    total_net_weight: str = Form(""),
    total_gross_weight: str = Form(""),
    booking_no: Annotated[Optional[str], Form()] = None,
    country_of_origin: Annotated[Optional[str], Form()] = None,
    destination_country: Annotated[Optional[str], Form()] = None,
    exporter_address: Annotated[Optional[str], Form()] = None,
    exporter_email: Annotated[Optional[str], Form()] = None,
    exporter_phone: Annotated[Optional[str], Form()] = None,
    consignee_address: Annotated[Optional[str], Form()] = None,
    consignee_email: Annotated[Optional[str], Form()] = None,
):
    account_id = _account_id(request)
    validate_si_links(packing_no, invoice_no, account_id, shipment_no)
    shipper = require_text("Shipper", shipper)
    consignee = require_text("Consignee", consignee)
    saved = {}
    def add_si(records):
        si_number = next_identifier(records, "si_no", "SI")
        record = build_record(
        si_number, si_date, packing_no, invoice_no, shipper, consignee,
        notify_party, carrier, vessel, voyage_no, port_of_loading, port_of_discharge,
        place_of_delivery, shipping_marks, freight_terms, special_instructions,
        item_name, hs_code, quantity, carton, net_weight, gross_weight,
        total_carton, total_net_weight, total_gross_weight,
        )
        assign_item_ids(record["items"], item_id)
        record.update({
            "shipment_no": shipment_no,
            "exporter_name": shipper,
            "consignee_name": consignee,
        })
        set_submitted_snapshot_fields(record, {
            "booking_no": booking_no,
            "country_of_origin": country_of_origin,
            "destination_country": destination_country,
            "exporter_address": exporter_address,
            "exporter_email": exporter_email,
            "exporter_phone": exporter_phone,
            "consignee_address": consignee_address,
            "consignee_email": consignee_email,
        })
        record = resolve_si_snapshot(record, account_id)
        record["account_id"] = account_id
        records.append(record)
        saved["si_no"] = si_number
    locked_json_mutation(SI_FILE, [], add_si, list)
    from app.audit_log import record_request_audit
    record_request_audit(request, "Create", "Shipping Instruction", saved["si_no"], path=SI_FILE.with_name("audit_log.json"))
    shipment_context = valid_shipment_context(shipment_no)
    if shipment_context:
        return RedirectResponse(
            shipment_context_redirect_url(shipment_context, "si_no", saved["si_no"], "/si-list"), status_code=303,
        )
    return RedirectResponse("/si-list", status_code=303)


@router.get("/edit-si/{si_no}", response_class=HTMLResponse)
def edit_si(si_no: str, request: Request):
    account_id = _account_id(request)
    record = resolve_si_snapshot(
        public_shipping_instruction(_owned_shipping_instruction(si_no, account_id)), account_id,
    )
    return render_form(
        record, f"/update-si/{html_attr(si_no)}", "Edit Shipping Instruction",
        "Update Shipping Instruction", show_si_no=True,
        shipment_no=record.get("shipment_no", ""), account_id=account_id,
    )


@router.post("/update-si/{si_no}")
def update_si(
    si_no: str,
    request: Request,
    si_date: str = Form(""),
    packing_no: str = Form(""),
    invoice_no: str = Form(""),
    shipper: str = Form(""),
    consignee: str = Form(""),
    notify_party: str = Form(""),
    carrier: str = Form(""),
    vessel: str = Form(""),
    voyage_no: str = Form(""),
    port_of_loading: str = Form(""),
    port_of_discharge: str = Form(""),
    place_of_delivery: str = Form(""),
    shipping_marks: str = Form(""),
    freight_terms: str = Form(""),
    special_instructions: str = Form(""),
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
    shipment_no: Annotated[Optional[str], Form()] = None,
    booking_no: Annotated[Optional[str], Form()] = None,
    country_of_origin: Annotated[Optional[str], Form()] = None,
    destination_country: Annotated[Optional[str], Form()] = None,
    exporter_address: Annotated[Optional[str], Form()] = None,
    exporter_email: Annotated[Optional[str], Form()] = None,
    exporter_phone: Annotated[Optional[str], Form()] = None,
    consignee_address: Annotated[Optional[str], Form()] = None,
    consignee_email: Annotated[Optional[str], Form()] = None,
):
    account_id = _account_id(request)
    current = public_shipping_instruction(_owned_shipping_instruction(si_no, account_id))
    effective_shipment_no = current.get("shipment_no", "") if shipment_no is None else shipment_no
    validate_si_links(packing_no, invoice_no, account_id, effective_shipment_no)
    shipper = require_text("Shipper", shipper)
    consignee = require_text("Consignee", consignee)
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
    def replace_si(records):
        for index, record in enumerate(records):
            if (record.get("si_no") != si_no
                    or str(record.get("account_id", "") or "").strip() != account_id):
                continue
            updated = build_record(
                si_no, si_date, packing_no, invoice_no, shipper, consignee,
                notify_party, carrier, vessel, voyage_no, port_of_loading,
                port_of_discharge, place_of_delivery, shipping_marks, freight_terms,
                special_instructions, item_name, hs_code, quantity, carton,
                net_weight, gross_weight, total_carton, total_net_weight,
                total_gross_weight,
            )
            assign_item_ids(updated["items"], item_id, current_items)
            updated.update({"exporter_name": shipper, "consignee_name": consignee})
            submitted_snapshot = {
                "shipment_no": shipment_no,
                "booking_no": booking_no,
                "country_of_origin": country_of_origin,
                "destination_country": destination_country,
                "exporter_address": exporter_address,
                "exporter_email": exporter_email,
                "exporter_phone": exporter_phone,
                "consignee_address": consignee_address,
                "consignee_email": consignee_email,
            }
            for field, value in submitted_snapshot.items():
                if value is not None:
                    updated[field] = value
                elif field in current:
                    updated[field] = deepcopy(current[field])
            updated = resolve_si_snapshot(updated, account_id)
            updated["account_id"] = account_id
            records[index] = updated
            return
        raise HTTPException(status_code=404, detail="Shipping instruction not found")
    locked_json_mutation(SI_FILE, [], replace_si, list)
    from app.audit_log import record_request_audit
    record_request_audit(request, "Update", "Shipping Instruction", si_no, path=SI_FILE.with_name("audit_log.json"))
    return RedirectResponse(
        shipment_detail_redirect_url(effective_shipment_no, account_id, "/si-list"), status_code=303,
    )


@router.get("/delete-si/{si_no}")
def delete_si(si_no: str, request: Request):
    _owned_shipping_instruction(si_no, _account_id(request))
    from app.archive import render_archive_page
    return render_archive_page("Shipping Instruction", si_no, f"/delete-si/{si_no}", "/si-list")

@router.post("/delete-si/{si_no}")
def confirm_delete_si(si_no: str, request: Request):
    account_id = _account_id(request)
    from app.archive import archive_document
    return archive_document(request, "shipping_instruction", si_no, "/si-list")
    _owned_shipping_instruction(si_no, account_id)
    dependencies = find_dependencies("Shipping Instruction", si_no, account_id)
    if dependencies:
        return render_delete_page("Shipping Instruction", si_no, f"/delete-si/{si_no}", "/si-list", dependencies, status_code=409)
    def remove(records):
        index = next((index for index, record in enumerate(records)
                      if isinstance(record, dict)
                      and str(record.get("si_no", "") or "").strip() == si_no
                      and str(record.get("account_id", "") or "").strip() == account_id), None)
        if index is None:
            raise HTTPException(status_code=404, detail="Shipping instruction not found")
        records.pop(index)
    locked_json_mutation(SI_FILE, [], remove, list)
    return RedirectResponse("/si-list", status_code=303)


@router.get("/si-data/{si_no}")
def si_data(si_no: str, request: Request):
    account_id = _account_id(request)
    return resolve_si_snapshot(
        public_shipping_instruction(_owned_shipping_instruction(si_no, account_id)), account_id,
    )


def create_shipping_instruction_pdf(payload):
    payload = public_shipping_instruction(payload)
    buffer = BytesIO()
    ensure_pdf_fonts()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4
    navy = colors.HexColor("#111827")
    light = colors.HexColor("#F3F4F6")
    border = colors.HexColor("#D1D5DB")
    muted = colors.HexColor("#6B7280")
    items = payload.get("items", [])

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
        pdf.drawCentredString(width / 2, height - 70, "SHIPPING INSTRUCTION")
        pdf.setFillColor(colors.black)

        pdf.setFont(TP_UNICODE_BOLD, 10)
        pdf.drawString(40, height - 122, "Document Information")
        pdf.setFont(TP_UNICODE, 9)
        left = [
            ("S/I No", payload.get("si_no", "")),
            ("S/I Date", payload.get("si_date", "")),
            ("Packing No", payload.get("packing_no", "")),
        ]
        right = [
            ("Invoice No", payload.get("invoice_no", "")),
        ]
        y = height - 140
        for label, value in left:
            pdf.drawString(48, y, f"{label}:")
            draw_text_fit(pdf, value, 130, y, 160, size=9)
            y -= 15
        y = height - 140
        for label, value in right:
            pdf.drawString(330, y, f"{label}:")
            draw_text_fit(pdf, value, 420, y, 130, size=9)
            y -= 15

        pdf.setFillColor(light)
        pdf.roundRect(40, height - 270, 160, 82, 6, stroke=0, fill=1)
        pdf.roundRect(218, height - 270, 160, 82, 6, stroke=0, fill=1)
        pdf.roundRect(396, height - 270, 160, 82, 6, stroke=0, fill=1)
        pdf.setFillColor(navy)
        pdf.setFont(TP_UNICODE_BOLD, 9)
        pdf.drawString(52, height - 207, "SHIPPER")
        pdf.drawString(230, height - 207, "CONSIGNEE")
        pdf.drawString(408, height - 207, "NOTIFY PARTY")
        pdf.setFillColor(colors.black)
        pdf.setFont(TP_UNICODE, 8)
        draw_text_fit(pdf, payload.get("shipper", ""), 52, height - 228, 130, size=8)
        draw_text_fit(pdf, payload.get("consignee", ""), 230, height - 228, 130, size=8)
        draw_text_fit(pdf, payload.get("notify_party", ""), 408, height - 228, 130, size=8)
        draw_text_fit(pdf, payload.get("exporter_address", ""), 52, height - 241, 130, size=7)
        draw_text_fit(pdf, " / ".join(filter(None, (
            payload.get("exporter_email", ""), payload.get("exporter_phone", ""),
        ))), 52, height - 253, 130, size=7)
        draw_text_fit(pdf, payload.get("consignee_address", ""), 230, height - 241, 130, size=7)
        draw_text_fit(pdf, payload.get("consignee_email", ""), 230, height - 253, 130, size=7)

        pdf.setFont(TP_UNICODE_BOLD, 10)
        pdf.drawString(40, height - 300, "Transport Information")
        pdf.setFont(TP_UNICODE, 9)
        info = [
            ("Carrier", payload.get("carrier", "")),
            ("Vessel", payload.get("vessel", "")),
            ("Voyage No", payload.get("voyage_no", "")),
            ("Port of Loading", payload.get("port_of_loading", "")),
            ("Port of Discharge", payload.get("port_of_discharge", "")),
            ("Place of Delivery", payload.get("place_of_delivery", "")),
        ]
        y2 = height - 318
        for index, (label, value) in enumerate(info):
            x = 48 if index % 2 == 0 else 318
            if index and index % 2 == 0:
                y2 -= 15
            pdf.drawString(x, y2, f"{label}:")
            draw_text_fit(pdf, value, x + 88, y2, 150, size=9)

    def table_header(y):
        pdf.setFillColor(navy)
        pdf.rect(40, y, width - 80, 24, stroke=0, fill=1)
        pdf.setFillColor(colors.white)
        pdf.setFont(TP_UNICODE_BOLD, 8)
        columns = [
            (48, "No"), (74, "Item"), (218, "HS Code"), (300, "Qty"),
            (354, "Carton"), (412, "Net Weight"), (492, "Gross Weight"),
        ]
        for x, label in columns:
            pdf.drawString(x, y + 8, label)
        pdf.setFillColor(colors.black)

    header()
    table_start_y = height - 385
    y = table_start_y
    table_header(y)
    y -= 20

    row_height = 22
    for idx, item in enumerate(items, start=1):
        if y < 150:
            footer()
            pdf.showPage()
            header()
            y = table_start_y
            table_header(y)
            y -= 20

        pdf.setStrokeColor(border)
        pdf.line(40, y - 5, width - 40, y - 5)
        pdf.setFillColor(colors.black)
        draw_text_fit(pdf, idx, 48, y + 4, 20, size=8)
        draw_text_fit(pdf, item.get("name", ""), 74, y + 4, 132, size=8)
        draw_text_fit(pdf, item.get("hs_code", ""), 218, y + 4, 68, size=8)
        draw_text_fit(pdf, item.get("quantity", ""), 300, y + 4, 42, size=8)
        draw_text_fit(pdf, item.get("carton", ""), 354, y + 4, 44, size=8)
        draw_text_fit(pdf, item.get("net_weight", ""), 412, y + 4, 66, size=8)
        draw_text_fit(pdf, item.get("gross_weight", ""), 492, y + 4, 58, size=8)
        y -= row_height

    details_height = 92
    summary_height = 78
    if y - details_height - summary_height < 105:
        footer()
        pdf.showPage()
        header()
        y = table_start_y

    y -= 15
    pdf.setFont(TP_UNICODE_BOLD, 10)
    pdf.drawString(40, y, "Shipping Marks")
    pdf.drawString(225, y, "Freight Terms")
    pdf.drawString(410, y, "Special Instructions")
    pdf.setFont(TP_UNICODE, 8)
    draw_text_fit(pdf, payload.get("shipping_marks", ""), 40, y - 16, 160, size=8)
    draw_text_fit(pdf, payload.get("freight_terms", ""), 225, y - 16, 160, size=8)
    draw_text_fit(pdf, payload.get("special_instructions", ""), 410, y - 16, 140, size=8)

    summary_y = y - details_height
    pdf.setFillColor(navy)
    pdf.roundRect(335, summary_y, 220, summary_height, 8, stroke=0, fill=1)
    pdf.setFillColor(colors.white)
    pdf.setFont(TP_UNICODE_BOLD, 10)
    pdf.drawString(350, summary_y + 52, f"Total Cartons: {payload.get('total_carton', '')}")
    pdf.drawString(350, summary_y + 32, f"Total Net Weight: {payload.get('total_net_weight', '')}")
    pdf.drawString(350, summary_y + 12, f"Total Gross Weight: {payload.get('total_gross_weight', '')}")
    pdf.setFillColor(colors.black)

    signature_y = max(90, summary_y - 42)
    pdf.setStrokeColor(colors.black)
    pdf.line(380, signature_y, 555, signature_y)
    pdf.setFont(TP_UNICODE, 9)
    pdf.drawString(415, signature_y - 15, "Authorized Signature")

    footer()
    pdf.save()
    buffer.seek(0)
    return buffer


@router.post("/si/pdf")
def create_si_pdf(request: Request, payload: dict = Body(...)):
    account_id = _account_id(request)
    validate_si_links(payload.get("packing_no", ""), payload.get("invoice_no", ""), account_id)
    payload = resolve_si_snapshot(public_shipping_instruction(payload), account_id)
    pdf_buffer = create_shipping_instruction_pdf(payload)
    return Response(
        pdf_buffer.getvalue(),
        media_type="application/pdf",
        headers={"Content-Disposition": "inline; filename=shipping_instruction.pdf"},
    )


@router.get("/si-pdf/{si_no}")
def si_pdf(si_no: str, request: Request):
    account_id = _account_id(request)
    record = resolve_si_snapshot(
        public_shipping_instruction(_owned_shipping_instruction(si_no, account_id)), account_id,
    )
    set_pdf_export_record(request, record)
    pdf_buffer = create_shipping_instruction_pdf(record)
    filename = f"{si_no}.pdf"
    return Response(
        pdf_buffer.getvalue(), media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )
