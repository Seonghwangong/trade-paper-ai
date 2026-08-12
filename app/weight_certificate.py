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

from app.storage import atomic_write_json, data_path, locked_json_mutation, next_identifier
from app.validation import require_consistent_reference, require_existing_reference, require_text
from app.referential_integrity import find_dependencies, render_delete_page
from app.account_weight import ensure_legacy_weight_ownership, public_weight
from app.snapshot import assign_item_ids, fill_missing_snapshot_fields, find_by_identifier, preserve_omitted_item_fields, resolve_source_chain, set_submitted_snapshot_fields
from app.export import set_pdf_export_record
from app.auth import USERS_FILE
from app.shipment import shipment_context_redirect_url, shipment_detail_redirect_url
from app import bill_of_lading as bill_of_lading_module
from app import packing as packing_module
from app import invoice as invoice_module
from app import product as product_module
from app import shipment as shipment_module
from app import buyer as buyer_module
from app.account_company import load_account_company
from app.routers.company import ACCOUNT_COMPANIES_FILE

WEIGHT_FILE = data_path("weight_certificates.json")
BL_FILE = data_path("bills_of_lading.json")
PACKING_FILE = data_path("packing_lists.json")
INVOICE_FILE = data_path("invoices.json")


def _account_id(request):
    user = request.scope.get("trade_paper_user") or {}
    return str(user.get("account_id", "") or "").strip()


def load_weight_records():
    return ensure_legacy_weight_ownership(WEIGHT_FILE, USERS_FILE)


def owned_weight_records(account_id):
    owner = str(account_id or "").strip()
    return [record for record in load_weight_records()
            if isinstance(record, dict) and str(record.get("account_id", "") or "").strip() == owner]


def load_weights(account_id):
    return [public_weight(record) for record in owned_weight_records(account_id)]


def _owned_weight(weight_no, account_id):
    target = str(weight_no or "").strip()
    record = find_by_identifier(
        owned_weight_records(account_id), "weight_no", target, normalize=True,
    )
    if record is None:
        raise HTTPException(status_code=404, detail="Weight certificate not found")
    return record


def save_weights(records):
    atomic_write_json(WEIGHT_FILE, records, list)


def load_bills_of_lading(account_id):
    return bill_of_lading_module.load_bills_of_lading(account_id)


def load_packing_lists(account_id):
    return packing_module.load_packing_lists(account_id)


def load_invoices(account_id):
    return invoice_module.load_invoices(account_id)


def load_products(account_id):
    return product_module.load_products(account_id)


def validate_weight_links(bl_no, packing_no, invoice_no, account_id, shipment_no=""):
    bill = require_existing_reference("Bill of Lading", bl_no, load_bills_of_lading(account_id), "bl_no", required=True)
    require_existing_reference("Packing List", packing_no, packing_module.load_packing_lists(account_id), "packing_no")
    require_existing_reference("Invoice", invoice_no, invoice_module.load_invoices(account_id), "invoice_no")
    require_consistent_reference("Packing List", packing_no, bill.get("packing_no", ""), "selected Bill of Lading")
    require_consistent_reference("Invoice", invoice_no, bill.get("invoice_no", ""), "selected Bill of Lading")
    if shipment_no:
        shipment = require_existing_reference("Shipment", shipment_no, shipment_module.load_shipments(account_id), "shipment_no", required=True)
        require_consistent_reference("Bill of Lading", bl_no, shipment.get("bl_no", ""), "selected Shipment")
        require_consistent_reference("Packing List", packing_no, shipment.get("packing_no", ""), "selected Shipment")
        require_consistent_reference("Invoice", invoice_no, shipment.get("invoice_no", ""), "selected Shipment")


def next_weight_no(records):
    return next_identifier(records, "weight_no", "WT")


def html_attr(value):
    return html_lib.escape(str(value or ""), quote=True)


def html_text(value):
    return html_lib.escape(str(value or ""))


def find_product(item, products):
    item_name = str(item.get("name", "") or "").strip().lower()
    item_hs = str(item.get("hs_code", "") or "").strip().lower()
    for product in products:
        if item_name and str(product.get("name", "") or "").strip().lower() == item_name:
            return product
    for product in products:
        if item_hs and str(product.get("hs_code", "") or "").strip().lower() == item_hs:
            return product
    return {}


def bl_select_html(selected, account_id):
    options = ['<select name="bl_no">', '<option value="">Select B/L</option>']
    for bill in load_bills_of_lading(account_id):
        bl_no = bill.get("bl_no", "")
        if not bl_no:
            continue
        selected_attr = " selected" if bl_no == selected else ""
        options.append(f'<option value="{html_attr(bl_no)}"{selected_attr}>{html_text(bl_no)}</option>')
    options.append("</select>")
    return "".join(options)


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


def build_items(item_name, hs_code, quantity, carton, net_weight, gross_weight, origin=None):
    carton = carton if isinstance(carton, (list, tuple)) else []
    net_weight = net_weight if isinstance(net_weight, (list, tuple)) else []
    gross_weight = gross_weight if isinstance(gross_weight, (list, tuple)) else []
    origin = origin if isinstance(origin, (list, tuple)) else []
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
<input type="text" name="origin" value="{html_attr(item.get('origin', ''))}" placeholder="Origin">
<input type="text" name="carton" value="{html_attr(item.get('carton', ''))}" placeholder="Carton">
<input type="text" name="net_weight" value="{html_attr(item.get('net_weight', ''))}" placeholder="Net Weight" oninput="calculateTotals()">
<input type="text" name="gross_weight" value="{html_attr(item.get('gross_weight', ''))}" placeholder="Gross Weight" oninput="calculateTotals()">
<button class="remove" type="button" onclick="removeItem(this)">Remove Item</button>
</div>
"""
    return rows


def blank_payload():
    return {
        "weight_no": "",
        "weight_date": datetime.now().strftime("%Y-%m-%d"),
        "shipment_no": "",
        "bl_no": "",
        "packing_no": "",
        "invoice_no": "",
        "exporter": "",
        "consignee": "",
        "exporter_name": "", "exporter_address": "", "exporter_email": "", "exporter_phone": "",
        "consignee_name": "", "consignee_address": "", "consignee_email": "",
        "country_of_origin": "", "destination_country": "",
        "transport_details": "",
        "port_of_loading": "",
        "port_of_discharge": "",
        "weighing_place": "",
        "weighing_method": "",
        "remarks": "",
        "items": [],
        "total_net_weight": "",
        "total_gross_weight": "",
    }


def resolve_weight_snapshot(record, account_id, shipment=None, bill=None, packing=None, invoice=None):
    """Resolve Weight Certificate snapshot from account-owned sources."""
    context = resolve_source_chain(
        record, account_id, document_id_field="weight_no",
        load_shipments=shipment_module.load_shipments, load_bills=load_bills_of_lading,
        load_packings=load_packing_lists, load_invoices=load_invoices,
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
        "transport_details": (shipment.get("transport_details"), " / ".join(x for x in (bill.get("vessel", ""), bill.get("voyage_no", "")) if x)),
    }
    fill_missing_snapshot_fields(resolved, scalar_sources, preserve_empty=preserve_empty)
    origins = [item.get("origin") for item in resolved.get("items", []) if isinstance(item, dict) and item.get("origin")]
    if ("country_of_origin" not in resolved or (not preserve_empty and not resolved["country_of_origin"])) and origins and len(set(origins)) == 1:
        resolved["country_of_origin"] = origins[0]
    resolved.update({"shipment_no": shipment_no, "bl_no": bl_no, "packing_no": packing_no, "invoice_no": invoice_no})
    if "total_net_weight" not in resolved or (not preserve_empty and not resolved["total_net_weight"]):
        resolved["total_net_weight"] = format_number(numeric_total(resolved.get("items", []), "net_weight"))
    if "total_gross_weight" not in resolved or (not preserve_empty and not resolved["total_gross_weight"]):
        resolved["total_gross_weight"] = format_number(numeric_total(resolved.get("items", []), "gross_weight"))
    return resolved


def payload_from_bl(bl_no, account_id):
    payload = blank_payload()
    if not bl_no:
        return payload

    for bill in load_bills_of_lading(account_id):
        if bill.get("bl_no") == bl_no:
            products = load_products(account_id)
            items = []
            for source_item in bill.get("items", []):
                item = dict(source_item) if isinstance(source_item, dict) else {}
                product = find_product(item, products)
                item["name"] = item.get("name", "") or product.get("name", "")
                item["hs_code"] = item.get("hs_code", "") or product.get("hs_code", "")
                items.append(item)
            transport_parts = [
                str(bill.get("vessel", "") or "").strip(),
                str(bill.get("voyage_no", "") or "").strip(),
            ]
            payload.update({
                "bl_no": bill.get("bl_no", ""),
                "packing_no": bill.get("packing_no", ""),
                "invoice_no": bill.get("invoice_no", ""),
                "exporter": bill.get("shipper", ""),
                "consignee": bill.get("consignee", ""),
                "transport_details": " / ".join(part for part in transport_parts if part),
                "port_of_loading": bill.get("port_of_loading", ""),
                "port_of_discharge": bill.get("port_of_discharge", ""),
                "items": items,
                "total_net_weight": format_number(numeric_total(items, "net_weight")),
                "total_gross_weight": format_number(numeric_total(items, "gross_weight")),
            })
            break
    return resolve_weight_snapshot(payload, account_id)


def build_record(
    weight_no, weight_date, bl_no, packing_no, invoice_no, exporter, consignee,
    transport_details, port_of_loading, port_of_discharge, weighing_place,
    weighing_method, remarks, item_name, hs_code, quantity, carton, net_weight,
    gross_weight, total_net_weight, total_gross_weight, shipment_no="", origin=None,
    country_of_origin="", destination_country="",
):
    items = build_items(item_name, hs_code, quantity, carton, net_weight, gross_weight, origin)
    if not total_net_weight:
        total_net_weight = format_number(numeric_total(items, "net_weight"))
    if not total_gross_weight:
        total_gross_weight = format_number(numeric_total(items, "gross_weight"))

    return {
        "weight_no": weight_no,
        "weight_date": weight_date,
        "shipment_no": shipment_no,
        "bl_no": bl_no,
        "packing_no": packing_no,
        "invoice_no": invoice_no,
        "exporter": exporter,
        "consignee": consignee,
        "country_of_origin": country_of_origin,
        "destination_country": destination_country,
        "transport_details": transport_details,
        "port_of_loading": port_of_loading,
        "port_of_discharge": port_of_discharge,
        "weighing_place": weighing_place,
        "weighing_method": weighing_method,
        "remarks": remarks,
        "items": items,
        "total_net_weight": total_net_weight,
        "total_gross_weight": total_gross_weight,
    }


def render_form(record, action, title, button_text, show_weight_no=False, shipment_no="", account_id=""):
    rows = build_item_rows(record.get("items", []))
    weight_no_input = ""
    if show_weight_no:
        weight_no_input = f'<input type="text" name="weight_no" value="{html_attr(record.get("weight_no", ""))}" placeholder="Weight No" readonly>'

    html = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Weight Certificate</title>
<style>
body{font-family:Arial,sans-serif;background:#f3f4f6;padding:40px;color:#111827;}
.container{max-width:1040px;margin:auto;background:white;padding:35px;border-radius:16px;box-shadow:0 12px 35px rgba(15,23,42,.08);}
h1{text-align:center;font-size:46px;margin:8px 0 10px;}
.sub{text-align:center;color:#6B7280;margin-bottom:35px;}
.nav-row{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:25px;}
.card{border:1px solid #E5E7EB;border-radius:16px;padding:25px;margin-bottom:25px;background:#fff;}
.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px;}
.item-row{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;border:1px solid #E5E7EB;border-radius:14px;padding:18px;margin-bottom:16px;background:#F9FAFB;}
input,select,textarea{width:100%;padding:14px;border:1px solid #D1D5DB;border-radius:10px;font-size:16px;box-sizing:border-box;background:white;}
textarea{min-height:100px;resize:vertical;}
button{padding:15px 18px;background:#111827;color:white;border:none;border-radius:12px;font-size:16px;cursor:pointer;}
.small{min-width:180px;}
.full{width:100%;margin-top:10px;font-size:18px;}
.add{width:100%;background:#374151;margin-bottom:20px;}
.remove{grid-column:1/-1;width:100%;background:#991B1B;}
.totals{display:flex;gap:18px;flex-wrap:wrap;font-size:17px;font-weight:bold;color:#111827;margin:8px 0 20px;}
@media(max-width:820px){body{padding:18px}.grid,.item-row{grid-template-columns:1fr}h1{font-size:34px}}
</style>
</head>
<body>
<div class="container">
<div class="nav-row">
<a href="/"><button class="small" type="button">Dashboard</button></a>
<a href="/weight-list"><button class="small" type="button">Weight List</button></a>
</div>
<h1>__TITLE__</h1>
<p class="sub">Create and manage export weight certificates from saved B/L cargo data</p>

<form action="__ACTION__" method="post">
__SHIPMENT_CONTEXT__
<div class="card">
<h2>Document Information</h2>
<div class="grid">
__WEIGHT_NO_INPUT__
<input type="date" name="weight_date" value="__WEIGHT_DATE__">
__BL_SELECT__
<input type="text" name="packing_no" value="__PACKING_NO__" placeholder="Packing No">
<input type="text" name="invoice_no" value="__INVOICE_NO__" placeholder="Invoice No">
</div>
</div>

<div class="card">
<h2>Exporter / Consignee</h2>
<div class="grid">
<input type="text" name="exporter_name" value="__EXPORTER__" placeholder="Exporter Name">
<input type="text" name="exporter_address" value="__EXPORTER_ADDRESS__" placeholder="Exporter Address">
<input type="email" name="exporter_email" value="__EXPORTER_EMAIL__" placeholder="Exporter Email">
<input type="text" name="exporter_phone" value="__EXPORTER_PHONE__" placeholder="Exporter Phone">
<input type="text" name="consignee_name" value="__CONSIGNEE__" placeholder="Consignee Name">
<input type="text" name="consignee_address" value="__CONSIGNEE_ADDRESS__" placeholder="Consignee Address">
<input type="email" name="consignee_email" value="__CONSIGNEE_EMAIL__" placeholder="Consignee Email">
<input type="text" name="country_of_origin" value="__COUNTRY_OF_ORIGIN__" placeholder="Country of Origin">
<input type="text" name="destination_country" value="__DESTINATION_COUNTRY__" placeholder="Destination Country">
</div>
</div>

<div class="card">
<h2>Weight Information</h2>
<div class="grid">
<input type="text" name="weighing_place" value="__WEIGHING_PLACE__" placeholder="Weighing Place">
<input type="text" name="weighing_method" value="__WEIGHING_METHOD__" placeholder="Weighing Method">
<input type="text" name="transport_details" value="__TRANSPORT_DETAILS__" placeholder="Transport Details">
<input type="text" name="port_of_loading" value="__PORT_OF_LOADING__" placeholder="Port of Loading">
<input type="text" name="port_of_discharge" value="__PORT_OF_DISCHARGE__" placeholder="Port of Discharge">
</div>
<br>
<textarea name="remarks" placeholder="Remarks">__REMARKS__</textarea>
</div>

<div class="card">
<h2>Goods Information</h2>
<div id="items">__ITEM_ROWS__</div>
<button class="add" type="button" onclick="addItem()">+ Add Item</button>
<input type="hidden" id="total_net_weight" name="total_net_weight" value="__TOTAL_NET_WEIGHT__">
<input type="hidden" id="total_gross_weight" name="total_gross_weight" value="__TOTAL_GROSS_WEIGHT__">
<div class="totals">
<span>Total Net Weight: <span id="netText">__TOTAL_NET_WEIGHT__</span></span>
<span>Total Gross Weight: <span id="grossText">__TOTAL_GROSS_WEIGHT__</span></span>
</div>
</div>

<button class="full" type="submit">__BUTTON_TEXT__</button>
</form>
</div>

<script>
function escapeAttr(value){
  return String(value ?? "").replaceAll("&","&amp;").replaceAll('"',"&quot;").replaceAll("<","&lt;").replaceAll(">","&gt;");
}
function itemRowHtml(item = {}){
  return `<div class="item-row">
    <input type="text" name="item_name" value="${escapeAttr(item.name || "")}" placeholder="Item">
    <input type="text" name="hs_code" value="${escapeAttr(item.hs_code || "")}" placeholder="HS Code">
    <input type="text" name="quantity" value="${escapeAttr(item.quantity || "")}" placeholder="Quantity">
    <input type="text" name="origin" value="${escapeAttr(item.origin || "")}" placeholder="Origin">
    <input type="text" name="carton" value="${escapeAttr(item.carton || "")}" placeholder="Carton">
    <input type="text" name="net_weight" value="${escapeAttr(item.net_weight || "")}" placeholder="Net Weight" oninput="calculateTotals()">
    <input type="text" name="gross_weight" value="${escapeAttr(item.gross_weight || "")}" placeholder="Gross Weight" oninput="calculateTotals()">
    <button class="remove" type="button" onclick="removeItem(this)">Remove Item</button>
  </div>`;
}
function renderItems(items){
  document.getElementById("items").innerHTML = (items.length ? items : [{}]).map(itemRowHtml).join("");
  calculateTotals();
}
function addItem(){
  const div = document.createElement("div");
  div.className = "item-row";
  div.innerHTML = itemRowHtml({}).replace('<div class="item-row">', '').replace('</div>', '');
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
  const net = sumByName("net_weight");
  const gross = sumByName("gross_weight");
  document.getElementById("total_net_weight").value = formatNumber(net);
  document.getElementById("total_gross_weight").value = formatNumber(gross);
  document.getElementById("netText").textContent = formatNumber(net);
  document.getElementById("grossText").textContent = formatNumber(gross);
}
async function loadBlPrefill(blNo){
  if(!blNo) return;
  try{
    const response = await fetch(`/weight-source/bl/${encodeURIComponent(blNo)}`);
    if(!response.ok) throw new Error(`${response.status} ${response.statusText}`);
    const bill = await response.json();
    document.querySelector('[name="packing_no"]').value = bill.packing_no || "";
    document.querySelector('[name="invoice_no"]').value = bill.invoice_no || "";
    document.querySelector('[name="exporter_name"]').value = bill.shipper || "";
    document.querySelector('[name="exporter_address"]').value = bill.shipper_address || "";
    document.querySelector('[name="exporter_email"]').value = bill.shipper_email || "";
    document.querySelector('[name="exporter_phone"]').value = bill.shipper_phone || "";
    document.querySelector('[name="consignee_name"]').value = bill.consignee || "";
    document.querySelector('[name="consignee_address"]').value = bill.consignee_address || "";
    document.querySelector('[name="consignee_email"]').value = bill.consignee_email || "";
    document.querySelector('[name="transport_details"]').value = [bill.vessel || "", bill.voyage_no || ""].filter(Boolean).join(" / ");
    document.querySelector('[name="port_of_loading"]').value = bill.port_of_loading || "";
    document.querySelector('[name="port_of_discharge"]').value = bill.port_of_discharge || "";
    const items = (bill.items || []).map(item => ({
      name: item.name || "",
      hs_code: item.hs_code || "",
      quantity: item.quantity || "",
      origin: item.origin || "",
      carton: item.carton || "",
      net_weight: item.net_weight || "",
      gross_weight: item.gross_weight || ""
    }));
    renderItems(items);
  }catch(error){
    console.error(`Weight B/L prefill failed for ${blNo}:`, error);
  }
}
document.querySelector('[name="bl_no"]')?.addEventListener("change", event => {
  loadBlPrefill(event.target.value);
});
calculateTotals();
</script>
</body>
</html>
"""
    replacements = {
        "__TITLE__": html_text(title),
        "__ACTION__": html_attr(action),
        "__WEIGHT_NO_INPUT__": weight_no_input,
        "__WEIGHT_DATE__": html_attr(record.get("weight_date", "")),
        "__BL_SELECT__": bl_select_html(record.get("bl_no", ""), account_id),
        "__PACKING_NO__": html_attr(record.get("packing_no", "")),
        "__INVOICE_NO__": html_attr(record.get("invoice_no", "")),
        "__EXPORTER__": html_attr(record.get("exporter", "")),
        "__EXPORTER_ADDRESS__": html_attr(record.get("exporter_address", "")),
        "__EXPORTER_EMAIL__": html_attr(record.get("exporter_email", "")),
        "__EXPORTER_PHONE__": html_attr(record.get("exporter_phone", "")),
        "__CONSIGNEE__": html_attr(record.get("consignee", "")),
        "__CONSIGNEE_ADDRESS__": html_attr(record.get("consignee_address", "")),
        "__CONSIGNEE_EMAIL__": html_attr(record.get("consignee_email", "")),
        "__COUNTRY_OF_ORIGIN__": html_attr(record.get("country_of_origin", "")),
        "__DESTINATION_COUNTRY__": html_attr(record.get("destination_country", "")),
        "__WEIGHING_PLACE__": html_attr(record.get("weighing_place", "")),
        "__WEIGHING_METHOD__": html_attr(record.get("weighing_method", "")),
        "__TRANSPORT_DETAILS__": html_attr(record.get("transport_details", "")),
        "__PORT_OF_LOADING__": html_attr(record.get("port_of_loading", "")),
        "__PORT_OF_DISCHARGE__": html_attr(record.get("port_of_discharge", "")),
        "__REMARKS__": html_text(record.get("remarks", "")),
        "__ITEM_ROWS__": rows,
        "__TOTAL_NET_WEIGHT__": html_attr(record.get("total_net_weight", "")),
        "__TOTAL_GROSS_WEIGHT__": html_attr(record.get("total_gross_weight", "")),
        "__BUTTON_TEXT__": html_text(button_text),
        "__SHIPMENT_CONTEXT__": f'<input type="hidden" name="shipment_no" value="{html_attr(shipment_no or record.get("shipment_no", ""))}">' if shipment_no or record.get("shipment_no") else "",
    }
    for key, value in replacements.items():
        html = html.replace(key, value)
    return HTMLResponse(html)


@router.get("/weight-list", response_class=HTMLResponse)
def weight_list(request: Request, search: str = ""):
    records = sorted(load_weights(_account_id(request)), key=lambda record: record.get("weight_no", ""), reverse=True)
    if search:
        term = search.lower()
        records = [
            record for record in records
            if term in str(record.get("weight_no", "")).lower()
            or term in str(record.get("bl_no", "")).lower()
            or term in str(record.get("exporter", "")).lower()
            or term in str(record.get("consignee", "")).lower()
        ]

    rows = ""
    for record in records:
        weight_no = record.get("weight_no", "")
        rows += f"""
<tr>
<td>{html_text(weight_no)}</td>
<td>{html_text(record.get('weight_date', ''))}</td>
<td>{html_text(record.get('bl_no', ''))}</td>
<td>{html_text(record.get('exporter', ''))}</td>
<td>{html_text(record.get('consignee', ''))}</td>
<td>{html_text(record.get('total_net_weight', ''))}</td>
<td>{html_text(record.get('total_gross_weight', ''))}</td>
<td><a class="link" href="/weight/{html_attr(weight_no)}">View</a></td>
<td><a class="link" href="/weight-pdf/{html_attr(weight_no)}">PDF</a></td>
<td><a class="link" href="/edit-weight/{html_attr(weight_no)}">Edit</a></td>
<td><a class="danger" href="/delete-weight/{html_attr(weight_no)}">Delete</a></td>
</tr>
"""

    html = f"""
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Weight Certificates</title>
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
<h1>Weight Certificates</h1>
<p class="sub">Manage export weight certificates and generate PDFs</p>
<div class="toolbar">
<div class="nav">
<a class="btn" href="/">Dashboard</a>
<a class="btn" href="/weight-form">+ New Weight Certificate</a>
</div>
<form class="search" action="/weight-list" method="get">
<input type="text" name="search" value="{html_attr(search)}" placeholder="Search weight no, B/L, exporter, consignee">
<button type="submit">Search</button>
<a class="btn reset" href="/weight-list">Reset</a>
</form>
</div>
<div class="count">Total Weight Certificates: {len(records)}</div>
<div class="table-wrap">
<table>
<thead>
<tr>
<th>Weight No</th><th>Date</th><th>B/L No</th><th>Exporter</th><th>Consignee</th><th>Total Net</th><th>Total Gross</th><th>View</th><th>PDF</th><th>Edit</th><th>Delete</th>
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


@router.get("/weight-source/bl/{bl_no}")
def weight_source_bl(bl_no: str, request: Request):
    record = find_by_identifier(load_bills_of_lading(request.scope["trade_paper_user"]["account_id"]), "bl_no", bl_no)
    if not record:
        raise HTTPException(status_code=404, detail="Bill of Lading not found")
    return record


def linked_status_card(label, value, exists_record, pdf_href="", edit_href=""):
    status = "Linked" if exists_record else "Missing"
    badge_class = "ok" if exists_record else "bad"
    links = ""
    if exists_record:
        if pdf_href:
            links += f'<a href="{html_attr(pdf_href)}">PDF</a>'
        if edit_href:
            links += f'<a href="{html_attr(edit_href)}">Edit</a>'
    return f"""
<div class="mini">
<b>{html_text(label)}</b>
<span>{html_text(value or "-")}</span>
<em class="{badge_class}">{status}</em>
<div class="actions">{links}</div>
</div>
"""


@router.get("/weight/{weight_no}", response_class=HTMLResponse)
def weight_detail(weight_no: str, request: Request):
    account_id = _account_id(request)
    record = resolve_weight_snapshot(public_weight(_owned_weight(weight_no, account_id)), account_id)

    bl_no = record.get("bl_no", "")
    packing_no = record.get("packing_no", "")
    invoice_no = record.get("invoice_no", "")
    cards = "".join([
        linked_status_card("B/L", bl_no, find_by_identifier(load_bills_of_lading(account_id), "bl_no", bl_no), f"/bl-pdf/{bl_no}", f"/edit-bl/{bl_no}"),
        linked_status_card("Packing List", packing_no, find_by_identifier(load_packing_lists(account_id), "packing_no", packing_no), f"/packing-list-pdf/{packing_no}", f"/edit-packing/{packing_no}"),
        linked_status_card("Commercial Invoice", invoice_no, find_by_identifier(load_invoices(account_id), "invoice_no", invoice_no), f"/invoice-pdf/{invoice_no}", f"/edit-invoice/{invoice_no}"),
    ])

    rows = ""
    for index, item in enumerate(record.get("items", []), start=1):
        rows += f"""
<tr>
<td>{index}</td>
<td>{html_text(item.get("name", ""))}</td>
<td>{html_text(item.get("hs_code", ""))}</td>
<td>{html_text(item.get("quantity", ""))}</td>
<td>{html_text(item.get("carton", ""))}</td>
<td>{html_text(item.get("net_weight", ""))}</td>
<td>{html_text(item.get("gross_weight", ""))}</td>
</tr>
"""
    if not rows:
        rows = '<tr><td colspan="7" style="text-align:center;color:#6B7280;padding:30px;">No goods items registered.</td></tr>'

    html = f"""<!DOCTYPE html><html><head><meta charset="UTF-8"><title>{html_text(weight_no)}</title>
<style>
body{{font-family:Arial,sans-serif;background:#f3f4f6;padding:40px;color:#111827;}}
.container{{width:94%;max-width:1180px;margin:auto;}}
.nav-row{{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:25px;}}
.btn{{display:inline-block;padding:13px 18px;background:#111827;color:white;border:none;border-radius:12px;text-decoration:none;font-size:15px;}}
.header{{background:#111827;color:white;border-radius:16px;padding:30px;box-shadow:0 12px 35px rgba(15,23,42,.12);}}
.header h1{{font-size:42px;margin:0 0 8px;}}
.meta{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px;margin-top:22px;}}
.meta div,.remarks{{background:#1F2937;border-radius:12px;padding:14px;}}
.label{{color:#CBD5E1;font-size:13px;margin-bottom:5px;}}
.value{{font-weight:bold;word-break:break-word;}}
.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:16px;margin:24px 0;}}
.section{{background:white;border:1px solid #E5E7EB;border-radius:16px;padding:24px;margin:24px 0;box-shadow:0 10px 25px rgba(15,23,42,.07);}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:16px;}}
.mini{{background:white;border:1px solid #E5E7EB;border-radius:16px;padding:20px;box-shadow:0 10px 25px rgba(15,23,42,.07);}}
.mini b,.mini span{{display:block;margin-bottom:10px;}}
.ok{{color:#166534;background:#DCFCE7;padding:7px 10px;border-radius:999px;font-style:normal;font-weight:bold;display:inline-block;}}
.bad{{color:#991B1B;background:#FEE2E2;padding:7px 10px;border-radius:999px;font-style:normal;font-weight:bold;display:inline-block;}}
.actions{{display:flex;gap:8px;margin-top:15px;flex-wrap:wrap;}}
.actions a{{background:#111827;color:white;text-decoration:none;padding:9px 11px;border-radius:9px;font-weight:bold;}}
.table-wrap{{background:white;border-radius:16px;overflow:auto;border:1px solid #E5E7EB;}}
table{{width:100%;border-collapse:collapse;min-width:860px;}}
th{{background:#111827;color:white;text-align:left;padding:13px;}}
td{{padding:13px;border-bottom:1px solid #E5E7EB;}}
@media(max-width:780px){{body{{padding:18px}}.meta{{grid-template-columns:1fr}}.header h1{{font-size:34px}}}}
</style></head><body><div class="container">
<div class="nav-row">
<a class="btn" href="/">Dashboard</a>
<a class="btn" href="/weight-list">Weight List</a>
<a class="btn" href="/edit-weight/{html_attr(weight_no)}">Edit</a>
<a class="btn" href="/weight-pdf/{html_attr(weight_no)}">PDF</a>
</div>
<div class="header">
<h1>{html_text(weight_no)}</h1>
<div>Weight Certificate</div>
<div class="meta">
<div><div class="label">Weight Date</div><div class="value">{html_text(record.get("weight_date", ""))}</div></div>
<div><div class="label">B/L No</div><div class="value">{html_text(bl_no)}</div></div>
<div><div class="label">Invoice No</div><div class="value">{html_text(invoice_no)}</div></div>
<div><div class="label">Packing No</div><div class="value">{html_text(packing_no)}</div></div>
<div><div class="label">Exporter</div><div class="value">{html_text(record.get("exporter", ""))}</div></div>
<div><div class="label">Consignee</div><div class="value">{html_text(record.get("consignee", ""))}</div></div>
<div><div class="label">Total Net Weight</div><div class="value">{html_text(record.get("total_net_weight", ""))}</div></div>
<div><div class="label">Total Gross Weight</div><div class="value">{html_text(record.get("total_gross_weight", ""))}</div></div>
</div>
<div class="remarks" style="margin-top:14px;"><div class="label">Remarks</div><div>{html_text(record.get("remarks", ""))}</div></div>
</div>
<div class="cards">{cards}</div>
<div class="section"><h2>Weight Information</h2><div class="grid">
<div class="mini"><b>Transport Details</b>{html_text(record.get("transport_details", ""))}</div>
<div class="mini"><b>Port of Loading</b>{html_text(record.get("port_of_loading", ""))}</div>
<div class="mini"><b>Port of Discharge</b>{html_text(record.get("port_of_discharge", ""))}</div>
<div class="mini"><b>Weighing Place</b>{html_text(record.get("weighing_place", ""))}</div>
<div class="mini"><b>Weighing Method</b>{html_text(record.get("weighing_method", ""))}</div>
</div></div>
<div class="section"><h2>Goods Information</h2><div class="table-wrap"><table><thead><tr><th>No</th><th>Item</th><th>HS Code</th><th>Quantity</th><th>Carton</th><th>Net Weight</th><th>Gross Weight</th></tr></thead><tbody>{rows}</tbody></table></div></div>
</div></body></html>"""
    return HTMLResponse(html)


@router.get("/weight-form", response_class=HTMLResponse)
def weight_form(request: Request, bl_no: str = "", shipment_no: str = ""):
    account_id = request.scope["trade_paper_user"]["account_id"]
    if shipment_no:
        record = blank_payload()
        record.update({"shipment_no": shipment_no, "bl_no": bl_no})
        record = resolve_weight_snapshot(record, account_id)
        validate_weight_links(record.get("bl_no", ""), record.get("packing_no", ""), record.get("invoice_no", ""), account_id, shipment_no)
    else:
        record = payload_from_bl(bl_no, account_id) if bl_no else blank_payload()
    record["weight_no"] = next_weight_no(load_weight_records())
    return render_form(record, "/weight", "New Weight Certificate", "Save Weight Certificate", show_weight_no=True, shipment_no=shipment_no, account_id=account_id)


@router.post("/weight")
def save_weight(
    request: Request,
    shipment_no: str = Form(""),
    weight_date: str = Form(""),
    bl_no: str = Form(""),
    packing_no: str = Form(""),
    invoice_no: str = Form(""),
    exporter: str = Form(""),
    consignee: str = Form(""),
    transport_details: str = Form(""),
    port_of_loading: str = Form(""),
    port_of_discharge: str = Form(""),
    weighing_place: str = Form(""),
    weighing_method: str = Form(""),
    remarks: str = Form(""),
    item_name: List[str] = Form([]),
    item_id: List[str] = Form([]),
    hs_code: List[str] = Form([]),
    quantity: List[str] = Form([]),
    carton: List[str] = Form([]),
    net_weight: List[str] = Form([]),
    gross_weight: List[str] = Form([]),
    total_net_weight: str = Form(""),
    total_gross_weight: str = Form(""),
    origin: Annotated[Optional[List[str]], Form()] = None,
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
    account_id = _account_id(request)
    validate_weight_links(bl_no, packing_no, invoice_no, account_id, shipment_no)
    exporter = require_text("Exporter", exporter_name or exporter)
    consignee = require_text("Consignee", consignee_name or consignee)
    saved = {}
    def add_weight(records):
        weight_number = next_identifier(records, "weight_no", "WT")
        record = build_record(
        weight_number, weight_date, bl_no, packing_no, invoice_no,
        exporter, consignee, transport_details, port_of_loading, port_of_discharge,
        weighing_place, weighing_method, remarks, item_name, hs_code, quantity,
        carton, net_weight, gross_weight, total_net_weight, total_gross_weight,
        shipment_no, origin, country_of_origin or "", destination_country or "",
        )
        assign_item_ids(record["items"], item_id)
        record.update({"exporter_name": exporter, "consignee_name": consignee})
        set_submitted_snapshot_fields(record, {
            "exporter_address": exporter_address, "exporter_email": exporter_email,
            "exporter_phone": exporter_phone, "consignee_address": consignee_address,
            "consignee_email": consignee_email, "country_of_origin": country_of_origin,
            "destination_country": destination_country,
        })
        record = resolve_weight_snapshot(record, account_id)
        record["account_id"] = account_id
        records.append(record)
        saved["weight_no"] = weight_number
    locked_json_mutation(WEIGHT_FILE, [], add_weight, list)
    return RedirectResponse(shipment_context_redirect_url(shipment_no, "weight_no", saved["weight_no"], "/weight-list"), status_code=303)


@router.get("/edit-weight/{weight_no}", response_class=HTMLResponse)
def edit_weight(weight_no: str, request: Request):
    account_id = _account_id(request)
    record = resolve_weight_snapshot(public_weight(_owned_weight(weight_no, account_id)), account_id)
    return render_form(record, f"/update-weight/{html_attr(weight_no)}",
                       "Edit Weight Certificate", "Update Weight Certificate",
                       show_weight_no=True, account_id=account_id)


@router.post("/update-weight/{weight_no}")
def update_weight(
    weight_no: str,
    request: Request,
    weight_date: str = Form(""),
    bl_no: str = Form(""),
    packing_no: str = Form(""),
    invoice_no: str = Form(""),
    exporter: str = Form(""),
    consignee: str = Form(""),
    transport_details: str = Form(""),
    port_of_loading: str = Form(""),
    port_of_discharge: str = Form(""),
    weighing_place: str = Form(""),
    weighing_method: str = Form(""),
    remarks: str = Form(""),
    item_name: List[str] = Form([]),
    item_id: List[str] = Form([]),
    hs_code: List[str] = Form([]),
    quantity: List[str] = Form([]),
    carton: Annotated[Optional[List[str]], Form()] = None,
    net_weight: Annotated[Optional[List[str]], Form()] = None,
    gross_weight: Annotated[Optional[List[str]], Form()] = None,
    total_net_weight: str = Form(""),
    total_gross_weight: str = Form(""),
    origin: Annotated[Optional[List[str]], Form()] = None,
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
    account_id = _account_id(request)
    current = _owned_weight(weight_no, account_id)
    validate_weight_links(bl_no, packing_no, invoice_no, account_id)
    exporter = require_text("Exporter", exporter_name or exporter)
    consignee = require_text("Consignee", consignee_name or consignee)
    def replace_weight(records):
        for index, record in enumerate(records):
            if record.get("weight_no") != weight_no or record.get("account_id") != account_id:
                continue
            updated = build_record(
                weight_no, weight_date, bl_no, packing_no, invoice_no,
                exporter, consignee, transport_details, port_of_loading,
                port_of_discharge, weighing_place, weighing_method, remarks,
                item_name, hs_code, quantity, carton, net_weight, gross_weight,
                total_net_weight, total_gross_weight, current.get("shipment_no", ""), origin,
                current.get("country_of_origin", "") if country_of_origin is None else country_of_origin,
                current.get("destination_country", "") if destination_country is None else destination_country,
            )
            assign_item_ids(updated["items"], item_id, current.get("items", []))
            preserve_omitted_item_fields(
                updated["items"], current.get("items", []),
                [field for values, field in ((origin, "origin"), (carton, "carton"), (net_weight, "net_weight"), (gross_weight, "gross_weight")) if values is None],
            )
            updated.update({
                "exporter_name": exporter,
                "exporter_address": current.get("exporter_address", "") if exporter_address is None else exporter_address,
                "exporter_email": current.get("exporter_email", "") if exporter_email is None else exporter_email,
                "exporter_phone": current.get("exporter_phone", "") if exporter_phone is None else exporter_phone,
                "consignee_name": consignee,
                "consignee_address": current.get("consignee_address", "") if consignee_address is None else consignee_address,
                "consignee_email": current.get("consignee_email", "") if consignee_email is None else consignee_email,
            })
            updated = resolve_weight_snapshot(updated, account_id)
            updated["account_id"] = account_id
            records[index] = updated
            return
        raise HTTPException(status_code=404, detail="Weight certificate not found")
    locked_json_mutation(WEIGHT_FILE, [], replace_weight, list)
    return RedirectResponse(
        shipment_detail_redirect_url(current.get("shipment_no", ""), account_id, "/weight-list"), status_code=303,
    )


@router.get("/delete-weight/{weight_no}")
def delete_weight(weight_no: str, request: Request):
    _owned_weight(weight_no, _account_id(request))
    return render_delete_page("Weight Certificate", weight_no, f"/delete-weight/{weight_no}", "/weight-list", find_dependencies("Weight Certificate", weight_no, _account_id(request)))

@router.post("/delete-weight/{weight_no}")
def confirm_delete_weight(weight_no: str, request: Request):
    account_id = _account_id(request)
    _owned_weight(weight_no, account_id)
    dependencies = find_dependencies("Weight Certificate", weight_no, account_id)
    if dependencies:
        return render_delete_page("Weight Certificate", weight_no, f"/delete-weight/{weight_no}", "/weight-list", dependencies, status_code=409)
    def remove(records):
        index = next((i for i, record in enumerate(records)
                      if record.get("weight_no") == weight_no and record.get("account_id") == account_id), None)
        if index is None:
            raise HTTPException(status_code=404, detail="Weight certificate not found")
        records.pop(index)
    locked_json_mutation(WEIGHT_FILE, [], remove, list)
    return RedirectResponse("/weight-list", status_code=303)


@router.get("/weight-data/{weight_no}")
def weight_data(weight_no: str, request: Request):
    account_id = _account_id(request)
    return resolve_weight_snapshot(public_weight(_owned_weight(weight_no, account_id)), account_id)


def create_weight_certificate_pdf(payload):
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
        pdf.drawCentredString(width / 2, height - 70, "WEIGHT CERTIFICATE")
        pdf.setFillColor(colors.black)
        pdf.setFont(TP_UNICODE_BOLD, 10)
        pdf.drawString(40, height - 122, "Document Information")
        pdf.setFont(TP_UNICODE, 9)
        left = [
            ("Weight No", payload.get("weight_no", "")),
            ("Weight Date", payload.get("weight_date", "")),
            ("B/L No", payload.get("bl_no", "")),
        ]
        right = [
            ("Packing No", payload.get("packing_no", "")),
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
        pdf.roundRect(40, height - 260, 245, 70, 6, stroke=0, fill=1)
        pdf.roundRect(310, height - 260, 245, 70, 6, stroke=0, fill=1)
        pdf.setFillColor(navy)
        pdf.setFont(TP_UNICODE_BOLD, 9)
        pdf.drawString(52, height - 208, "EXPORTER")
        pdf.drawString(322, height - 208, "CONSIGNEE")
        pdf.setFillColor(colors.black)
        pdf.setFont(TP_UNICODE, 9)
        draw_text_fit(pdf, payload.get("exporter", ""), 52, height - 228, 210, size=9)
        draw_text_fit(pdf, payload.get("consignee", ""), 322, height - 228, 210, size=9)
        draw_text_fit(pdf, payload.get("exporter_address", ""), 52, height - 241, 210, size=7)
        draw_text_fit(pdf, " / ".join(v for v in (payload.get("exporter_email", ""), payload.get("exporter_phone", "")) if v), 52, height - 251, 210, size=7)
        draw_text_fit(pdf, payload.get("consignee_address", ""), 322, height - 241, 210, size=7)
        draw_text_fit(pdf, payload.get("consignee_email", ""), 322, height - 251, 210, size=7)

        pdf.setFont(TP_UNICODE_BOLD, 10)
        pdf.drawString(40, height - 292, "Weight Information")
        pdf.setFont(TP_UNICODE, 9)
        y2 = height - 310
        info = [
            ("Weighing Place", payload.get("weighing_place", "")),
            ("Weighing Method", payload.get("weighing_method", "")),
            ("Transport Details", payload.get("transport_details", "")),
            ("Port of Loading", payload.get("port_of_loading", "")),
            ("Port of Discharge", payload.get("port_of_discharge", "")),
        ]
        for label, value in info:
            pdf.drawString(48, y2, f"{label}:")
            draw_text_fit(pdf, value, 145, y2, 380, size=9)
            y2 -= 14

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
    table_start_y = height - 425
    y = table_start_y
    table_header(y)
    y -= 20

    pdf.setFont(TP_UNICODE, 8)
    row_height = 22
    for idx, item in enumerate(items, start=1):
        if y < 120:
            footer()
            pdf.showPage()
            header()
            y = table_start_y
            table_header(y)
            y -= 20
            pdf.setFont(TP_UNICODE, 8)

        pdf.setStrokeColor(border)
        pdf.line(40, y - 5, width - 40, y - 5)
        pdf.setFillColor(colors.black)
        pdf.setFont(TP_UNICODE, 8)
        draw_text_fit(pdf, idx, 48, y + 4, 20, size=8)
        draw_text_fit(pdf, item.get("name", ""), 74, y + 4, 132, size=8)
        draw_text_fit(pdf, item.get("hs_code", ""), 218, y + 4, 68, size=8)
        draw_text_fit(pdf, item.get("quantity", ""), 300, y + 4, 42, size=8)
        draw_text_fit(pdf, item.get("carton", ""), 354, y + 4, 44, size=8)
        draw_text_fit(pdf, item.get("net_weight", ""), 412, y + 4, 66, size=8)
        draw_text_fit(pdf, item.get("gross_weight", ""), 492, y + 4, 58, size=8)
        y -= row_height

    summary_height = 74
    if y - summary_height < 105:
        footer()
        pdf.showPage()
        header()
        y = table_start_y

    summary_y = y - summary_height - 10
    pdf.setFillColor(navy)
    pdf.roundRect(335, summary_y, 220, summary_height, 8, stroke=0, fill=1)
    pdf.setFillColor(colors.white)
    pdf.setFont(TP_UNICODE_BOLD, 10)
    pdf.drawString(350, summary_y + 48, f"Total Net Weight: {payload.get('total_net_weight', '')}")
    pdf.drawString(350, summary_y + 28, f"Total Gross Weight: {payload.get('total_gross_weight', '')}")
    pdf.setFillColor(colors.black)

    remarks_y = summary_y - 32
    if payload.get("remarks"):
        pdf.setFont(TP_UNICODE_BOLD, 10)
        pdf.drawString(40, remarks_y, "Remarks")
        pdf.setFont(TP_UNICODE, 9)
        draw_text_fit(pdf, payload.get("remarks", ""), 40, remarks_y - 16, 500, size=9)

    signature_y = max(90, remarks_y - 58)
    pdf.setStrokeColor(colors.black)
    pdf.line(380, signature_y, 555, signature_y)
    pdf.setFont(TP_UNICODE, 9)
    pdf.drawString(415, signature_y - 15, "Authorized Signature")

    footer()
    pdf.save()
    buffer.seek(0)
    return buffer


@router.post("/weight/pdf")
def create_weight_pdf(request: Request, payload: dict = Body(...)):
    account_id = _account_id(request)
    validate_weight_links(payload.get("bl_no", ""), payload.get("packing_no", ""), payload.get("invoice_no", ""), account_id)
    payload = public_weight(payload)
    payload = resolve_weight_snapshot(payload, account_id)
    pdf_buffer = create_weight_certificate_pdf(payload)
    return Response(
        pdf_buffer.getvalue(),
        media_type="application/pdf",
        headers={"Content-Disposition": "inline; filename=weight_certificate.pdf"},
    )


@router.get("/weight-pdf/{weight_no}")
def weight_pdf(weight_no: str, request: Request):
    account_id = _account_id(request)
    record = resolve_weight_snapshot(public_weight(_owned_weight(weight_no, account_id)), account_id)
    set_pdf_export_record(request, record)
    validate_weight_links(record.get("bl_no", ""), record.get("packing_no", ""), record.get("invoice_no", ""), _account_id(request))
    pdf_buffer = create_weight_certificate_pdf(record)
    filename = f"{weight_no}.pdf"
    return Response(pdf_buffer.getvalue(), media_type="application/pdf",
                    headers={"Content-Disposition": f"attachment; filename={filename}"})
