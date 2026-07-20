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
from app.validation import require_at_least_one_reference, require_consistent_reference, require_existing_reference, require_text
from app.referential_integrity import confirmed_identifier_delete, identifier_delete_confirmation

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


def load_containers():
    return load_json(CONTAINER_FILE, [])


def save_containers(records):
    atomic_write_json(CONTAINER_FILE, records, list)


def load_shipments():
    return load_json(SHIPMENT_FILE, [])


def load_packing_lists():
    return load_json(PACKING_FILE, [])


def load_bills_of_lading():
    return load_json(BL_FILE, [])


def validate_container_links(shipment_no, packing_no, invoice_no, bl_no):
    shipments = load_shipments()
    packings = load_packing_lists()
    require_at_least_one_reference(
        ("Shipment", shipment_no, shipments, "shipment_no"),
        ("Packing List", packing_no, packings, "packing_no"),
    )
    shipment = require_existing_reference("Shipment", shipment_no, shipments, "shipment_no")
    packing = require_existing_reference("Packing List", packing_no, packings, "packing_no")
    bill = require_existing_reference("Bill of Lading", bl_no, load_bills_of_lading(), "bl_no")
    require_existing_reference("Invoice", invoice_no, load_json(INVOICE_FILE, []), "invoice_no")
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
    numbers = [
        int(record.get("container_record_no", "CON-000").split("-")[1])
        for record in records
        if record.get("container_record_no", "").startswith("CON-")
    ]
    return f"CON-{max(numbers, default=0) + 1:03d}"


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


def shipment_exists(shipment_no):
    return bool(find_record(load_shipments(), "shipment_no", shipment_no))


def packing_exists(packing_no):
    return bool(find_record(load_packing_lists(), "packing_no", packing_no))


def bl_exists(bl_no):
    return bool(find_record(load_bills_of_lading(), "bl_no", bl_no))


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
        "container_no": "",
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


def copy_packing_payload(payload, packing_no):
    packing = find_record(load_packing_lists(), "packing_no", packing_no)
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


def copy_bl_payload(payload, bl_no):
    bill = find_record(load_bills_of_lading(), "bl_no", bl_no)
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


def payload_from_sources(shipment_no="", packing_no="", bl_no=""):
    payload = blank_payload()
    if shipment_no and shipment_exists(shipment_no):
        payload["shipment_no"] = shipment_no
    if packing_no:
        payload = copy_packing_payload(payload, packing_no)
    if bl_no:
        payload = copy_bl_payload(payload, bl_no)
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


def render_form(record, action, title, button_text, show_no=False):
    rows = build_item_rows(record.get("items", []))
    no_input = ""
    if show_no:
        no_input = f'<div class="field"><label>Container Record No</label><input type="text" name="container_record_no" value="{html_attr(record.get("container_record_no", ""))}" placeholder="Container Record No" readonly></div>'
    shipment_select = select_html("shipment_no", record.get("shipment_no", ""), doc_options(load_shipments(), "shipment_no"), "Select Shipment")
    packing_select = select_html("packing_no", record.get("packing_no", ""), doc_options(load_packing_lists(), "packing_no"), "Select Packing List")
    bl_select = select_html("bl_no", record.get("bl_no", ""), doc_options(load_bills_of_lading(), "bl_no"), "Select B/L")

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
def container_list(search: str = ""):
    records = sorted(load_containers(), key=lambda r: r.get("container_record_no", ""), reverse=True)
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
def container_form(shipment_no: str = "", packing_no: str = "", bl_no: str = ""):
    record = payload_from_sources(shipment_no, packing_no, bl_no)
    record["container_record_no"] = next_container_record_no(load_containers())
    return render_form(record, "/container", "New Container Load Plan", "Save Container Record", show_no=True)


@router.post("/container")
def save_container(
    container_date: str = Form(""), shipment_no: str = Form(""), packing_no: str = Form(""),
    bl_no: str = Form(""), invoice_no: str = Form(""), container_no: str = Form(""),
    seal_no: str = Form(""), container_type: str = Form(""), carrier: str = Form(""),
    vessel: str = Form(""), voyage_no: str = Form(""), etd: str = Form(""), eta: str = Form(""),
    port_of_loading: str = Form(""), port_of_discharge: str = Form(""),
    place_of_delivery: str = Form(""), loading_place: str = Form(""), remarks: str = Form(""),
    item_name: List[str] = Form([]), hs_code: List[str] = Form([]), quantity: List[str] = Form([]),
    carton: List[str] = Form([]), net_weight: List[str] = Form([]), gross_weight: List[str] = Form([]),
    total_carton: str = Form(""), total_net_weight: str = Form(""), total_gross_weight: str = Form(""),
):
    container_no = require_text("Container number", container_no)
    validate_container_links(shipment_no, packing_no, invoice_no, bl_no)
    def add_container(records):
        record = build_record(
        next_identifier(records, "container_record_no", "CON"), container_date, shipment_no, packing_no, bl_no,
        invoice_no, container_no, seal_no, container_type, carrier, vessel, voyage_no,
        etd, eta, port_of_loading, port_of_discharge, place_of_delivery, loading_place,
        remarks, item_name, hs_code, quantity, carton, net_weight, gross_weight,
        total_carton, total_net_weight, total_gross_weight,
        )
        records.append(record)
    locked_json_mutation(CONTAINER_FILE, [], add_container, list)
    return RedirectResponse("/container-list", status_code=303)


@router.get("/edit-container/{container_record_no}", response_class=HTMLResponse)
def edit_container(container_record_no: str):
    record = find_record(load_containers(), "container_record_no", container_record_no)
    if not record:
        raise HTTPException(status_code=404, detail="Container record not found")
    return render_form(record, f"/update-container/{html_attr(container_record_no)}", "Edit Container Load Plan", "Update Container Record", show_no=True)


@router.post("/update-container/{container_record_no}")
def update_container(
    container_record_no: str,
    container_date: str = Form(""), shipment_no: str = Form(""), packing_no: str = Form(""),
    bl_no: str = Form(""), invoice_no: str = Form(""), container_no: str = Form(""),
    seal_no: str = Form(""), container_type: str = Form(""), carrier: str = Form(""),
    vessel: str = Form(""), voyage_no: str = Form(""), etd: str = Form(""), eta: str = Form(""),
    port_of_loading: str = Form(""), port_of_discharge: str = Form(""),
    place_of_delivery: str = Form(""), loading_place: str = Form(""), remarks: str = Form(""),
    item_name: List[str] = Form([]), hs_code: List[str] = Form([]), quantity: List[str] = Form([]),
    carton: List[str] = Form([]), net_weight: List[str] = Form([]), gross_weight: List[str] = Form([]),
    total_carton: str = Form(""), total_net_weight: str = Form(""), total_gross_weight: str = Form(""),
):
    container_no = require_text("Container number", container_no)
    validate_container_links(shipment_no, packing_no, invoice_no, bl_no)
    def replace_container(records):
        for index, record in enumerate(records):
            if record.get("container_record_no") != container_record_no:
                continue
            records[index] = build_record(
                container_record_no, container_date, shipment_no, packing_no, bl_no,
                invoice_no, container_no, seal_no, container_type, carrier, vessel,
                voyage_no, etd, eta, port_of_loading, port_of_discharge, place_of_delivery,
                loading_place, remarks, item_name, hs_code, quantity, carton, net_weight,
                gross_weight, total_carton, total_net_weight, total_gross_weight,
            )
            return
        raise HTTPException(status_code=404, detail="Container record not found")
    locked_json_mutation(CONTAINER_FILE, [], replace_container, list)
    return RedirectResponse("/container-list", status_code=303)


@router.get("/delete-container/{container_record_no}")
def delete_container(container_record_no: str):
    return identifier_delete_confirmation("Container Management", "Container Management", container_record_no, CONTAINER_FILE, "container_record_no", f"/delete-container/{container_record_no}", "/container-list")

@router.post("/delete-container/{container_record_no}")
def confirm_delete_container(container_record_no: str):
    return confirmed_identifier_delete("Container Management", "Container Management", container_record_no, CONTAINER_FILE, "container_record_no", f"/delete-container/{container_record_no}", "/container-list", "/container-list")


@router.get("/container-data/{container_record_no}")
def container_data(container_record_no: str):
    record = find_record(load_containers(), "container_record_no", container_record_no)
    if not record:
        raise HTTPException(status_code=404, detail="Container record not found")
    return record


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
def container_detail(container_record_no: str):
    record = find_record(load_containers(), "container_record_no", container_record_no)
    if not record:
        raise HTTPException(status_code=404, detail="Container record not found")
    shipment_no = record.get("shipment_no", "")
    packing_no = record.get("packing_no", "")
    bl_no = record.get("bl_no", "")
    rows = "".join(
        f"<tr><td>{i}</td><td>{html_text(item.get('name',''))}</td><td>{html_text(item.get('hs_code',''))}</td><td>{html_text(item.get('quantity',''))}</td><td>{html_text(item.get('carton',''))}</td><td>{html_text(item.get('net_weight',''))}</td><td>{html_text(item.get('gross_weight',''))}</td></tr>"
        for i, item in enumerate(record.get("items", []), 1)
    )
    cards = (
        status_card("Shipment", shipment_no, shipment_exists(shipment_no), detail=f"/shipment/{shipment_no}" if shipment_no else "")
        + status_card("Packing List", packing_no, packing_exists(packing_no), pdf=f"/packing-list-pdf/{packing_no}" if packing_no else "", edit=f"/edit-packing/{packing_no}" if packing_no else "")
        + status_card("Bill of Lading", bl_no, bl_exists(bl_no), pdf=f"/bl-pdf/{bl_no}" if bl_no else "", edit=f"/edit-bl/{bl_no}" if bl_no else "")
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
</div><div class="remarks"><div class="label">Remarks</div><div>{html_text(record.get("remarks",""))}</div></div></div>
<div class="cards">{cards}</div>
<div class="table-wrap"><table><thead><tr><th>No</th><th>Item</th><th>HS Code</th><th>Qty</th><th>Carton</th><th>Net Weight</th><th>Gross Weight</th></tr></thead><tbody>{rows}</tbody></table></div>
<div class="cards"><div class="mini"><b>Total Cartons</b><span>{html_text(record.get("total_carton",""))}</span></div><div class="mini"><b>Total Net Weight</b><span>{html_text(record.get("total_net_weight",""))}</span></div><div class="mini"><b>Total Gross Weight</b><span>{html_text(record.get("total_gross_weight",""))}</span></div></div>
</div></body></html>"""
    return HTMLResponse(html)


def draw_text_fit(pdf, text, x, y, max_width, font="Helvetica", size=8, min_size=6):
    text = str(text or "")
    current_size = size
    while current_size > min_size and pdf.stringWidth(text, font, current_size) > max_width:
        current_size -= 0.5
    pdf.setFont(font, current_size)
    pdf.drawString(x, y, text)


def create_container_pdf_buffer(payload):
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
        pdf.drawCentredString(width / 2, height - 70, "CONTAINER LOAD PLAN")
        pdf.setFillColor(colors.black)
        pdf.setFont("Helvetica", 9)
        info = [
            ("Record No", payload.get("container_record_no", "")), ("Date", payload.get("container_date", "")),
            ("Container No", payload.get("container_no", "")), ("Seal No", payload.get("seal_no", "")),
            ("Type", payload.get("container_type", "")), ("Shipment No", payload.get("shipment_no", "")),
            ("Packing No", payload.get("packing_no", "")), ("B/L No", payload.get("bl_no", "")),
            ("ETD", payload.get("etd", "")), ("ETA", payload.get("eta", "")),
        ]
        y = height - 122
        for idx, (label, value) in enumerate(info):
            x = 48 if idx % 2 == 0 else 318
            if idx and idx % 2 == 0:
                y -= 16
            pdf.drawString(x, y, f"{label}:")
            draw_text_fit(pdf, value, x + 78, y, 155, size=9)
        y2 = height - 215
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
        pdf.setFont("Helvetica-Bold", 8)
        for x, label in [(48, "No"), (74, "Item"), (218, "HS Code"), (300, "Qty"), (354, "Carton"), (412, "Net Weight"), (492, "Gross Weight")]:
            pdf.drawString(x, y + 8, label)
        pdf.setFillColor(colors.black)

    header()
    table_start_y = height - 335
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
    pdf.setFont("Helvetica-Bold", 10)
    pdf.drawString(350, summary_y + 52, f"Total Cartons: {payload.get('total_carton', '')}")
    pdf.drawString(350, summary_y + 32, f"Total Net Weight: {payload.get('total_net_weight', '')}")
    pdf.drawString(350, summary_y + 12, f"Total Gross Weight: {payload.get('total_gross_weight', '')}")
    pdf.setFillColor(colors.black)
    if payload.get("remarks"):
        pdf.setFont("Helvetica-Bold", 10)
        pdf.drawString(40, summary_y + 52, "Remarks")
        draw_text_fit(pdf, payload.get("remarks", ""), 40, summary_y + 34, 260, size=9)
    pdf.setStrokeColor(colors.black)
    pdf.line(380, max(90, summary_y - 42), 555, max(90, summary_y - 42))
    pdf.setFont("Helvetica", 9)
    pdf.drawString(415, max(75, summary_y - 57), "Authorized Signature")
    footer()
    pdf.save()
    buffer.seek(0)
    return buffer


@router.post("/container/pdf")
def create_container_pdf(payload: dict = Body(...)):
    pdf_buffer = create_container_pdf_buffer(payload)
    filename = f"{payload.get('container_record_no', 'container')}.pdf"
    return Response(pdf_buffer.getvalue(), media_type="application/pdf", headers={"Content-Disposition": f"attachment; filename={filename}"})


@router.get("/container-pdf/{container_record_no}")
def container_pdf(container_record_no: str):
    record = find_record(load_containers(), "container_record_no", container_record_no)
    if not record:
        raise HTTPException(status_code=404, detail="Container record not found")
    pdf_buffer = create_container_pdf_buffer(record)
    return Response(pdf_buffer.getvalue(), media_type="application/pdf", headers={"Content-Disposition": f"attachment; filename={container_record_no}.pdf"})
