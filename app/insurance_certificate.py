from typing import Annotated, List, Optional
from copy import deepcopy
from datetime import datetime
from io import BytesIO
import html as html_lib
import json

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
from app.account_insurance import ensure_legacy_insurance_ownership, public_insurance
from app.snapshot import assign_item_ids, fill_missing_snapshot_fields, find_by_identifier, preserve_omitted_item_fields, resolve_source_chain, set_submitted_snapshot_fields
from app.export import set_pdf_export_record
from app.auth import USERS_FILE
from app.shipment import shipment_context_redirect_url, shipment_detail_redirect_url
from app import product as product_module
from app import bill_of_lading as bill_of_lading_module
from app import packing as packing_module
from app import invoice as invoice_module
from app import shipment as shipment_module
from app import buyer as buyer_module
from app.account_company import load_account_company
from app.routers.company import ACCOUNT_COMPANIES_FILE

INSURANCE_FILE = data_path("insurance_certificates.json")
BL_FILE = data_path("bills_of_lading.json")
PRODUCT_FILE = data_path("products.json")
PACKING_FILE = data_path("packing_lists.json")
INVOICE_FILE = data_path("invoices.json")


def _account_id(request):
    user = request.scope.get("trade_paper_user") or {}
    return str(user.get("account_id", "") or "").strip()


def load_insurance_records():
    return ensure_legacy_insurance_ownership(INSURANCE_FILE, USERS_FILE)


def owned_insurance_records(account_id):
    owner = str(account_id or "").strip()
    return [record for record in load_insurance_records()
            if isinstance(record, dict) and str(record.get("account_id", "") or "").strip() == owner]


def load_insurances(account_id):
    return [public_insurance(record) for record in owned_insurance_records(account_id)]


def _owned_insurance(insurance_no, account_id):
    target = str(insurance_no or "").strip()
    record = find_by_identifier(
        owned_insurance_records(account_id), "insurance_no", target, normalize=True,
    )
    if record is None:
        raise HTTPException(status_code=404, detail="Insurance Certificate not found")
    return record


def save_insurances(records):
    atomic_write_json(INSURANCE_FILE, records, list)


def load_bills_of_lading(account_id):
    return bill_of_lading_module.load_bills_of_lading(account_id)


def load_products(account_id):
    return product_module.load_products(account_id)


def load_packing_lists(account_id):
    return packing_module.load_packing_lists(account_id)


def load_invoices(account_id):
    return invoice_module.load_invoices(account_id)


PARTY_SNAPSHOT_FIELDS = (
    "exporter_name", "exporter_address", "exporter_email", "exporter_phone",
    "consignee_name", "consignee_address", "consignee_email",
)


def validate_insurance_links(bl_no, packing_no, invoice_no, account_id, shipment_no=""):
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


def html_attr(value):
    return html_lib.escape(str(value or ""), quote=True)


def html_text(value):
    return html_lib.escape(str(value or ""))


def bl_select_html(selected, account_id):
    options = ['<select name="bl_no">', '<option value="">Select B/L</option>']
    for bl in load_bills_of_lading(account_id):
        bl_no = bl.get("bl_no", "")
        if not bl_no:
            continue
        selected_attr = " selected" if bl_no == selected else ""
        options.append(f'<option value="{html_attr(bl_no)}"{selected_attr}>{html_text(bl_no)}</option>')
    options.append("</select>")
    return "".join(options)


def find_product(item, products):
    item_name = str(item.get("name", "")).strip().lower()
    item_hs = str(item.get("hs_code", "")).strip().lower()

    for product in products:
        if item_name and str(product.get("name", "")).strip().lower() == item_name:
            return product

    for product in products:
        if item_hs and str(product.get("hs_code", "")).strip().lower() == item_hs:
            return product

    return {}


def build_items(name, hs_code, quantity, origin=None, carton=None, net_weight=None, gross_weight=None):
    origin = origin if isinstance(origin, (list, tuple)) else []
    carton = carton if isinstance(carton, (list, tuple)) else []
    net_weight = net_weight if isinstance(net_weight, (list, tuple)) else []
    gross_weight = gross_weight if isinstance(gross_weight, (list, tuple)) else []
    items = []
    for i in range(len(name)):
        if not name[i].strip():
            continue
        items.append({
            "name": name[i],
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
<input type="text" name="net_weight" value="{html_attr(item.get('net_weight', ''))}" placeholder="Net Weight">
<input type="text" name="gross_weight" value="{html_attr(item.get('gross_weight', ''))}" placeholder="Gross Weight">
<button class="remove" type="button" onclick="removeItem(this)">Remove Item</button>
</div>
"""
    return rows


def blank_payload():
    return {
        "insurance_date": datetime.now().strftime("%Y-%m-%d"),
        "shipment_no": "",
        "bl_no": "",
        "packing_no": "",
        "invoice_no": "",
        "exporter": "",
        "consignee": "",
        "insurance_company": "",
        "policy_no": "",
        "insured_amount": "",
        "currency": "USD",
        "inspection_location": "",
        "coverage_type": "",
        "remarks": "",
        "port_of_loading": "",
        "port_of_discharge": "",
        "transport_details": "",
        "items": [],
    }


def resolve_insurance_snapshot(record, account_id, shipment=None, bill=None, packing=None, invoice=None):
    """Resolve a read-only Insurance snapshot from account-owned sources."""
    context = resolve_source_chain(
        record, account_id, document_id_field="insurance_no",
        load_shipments=shipment_module.load_shipments, load_bills=load_bills_of_lading,
        load_packings=packing_module.load_packing_lists, load_invoices=invoice_module.load_invoices,
        load_company=lambda owner: load_account_company(owner, ACCOUNT_COMPANIES_FILE),
        load_buyers=buyer_module.load_buyers, shipment=shipment, bill=bill,
        packing=packing, invoice=invoice,
    )
    resolved, preserve_empty = context.resolved, context.preserve_empty
    shipment, bill, packing, invoice = context.shipment, context.bill, context.packing, context.invoice
    company, buyer = context.company, context.buyer
    shipment_no, bl_no, packing_no, invoice_no = context.shipment_no, context.bl_no, context.packing_no, context.invoice_no
    sources = {
        "exporter_name": (shipment.get("shipper"), bill.get("shipper"), packing.get("seller"), invoice.get("seller"), company.get("name")),
        "exporter_address": (shipment.get("shipper_address"), bill.get("shipper_address"), packing.get("seller_address"), invoice.get("seller_address"), company.get("address")),
        "exporter_email": (shipment.get("shipper_email"), bill.get("shipper_email"), packing.get("seller_email"), invoice.get("seller_email"), company.get("email")),
        "exporter_phone": (shipment.get("shipper_phone"), bill.get("shipper_phone"), packing.get("seller_phone"), invoice.get("seller_phone"), company.get("phone")),
        "consignee_name": (shipment.get("consignee"), bill.get("consignee"), packing.get("buyer"), invoice.get("buyer"), buyer.get("name")),
        "consignee_address": (shipment.get("consignee_address"), bill.get("consignee_address"), packing.get("buyer_address"), invoice.get("buyer_address"), buyer.get("address")),
        "consignee_email": (shipment.get("consignee_email"), bill.get("consignee_email"), packing.get("buyer_email"), invoice.get("buyer_email"), buyer.get("email")),
    }
    fill_missing_snapshot_fields(resolved, sources, preserve_empty=preserve_empty)
    resolved["exporter"] = resolved.get("exporter_name", resolved.get("exporter", ""))
    resolved["consignee"] = resolved.get("consignee_name", resolved.get("consignee", ""))
    if "items" not in resolved or (not preserve_empty and not resolved["items"]):
        resolved["items"] = deepcopy(shipment.get("items") or bill.get("items") or packing.get("items") or invoice.get("items") or [])
    field_sources = {
        "port_of_loading": (shipment.get("port_of_loading"), bill.get("port_of_loading")),
        "port_of_discharge": (shipment.get("port_of_discharge"), bill.get("port_of_discharge")),
        "origin_country": (shipment.get("origin_country"), bill.get("port_of_loading")),
        "destination_country": (shipment.get("destination_country"), bill.get("place_of_delivery"), bill.get("port_of_discharge")),
        "transport_details": (shipment.get("transport_details"), " ".join(x for x in (bill.get("vessel", ""), bill.get("voyage_no", "")) if x)),
    }
    fill_missing_snapshot_fields(resolved, field_sources, preserve_empty=preserve_empty)
    resolved.update({"shipment_no": shipment_no, "bl_no": bl_no, "packing_no": packing_no, "invoice_no": invoice_no})
    return resolved


def payload_from_bl(bl_no, products, account_id):
    payload = blank_payload()
    if not bl_no:
        return payload

    for bl in load_bills_of_lading(account_id):
        if bl.get("bl_no") != bl_no:
            continue

        items = []
        for item in bl.get("items", []):
            product = find_product(item, products)
            items.append({
                "name": item.get("name", "") or product.get("name", ""),
                "hs_code": item.get("hs_code", "") or product.get("hs_code", ""),
                "quantity": item.get("quantity", ""),
            })

        payload.update({
            "bl_no": bl.get("bl_no", ""),
            "packing_no": bl.get("packing_no", ""),
            "invoice_no": bl.get("invoice_no", ""),
            "exporter": bl.get("shipper", ""),
            "consignee": bl.get("consignee", ""),
            "port_of_loading": bl.get("port_of_loading", ""),
            "port_of_discharge": bl.get("port_of_discharge", ""),
            "transport_details": " ".join(part for part in [bl.get("vessel", ""), bl.get("voyage_no", "")] if part),
            "items": items,
        })
        break

    return resolve_insurance_snapshot(payload, account_id)


def build_record(
    insurance_no, insurance_date, bl_no, packing_no, invoice_no, exporter,
    consignee, insurance_company, policy_no, insured_amount, currency,
    inspection_location, coverage_type,
    remarks, port_of_loading, port_of_discharge, transport_details,
    item_name, hs_code, quantity, shipment_no="", origin=None, carton=None,
    net_weight=None, gross_weight=None, origin_country="", destination_country="",
):
    return {
        "insurance_no": insurance_no,
        "insurance_date": insurance_date,
        "shipment_no": shipment_no,
        "bl_no": bl_no,
        "packing_no": packing_no,
        "invoice_no": invoice_no,
        "exporter": exporter,
        "consignee": consignee,
        "insurance_company": insurance_company,
        "policy_no": policy_no,
        "insured_amount": insured_amount,
        "currency": currency,
        "inspection_location": inspection_location,
        "coverage_type": coverage_type,
        "remarks": remarks,
        "port_of_loading": port_of_loading,
        "port_of_discharge": port_of_discharge,
        "transport_details": transport_details,
        "origin_country": origin_country,
        "destination_country": destination_country,
        "items": build_items(item_name, hs_code, quantity, origin, carton, net_weight, gross_weight),
    }


def render_form(record, action, title, button_text, show_insurance_no=False, shipment_no="", products=None, account_id=""):
    insurance_no_input = ""
    if show_insurance_no:
        insurance_no_input = f'<input type="text" value="{html_attr(record.get("insurance_no", ""))}" placeholder="Insurance No" readonly>'
    product_master_json = json.dumps(products or [], ensure_ascii=False)

    html = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Insurance Certificate</title>
<style>
body{font-family:Arial,sans-serif;background:#f3f4f6;padding:40px;}
.container{max-width:960px;margin:auto;background:white;padding:35px;border-radius:16px;}
h1{text-align:center;font-size:48px;margin-bottom:10px;}
.sub{text-align:center;color:#6B7280;margin-bottom:35px;}
.nav-row{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:25px;}
.card{border:1px solid #E5E7EB;border-radius:16px;padding:25px;margin-bottom:25px;background:#fff;}
.item-row{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;border:1px solid #E5E7EB;border-radius:14px;padding:20px;margin-bottom:18px;background:#F9FAFB;}
input,select,textarea{width:100%;padding:14px;margin-bottom:14px;border:1px solid #D1D5DB;border-radius:10px;font-size:16px;box-sizing:border-box;font-family:Arial,sans-serif;background:white;}
button{padding:16px;background:#111827;color:white;border:none;border-radius:12px;font-size:18px;cursor:pointer;}
.small{width:220px;margin-bottom:25px;}
.full{width:100%;margin-top:10px;}
.add{width:100%;background:#374151;margin-bottom:20px;}
.remove{grid-column:1/-1;width:100%;background:#991B1B;margin-top:4px;}
@media(max-width:820px){body{padding:18px}.item-row{grid-template-columns:1fr}h1{font-size:36px}}
</style>
</head>
<body>
<div class="container">
<div class="nav-row">
<a href="/"><button class="small" type="button">Dashboard</button></a>
<a href="/insurance-list"><button class="small" type="button">Insurance List</button></a>
</div>
<h1>__TITLE__</h1>
<p class="sub">Create insurance certificate from shipment and product master data</p>
<form action="__ACTION__" method="post">
__SHIPMENT_CONTEXT__
<div class="card">
<h2>Document Information</h2>
__INSPECTION_NO_INPUT__
<input type="date" name="insurance_date" value="__INSPECTION_DATE__">
__BL_SELECT__
<input type="text" name="packing_no" value="__PACKING_NO__" placeholder="Packing No">
<input type="text" name="invoice_no" value="__INVOICE_NO__" placeholder="Invoice No">
</div>
<div class="card">
<h2>Party Information</h2>
<input type="text" name="exporter_name" value="__EXPORTER__" placeholder="Exporter Name">
<input type="text" name="exporter_address" value="__EXPORTER_ADDRESS__" placeholder="Exporter Address">
<input type="email" name="exporter_email" value="__EXPORTER_EMAIL__" placeholder="Exporter Email">
<input type="text" name="exporter_phone" value="__EXPORTER_PHONE__" placeholder="Exporter Phone">
<input type="text" name="consignee_name" value="__CONSIGNEE__" placeholder="Consignee Name">
<input type="text" name="consignee_address" value="__CONSIGNEE_ADDRESS__" placeholder="Consignee Address">
<input type="email" name="consignee_email" value="__CONSIGNEE_EMAIL__" placeholder="Consignee Email">
</div>
<div class="card">
<h2>Insurance Information</h2>
<input type="text" name="insurance_company" value="__INSURANCE_COMPANY__" placeholder="Insurance Company">
<input type="text" name="policy_no" value="__POLICY_NO__" placeholder="Policy No">
<input type="text" name="insured_amount" value="__INSURED_AMOUNT__" placeholder="Insured Amount">
<input type="text" name="currency" value="__CURRENCY__" placeholder="Currency">
<input type="text" name="inspection_location" value="__INSPECTION_LOCATION__" placeholder="Inspection Location">
<input type="text" name="coverage_type" value="__COVERAGE_TYPE__" placeholder="Coverage Type">
<input type="text" name="transport_details" value="__TRANSPORT_DETAILS__" placeholder="Transport Details">
<input type="text" name="port_of_loading" value="__PORT_OF_LOADING__" placeholder="Port of Loading">
<input type="text" name="port_of_discharge" value="__PORT_OF_DISCHARGE__" placeholder="Port of Discharge">
<input type="text" name="origin_country" value="__ORIGIN_COUNTRY__" placeholder="Origin Country">
<input type="text" name="destination_country" value="__DESTINATION_COUNTRY__" placeholder="Destination Country">
<textarea name="remarks" placeholder="Remarks">__REMARKS__</textarea>
</div>
<div class="card">
<h2>Goods Information</h2>
<div id="items_area">
__ITEM_ROWS__
</div>
<button class="add" type="button" onclick="addItem()">+ Add Item</button>
</div>
<button class="full" type="submit">__BUTTON_TEXT__</button>
</form>
</div>
<script>
const PRODUCT_MASTER = __PRODUCT_MASTER__;
function escapeAttr(value){
    return String(value ?? "").replaceAll("&","&amp;").replaceAll('"',"&quot;").replaceAll("<","&lt;").replaceAll(">","&gt;");
}
function itemNameKey(item){
    return String(item.name || "").trim().toLowerCase();
}
function hsKey(item){
    return String(item.hs_code || "").trim().toLowerCase();
}
function productFor(item){
    const name = itemNameKey(item);
    const hs = hsKey(item);
    return PRODUCT_MASTER.find(product => name && itemNameKey(product) === name)
        || PRODUCT_MASTER.find(product => hs && hsKey(product) === hs)
        || {};
}
function itemRowHtml(item = {}){
    return `<div class="item-row">
<input type="text" name="item_name" value="${escapeAttr(item.name || "")}" placeholder="Item">
<input type="text" name="hs_code" value="${escapeAttr(item.hs_code || "")}" placeholder="HS Code">
<input type="text" name="quantity" value="${escapeAttr(item.quantity || "")}" placeholder="Quantity">
<input type="text" name="origin" value="${escapeAttr(item.origin || "")}" placeholder="Origin">
<input type="text" name="carton" value="${escapeAttr(item.carton || "")}" placeholder="Carton">
<input type="text" name="net_weight" value="${escapeAttr(item.net_weight || "")}" placeholder="Net Weight">
<input type="text" name="gross_weight" value="${escapeAttr(item.gross_weight || "")}" placeholder="Gross Weight">
<button class="remove" type="button" onclick="removeItem(this)">Remove Item</button>
</div>`;
}
function renderItems(items){
    document.getElementById("items_area").innerHTML = (items.length ? items : [{}]).map(itemRowHtml).join("");
}
function addItem(){
    const area = document.getElementById("items_area");
    const first = document.querySelector(".item-row");
    const row = first.cloneNode(true);
    row.querySelectorAll("input").forEach(input => input.value = "");
    area.appendChild(row);
}
function removeItem(button){
    const rows = document.querySelectorAll(".item-row");
    if(rows.length <= 1) return;
    button.closest(".item-row").remove();
}
async function loadBlPrefill(blNo){
    if(!blNo) return;
    try{
        const response = await fetch(`/insurance-source/bl/${encodeURIComponent(blNo)}`);
        if(!response.ok) throw new Error(`${response.status} ${response.statusText}`);
        const bl = await response.json();
        document.querySelector('[name="packing_no"]').value = bl.packing_no || "";
        document.querySelector('[name="invoice_no"]').value = bl.invoice_no || "";
        document.querySelector('[name="exporter_name"]').value = bl.shipper || "";
        document.querySelector('[name="exporter_address"]').value = bl.shipper_address || "";
        document.querySelector('[name="exporter_email"]').value = bl.shipper_email || "";
        document.querySelector('[name="exporter_phone"]').value = bl.shipper_phone || "";
        document.querySelector('[name="consignee_name"]').value = bl.consignee || "";
        document.querySelector('[name="consignee_address"]').value = bl.consignee_address || "";
        document.querySelector('[name="consignee_email"]').value = bl.consignee_email || "";
        document.querySelector('[name="transport_details"]').value = [bl.vessel || "", bl.voyage_no || ""].filter(Boolean).join(" ");
        document.querySelector('[name="port_of_loading"]').value = bl.port_of_loading || "";
        document.querySelector('[name="port_of_discharge"]').value = bl.port_of_discharge || "";
        const items = (bl.items || []).map(item => {
            const product = productFor(item);
            return {
                name: item.name || product.name || "",
                hs_code: item.hs_code || product.hs_code || "",
                quantity: item.quantity || ""
                ,origin: item.origin || "", carton: item.carton || "",
                net_weight: item.net_weight || "", gross_weight: item.gross_weight || ""
            };
        });
        renderItems(items);
    }catch(error){
        console.error(`Insurance B/L prefill failed for ${blNo}:`, error);
    }
}
document.querySelector('[name="bl_no"]')?.addEventListener("change", event => {
    loadBlPrefill(event.target.value);
});
</script>
</body>
</html>
"""
    replacements = {
        "__TITLE__": html_attr(title),
        "__ACTION__": html_attr(action),
        "__INSPECTION_NO_INPUT__": insurance_no_input,
        "__INSPECTION_DATE__": html_attr(record.get("insurance_date", "")),
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
        "__INSURANCE_COMPANY__": html_attr(record.get("insurance_company", "")),
        "__POLICY_NO__": html_attr(record.get("policy_no", "")),
        "__INSURED_AMOUNT__": html_attr(record.get("insured_amount", "")),
        "__CURRENCY__": html_attr(record.get("currency", "USD")),
        "__INSPECTION_LOCATION__": html_attr(record.get("inspection_location", "")),
        "__COVERAGE_TYPE__": html_attr(record.get("coverage_type", "")),
        "__REMARKS__": html_attr(record.get("remarks", "")),
        "__PORT_OF_LOADING__": html_attr(record.get("port_of_loading", "")),
        "__PORT_OF_DISCHARGE__": html_attr(record.get("port_of_discharge", "")),
        "__TRANSPORT_DETAILS__": html_attr(record.get("transport_details", "")),
        "__ORIGIN_COUNTRY__": html_attr(record.get("origin_country", "")),
        "__DESTINATION_COUNTRY__": html_attr(record.get("destination_country", "")),
        "__ITEM_ROWS__": build_item_rows(record.get("items", [])),
        "__BUTTON_TEXT__": html_attr(button_text),
        "__PRODUCT_MASTER__": product_master_json,
        "__SHIPMENT_CONTEXT__": f'<input type="hidden" name="shipment_no" value="{html_attr(shipment_no or record.get("shipment_no", ""))}">' if shipment_no or record.get("shipment_no") else "",
    }
    for key, value in replacements.items():
        html = html.replace(key, value)
    return HTMLResponse(html)


@router.get("/insurance-list")
def inspection_list(request: Request, search: str = ""):
    records = list(reversed(load_insurances(_account_id(request))))
    if search:
        q = search.lower()
        records = [
            record for record in records
            if q in str(record.get("insurance_no", "")).lower()
            or q in str(record.get("bl_no", "")).lower()
            or q in str(record.get("invoice_no", "")).lower()
            or q in str(record.get("exporter", "")).lower()
            or q in str(record.get("consignee", "")).lower()
            or q in str(record.get("items", "")).lower()
        ]

    html = f"""
<!DOCTYPE html><html><head><meta charset="UTF-8"><title>Insurance Certificate List</title>
<style>
body{{font-family:Arial,sans-serif;background:#f3f4f6;padding:40px;color:#111827;}}
.container{{width:94%;margin:auto;}}
h1{{text-align:center;font-size:48px;margin:8px 0 10px;}}
.sub{{text-align:center;color:#6B7280;margin-bottom:30px;}}
.toolbar{{display:flex;justify-content:space-between;align-items:center;gap:18px;flex-wrap:wrap;margin-bottom:20px;}}
.nav,.search{{display:flex;gap:12px;align-items:center;flex-wrap:wrap;}}
.btn,button{{display:inline-block;padding:13px 18px;background:#111827;color:white;border:none;border-radius:12px;text-decoration:none;font-size:15px;cursor:pointer;}}
.reset{{background:#6B7280;}}
input{{padding:13px;width:450px;max-width:100%;border:1px solid #D1D5DB;border-radius:10px;font-size:15px;box-sizing:border-box;}}
.count{{font-size:18px;font-weight:bold;margin:22px 0;color:#374151;}}
.table-wrap{{background:white;border:1px solid #E5E7EB;border-radius:16px;overflow:auto;box-shadow:0 12px 35px rgba(15,23,42,.08);}}
table{{width:100%;border-collapse:collapse;min-width:1040px;}}
th{{background:#111827;color:white;text-align:left;padding:14px;font-size:14px;}}
td{{padding:14px;border-bottom:1px solid #E5E7EB;font-size:14px;}}
.link{{color:#111827;font-weight:bold;text-decoration:none;}}
.pdf{{color:#2563EB;font-weight:bold;text-decoration:none;}}
.danger{{color:#DC2626;font-weight:bold;text-decoration:none;}}
@media(max-width:780px){{body{{padding:18px}}h1{{font-size:36px}}}}
</style></head><body><div class="container">
<h1>Insurance Certificate List</h1>
<p class="sub">Manage all Insurance Certificate documents</p>
<div class="toolbar">
<div class="nav"><a class="btn" href="/">Dashboard</a><a class="btn" href="/insurance-form">+ New Insurance</a></div>
<form class="search" action="/insurance-list" method="get">
<input type="text" name="search" value="{html_attr(search)}" placeholder="Search insurance, B/L, invoice, exporter, consignee or item">
<button type="submit">Search</button><a class="btn reset" href="/insurance-list">Reset</a>
</form></div>
<div class="count">Total Insurance Certificates: {len(records)}</div>
<div class="table-wrap"><table><thead><tr>
<th>Insurance No</th><th>B/L No</th><th>Invoice</th><th>Exporter</th><th>Consignee</th><th>View</th><th>PDF</th><th>Edit</th><th>Delete</th>
</tr></thead><tbody>
"""
    if not records:
        html += '<tr><td colspan="9" style="padding:35px;text-align:center;color:#6B7280;">No Insurance Certificates have been registered yet.</td></tr>'
    else:
        for record in records:
            insurance_no = record.get("insurance_no", "")
            html += f"""
<tr>
<td>{html_text(insurance_no)}</td>
<td>{html_text(record.get("bl_no", ""))}</td>
<td>{html_text(record.get("invoice_no", ""))}</td>
<td>{html_text(record.get("exporter", ""))}</td>
<td>{html_text(record.get("consignee", ""))}</td>
<td><a class="link" href="/insurance/{html_attr(insurance_no)}">View</a></td>
<td><a class="pdf" href="/insurance-pdf/{html_attr(insurance_no)}">PDF</a></td>
<td><a class="link" href="/edit-insurance/{html_attr(insurance_no)}">Edit</a></td>
<td><a class="danger" href="/delete-insurance/{html_attr(insurance_no)}">Delete</a></td>
</tr>
"""
    html += "</tbody></table></div></div></body></html>"
    return HTMLResponse(html)


@router.get("/insurance-form")
def inspection_form(request: Request, bl_no: str = "", shipment_no: str = ""):
    account_id = request.scope["trade_paper_user"]["account_id"]
    products = product_module.load_products(account_id)
    if shipment_no:
        record = blank_payload()
        record.update({"shipment_no": shipment_no, "bl_no": bl_no})
        record = resolve_insurance_snapshot(record, account_id)
        validate_insurance_links(record.get("bl_no", ""), record.get("packing_no", ""), record.get("invoice_no", ""), account_id, shipment_no)
    else:
        record = payload_from_bl(bl_no, products, account_id)
    return render_form(record, "/insurance", "Insurance Certificate", "Save Insurance Certificate", shipment_no=shipment_no, products=products, account_id=account_id)


@router.get("/insurance-source/bl/{bl_no}")
def insurance_source_bl(bl_no: str, request: Request):
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


@router.get("/insurance/{insurance_no}")
def insurance_detail(insurance_no: str, request: Request):
    account_id = _account_id(request)
    record = resolve_insurance_snapshot(public_insurance(_owned_insurance(insurance_no, account_id)), account_id)

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
</tr>
"""
    if not rows:
        rows = '<tr><td colspan="4" style="text-align:center;color:#6B7280;padding:30px;">No goods items registered.</td></tr>'

    html = f"""<!DOCTYPE html><html><head><meta charset="UTF-8"><title>{html_text(insurance_no)}</title>
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
table{{width:100%;border-collapse:collapse;min-width:680px;}}
th{{background:#111827;color:white;text-align:left;padding:13px;}}
td{{padding:13px;border-bottom:1px solid #E5E7EB;}}
@media(max-width:780px){{body{{padding:18px}}.meta{{grid-template-columns:1fr}}.header h1{{font-size:34px}}}}
</style></head><body><div class="container">
<div class="nav-row">
<a class="btn" href="/">Dashboard</a>
<a class="btn" href="/insurance-list">Insurance List</a>
<a class="btn" href="/edit-insurance/{html_attr(insurance_no)}">Edit</a>
<a class="btn" href="/insurance-pdf/{html_attr(insurance_no)}">PDF</a>
</div>
<div class="header">
<h1>{html_text(insurance_no)}</h1>
<div>Insurance Certificate</div>
<div class="meta">
<div><div class="label">Insurance Date</div><div class="value">{html_text(record.get("insurance_date", ""))}</div></div>
<div><div class="label">B/L No</div><div class="value">{html_text(bl_no)}</div></div>
<div><div class="label">Invoice No</div><div class="value">{html_text(invoice_no)}</div></div>
<div><div class="label">Packing No</div><div class="value">{html_text(packing_no)}</div></div>
<div><div class="label">Exporter</div><div class="value">{html_text(record.get("exporter", ""))}</div></div>
<div><div class="label">Consignee</div><div class="value">{html_text(record.get("consignee", ""))}</div></div>
<div><div class="label">Insurance Company</div><div class="value">{html_text(record.get("insurance_company", ""))}</div></div>
<div><div class="label">Policy No</div><div class="value">{html_text(record.get("policy_no", ""))}</div></div>
<div><div class="label">Insured Amount</div><div class="value">{html_text(record.get("insured_amount", ""))}</div></div>
<div><div class="label">Currency</div><div class="value">{html_text(record.get("currency", ""))}</div></div>
<div><div class="label">Coverage Type</div><div class="value">{html_text(record.get("coverage_type", ""))}</div></div>
<div><div class="label">Inspection Location</div><div class="value">{html_text(record.get("inspection_location", ""))}</div></div>
</div>
<div class="remarks" style="margin-top:14px;"><div class="label">Remarks</div><div>{html_text(record.get("remarks", ""))}</div></div>
</div>
<div class="cards">{cards}</div>
<div class="section"><h2>Transport Information</h2><div class="grid">
<div class="mini"><b>Transport Details</b>{html_text(record.get("transport_details", ""))}</div>
<div class="mini"><b>Port of Loading</b>{html_text(record.get("port_of_loading", ""))}</div>
<div class="mini"><b>Port of Discharge</b>{html_text(record.get("port_of_discharge", ""))}</div>
</div></div>
<div class="section"><h2>Goods Information</h2><div class="table-wrap"><table><thead><tr><th>No</th><th>Item</th><th>HS Code</th><th>Quantity</th></tr></thead><tbody>{rows}</tbody></table></div></div>
</div></body></html>"""
    return HTMLResponse(html)


@router.post("/insurance")
def save_inspection(
    request: Request,
    shipment_no: str = Form(""),
    insurance_date: str = Form(""),
    bl_no: str = Form(""),
    packing_no: str = Form(""),
    invoice_no: str = Form(""),
    exporter: str = Form(""),
    consignee: str = Form(""),
    insurance_company: str = Form(""),
    policy_no: str = Form(""),
    insured_amount: str = Form(""),
    currency: str = Form("USD"),
    inspection_location: str = Form(""),
    coverage_type: str = Form(""),
    remarks: str = Form(""),
    port_of_loading: str = Form(""),
    port_of_discharge: str = Form(""),
    transport_details: str = Form(""),
    item_name: List[str] = Form([]),
    item_id: List[str] = Form([]),
    hs_code: List[str] = Form([]),
    quantity: List[str] = Form([]),
    origin: Annotated[Optional[List[str]], Form()] = None,
    carton: Annotated[Optional[List[str]], Form()] = None,
    net_weight: Annotated[Optional[List[str]], Form()] = None,
    gross_weight: Annotated[Optional[List[str]], Form()] = None,
    exporter_name: Annotated[Optional[str], Form()] = None,
    exporter_address: Annotated[Optional[str], Form()] = None,
    exporter_email: Annotated[Optional[str], Form()] = None,
    exporter_phone: Annotated[Optional[str], Form()] = None,
    consignee_name: Annotated[Optional[str], Form()] = None,
    consignee_address: Annotated[Optional[str], Form()] = None,
    consignee_email: Annotated[Optional[str], Form()] = None,
    origin_country: Annotated[Optional[str], Form()] = None,
    destination_country: Annotated[Optional[str], Form()] = None,
):
    account_id = _account_id(request)
    validate_insurance_links(bl_no, packing_no, invoice_no, account_id, shipment_no)
    exporter = require_text("Exporter", exporter_name or exporter)
    consignee = require_text("Consignee", consignee_name or consignee)
    policy_no = require_text("Policy number", policy_no)
    saved = {}
    def add_insurance(records):
        insurance_number = next_identifier(records, "insurance_no", "INS")
        record = build_record(
        insurance_number, insurance_date, bl_no, packing_no,
        invoice_no, exporter, consignee, insurance_company, policy_no,
        insured_amount, currency,
        inspection_location, coverage_type, remarks, port_of_loading,
        port_of_discharge, transport_details, item_name, hs_code, quantity,
        shipment_no, origin, carton, net_weight, gross_weight, origin_country or "", destination_country or "",
        )
        assign_item_ids(record["items"], item_id)
        record.update({"exporter_name": exporter, "consignee_name": consignee})
        set_submitted_snapshot_fields(record, {
            "exporter_address": exporter_address, "exporter_email": exporter_email,
            "exporter_phone": exporter_phone, "consignee_address": consignee_address,
            "consignee_email": consignee_email, "origin_country": origin_country,
            "destination_country": destination_country,
        })
        record = resolve_insurance_snapshot(record, account_id)
        record["account_id"] = account_id
        records.append(record)
        saved["insurance_no"] = insurance_number
    locked_json_mutation(INSURANCE_FILE, [], add_insurance, list)
    return RedirectResponse(url=shipment_context_redirect_url(shipment_no, "insurance_no", saved["insurance_no"], "/insurance-list"), status_code=303)


@router.get("/edit-insurance/{insurance_no}")
def edit_inspection(insurance_no: str, request: Request):
    account_id = _account_id(request)
    products = product_module.load_products(account_id)
    record = resolve_insurance_snapshot(public_insurance(_owned_insurance(insurance_no, account_id)), account_id)
    return render_form(record, f"/update-insurance/{insurance_no}", "Edit Insurance Certificate", "Update Insurance Certificate", True, products=products, account_id=account_id)


@router.post("/update-insurance/{insurance_no}")
def update_inspection(
    insurance_no: str,
    request: Request,
    insurance_date: str = Form(""),
    bl_no: str = Form(""),
    packing_no: str = Form(""),
    invoice_no: str = Form(""),
    exporter: str = Form(""),
    consignee: str = Form(""),
    insurance_company: str = Form(""),
    policy_no: str = Form(""),
    insured_amount: str = Form(""),
    currency: str = Form("USD"),
    inspection_location: str = Form(""),
    coverage_type: str = Form(""),
    remarks: str = Form(""),
    port_of_loading: str = Form(""),
    port_of_discharge: str = Form(""),
    transport_details: str = Form(""),
    item_name: List[str] = Form([]),
    item_id: List[str] = Form([]),
    hs_code: List[str] = Form([]),
    quantity: List[str] = Form([]),
    origin: Annotated[Optional[List[str]], Form()] = None,
    carton: Annotated[Optional[List[str]], Form()] = None,
    net_weight: Annotated[Optional[List[str]], Form()] = None,
    gross_weight: Annotated[Optional[List[str]], Form()] = None,
    exporter_name: Annotated[Optional[str], Form()] = None,
    exporter_address: Annotated[Optional[str], Form()] = None,
    exporter_email: Annotated[Optional[str], Form()] = None,
    exporter_phone: Annotated[Optional[str], Form()] = None,
    consignee_name: Annotated[Optional[str], Form()] = None,
    consignee_address: Annotated[Optional[str], Form()] = None,
    consignee_email: Annotated[Optional[str], Form()] = None,
    origin_country: Annotated[Optional[str], Form()] = None,
    destination_country: Annotated[Optional[str], Form()] = None,
):
    account_id = _account_id(request)
    current = _owned_insurance(insurance_no, account_id)
    validate_insurance_links(bl_no, packing_no, invoice_no, account_id)
    exporter = require_text("Exporter", exporter_name or exporter)
    consignee = require_text("Consignee", consignee_name or consignee)
    policy_no = require_text("Policy number", policy_no)
    updated = build_record(
        insurance_no, insurance_date, bl_no, packing_no, invoice_no,
        exporter, consignee, insurance_company, policy_no, insured_amount,
        currency, inspection_location,
        coverage_type, remarks, port_of_loading, port_of_discharge,
        transport_details, item_name, hs_code, quantity,
        current.get("shipment_no", ""), origin, carton, net_weight, gross_weight,
        current.get("origin_country", "") if origin_country is None else origin_country,
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
    updated = resolve_insurance_snapshot(updated, account_id)
    updated["account_id"] = account_id
    def replace_insurance(records):
        for index, record in enumerate(records):
            if record.get("insurance_no") == insurance_no and record.get("account_id") == account_id:
                records[index] = updated
                return
        raise HTTPException(status_code=404, detail="Insurance Certificate not found")
    locked_json_mutation(INSURANCE_FILE, [], replace_insurance, list)
    return RedirectResponse(
        url=shipment_detail_redirect_url(current.get("shipment_no", ""), account_id, "/insurance-list"), status_code=303,
    )


@router.get("/delete-insurance/{insurance_no}")
def delete_inspection(insurance_no: str, request: Request):
    _owned_insurance(insurance_no, _account_id(request))
    return render_delete_page("Insurance Certificate", insurance_no, f"/delete-insurance/{insurance_no}", "/insurance-list", find_dependencies("Insurance Certificate", insurance_no, _account_id(request)))

@router.post("/delete-insurance/{insurance_no}")
def confirm_delete_insurance(insurance_no: str, request: Request):
    account_id = _account_id(request)
    _owned_insurance(insurance_no, account_id)
    dependencies = find_dependencies("Insurance Certificate", insurance_no, account_id)
    if dependencies:
        return render_delete_page("Insurance Certificate", insurance_no, f"/delete-insurance/{insurance_no}", "/insurance-list", dependencies, status_code=409)
    def remove(records):
        index = next((i for i, record in enumerate(records)
                      if record.get("insurance_no") == insurance_no and record.get("account_id") == account_id), None)
        if index is None:
            raise HTTPException(status_code=404, detail="Insurance Certificate not found")
        records.pop(index)
    locked_json_mutation(INSURANCE_FILE, [], remove, list)
    return RedirectResponse(url="/insurance-list", status_code=303)


@router.get("/insurance-data/{insurance_no}")
def inspection_data(insurance_no: str, request: Request):
    account_id = _account_id(request)
    return resolve_insurance_snapshot(public_insurance(_owned_insurance(insurance_no, account_id)), account_id)


@router.post("/insurance/pdf")
def create_inspection_pdf(request: Request, payload: dict = Body(...)):
    account_id = _account_id(request)
    validate_insurance_links(payload.get("bl_no", ""), payload.get("packing_no", ""), payload.get("invoice_no", ""), account_id)
    payload = public_insurance(payload)
    payload = resolve_insurance_snapshot(payload, account_id)
    insurance_no = payload.get("insurance_no") or "-"
    insurance_date = payload.get("insurance_date") or datetime.now().strftime("%Y-%m-%d")
    bl_no = payload.get("bl_no", "")
    packing_no = payload.get("packing_no", "")
    invoice_no = payload.get("invoice_no", "")
    exporter = payload.get("exporter", "")
    consignee = payload.get("consignee", "")
    exporter_address = payload.get("exporter_address", "")
    exporter_contact = " / ".join(value for value in (payload.get("exporter_email", ""), payload.get("exporter_phone", "")) if value)
    consignee_address = payload.get("consignee_address", "")
    consignee_email = payload.get("consignee_email", "")
    insurance_company = payload.get("insurance_company", "")
    policy_no = payload.get("policy_no", "")
    insured_amount = payload.get("insured_amount", "")
    currency = payload.get("currency", "USD")
    inspection_location = payload.get("inspection_location", "")
    coverage_type = payload.get("coverage_type", "")
    remarks = payload.get("remarks", "")
    port_of_loading = payload.get("port_of_loading", "")
    port_of_discharge = payload.get("port_of_discharge", "")
    transport_details = payload.get("transport_details", "")
    items = payload.get("items", [])

    buffer = BytesIO()
    ensure_pdf_fonts()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4
    pdf.setTitle(f"Insurance Certificate {insurance_no}")

    table_x = 45
    table_w = 505
    table_header_h = 28
    row_h = 26
    row_min_bottom = 145
    result_h = 105
    result_gap = 20

    def fit_text(text, max_width, font_name=TP_UNICODE, font_size=8):
        return fit_pdf_text(pdf, text, max_width, font_name, font_size)

    def draw_header():
        pdf.setFillColor(colors.HexColor("#111827"))
        pdf.rect(0, height - 90, width, 90, fill=1, stroke=0)
        pdf.setFillColor(colors.white)
        pdf.setFont(TP_UNICODE_BOLD, 23)
        pdf.drawString(45, height - 55, "INSURANCE CERTIFICATE")
        pdf.setFont(TP_UNICODE, 9)
        pdf.drawRightString(width - 45, height - 38, "Trade Paper AI")
        pdf.drawRightString(width - 45, height - 55, "Automated Trade Document")

        pdf.setFillColor(colors.black)
        pdf.setFont(TP_UNICODE_BOLD, 10)
        pdf.drawString(45, height - 118, fit_text(f"Insurance No: {insurance_no}", 505, TP_UNICODE_BOLD, 10))
        pdf.drawString(45, height - 136, f"Insurance Date: {insurance_date}")
        pdf.drawString(45, height - 154, f"B/L No: {bl_no}")
        pdf.drawString(45, height - 172, f"Packing No: {packing_no}")
        pdf.drawString(300, height - 172, f"Invoice No: {invoice_no}")

        pdf.setStrokeColor(colors.HexColor("#D1D5DB"))
        pdf.setFillColor(colors.HexColor("#F9FAFB"))
        pdf.roundRect(45, height - 260, 240, 72, 8, fill=1)
        pdf.roundRect(310, height - 260, 240, 72, 8, fill=1)
        pdf.setFillColor(colors.HexColor("#111827"))
        pdf.setFont(TP_UNICODE_BOLD, 10)
        pdf.drawString(60, height - 207, "EXPORTER")
        pdf.drawString(325, height - 207, "CONSIGNEE")
        pdf.setFont(TP_UNICODE, 8)
        pdf.drawString(60, height - 230, fit_text(exporter, 195))
        pdf.drawString(325, height - 230, fit_text(consignee, 195))
        pdf.drawString(60, height - 242, fit_text(exporter_address, 195, font_size=7))
        pdf.drawString(60, height - 252, fit_text(exporter_contact, 195, font_size=7))
        pdf.drawString(325, height - 242, fit_text(consignee_address, 195, font_size=7))
        pdf.drawString(325, height - 252, fit_text(consignee_email, 195, font_size=7))

        pdf.setFillColor(colors.black)
        pdf.setFont(TP_UNICODE_BOLD, 8)
        pdf.drawString(45, height - 287, fit_text(f"Insurance Company: {insurance_company}", 235, TP_UNICODE_BOLD, 8))
        pdf.drawString(300, height - 287, fit_text(f"Policy No: {policy_no}", 250, TP_UNICODE_BOLD, 8))
        pdf.drawString(45, height - 304, f"Insured Amount: {currency} {insured_amount}")
        pdf.drawString(300, height - 304, fit_text(f"Coverage Type: {coverage_type}", 250, TP_UNICODE_BOLD, 8))
        pdf.drawString(45, height - 321, fit_text(f"Inspection Location: {inspection_location}", 235, TP_UNICODE_BOLD, 8))
        pdf.drawString(300, height - 321, fit_text(f"Transport: {transport_details}", 250, TP_UNICODE_BOLD, 8))

    def draw_table_header():
        header_y = height - 355
        pdf.setFillColor(colors.HexColor("#E5E7EB"))
        pdf.rect(table_x, header_y, table_w, table_header_h, fill=1, stroke=0)
        pdf.setFillColor(colors.black)
        pdf.setFont(TP_UNICODE_BOLD, 8)
        pdf.drawString(52, header_y + 10, "No")
        pdf.drawString(82, header_y + 10, "Item")
        pdf.drawString(270, header_y + 10, "HS Code")
        pdf.drawRightString(540, header_y + 10, "Quantity")
        pdf.setFont(TP_UNICODE, 8)
        pdf.setStrokeColor(colors.HexColor("#D1D5DB"))
        return header_y - table_header_h

    def start_page():
        draw_header()
        return draw_table_header()

    def draw_footer():
        pdf.setFillColor(colors.black)
        pdf.setFont(TP_UNICODE, 10)
        pdf.drawString(45, 115, "Authorized Signature:")
        pdf.line(170, 115, 330, 115)
        pdf.setFillColor(colors.HexColor("#6B7280"))
        pdf.setFont(TP_UNICODE, 8)
        pdf.drawString(45, 60, "This document was generated by Trade Paper AI.")
        pdf.drawString(45, 45, "For trade documentation automation.")

    y = start_page()
    item_count = len(items)
    for index, item in enumerate(items, start=1):
        required_bottom = row_min_bottom + result_h + result_gap + row_h if index == item_count else row_min_bottom
        if y < required_bottom:
            pdf.showPage()
            y = start_page()
        pdf.rect(table_x, y, table_w, row_h, fill=0)
        pdf.drawString(52, y + 9, str(index))
        pdf.drawString(82, y + 9, fit_text(item.get("name", ""), 165))
        pdf.drawString(270, y + 9, str(item.get("hs_code", "")))
        cargo_detail = " / ".join(part for part in (
            str(item.get("quantity", "")), str(item.get("origin", "")),
            f"C:{item.get('carton', '')}", f"N:{item.get('net_weight', '')}",
            f"G:{item.get('gross_weight', '')}",
        ) if part and not part.endswith(":"))
        pdf.drawRightString(540, y + 9, fit_text(cargo_detail, 190))
        y -= row_h

    result_top = y - result_gap
    result_bottom = result_top - result_h
    if result_bottom < row_min_bottom:
        pdf.showPage()
        y = start_page()
        result_top = y - result_gap
        result_bottom = result_top - result_h

    pdf.setFillColor(colors.HexColor("#111827"))
    pdf.roundRect(45, result_bottom, 505, result_h, 8, fill=1, stroke=0)
    pdf.setFillColor(colors.white)
    pdf.setFont(TP_UNICODE_BOLD, 10)
    pdf.drawString(60, result_top - 25, "Insurance Information")
    pdf.setFont(TP_UNICODE, 9)
    pdf.drawString(60, result_top - 45, fit_text(f"Coverage Type: {coverage_type}", 455, TP_UNICODE, 9))
    pdf.drawString(60, result_top - 63, fit_text(f"Policy Number: {policy_no} | Insured Amount: {currency} {insured_amount}", 455, TP_UNICODE, 9))
    if remarks:
        pdf.drawString(60, result_top - 81, fit_text(f"Remarks: {remarks}", 455, TP_UNICODE, 9))

    draw_footer()
    pdf.showPage()
    pdf.save()
    buffer.seek(0)
    return Response(
        content=buffer.getvalue(),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{insurance_no}.pdf"'},
    )


@router.get("/insurance-pdf/{insurance_no}")
def inspection_pdf(insurance_no: str, request: Request):
    account_id = _account_id(request)
    record = resolve_insurance_snapshot(public_insurance(_owned_insurance(insurance_no, account_id)), account_id)
    set_pdf_export_record(request, record)
    return create_inspection_pdf(request, record)
