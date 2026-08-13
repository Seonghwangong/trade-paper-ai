from typing import Annotated, List, Optional
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
from app.account_bill_of_lading import ensure_legacy_bill_of_lading_ownership, public_bill_of_lading
from app.snapshot import assign_item_ids, fill_missing_snapshot_fields, snapshot_value
from app.account_company import load_account_company
from app.export import set_pdf_export_record
from app.auth import USERS_FILE
from app import packing as packing_module
from app import invoice as invoice_module
from app import buyer as buyer_module
from app.routers.company import ACCOUNT_COMPANIES_FILE
from app.shipment import direct_document_shipment_no, shipment_context_redirect_url, shipment_detail_redirect_url
from app.ui import badge, button, form_css, form_footer, imported_section_css, metadata, navigation_footer, page_shell, render_imported_section, search_toolbar, section_card, table

BL_FILE = data_path("bills_of_lading.json")
PACKING_FILE = data_path("packing_lists.json")
INVOICE_FILE = data_path("invoices.json")


def _account_id(request):
    user = request.scope.get("trade_paper_user") or {}
    return str(user.get("account_id", "") or "").strip()


def load_bill_of_lading_records():
    return ensure_legacy_bill_of_lading_ownership(BL_FILE, USERS_FILE)


def owned_bill_of_lading_records(account_id):
    owner = str(account_id or "").strip()
    return [
        record for record in load_bill_of_lading_records()
        if isinstance(record, dict)
        and str(record.get("account_id", "") or "").strip() == owner
    ]


def load_bills_of_lading(account_id):
    return [public_bill_of_lading(record) for record in owned_bill_of_lading_records(account_id)]


def _owned_bl(bl_no, account_id):
    target = str(bl_no or "").strip()
    record = next(
        (record for record in owned_bill_of_lading_records(account_id)
         if str(record.get("bl_no", "") or "").strip() == target),
        None,
    )
    if record is None:
        raise HTTPException(status_code=404, detail="Bill of Lading not found")
    return record


def save_bills_of_lading(records):
    atomic_write_json(BL_FILE, records, list)


def load_packing_lists(account_id):
    return packing_module.load_packing_lists(account_id)


def load_bookings(account_id):
    from app import booking_confirmation as booking_module
    return booking_module.load_bookings(account_id)


def validate_bl_links(packing_no, invoice_no, account_id, shipment_no="", booking_record_no=""):
    packing = require_existing_reference("Packing List", packing_no, load_packing_lists(account_id), "packing_no", required=True)
    require_consistent_reference("Invoice", invoice_no, packing.get("invoice_no", ""), "selected Packing List")
    require_existing_reference("Invoice", invoice_no or packing.get("invoice_no", ""), invoice_module.load_invoices(account_id), "invoice_no", required=True)
    if shipment_no:
        from app import shipment as shipment_module
        shipment = require_existing_reference("Shipment", shipment_no, shipment_module.load_shipments(account_id), "shipment_no", required=True)
        require_consistent_reference("Packing List", packing_no, shipment.get("packing_no", ""), "selected Shipment")
        require_consistent_reference("Invoice", invoice_no, shipment.get("invoice_no", ""), "selected Shipment")
    if booking_record_no:
        booking = require_existing_reference(
            "Booking Confirmation", booking_record_no, load_bookings(account_id),
            "booking_record_no", required=True,
        )
        require_consistent_reference("Shipment", shipment_no, booking.get("shipment_no", ""), "selected Booking")
        require_consistent_reference("Packing List", packing_no, booking.get("packing_no", ""), "selected Booking")
        require_consistent_reference("Invoice", invoice_no, booking.get("invoice_no", ""), "selected Booking")


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


def build_item_rows(items, readonly=False):
    if not items:
        items = [{}]

    rows = ""
    readonly_attr = " readonly" if readonly else ""
    for item in items:
        rows += f"""
<div class="item-row">
<input type="hidden" name="item_id" value="{html_attr(item.get('item_id', ''))}">
<input type="text" name="item_name" value="{html_attr(item.get('name', ''))}" placeholder="Item Name"{readonly_attr}>
<input type="text" name="quantity" value="{html_attr(item.get('quantity', ''))}" placeholder="Quantity"{readonly_attr}>
<input type="text" name="hs_code" value="{html_attr(item.get('hs_code', ''))}" placeholder="HS Code"{readonly_attr}>
<input type="text" name="carton" value="{html_attr(item.get('carton', ''))}" placeholder="Carton"{readonly_attr}>
<input type="text" name="net_weight" value="{html_attr(item.get('net_weight', ''))}" placeholder="Net Weight"{readonly_attr}>
<input type="text" name="gross_weight" value="{html_attr(item.get('gross_weight', ''))}" placeholder="Gross Weight"{readonly_attr}>
</div>
"""
    return rows


def blank_payload():
    return {
        "bl_no": "",
        "booking_record_no": "",
        "shipment_no": "",
        "si_no": "",
        "packing_no": "",
        "invoice_no": "",
        "shipper": "",
        "shipper_address": "",
        "shipper_email": "",
        "shipper_phone": "",
        "consignee": "",
        "consignee_address": "",
        "consignee_email": "",
        "notify_party": "",
        "carrier": "",
        "vessel": "",
        "voyage_no": "",
        "port_of_loading": "",
        "port_of_discharge": "",
        "place_of_delivery": "",
        "place_of_receipt": "",
        "freight_term": "",
        "bl_date": datetime.now().strftime("%Y-%m-%d"),
        "items": [],
        "total_carton": "",
        "total_net_weight": "",
        "total_gross_weight": "",
    }


def resolve_party_snapshot(payload, account_id, packing=None, invoice=None):
    resolved = dict(payload or {})
    preserve_empty = bool(resolved.get("bl_no"))
    packing_no = str(resolved.get("packing_no", "") or "").strip()
    invoice_no = str(resolved.get("invoice_no", "") or "").strip()

    if packing is None and packing_no:
        packing = next(
            (record for record in load_packing_lists(account_id)
             if str(record.get("packing_no", "") or "").strip() == packing_no),
            {},
        )
    packing = packing or {}
    invoice_no = str(snapshot_value(resolved, "invoice_no", (packing.get("invoice_no", ""),), preserve_empty=preserve_empty) or "").strip()
    if invoice is None and invoice_no:
        invoice = next(
            (record for record in invoice_module.load_invoices(account_id)
             if str(record.get("invoice_no", "") or "").strip() == invoice_no),
            {},
        )
    invoice = invoice or {}

    company = load_account_company(account_id, ACCOUNT_COMPANIES_FILE)
    consignee = (
        resolved.get("consignee") or packing.get("buyer")
        or invoice.get("buyer") or ""
    )
    buyer = next(
        (record for record in buyer_module.load_buyers(account_id)
         if str(record.get("name", "") or "").strip().casefold()
         == str(consignee or "").strip().casefold()),
        {},
    )

    fallbacks = {
        "shipper": (packing.get("seller"), invoice.get("seller"), company.get("name")),
        "shipper_address": (packing.get("seller_address"), invoice.get("seller_address"), company.get("address")),
        "shipper_email": (packing.get("seller_email"), invoice.get("seller_email"), company.get("email")),
        "shipper_phone": (packing.get("seller_phone"), invoice.get("seller_phone"), company.get("phone")),
        "consignee": (packing.get("buyer"), invoice.get("buyer"), buyer.get("name")),
        "consignee_address": (packing.get("buyer_address"), invoice.get("buyer_address"), buyer.get("address")),
        "consignee_email": (packing.get("buyer_email"), invoice.get("buyer_email"), buyer.get("email")),
    }
    fill_missing_snapshot_fields(resolved, fallbacks, preserve_empty=preserve_empty)
    resolved["invoice_no"] = invoice_no
    from app import product as product_module
    product_module.enrich_items_from_products(resolved.get("items", []), account_id)
    return resolved


def payload_from_packing(packing_no, account_id):
    payload = blank_payload()
    if not packing_no:
        return payload

    for packing in load_packing_lists(account_id):
        if packing.get("packing_no") == packing_no:
            items = packing.get("items", [])
            payload.update({
                "packing_no": packing.get("packing_no", ""),
                "invoice_no": packing.get("invoice_no", ""),
                "shipper": packing.get("seller", ""),
                "shipper_address": packing.get("seller_address", ""),
                "shipper_email": packing.get("seller_email", ""),
                "shipper_phone": packing.get("seller_phone", ""),
                "consignee": packing.get("buyer", ""),
                "consignee_address": packing.get("buyer_address", ""),
                "consignee_email": packing.get("buyer_email", ""),
                "items": items,
                "total_carton": format_number(numeric_total(items, "carton")),
                "total_net_weight": format_number(numeric_total(items, "net_weight")),
                "total_gross_weight": format_number(numeric_total(items, "gross_weight")),
            })
            return resolve_party_snapshot(payload, account_id, packing=packing)
    return payload


def payload_from_booking(booking_record_no, account_id):
    """Build a new B/L snapshot from one account-owned Booking Confirmation."""
    target = str(booking_record_no or "").strip()
    if not target:
        return blank_payload()
    booking = next(
        (record for record in load_bookings(account_id)
         if str(record.get("booking_record_no", "") or "").strip() == target),
        None,
    )
    if booking is None:
        raise HTTPException(status_code=404, detail="Booking not found")
    from app import booking_confirmation as booking_module
    snapshot = booking_module.resolve_booking_snapshot(booking, account_id)
    payload = blank_payload()
    payload.update({
        "booking_record_no": target,
        "shipment_no": snapshot.get("shipment_no", ""),
        "si_no": snapshot.get("si_no", ""),
        "packing_no": snapshot.get("packing_no", ""),
        "invoice_no": snapshot.get("invoice_no", ""),
        "shipper": snapshot.get("exporter_name", snapshot.get("exporter", "")),
        "shipper_address": snapshot.get("exporter_address", ""),
        "shipper_email": snapshot.get("exporter_email", ""),
        "shipper_phone": snapshot.get("exporter_phone", ""),
        "consignee": snapshot.get("consignee_name", snapshot.get("consignee", "")),
        "consignee_address": snapshot.get("consignee_address", ""),
        "consignee_email": snapshot.get("consignee_email", ""),
        "carrier": snapshot.get("carrier", ""),
        "vessel": snapshot.get("vessel", ""),
        "voyage_no": snapshot.get("voyage_no", ""),
        "port_of_loading": snapshot.get("port_of_loading", ""),
        "port_of_discharge": snapshot.get("port_of_discharge", ""),
        "place_of_delivery": snapshot.get("place_of_delivery", ""),
        "items": snapshot.get("items", []),
        "total_carton": snapshot.get("total_carton", ""),
        "total_net_weight": snapshot.get("total_net_weight", ""),
        "total_gross_weight": snapshot.get("total_gross_weight", ""),
    })
    return payload


def build_record(
    bl_no, packing_no, invoice_no, shipper, consignee, notify_party, vessel,
    voyage_no, port_of_loading, port_of_discharge, place_of_delivery, bl_date,
    item_name, quantity, hs_code, carton, net_weight, gross_weight,
    total_carton, total_net_weight, total_gross_weight, booking_record_no="",
    shipment_no="", si_no="", carrier="", place_of_receipt="", freight_term="",
):
    items = build_items(item_name, quantity, hs_code, carton, net_weight, gross_weight)
    return {
        "bl_no": bl_no,
        "booking_record_no": booking_record_no,
        "shipment_no": shipment_no,
        "si_no": si_no,
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
        "place_of_receipt": place_of_receipt,
        "freight_term": freight_term,
        "bl_date": bl_date,
        "items": items,
        "total_carton": total_carton,
        "total_net_weight": total_net_weight,
        "total_gross_weight": total_gross_weight,
    }


def render_form(record, action, title, button_text, show_bl_no=False, shipment_no="", account_id="", create_mode=False):
    rows = build_item_rows(record.get("items", []), readonly=True)
    bl_no_input = ""
    if show_bl_no:
        bl_no_input = f'<input type="text" value="{html_attr(record.get("bl_no", ""))}" placeholder="B/L No" readonly>'
    else:
        bl_no_input = f'<input type="text" value="{html_attr(record.get("bl_no", ""))}" placeholder="Generated on save" readonly>'

    selected_booking = str(record.get("booking_record_no", "") or "")
    if create_mode:
        options = ['<option value="">Select Booking</option>']
        for booking in reversed(load_bookings(account_id)):
            value = str(booking.get("booking_record_no", "") or "")
            selected = " selected" if value == selected_booking else ""
            options.append(f'<option value="{html_attr(value)}"{selected}>{html_attr(value)} · {html_attr(booking.get("booking_reference", booking.get("booking_no", "")))}</option>')
        required = '' if record.get("packing_no") and not selected_booking else ' required aria-required="true"'
        booking_control = f'<select name="booking_record_no"{required} onchange="selectBooking(this.value)">{"".join(options)}</select>'
    else:
        booking_control = f'<input type="hidden" name="booking_record_no" value="{html_attr(selected_booking)}"><input type="text" value="{html_attr(selected_booking)}" readonly>'

    def readonly_field(name, value):
        return f'<input type="hidden" name="{html_attr(name)}" value="{html_attr(value)}"><input type="text" value="{html_attr(value)}" readonly>'

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
function selectBooking(value){
    const url = new URL('/bl-form', window.location.origin);
    if(value) url.searchParams.set('booking_record_no', value);
    window.location.assign(url.toString());
}
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
        "__BOOKING_CONTROL__": booking_control,
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
        "__CARRIER__": html_attr(record.get("carrier", "")),
        "__PLACE_OF_RECEIPT__": html_attr(record.get("place_of_receipt", "")),
        "__FREIGHT_TERM__": html_attr(record.get("freight_term", "")),
        "__ITEM_ROWS__": rows,
        "__TOTAL_CARTON__": html_attr(record.get("total_carton", "")),
        "__TOTAL_NET_WEIGHT__": html_attr(record.get("total_net_weight", "")),
        "__TOTAL_GROSS_WEIGHT__": html_attr(record.get("total_gross_weight", "")),
        "__BUTTON_TEXT__": html_attr(button_text),
        "__FORM_CSS__": form_css(max_width=960) + imported_section_css() + "\n.tp-imported-content .card{margin:0;padding:0;border:0;background:transparent}",
        "__NAVIGATION__": navigation_footer("/bl-list", "B/L List", state="Editing" if show_bl_no else "New"),
        "__DOCUMENT_SECTION__": section_card("Document Information", metadata([
            ("Booking *", booking_control),
            ("B/L No", bl_no_input),
            ("Shipment", readonly_field("shipment_no", record.get("shipment_no", shipment_no))),
            ("Shipping Instruction", readonly_field("si_no", record.get("si_no", ""))),
            ("Commercial Invoice", readonly_field("invoice_no", record.get("invoice_no", ""))),
            ("Packing List", readonly_field("packing_no", record.get("packing_no", ""))),
            ("Issue Date", f'<input type="date" name="bl_date" value="{html_attr(record.get("bl_date", ""))}">'),
        ])),
        "__PARTY_SECTION__": render_imported_section("party", section_card("Party Information", metadata([
            ("Shipper", f'<input type="text" name="shipper" value="{html_attr(record.get("shipper", ""))}" readonly>'),
            ("Shipper Address", f'<input type="text" name="shipper_address" value="{html_attr(record.get("shipper_address", ""))}" readonly>'),
            ("Shipper Email", f'<input type="email" name="shipper_email" value="{html_attr(record.get("shipper_email", ""))}" readonly>'),
            ("Shipper Phone", f'<input type="text" name="shipper_phone" value="{html_attr(record.get("shipper_phone", ""))}" readonly>'),
            ("Consignee", f'<input type="text" name="consignee" value="{html_attr(record.get("consignee", ""))}" readonly>'),
            ("Consignee Address", f'<input type="text" name="consignee_address" value="{html_attr(record.get("consignee_address", ""))}" readonly>'),
            ("Consignee Email", f'<input type="email" name="consignee_email" value="{html_attr(record.get("consignee_email", ""))}" readonly>'),
            ("Notify Party", f'<input type="text" name="notify_party" value="{html_attr(record.get("notify_party", ""))}" readonly>'),
        ]))),
        "__TRANSPORT_SECTION__": section_card("Transport Information", f'<input type="text" name="carrier" value="{html_attr(record.get("carrier", ""))}" placeholder="Ocean Carrier"><input type="text" name="vessel" value="{html_attr(record.get("vessel", ""))}" placeholder="Vessel"><input type="text" name="voyage_no" value="{html_attr(record.get("voyage_no", ""))}" placeholder="Voyage"><input type="text" name="port_of_loading" value="{html_attr(record.get("port_of_loading", ""))}" placeholder="Port of Loading"><input type="text" name="port_of_discharge" value="{html_attr(record.get("port_of_discharge", ""))}" placeholder="Port of Discharge"><input type="text" name="place_of_receipt" value="{html_attr(record.get("place_of_receipt", ""))}" placeholder="Place of Receipt"><input type="text" name="place_of_delivery" value="{html_attr(record.get("place_of_delivery", ""))}" placeholder="Place of Delivery"><input type="text" name="freight_term" value="{html_attr(record.get("freight_term", ""))}" placeholder="Freight Term">'),
        "__CARGO_SECTION__": render_imported_section("cargo", section_card("Cargo Information", f'<div id="items_area">{rows}</div><input id="total_carton" type="hidden" name="total_carton" value="{html_attr(record.get("total_carton", ""))}"><input id="total_net_weight" type="hidden" name="total_net_weight" value="{html_attr(record.get("total_net_weight", ""))}"><input id="total_gross_weight" type="hidden" name="total_gross_weight" value="{html_attr(record.get("total_gross_weight", ""))}"><div class="totals" id="totals_text"></div>')),
        "__FORM_FOOTER__": form_footer("/bl-list", button_text),
        "__SHIPMENT_CONTEXT__": "",
    }
    for key, value in replacements.items():
        html = html.replace(key, value)
    return HTMLResponse(html)


@router.get("/bl-list")
def bl_list(request: Request, search: str = ""):
    records = list(reversed(load_bills_of_lading(_account_id(request))))
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
            button("Send Email", f"/send-email/bill-of-lading/{bl_no}", "secondary"),
            button("Edit", f"/edit-bl/{bl_no}", "secondary"),
            button("Delete", f"/delete-bl/{bl_no}", "danger"),
        ])
    content = search_toolbar(button("+ New B/L", "/bl-form"), button("Dashboard", "/", "secondary"), action="/bl-list", value=search, placeholder="Search B/L, packing, invoice, shipper, consignee or item", reset_url="/bl-list", count_label=f"Total Bills of Lading : {len(records)}")
    content += table(["B/L No", "Packing", "Invoice", "Shipper", "Consignee", "PDF", "Email", "Edit", "Delete"], rows, empty_message="No Bills of Lading have been registered yet.")
    return HTMLResponse(page_shell("Bill of Lading List", content, subtitle="Manage all Bill of Lading documents"))


@router.get("/bl-form")
def bl_form(request: Request, booking_record_no: str = "", packing_no: str = "", shipment_no: str = ""):
    account_id = _account_id(request)
    record = payload_from_booking(booking_record_no, account_id) if booking_record_no else payload_from_packing(packing_no, account_id)
    if booking_record_no:
        shipment_no = record.get("shipment_no", "")
        validate_bl_links(record.get("packing_no", ""), record.get("invoice_no", ""), account_id, shipment_no, booking_record_no)
    elif packing_no or shipment_no:
        validate_bl_links(record.get("packing_no", packing_no), record.get("invoice_no", ""), account_id, shipment_no)
    record["bl_no"] = next_identifier(load_bill_of_lading_records(), "bl_no", "BL")
    return render_form(record, "/bl", "Bill of Lading", "Save Bill of Lading", shipment_no=shipment_no, account_id=account_id, create_mode=True)


def bl_success_response(record):
    bl_no = str(record.get("bl_no", "") or "")
    shipment_no = str(record.get("shipment_no", "") or "")
    co_url = f'/co-form?bl_no={html_attr(bl_no)}&amp;shipment_no={html_attr(shipment_no)}'
    return HTMLResponse(f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Bill of Lading Saved</title><style>*{{box-sizing:border-box}}body{{margin:0;background:#F3F4F6;color:#111827;font-family:Arial,sans-serif}}main{{min-height:100vh;display:grid;place-items:center;padding:24px}}.card{{width:min(620px,100%);padding:34px;border:1px solid #E5E7EB;border-radius:18px;background:#fff;text-align:center;box-shadow:0 14px 34px rgba(15,23,42,.09)}}h1{{margin:0 0 10px}}p{{color:#475569}}.actions{{display:flex;justify-content:center;gap:10px;flex-wrap:wrap;margin-top:20px}}a{{display:inline-flex;min-height:46px;align-items:center;padding:11px 16px;border-radius:10px;background:#E5E7EB;color:#111827;text-decoration:none;font-weight:800}}a.primary{{background:#111827;color:#fff}}</style></head><body><main><section class="card"><h1>Bill of Lading Saved</h1><p>✓ {html_attr(bl_no)} was created successfully.</p><div class="actions"><a class="primary" href="{co_url}">Continue to Certificate of Origin →</a><a href="/bl-pdf/{html_attr(bl_no)}">View PDF</a><a href="/shipment/{html_attr(shipment_no)}">View Shipment</a></div></section></main></body></html>''')


@router.post("/bl")
def save_bl(
    request: Request,
    booking_record_no: str = Form(""),
    shipment_no: str = Form(""),
    si_no: str = Form(""),
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
    place_of_receipt: str = Form(""),
    freight_term: str = Form(""),
    bl_date: str = Form(""),
    item_name: List[str] = Form([]),
    item_id: List[str] = Form([]),
    quantity: List[str] = Form([]),
    hs_code: List[str] = Form([]),
    carton: List[str] = Form([]),
    net_weight: List[str] = Form([]),
    gross_weight: List[str] = Form([]),
    total_carton: str = Form(""),
    total_net_weight: str = Form(""),
    total_gross_weight: str = Form(""),
    shipper_address: Annotated[Optional[str], Form()] = None,
    shipper_email: Annotated[Optional[str], Form()] = None,
    shipper_phone: Annotated[Optional[str], Form()] = None,
    consignee_address: Annotated[Optional[str], Form()] = None,
    consignee_email: Annotated[Optional[str], Form()] = None,
):
    account_id = _account_id(request)
    booking_record_no = booking_record_no if isinstance(booking_record_no, str) else ""
    si_no = si_no if isinstance(si_no, str) else ""
    carrier = carrier if isinstance(carrier, str) else ""
    place_of_receipt = place_of_receipt if isinstance(place_of_receipt, str) else ""
    freight_term = freight_term if isinstance(freight_term, str) else ""
    item_id = item_id if isinstance(item_id, (list, tuple)) else []
    total_carton = total_carton if isinstance(total_carton, str) else ""
    total_net_weight = total_net_weight if isinstance(total_net_weight, str) else ""
    total_gross_weight = total_gross_weight if isinstance(total_gross_weight, str) else ""
    shipper_address = shipper_address if isinstance(shipper_address, str) else None
    shipper_email = shipper_email if isinstance(shipper_email, str) else None
    shipper_phone = shipper_phone if isinstance(shipper_phone, str) else None
    consignee_address = consignee_address if isinstance(consignee_address, str) else None
    consignee_email = consignee_email if isinstance(consignee_email, str) else None
    validate_bl_links(packing_no, invoice_no, account_id, shipment_no, booking_record_no)
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
        booking_record_no, shipment_no, si_no, carrier, place_of_receipt, freight_term,
        )
        assign_item_ids(record["items"], item_id)
        record.update({
            "shipper_address": shipper_address or "",
            "shipper_email": shipper_email or "",
            "shipper_phone": shipper_phone or "",
            "consignee_address": consignee_address or "",
            "consignee_email": consignee_email or "",
        })
        record = resolve_party_snapshot(record, account_id)
        record["account_id"] = account_id
        records.append(record)
        saved.update(record)
    locked_json_mutation(BL_FILE, [], add_bl, list)
    shipment_context_redirect_url(shipment_no, "bl_no", saved["bl_no"], "/bl-list")
    return bl_success_response(saved)


@router.get("/edit-bl/{bl_no}")
def edit_bl(bl_no: str, request: Request):
    record = public_bill_of_lading(_owned_bl(bl_no, _account_id(request)))
    record = resolve_party_snapshot(record, _account_id(request))
    return render_form(record, f"/update-bl/{bl_no}", "Edit Bill of Lading", "Update Bill of Lading", True, shipment_no=record.get("shipment_no", ""), account_id=_account_id(request))


@router.post("/update-bl/{bl_no}")
def update_bl(
    bl_no: str,
    request: Request,
    booking_record_no: str = Form(""),
    shipment_no: str = Form(""),
    si_no: str = Form(""),
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
    place_of_receipt: str = Form(""),
    freight_term: str = Form(""),
    bl_date: str = Form(""),
    item_name: List[str] = Form([]),
    item_id: List[str] = Form([]),
    quantity: List[str] = Form([]),
    hs_code: List[str] = Form([]),
    carton: List[str] = Form([]),
    net_weight: List[str] = Form([]),
    gross_weight: List[str] = Form([]),
    total_carton: str = Form(""),
    total_net_weight: str = Form(""),
    total_gross_weight: str = Form(""),
    shipper_address: Annotated[Optional[str], Form()] = None,
    shipper_email: Annotated[Optional[str], Form()] = None,
    shipper_phone: Annotated[Optional[str], Form()] = None,
    consignee_address: Annotated[Optional[str], Form()] = None,
    consignee_email: Annotated[Optional[str], Form()] = None,
):
    account_id = _account_id(request)
    current = _owned_bl(bl_no, account_id)
    booking_record_no = booking_record_no if isinstance(booking_record_no, str) else ""
    shipment_no = shipment_no if isinstance(shipment_no, str) else ""
    si_no = si_no if isinstance(si_no, str) else ""
    carrier = carrier if isinstance(carrier, str) else str(current.get("carrier", "") or "")
    place_of_receipt = place_of_receipt if isinstance(place_of_receipt, str) else str(current.get("place_of_receipt", "") or "")
    freight_term = freight_term if isinstance(freight_term, str) else str(current.get("freight_term", "") or "")
    booking_record_no = booking_record_no or current.get("booking_record_no", "")
    shipment_no = shipment_no or current.get("shipment_no", "")
    si_no = si_no or current.get("si_no", "")
    validate_bl_links(packing_no, invoice_no, account_id, shipment_no, booking_record_no)
    shipper = require_text("Shipper", shipper)
    consignee = require_text("Consignee", consignee)
    updated = build_record(
        bl_no, packing_no, invoice_no, shipper, consignee, notify_party,
        vessel, voyage_no, port_of_loading, port_of_discharge,
        place_of_delivery, bl_date, item_name, quantity, hs_code, carton,
        net_weight, gross_weight, total_carton, total_net_weight,
        total_gross_weight,
        booking_record_no, shipment_no, si_no, carrier, place_of_receipt, freight_term,
    )
    assign_item_ids(updated["items"], item_id, current.get("items", []))
    updated.update({
        "shipper_address": current.get("shipper_address", "") if shipper_address is None else shipper_address,
        "shipper_email": current.get("shipper_email", "") if shipper_email is None else shipper_email,
        "shipper_phone": current.get("shipper_phone", "") if shipper_phone is None else shipper_phone,
        "consignee_address": current.get("consignee_address", "") if consignee_address is None else consignee_address,
        "consignee_email": current.get("consignee_email", "") if consignee_email is None else consignee_email,
    })
    updated = resolve_party_snapshot(updated, account_id)
    updated["account_id"] = account_id
    def replace_bl(records):
        for index, record in enumerate(records):
            if record.get("bl_no") == bl_no and record.get("account_id") == account_id:
                records[index] = updated
                return
        raise HTTPException(status_code=404, detail="Bill of Lading not found")
    locked_json_mutation(BL_FILE, [], replace_bl, list)
    shipment_no = direct_document_shipment_no("bl_no", bl_no, account_id)
    return RedirectResponse(
        url=shipment_detail_redirect_url(shipment_no, account_id, "/bl-list"), status_code=303,
    )


@router.get("/delete-bl/{bl_no}")
def delete_bl(bl_no: str, request: Request):
    _owned_bl(bl_no, _account_id(request))
    return render_delete_page("Bill of Lading", bl_no, f"/delete-bl/{bl_no}", "/bl-list", find_dependencies("Bill of Lading", bl_no, _account_id(request)))

@router.post("/delete-bl/{bl_no}")
def confirm_delete_bl(bl_no: str, request: Request):
    account_id = _account_id(request)
    _owned_bl(bl_no, account_id)
    dependencies = find_dependencies("Bill of Lading", bl_no, account_id)
    if dependencies:
        return render_delete_page("Bill of Lading", bl_no, f"/delete-bl/{bl_no}", "/bl-list", dependencies, status_code=409)
    def remove(records):
        index = next((index for index, record in enumerate(records)
                      if isinstance(record, dict) and record.get("bl_no") == bl_no
                      and record.get("account_id") == account_id), None)
        if index is None:
            raise HTTPException(status_code=404, detail="Bill of Lading not found")
        records.pop(index)
    locked_json_mutation(BL_FILE, [], remove, list)
    return RedirectResponse("/bl-list", status_code=303)


@router.get("/bl-data/{bl_no}")
def bl_data(bl_no: str, request: Request):
    account_id = _account_id(request)
    record = public_bill_of_lading(_owned_bl(bl_no, account_id))
    return resolve_party_snapshot(record, account_id)


@router.post("/bl/pdf")
def create_bl_pdf(request: Request, payload: dict = Body(...)):
    account_id = _account_id(request)
    validate_bl_links(payload.get("packing_no", ""), payload.get("invoice_no", ""), account_id, payload.get("shipment_no", ""))
    payload = public_bill_of_lading(payload)
    payload = resolve_party_snapshot(payload, account_id)
    bl_no = payload.get("bl_no") or "-"
    packing_no = payload.get("packing_no", "")
    invoice_no = payload.get("invoice_no", "")
    bl_date = payload.get("bl_date") or datetime.now().strftime("%Y-%m-%d")
    shipper = payload.get("shipper", "")
    shipper_address = payload.get("shipper_address", "")
    shipper_email = payload.get("shipper_email", "")
    shipper_phone = payload.get("shipper_phone", "")
    consignee = payload.get("consignee", "")
    consignee_address = payload.get("consignee_address", "")
    consignee_email = payload.get("consignee_email", "")
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
    ensure_pdf_fonts()
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

    def fit_text(text, max_width, font_name=TP_UNICODE, font_size=8):
        return fit_pdf_text(pdf, text, max_width, font_name, font_size)

    def draw_header():
        pdf.setFillColor(colors.HexColor("#111827"))
        pdf.rect(0, height - 90, width, 90, fill=1, stroke=0)
        pdf.setFillColor(colors.white)
        pdf.setFont(TP_UNICODE_BOLD, 24)
        pdf.drawString(45, height - 55, "BILL OF LADING")
        pdf.setFont(TP_UNICODE, 9)
        pdf.drawRightString(width - 45, height - 38, "Trade Paper AI")
        pdf.drawRightString(width - 45, height - 55, "Automated Trade Document")

        pdf.setFillColor(colors.black)
        pdf.setFont(TP_UNICODE_BOLD, 10)
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
        pdf.setFont(TP_UNICODE_BOLD, 9)
        pdf.drawString(58, height - 205, "SHIPPER")
        pdf.drawString(233, height - 205, "CONSIGNEE")
        pdf.drawString(408, height - 205, "NOTIFY PARTY")
        pdf.setFont(TP_UNICODE, 8)
        pdf.drawString(58, height - 224, fit_text(shipper, 130))
        pdf.drawString(233, height - 224, fit_text(consignee, 130))
        pdf.drawString(408, height - 228, fit_text(notify_party, 120))
        pdf.setFont(TP_UNICODE, 7)
        pdf.drawString(58, height - 237, fit_text(shipper_address, 130, font_size=7))
        pdf.drawString(58, height - 248, fit_text(shipper_email, 130, font_size=7))
        pdf.drawString(58, height - 259, fit_text(shipper_phone, 130, font_size=7))
        pdf.drawString(233, height - 241, fit_text(consignee_address, 130, font_size=7))
        pdf.drawString(233, height - 253, fit_text(consignee_email, 130, font_size=7))

        pdf.setFillColor(colors.black)
        pdf.setFont(TP_UNICODE_BOLD, 8)
        pdf.drawString(45, height - 292, fit_text(f"Vessel: {vessel}", 125, TP_UNICODE_BOLD, 8))
        pdf.drawString(185, height - 292, fit_text(f"Voyage No: {voyage_no}", 125, TP_UNICODE_BOLD, 8))
        pdf.drawString(325, height - 292, fit_text(f"Port of Loading: {port_of_loading}", 220, TP_UNICODE_BOLD, 8))
        pdf.drawString(45, height - 309, fit_text(f"Port of Discharge: {port_of_discharge}", 265, TP_UNICODE_BOLD, 8))
        pdf.drawString(325, height - 309, fit_text(f"Place of Delivery: {place_of_delivery}", 220, TP_UNICODE_BOLD, 8))

    def draw_table_header():
        header_y = height - 345
        pdf.setFillColor(colors.HexColor("#E5E7EB"))
        pdf.rect(table_x, header_y, table_w, table_header_h, fill=1, stroke=0)
        pdf.setFillColor(colors.black)
        pdf.setFont(TP_UNICODE_BOLD, 8)
        pdf.drawString(52, header_y + 10, "No")
        pdf.drawString(80, header_y + 10, "Item")
        pdf.drawRightString(235, header_y + 10, "Quantity")
        pdf.drawString(270, header_y + 10, "HS Code")
        pdf.drawRightString(370, header_y + 10, "Carton")
        pdf.drawRightString(455, header_y + 10, "Net Weight")
        pdf.drawRightString(540, header_y + 10, "Gross Weight")
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
    pdf.setFont(TP_UNICODE_BOLD, 10)
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
def bl_pdf(bl_no: str, request: Request):
    record = public_bill_of_lading(_owned_bl(bl_no, _account_id(request)))
    set_pdf_export_record(request, record)
    return create_bl_pdf(request, record)
