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

CO_FILE = data_path("certificates_of_origin.json")
BL_FILE = data_path("bills_of_lading.json")
PRODUCT_FILE = data_path("products.json")
PACKING_FILE = data_path("packing_lists.json")
INVOICE_FILE = data_path("invoices.json")


def load_certificates():
    return load_json_strict(CO_FILE, [], list)


def save_certificates(records):
    atomic_write_json(CO_FILE, records, list)


def load_bills_of_lading():
    return load_json_strict(BL_FILE, [], list)


def load_products():
    return load_json_strict(PRODUCT_FILE, [], list)


def validate_co_links(bl_no, packing_no, invoice_no):
    bill = require_existing_reference("Bill of Lading", bl_no, load_bills_of_lading(), "bl_no", required=True)
    require_existing_reference("Packing List", packing_no, load_json_strict(PACKING_FILE, [], list), "packing_no")
    require_existing_reference("Invoice", invoice_no, load_json_strict(INVOICE_FILE, [], list), "invoice_no")
    require_consistent_reference("Packing List", packing_no, bill.get("packing_no", ""), "selected Bill of Lading")
    require_consistent_reference("Invoice", invoice_no, bill.get("invoice_no", ""), "selected Bill of Lading")


def next_co_no(records):
    return next_identifier(records, "co_no", "CO")
    numbers = [
        int(record.get("co_no", "CO-000").split("-")[1])
        for record in records
        if record.get("co_no", "").startswith("CO-")
    ]
    return f"CO-{max(numbers, default=0) + 1:03d}"


def html_attr(value):
    return html_lib.escape(str(value or ""), quote=True)


def html_text(value):
    return html_lib.escape(str(value or ""))


def bl_select_html(selected):
    options = ['<select name="bl_no">', '<option value="">Select B/L</option>']
    for bl in load_bills_of_lading():
        bl_no = bl.get("bl_no", "")
        if not bl_no:
            continue
        selected_attr = " selected" if bl_no == selected else ""
        options.append(f'<option value="{html_attr(bl_no)}"{selected_attr}>{html_text(bl_no)}</option>')
    options.append("</select>")
    return "".join(options)


def find_product_origin(item, products):
    item_name = str(item.get("name", "")).strip().lower()
    item_hs = str(item.get("hs_code", "")).strip().lower()

    for product in products:
        if item_name and str(product.get("name", "")).strip().lower() == item_name:
            return product.get("origin", "")

    for product in products:
        if item_hs and str(product.get("hs_code", "")).strip().lower() == item_hs:
            return product.get("origin", "")

    return item.get("origin", "")


def build_items(name, hs_code, quantity, origin):
    items = []
    for i in range(len(name)):
        if not name[i].strip():
            continue
        items.append({
            "name": name[i],
            "hs_code": hs_code[i] if i < len(hs_code) else "",
            "quantity": quantity[i] if i < len(quantity) else "",
            "origin": origin[i] if i < len(origin) else "",
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
<input type="text" name="hs_code" value="{html_attr(item.get('hs_code', ''))}" placeholder="HS Code">
<input type="text" name="quantity" value="{html_attr(item.get('quantity', ''))}" placeholder="Quantity">
<input type="text" name="origin" value="{html_attr(item.get('origin', ''))}" placeholder="Origin">
<button class="remove" type="button" onclick="removeItem(this)">Remove Item</button>
</div>
"""
    return rows


def blank_payload():
    return {
        "co_date": datetime.now().strftime("%Y-%m-%d"),
        "bl_no": "",
        "invoice_no": "",
        "packing_no": "",
        "exporter": "",
        "consignee": "",
        "country_of_origin": "",
        "destination_country": "",
        "transport_details": "",
        "port_of_loading": "",
        "port_of_discharge": "",
        "remarks": "",
        "items": [],
    }


def payload_from_bl(bl_no):
    payload = blank_payload()
    if not bl_no:
        return payload

    products = load_products()
    for bl in load_bills_of_lading():
        if bl.get("bl_no") != bl_no:
            continue

        items = []
        origins = []
        for item in bl.get("items", []):
            origin = find_product_origin(item, products)
            if origin:
                origins.append(origin)
            items.append({
                "name": item.get("name", ""),
                "hs_code": item.get("hs_code", ""),
                "quantity": item.get("quantity", ""),
                "origin": origin,
            })

        unique_origins = set(origins)
        country_of_origin = origins[0] if len(unique_origins) == 1 else ""

        payload.update({
            "bl_no": bl.get("bl_no", ""),
            "invoice_no": bl.get("invoice_no", ""),
            "packing_no": bl.get("packing_no", ""),
            "exporter": bl.get("shipper", ""),
            "consignee": bl.get("consignee", ""),
            "country_of_origin": country_of_origin,
            "transport_details": " ".join(part for part in [bl.get("vessel", ""), bl.get("voyage_no", "")] if part),
            "port_of_loading": bl.get("port_of_loading", ""),
            "port_of_discharge": bl.get("port_of_discharge", ""),
            "items": items,
        })
        break

    return payload


def build_record(
    co_no, co_date, bl_no, invoice_no, packing_no, exporter, consignee,
    country_of_origin, destination_country, transport_details,
    port_of_loading, port_of_discharge, remarks, item_name, hs_code,
    quantity, origin,
):
    return {
        "co_no": co_no,
        "co_date": co_date,
        "bl_no": bl_no,
        "invoice_no": invoice_no,
        "packing_no": packing_no,
        "exporter": exporter,
        "consignee": consignee,
        "country_of_origin": country_of_origin,
        "destination_country": destination_country,
        "transport_details": transport_details,
        "port_of_loading": port_of_loading,
        "port_of_discharge": port_of_discharge,
        "remarks": remarks,
        "items": build_items(item_name, hs_code, quantity, origin),
    }


def render_form(record, action, title, button_text, show_co_no=False, shipment_no=""):
    co_no_input = ""
    if show_co_no:
        co_no_input = f'<input type="text" value="{html_attr(record.get("co_no", ""))}" placeholder="C/O No" readonly>'
    product_master_json = json.dumps(load_products(), ensure_ascii=False)

    html = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Certificate of Origin</title>
<style>
body{font-family:Arial,sans-serif;background:#f3f4f6;padding:40px;}
.container{max-width:960px;margin:auto;background:white;padding:35px;border-radius:16px;}
h1{text-align:center;font-size:48px;margin-bottom:10px;}
.sub{text-align:center;color:#6B7280;margin-bottom:35px;}
.nav-row{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:25px;}
.card{border:1px solid #E5E7EB;border-radius:16px;padding:25px;margin-bottom:25px;background:#fff;}
.item-row{display:grid;grid-template-columns:1.5fr 1fr .8fr 1fr;gap:12px;border:1px solid #E5E7EB;border-radius:14px;padding:20px;margin-bottom:18px;background:#F9FAFB;}
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
<a href="/co-list"><button class="small" type="button">C/O List</button></a>
</div>
<h1>__TITLE__</h1>
<p class="sub">Certify goods origin from shipment and product master data</p>
<form action="__ACTION__" method="post">
__SHIPMENT_CONTEXT__
<div class="card">
<h2>Document Information</h2>
__CO_NO_INPUT__
<input type="date" name="co_date" value="__CO_DATE__">
__BL_SELECT__
<input type="text" name="invoice_no" value="__INVOICE_NO__" placeholder="Invoice No">
<input type="text" name="packing_no" value="__PACKING_NO__" placeholder="Packing No">
</div>
<div class="card">
<h2>Party Information</h2>
<input type="text" name="exporter" value="__EXPORTER__" placeholder="Exporter">
<input type="text" name="consignee" value="__CONSIGNEE__" placeholder="Consignee">
</div>
<div class="card">
<h2>Origin And Shipment</h2>
<input type="text" name="country_of_origin" value="__COUNTRY_OF_ORIGIN__" placeholder="Country of Origin">
<input type="text" name="destination_country" value="__DESTINATION_COUNTRY__" placeholder="Destination Country">
<input type="text" name="transport_details" value="__TRANSPORT_DETAILS__" placeholder="Transport Details">
<input type="text" name="port_of_loading" value="__PORT_OF_LOADING__" placeholder="Port of Loading">
<input type="text" name="port_of_discharge" value="__PORT_OF_DISCHARGE__" placeholder="Port of Discharge">
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
function productOriginFor(item){
    const name = itemNameKey(item);
    const hs = hsKey(item);
    const product = PRODUCT_MASTER.find(product => name && itemNameKey(product) === name)
        || PRODUCT_MASTER.find(product => hs && hsKey(product) === hs);
    return product?.origin || "";
}
function rowData(row){
    return {
        name: row.querySelector('[name="item_name"]')?.value || "",
        hs_code: row.querySelector('[name="hs_code"]')?.value || "",
        quantity: row.querySelector('[name="quantity"]')?.value || "",
        origin: row.querySelector('[name="origin"]')?.value || ""
    };
}
function currentItems(){
    return Array.from(document.querySelectorAll("#items_area .item-row")).map(rowData);
}
function matchItem(target, candidates){
    const name = itemNameKey(target);
    const hs = hsKey(target);
    return candidates.find(item => name && itemNameKey(item) === name)
        || candidates.find(item => hs && hsKey(item) === hs);
}
function itemRowHtml(item = {}){
    return `<div class="item-row">
<input type="text" name="item_name" value="${escapeAttr(item.name || "")}" placeholder="Item Name">
<input type="text" name="hs_code" value="${escapeAttr(item.hs_code || "")}" placeholder="HS Code">
<input type="text" name="quantity" value="${escapeAttr(item.quantity || "")}" placeholder="Quantity">
<input type="text" name="origin" value="${escapeAttr(item.origin || "")}" placeholder="Origin">
<button class="remove" type="button" onclick="removeItem(this)">Remove Item</button>
</div>`;
}
function updateCountryOfOrigin(){
    const origins = Array.from(document.querySelectorAll('[name="origin"]'))
        .map(input => input.value.trim())
        .filter(Boolean);
    const unique = Array.from(new Set(origins));
    const country = document.querySelector('[name="country_of_origin"]');
    if(country) country.value = unique.length === 1 ? unique[0] : "";
}
function renderItems(items){
    document.getElementById("items_area").innerHTML = (items.length ? items : [{}]).map(itemRowHtml).join("");
    updateCountryOfOrigin();
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
    updateCountryOfOrigin();
}
async function loadBlPrefill(blNo){
    if(!blNo) return;
    try{
        const response = await fetch(`/co-source/bl/${encodeURIComponent(blNo)}`);
        if(!response.ok) throw new Error(`${response.status} ${response.statusText}`);
        const bl = await response.json();
        const current = currentItems();
        document.querySelector('[name="invoice_no"]').value = bl.invoice_no || "";
        document.querySelector('[name="packing_no"]').value = bl.packing_no || "";
        document.querySelector('[name="exporter"]').value = bl.shipper || "";
        document.querySelector('[name="consignee"]').value = bl.consignee || "";
        document.querySelector('[name="transport_details"]').value = [bl.vessel || "", bl.voyage_no || ""].filter(Boolean).join(" ");
        document.querySelector('[name="port_of_loading"]').value = bl.port_of_loading || "";
        document.querySelector('[name="port_of_discharge"]').value = bl.port_of_discharge || "";
        document.querySelector('[name="destination_country"]').value = bl.place_of_delivery || "";
        const items = (bl.items || []).map(item => {
            const existing = matchItem(item, current) || {};
            const productOrigin = productOriginFor(item);
            return {
                name: item.name || "",
                hs_code: item.hs_code || "",
                quantity: item.quantity || "",
                origin: existing.origin || productOrigin || ""
            };
        });
        renderItems(items);
    }catch(error){
        console.error(`C/O B/L prefill failed for ${blNo}:`, error);
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
        "__CO_NO_INPUT__": co_no_input,
        "__CO_DATE__": html_attr(record.get("co_date", "")),
        "__BL_SELECT__": bl_select_html(record.get("bl_no", "")),
        "__INVOICE_NO__": html_attr(record.get("invoice_no", "")),
        "__PACKING_NO__": html_attr(record.get("packing_no", "")),
        "__EXPORTER__": html_attr(record.get("exporter", "")),
        "__CONSIGNEE__": html_attr(record.get("consignee", "")),
        "__COUNTRY_OF_ORIGIN__": html_attr(record.get("country_of_origin", "")),
        "__DESTINATION_COUNTRY__": html_attr(record.get("destination_country", "")),
        "__TRANSPORT_DETAILS__": html_attr(record.get("transport_details", "")),
        "__PORT_OF_LOADING__": html_attr(record.get("port_of_loading", "")),
        "__PORT_OF_DISCHARGE__": html_attr(record.get("port_of_discharge", "")),
        "__REMARKS__": html_attr(record.get("remarks", "")),
        "__ITEM_ROWS__": build_item_rows(record.get("items", [])),
        "__BUTTON_TEXT__": html_attr(button_text),
        "__SHIPMENT_CONTEXT__": f'<input type="hidden" name="shipment_no" value="{html_attr(shipment_no)}">' if shipment_no else "",
        "__PRODUCT_MASTER__": product_master_json,
    }
    for key, value in replacements.items():
        html = html.replace(key, value)
    return HTMLResponse(html)


@router.get("/co-list")
def co_list(search: str = ""):
    records = list(reversed(load_certificates()))
    if search:
        q = search.lower()
        records = [
            record for record in records
            if q in str(record.get("co_no", "")).lower()
            or q in str(record.get("bl_no", "")).lower()
            or q in str(record.get("invoice_no", "")).lower()
            or q in str(record.get("exporter", "")).lower()
            or q in str(record.get("consignee", "")).lower()
            or q in str(record.get("items", "")).lower()
        ]

    html = f"""
<!DOCTYPE html><html><head><meta charset="UTF-8"><title>Certificate of Origin List</title>
<style>
body{{font-family:Arial,sans-serif;background:#f3f4f6;padding:40px;color:#111827;}}
.container{{width:94%;margin:auto;}}
h1{{text-align:center;font-size:48px;margin:8px 0 10px;}}
.sub{{text-align:center;color:#6B7280;margin-bottom:30px;}}
.toolbar{{display:flex;justify-content:space-between;align-items:center;gap:18px;flex-wrap:wrap;margin-bottom:20px;}}
.nav,.search{{display:flex;gap:12px;align-items:center;flex-wrap:wrap;}}
.btn,button{{display:inline-block;padding:13px 18px;background:#111827;color:white;border:none;border-radius:12px;text-decoration:none;font-size:15px;cursor:pointer;}}
.reset{{background:#6B7280;}}
input{{padding:13px;width:430px;max-width:100%;border:1px solid #D1D5DB;border-radius:10px;font-size:15px;box-sizing:border-box;}}
.count{{font-size:18px;font-weight:bold;margin:22px 0;color:#374151;}}
.table-wrap{{background:white;border:1px solid #E5E7EB;border-radius:16px;overflow:auto;box-shadow:0 12px 35px rgba(15,23,42,.08);}}
table{{width:100%;border-collapse:collapse;min-width:980px;}}
th{{background:#111827;color:white;text-align:left;padding:14px;font-size:14px;}}
td{{padding:14px;border-bottom:1px solid #E5E7EB;font-size:14px;}}
.link{{color:#111827;font-weight:bold;text-decoration:none;}}
.pdf{{color:#2563EB;font-weight:bold;text-decoration:none;}}
.danger{{color:#DC2626;font-weight:bold;text-decoration:none;}}
@media(max-width:780px){{body{{padding:18px}}h1{{font-size:36px}}}}
</style></head><body><div class="container">
<h1>Certificate of Origin List</h1>
<p class="sub">Manage all Certificate of Origin documents</p>
<div class="toolbar">
<div class="nav"><a class="btn" href="/">Dashboard</a><a class="btn" href="/co-form">+ New C/O</a></div>
<form class="search" action="/co-list" method="get">
<input type="text" name="search" value="{html_attr(search)}" placeholder="Search C/O, B/L, invoice, exporter, consignee or item">
<button type="submit">Search</button><a class="btn reset" href="/co-list">Reset</a>
</form></div>
<div class="count">Total Certificates: {len(records)}</div>
<div class="table-wrap"><table><thead><tr>
<th>C/O No</th><th>B/L No</th><th>Invoice</th><th>Exporter</th><th>Consignee</th><th>View</th><th>PDF</th><th>Edit</th><th>Delete</th>
</tr></thead><tbody>
"""
    if not records:
        html += '<tr><td colspan="9" style="padding:35px;text-align:center;color:#6B7280;">No Certificates of Origin have been registered yet.</td></tr>'
    else:
        for record in records:
            co_no = record.get("co_no", "")
            html += f"""
<tr>
<td>{html_text(co_no)}</td>
<td>{html_text(record.get("bl_no", ""))}</td>
<td>{html_text(record.get("invoice_no", ""))}</td>
<td>{html_text(record.get("exporter", ""))}</td>
<td>{html_text(record.get("consignee", ""))}</td>
<td><a class="link" href="/co/{html_attr(co_no)}">View</a></td>
<td><a class="pdf" href="/co-pdf/{html_attr(co_no)}">PDF</a></td>
<td><a class="link" href="/edit-co/{html_attr(co_no)}">Edit</a></td>
<td><a class="danger" href="/delete-co/{html_attr(co_no)}">Delete</a></td>
</tr>
"""
    html += "</tbody></table></div></div></body></html>"
    return HTMLResponse(html)


@router.get("/co-form")
def co_form(bl_no: str = "", shipment_no: str = ""):
    return render_form(payload_from_bl(bl_no), "/co", "Certificate of Origin", "Save Certificate of Origin", shipment_no=shipment_no)


@router.get("/co-source/bl/{bl_no}")
def co_source_bl(bl_no: str):
    for record in load_bills_of_lading():
        if record.get("bl_no") == bl_no:
            return record
    raise HTTPException(status_code=404, detail="Bill of Lading not found")


@router.get("/co/{co_no}")
def co_detail(co_no: str):
    record = None
    for saved in load_certificates():
        if saved.get("co_no") == co_no:
            record = saved
            break
    if not record:
        raise HTTPException(status_code=404, detail="Certificate of Origin not found")

    rows = ""
    for index, item in enumerate(record.get("items", []), start=1):
        rows += f"""
<tr>
<td>{index}</td>
<td>{html_text(item.get("name", ""))}</td>
<td>{html_text(item.get("hs_code", ""))}</td>
<td>{html_text(item.get("quantity", ""))}</td>
<td>{html_text(item.get("origin", ""))}</td>
</tr>
"""
    if not rows:
        rows = '<tr><td colspan="5" style="text-align:center;color:#6B7280;padding:30px;">No goods items registered.</td></tr>'

    html = f"""<!DOCTYPE html><html><head><meta charset="UTF-8"><title>{html_text(co_no)}</title>
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
.section{{background:white;border:1px solid #E5E7EB;border-radius:16px;padding:24px;margin:24px 0;box-shadow:0 10px 25px rgba(15,23,42,.07);}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:16px;}}
.mini{{border:1px solid #E5E7EB;border-radius:12px;padding:16px;background:#F9FAFB;}}
.mini b{{display:block;margin-bottom:8px;color:#374151;}}
.table-wrap{{background:white;border-radius:16px;overflow:auto;border:1px solid #E5E7EB;}}
table{{width:100%;border-collapse:collapse;min-width:760px;}}
th{{background:#111827;color:white;text-align:left;padding:13px;}}
td{{padding:13px;border-bottom:1px solid #E5E7EB;}}
@media(max-width:780px){{body{{padding:18px}}.meta{{grid-template-columns:1fr}}.header h1{{font-size:34px}}}}
</style></head><body><div class="container">
<div class="nav-row">
<a class="btn" href="/">Dashboard</a>
<a class="btn" href="/co-list">C/O List</a>
<a class="btn" href="/edit-co/{html_attr(co_no)}">Edit</a>
<a class="btn" href="/co-pdf/{html_attr(co_no)}">PDF</a>
</div>
<div class="header">
<h1>{html_text(co_no)}</h1>
<div>Certificate of Origin</div>
<div class="meta">
<div><div class="label">C/O Date</div><div class="value">{html_text(record.get("co_date", ""))}</div></div>
<div><div class="label">B/L No</div><div class="value">{html_text(record.get("bl_no", ""))}</div></div>
<div><div class="label">Invoice No</div><div class="value">{html_text(record.get("invoice_no", ""))}</div></div>
<div><div class="label">Packing No</div><div class="value">{html_text(record.get("packing_no", ""))}</div></div>
<div><div class="label">Exporter</div><div class="value">{html_text(record.get("exporter", ""))}</div></div>
<div><div class="label">Consignee</div><div class="value">{html_text(record.get("consignee", ""))}</div></div>
<div><div class="label">Country of Origin</div><div class="value">{html_text(record.get("country_of_origin", ""))}</div></div>
<div><div class="label">Destination Country</div><div class="value">{html_text(record.get("destination_country", ""))}</div></div>
</div>
<div class="remarks" style="margin-top:14px;"><div class="label">Remarks</div><div>{html_text(record.get("remarks", ""))}</div></div>
</div>
<div class="section"><h2>Shipment Information</h2><div class="grid">
<div class="mini"><b>Transport Details</b>{html_text(record.get("transport_details", ""))}</div>
<div class="mini"><b>Port of Loading</b>{html_text(record.get("port_of_loading", ""))}</div>
<div class="mini"><b>Port of Discharge</b>{html_text(record.get("port_of_discharge", ""))}</div>
</div></div>
<div class="section"><h2>Goods Information</h2><div class="table-wrap"><table><thead><tr><th>No</th><th>Item</th><th>HS Code</th><th>Quantity</th><th>Origin</th></tr></thead><tbody>{rows}</tbody></table></div></div>
</div></body></html>"""
    return HTMLResponse(html)


@router.post("/co")
def save_co(
    shipment_no: str = Form(""),
    co_date: str = Form(""),
    bl_no: str = Form(""),
    invoice_no: str = Form(""),
    packing_no: str = Form(""),
    exporter: str = Form(""),
    consignee: str = Form(""),
    country_of_origin: str = Form(""),
    destination_country: str = Form(""),
    transport_details: str = Form(""),
    port_of_loading: str = Form(""),
    port_of_discharge: str = Form(""),
    remarks: str = Form(""),
    item_name: List[str] = Form([]),
    hs_code: List[str] = Form([]),
    quantity: List[str] = Form([]),
    origin: List[str] = Form([]),
):
    validate_co_links(bl_no, packing_no, invoice_no)
    exporter = require_text("Exporter", exporter)
    consignee = require_text("Consignee", consignee)
    saved = {}
    def add_co(records):
        co_number = next_identifier(records, "co_no", "CO")
        record = build_record(
        co_number, co_date, bl_no, invoice_no, packing_no, exporter,
        consignee, country_of_origin, destination_country, transport_details,
        port_of_loading, port_of_discharge, remarks, item_name, hs_code,
        quantity, origin,
        )
        records.append(record)
        saved["co_no"] = co_number
    locked_json_mutation(CO_FILE, [], add_co, list)
    return RedirectResponse(url=shipment_context_redirect_url(shipment_no, "co_no", saved["co_no"], "/co-list"), status_code=303)


@router.get("/edit-co/{co_no}")
def edit_co(co_no: str):
    for record in load_certificates():
        if record.get("co_no") == co_no:
            return render_form(record, f"/update-co/{co_no}", "Edit Certificate of Origin", "Update Certificate of Origin", True)
    return HTMLResponse("Certificate of Origin Not Found", status_code=404)


@router.post("/update-co/{co_no}")
def update_co(
    co_no: str,
    co_date: str = Form(""),
    bl_no: str = Form(""),
    invoice_no: str = Form(""),
    packing_no: str = Form(""),
    exporter: str = Form(""),
    consignee: str = Form(""),
    country_of_origin: str = Form(""),
    destination_country: str = Form(""),
    transport_details: str = Form(""),
    port_of_loading: str = Form(""),
    port_of_discharge: str = Form(""),
    remarks: str = Form(""),
    item_name: List[str] = Form([]),
    hs_code: List[str] = Form([]),
    quantity: List[str] = Form([]),
    origin: List[str] = Form([]),
):
    validate_co_links(bl_no, packing_no, invoice_no)
    exporter = require_text("Exporter", exporter)
    consignee = require_text("Consignee", consignee)
    updated = build_record(
        co_no, co_date, bl_no, invoice_no, packing_no, exporter, consignee,
        country_of_origin, destination_country, transport_details,
        port_of_loading, port_of_discharge, remarks, item_name, hs_code,
        quantity, origin,
    )
    def replace_co(records):
        for index, record in enumerate(records):
            if record.get("co_no") == co_no:
                records[index] = updated
                return
        raise HTTPException(status_code=404, detail="Certificate of Origin not found")
    locked_json_mutation(CO_FILE, [], replace_co, list)
    return RedirectResponse(url="/co-list", status_code=303)


@router.get("/delete-co/{co_no}")
def delete_co(co_no: str):
    return identifier_delete_confirmation("Certificate of Origin", "Certificate of Origin", co_no, CO_FILE, "co_no", f"/delete-co/{co_no}", "/co-list")

@router.post("/delete-co/{co_no}")
def confirm_delete_co(co_no: str):
    return confirmed_identifier_delete("Certificate of Origin", "Certificate of Origin", co_no, CO_FILE, "co_no", f"/delete-co/{co_no}", "/co-list", "/co-list")


@router.get("/co-data/{co_no}")
def co_data(co_no: str):
    for record in load_certificates():
        if record.get("co_no") == co_no:
            return record
    raise HTTPException(status_code=404, detail="Certificate of Origin not found")


@router.post("/co/pdf")
def create_co_pdf(payload: dict = Body(...)):
    co_no = payload.get("co_no") or "-"
    co_date = payload.get("co_date") or datetime.now().strftime("%Y-%m-%d")
    bl_no = payload.get("bl_no", "")
    invoice_no = payload.get("invoice_no", "")
    exporter = payload.get("exporter", "")
    consignee = payload.get("consignee", "")
    country_of_origin = payload.get("country_of_origin", "")
    destination_country = payload.get("destination_country", "")
    transport_details = payload.get("transport_details", "")
    port_of_loading = payload.get("port_of_loading", "")
    port_of_discharge = payload.get("port_of_discharge", "")
    remarks = payload.get("remarks", "")
    items = payload.get("items", [])

    buffer = BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4
    pdf.setTitle(f"Certificate of Origin {co_no}")

    table_x = 45
    table_w = 505
    table_header_h = 28
    row_h = 26
    row_min_bottom = 145
    statement_h = 90
    statement_gap = 20

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
        pdf.setFont("Helvetica-Bold", 23)
        pdf.drawString(45, height - 55, "CERTIFICATE OF ORIGIN")
        pdf.setFont("Helvetica", 9)
        pdf.drawRightString(width - 45, height - 38, "Trade Paper AI")
        pdf.drawRightString(width - 45, height - 55, "Automated Trade Document")

        pdf.setFillColor(colors.black)
        pdf.setFont("Helvetica-Bold", 10)
        pdf.drawString(45, height - 118, f"C/O No: {co_no}")
        pdf.drawString(45, height - 136, f"C/O Date: {co_date}")
        pdf.drawString(45, height - 154, f"B/L No: {bl_no}")
        pdf.drawString(45, height - 172, f"Invoice No: {invoice_no}")

        pdf.setStrokeColor(colors.HexColor("#D1D5DB"))
        pdf.setFillColor(colors.HexColor("#F9FAFB"))
        pdf.roundRect(45, height - 260, 240, 72, 8, fill=1)
        pdf.roundRect(310, height - 260, 240, 72, 8, fill=1)
        pdf.setFillColor(colors.HexColor("#111827"))
        pdf.setFont("Helvetica-Bold", 10)
        pdf.drawString(60, height - 207, "EXPORTER")
        pdf.drawString(325, height - 207, "CONSIGNEE")
        pdf.setFont("Helvetica", 8)
        pdf.drawString(60, height - 230, fit_text(exporter, 195))
        pdf.drawString(325, height - 230, fit_text(consignee, 195))

        pdf.setFillColor(colors.black)
        pdf.setFont("Helvetica-Bold", 8)
        pdf.drawString(45, height - 287, f"Country of Origin: {country_of_origin}")
        pdf.drawString(300, height - 287, f"Destination Country: {destination_country}")
        pdf.drawString(45, height - 304, f"Transport Details: {transport_details}")
        pdf.drawString(45, height - 321, f"Port of Loading: {port_of_loading}")
        pdf.drawString(300, height - 321, f"Port of Discharge: {port_of_discharge}")

    def draw_table_header():
        header_y = height - 355
        pdf.setFillColor(colors.HexColor("#E5E7EB"))
        pdf.rect(table_x, header_y, table_w, table_header_h, fill=1, stroke=0)
        pdf.setFillColor(colors.black)
        pdf.setFont("Helvetica-Bold", 8)
        pdf.drawString(52, header_y + 10, "No")
        pdf.drawString(82, header_y + 10, "Item")
        pdf.drawString(245, header_y + 10, "HS Code")
        pdf.drawRightString(390, header_y + 10, "Quantity")
        pdf.drawString(425, header_y + 10, "Origin")
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
        required_bottom = row_min_bottom + statement_h + statement_gap + row_h if index == item_count else row_min_bottom
        if y < required_bottom:
            pdf.showPage()
            y = start_page()
        pdf.rect(table_x, y, table_w, row_h, fill=0)
        pdf.drawString(52, y + 9, str(index))
        pdf.drawString(82, y + 9, fit_text(item.get("name", ""), 140))
        pdf.drawString(245, y + 9, str(item.get("hs_code", "")))
        pdf.drawRightString(390, y + 9, str(item.get("quantity", "")))
        pdf.drawString(425, y + 9, fit_text(item.get("origin", ""), 100))
        y -= row_h

    statement_top = y - statement_gap
    statement_bottom = statement_top - statement_h
    if statement_bottom < row_min_bottom:
        pdf.showPage()
        y = start_page()
        statement_top = y - statement_gap
        statement_bottom = statement_top - statement_h

    pdf.setFillColor(colors.HexColor("#111827"))
    pdf.roundRect(45, statement_bottom, 505, statement_h, 8, fill=1, stroke=0)
    pdf.setFillColor(colors.white)
    pdf.setFont("Helvetica-Bold", 10)
    pdf.drawString(60, statement_top - 25, "Certification Statement")
    pdf.setFont("Helvetica", 9)
    pdf.drawString(60, statement_top - 45, "We certify that the goods described in this document are of the stated origin.")
    if remarks:
        pdf.drawString(60, statement_top - 63, fit_text(f"Remarks: {remarks}", 455, "Helvetica", 9))

    draw_footer()
    pdf.showPage()
    pdf.save()
    buffer.seek(0)
    return Response(
        content=buffer.getvalue(),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{co_no}.pdf"'},
    )


@router.get("/co-pdf/{co_no}")
def co_pdf(co_no: str):
    for record in load_certificates():
        if record.get("co_no") == co_no:
            return create_co_pdf(record)
    return {"error": "Certificate of Origin not found"}
