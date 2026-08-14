from io import BytesIO
from datetime import datetime
from typing import Annotated, List, Optional
from fastapi import APIRouter, Body, Response, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.lib import colors
from app.pdf_fonts import TP_UNICODE, TP_UNICODE_BOLD, ensure_pdf_fonts, fit_pdf_text
import html as html_lib

from app.storage import atomic_write_json, data_path, load_json_strict, locked_json_mutation, next_identifier
from app.validation import DataValidationError, require_items, require_text
from app.referential_integrity import find_dependencies, render_delete_page
from app.shipment import direct_document_shipment_no, link_direct_document, shipment_detail_redirect_url
from app.ui import badge, button, form_footer, form_page, metadata, navigation_footer, page_shell, search_toolbar, section_card, table
from app.account_packing import ensure_legacy_packing_ownership, public_packing
from app.account_company import load_account_company
from app.auth import USERS_FILE
from app.routers.company import ACCOUNT_COMPANIES_FILE
from app import invoice as invoice_module
from app import buyer as buyer_module
from app.export import set_pdf_export_record
from app.snapshot import assign_item_ids

COMPANY_FILE = data_path("company.json")
PACKING_FILE = data_path("packing_lists.json")
INVOICE_FILE = data_path("invoices.json")

router = APIRouter()


def load_company():
    return load_json_strict(COMPANY_FILE, {}, dict)


def _account_id(request):
    user = request.scope.get("trade_paper_user") or {}
    return str(user.get("account_id", "") or "").strip()


def load_packing_records():
    return ensure_legacy_packing_ownership(PACKING_FILE, USERS_FILE)


def owned_packing_records(account_id):
    owner = str(account_id or "").strip()
    return [
        record for record in load_packing_records()
        if isinstance(record, dict)
        and str(record.get("account_id", "") or "").strip() == owner
        and not record.get("archived_at")
    ]


def load_packing_lists(account_id):
    return [public_packing(record) for record in owned_packing_records(account_id)]


def _owned_packing(packing_no, account_id):
    target = str(packing_no or "").strip()
    record = next(
        (record for record in owned_packing_records(account_id)
         if str(record.get("packing_no", "") or "").strip() == target),
        None,
    )
    if record is None:
        raise HTTPException(status_code=404, detail="Packing List not found")
    return record


def _owned_invoice(invoice_no, account_id):
    target = str(invoice_no or "").strip()
    record = next(
        (record for record in invoice_module.owned_invoice_records(account_id)
         if str(record.get("invoice_no", "") or "").strip() == target),
        None,
    )
    if record is None:
        raise HTTPException(status_code=404, detail="Invoice not found")
    return record


def _buyer_master(account_id, buyer_name):
    target = str(buyer_name or "").strip().casefold()
    return next(
        (
            record for record in buyer_module.load_buyers(account_id)
            if str(record.get("name", "") or "").strip().casefold() == target
        ),
        {},
    )


def save_packing_lists(packing_lists):
    atomic_write_json(PACKING_FILE, packing_lists, list)


def load_invoice_records():
    return load_json_strict(INVOICE_FILE, [], list)


@router.post("/packing-list")
def create_packing_list(request: Request, payload: dict = Body(...)):
    record = dict(payload)
    record.pop("account_id", None)
    account_id = _account_id(request)
    record["account_id"] = account_id
    shipment_no = str(record.pop("shipment_no", "") or "").strip()
    invoice_no = require_text("Invoice No", record.get("invoice_no", ""))
    source_invoice = public_packing(_owned_invoice(invoice_no, account_id))
    record["invoice_no"] = invoice_no
    record["seller"] = require_text("Seller", record.get("seller", ""))
    record["buyer"] = require_text("Buyer", record.get("buyer", ""))
    company = load_account_company(account_id, ACCOUNT_COMPANIES_FILE)
    buyer = _buyer_master(account_id, record["buyer"])
    snapshot_fallbacks = {
        "seller_address": source_invoice.get("seller_address") or company.get("address", ""),
        "seller_email": source_invoice.get("seller_email") or company.get("email", ""),
        "seller_phone": source_invoice.get("seller_phone") or company.get("phone", ""),
        "buyer_address": source_invoice.get("buyer_address") or buyer.get("address", ""),
        "buyer_email": source_invoice.get("buyer_email") or buyer.get("email", ""),
    }
    for field, fallback in snapshot_fallbacks.items():
        record[field] = record.get(field) or fallback
    require_items(record.get("items", []))
    from app import product as product_module
    product_module.enrich_items_from_products(record.get("items", []), account_id)
    record["items"] = assign_item_ids([dict(item) for item in record.get("items", [])])
    def add_packing(records):
        record["packing_no"] = next_identifier(records, "packing_no", "PK")
        records.append(record)
    locked_json_mutation(PACKING_FILE, [], add_packing, list)
    from app.audit_log import record_request_audit
    record_request_audit(request, "Create", "Packing List", record["packing_no"], path=PACKING_FILE.with_name("audit_log.json"))
    link_direct_document(shipment_no, "packing_no", record["packing_no"])
    return public_packing(record)


@router.get("/packing-list")
def packing_list(request: Request, search: str = ""):
    packing_lists = load_packing_lists(_account_id(request))
    packing_lists = list(reversed(packing_lists))

    if search:
        search_lower = search.lower()
        packing_lists = [
            p for p in packing_lists
            if search_lower in str(p.get("packing_no", "")).lower()
            or search_lower in str(p.get("invoice_no", "")).lower()
            or search_lower in str(p.get("seller", "")).lower()
            or search_lower in str(p.get("buyer", "")).lower()
            or search_lower in str(p.get("items", "")).lower()
        ]

    rows = []
    for packing in packing_lists:
        if not packing.get("packing_no"):
            continue

        items = packing.get("items", [])

        escaped = lambda value: html_lib.escape(str(value or ""))
        packing_no = str(packing.get("packing_no", "") or "")
        rows.append([
            badge(packing_no), escaped(packing.get("invoice_no", "")), escaped(packing.get("seller", "")),
            escaped(packing.get("buyer", "")), "<br>".join(escaped(item.get("name", "")) for item in items),
            "<br>".join(escaped(item.get("quantity", "")) for item in items),
            "<br>".join(escaped(item.get("hs_code", "")) for item in items),
            "<br>".join(escaped(item.get("carton", "")) for item in items),
            "<br>".join(escaped(item.get("net_weight", "")) for item in items),
            "<br>".join(escaped(item.get("gross_weight", "")) for item in items),
            button("Download PDF", f"/packing-list-pdf/{packing_no}", "secondary"),
            button("Send Email", f"/send-email/packing/{packing_no}", "secondary"),
            button("Edit", f"/edit-packing/{packing_no}", "secondary"),
            button("Delete", f"/packing-delete/{packing_no}", "danger"),
        ])
    content = search_toolbar(button("+ New Packing", "/packing-page"), button("← Dashboard", "/", "secondary"), action="/packing-list", value=search, placeholder="Search packing, invoice, buyer, seller or item", reset_url="/packing-list", count_label=f"Total Packing Lists : {len(packing_lists)}")
    content += table(["Packing No", "Invoice No", "Seller", "Buyer", "Item", "Quantity", "HS Code", "Carton", "Net", "Gross", "PDF", "Email", "Edit", "Delete"], rows)
    return HTMLResponse(page_shell("Packing List", content, subtitle="Manage all packing documents"))

@router.post("/packing")
def save_packing(
    request: Request,
    invoice_no: str = Form(""),
    seller: str = Form(""),
    buyer: str = Form(""),
    item_name: List[str] = Form([]),
    hs_code: List[str] = Form([]),
    carton: List[str] = Form([]),
    net_weight: List[str] = Form([]),
    gross_weight: List[str] = Form([]),
    seller_address: Annotated[Optional[str], Form()] = None,
    seller_email: Annotated[Optional[str], Form()] = None,
    seller_phone: Annotated[Optional[str], Form()] = None,
    buyer_address: Annotated[Optional[str], Form()] = None,
    buyer_email: Annotated[Optional[str], Form()] = None,
    item_id: List[str] = Form([]),
    origin: List[str] = Form([]),
    unit: List[str] = Form([]),
):
    invoice_no = require_text("Invoice No", invoice_no)
    account_id = _account_id(request)
    _owned_invoice(invoice_no, account_id)
    origin = origin if isinstance(origin, list) else []
    unit = unit if isinstance(unit, list) else []
    seller = require_text("Seller", seller)
    buyer = require_text("Buyer", buyer)
    items = []

    for i in range(len(item_name)):
        if not item_name[i].strip():
            continue

        items.append({
            "name": item_name[i],
            "hs_code": hs_code[i] if i < len(hs_code) else "",
            "origin": origin[i] if i < len(origin) else "",
            "unit": unit[i] if i < len(unit) else "",
            "carton": carton[i] if i < len(carton) else "",
            "net_weight": net_weight[i] if i < len(net_weight) else "",
            "gross_weight": gross_weight[i] if i < len(gross_weight) else "",
        })

    require_items(items)
    from app import product as product_module
    product_module.enrich_items_from_products(items, account_id)
    assign_item_ids(items, item_id)
    saved = {}
    def add_packing(packing_lists):
        packing = {
        "account_id": account_id,
        "packing_no": next_identifier(packing_lists, "packing_no", "PK"),
        "invoice_no": invoice_no,
        "seller": seller,
        "seller_address": seller_address or "",
        "seller_email": seller_email or "",
        "seller_phone": seller_phone or "",
        "buyer": buyer,
        "buyer_address": buyer_address or "",
        "buyer_email": buyer_email or "",
        "items": items,
        }
        packing_lists.append(packing)
        saved["packing_no"] = packing["packing_no"]
    locked_json_mutation(PACKING_FILE, [], add_packing, list)
    from app.audit_log import record_request_audit
    record_request_audit(request, "Create", "Packing List", saved["packing_no"], path=PACKING_FILE.with_name("audit_log.json"))

    return RedirectResponse(url="/packing-list", status_code=303)


@router.get("/edit-packing/{packing_no}")
def edit_packing(packing_no: str, request: Request):
    account_id = _account_id(request)
    packing = public_packing(_owned_packing(packing_no, account_id))
    company = load_account_company(account_id, ACCOUNT_COMPANIES_FILE)
    buyer = _buyer_master(account_id, packing.get("buyer", ""))
    if packing:
            items = packing.get("items", [])

            if not items:
                items = [{}]

            info = metadata([
                ("Packing No", f'<input type="text" value="{packing.get("packing_no", "")}" readonly>'),
                ("Invoice No", f'<input type="text" name="invoice_no" value="{packing.get("invoice_no", "")}">'),
                ("Seller", f'<input type="text" name="seller" value="{packing.get("seller", "")}">'),
                ("Seller Address", f'<input type="text" name="seller_address" value="{packing.get("seller_address") or company.get("address", "")}">'),
                ("Seller Email", f'<input type="text" name="seller_email" value="{packing.get("seller_email") or company.get("email", "")}">'),
                ("Seller Phone", f'<input type="text" name="seller_phone" value="{packing.get("seller_phone") or company.get("phone", "")}">'),
                ("Buyer", f'<input type="text" name="buyer" value="{packing.get("buyer", "")}">'),
                ("Buyer Address", f'<input type="text" name="buyer_address" value="{packing.get("buyer_address") or buyer.get("address", "")}">'),
                ("Buyer Email", f'<input type="text" name="buyer_email" value="{packing.get("buyer_email") or buyer.get("email", "")}">'),
            ])
            html = f'<form action="/update-packing/{packing_no}" method="post">' + section_card("Packing Information", info)

            for item in items:
                html += f"""
<div class="item-row">
<input type="hidden" name="item_id" value="{html_lib.escape(str(item.get('item_id','') or ''), quote=True)}">

<p>Item Name</p>
<input type="text" name="item_name" value="{item.get('name','')}">

<p>Quantity</p>
<input type="number" step="any" name="quantity" value="{item.get('quantity','')}">

<p>HS Code</p>
<input type="text" name="hs_code" value="{item.get('hs_code','')}">

<p>Country of Origin</p>
<input type="text" name="origin" value="{html_lib.escape(str(item.get('origin','') or ''), quote=True)}">

<p>Unit</p>
<input type="text" name="unit" value="{html_lib.escape(str(item.get('unit','') or ''), quote=True)}">

<p>Carton</p>
<input type="text" name="carton" value="{item.get('carton','')}">

<p>Net Weight</p>
<input type="text" name="net_weight" value="{item.get('net_weight','')}">

<p>Gross Weight</p>
<input type="text" name="gross_weight" value="{item.get('gross_weight','')}">

</div>
"""

            html += form_footer("/packing-list", "Update Packing") + "</form>"
            navigation = navigation_footer("/packing-list", "← Packing List", state="Editing")
            return HTMLResponse(form_page("Edit Packing List", html, subtitle="Update packing list information", navigation=navigation))

@router.post("/update-packing/{packing_no}")
def update_packing(
    packing_no: str,
    request: Request,
    invoice_no: str = Form(""),
    seller: str = Form(""),
    buyer: str = Form(""),
    item_name: List[str] = Form([]),
    quantity: List[str] = Form([]),
    hs_code: List[str] = Form([]),
    carton: List[str] = Form([]),
    net_weight: List[str] = Form([]),
    gross_weight: List[str] = Form([]),
    seller_address: Annotated[Optional[str], Form()] = None,
    seller_email: Annotated[Optional[str], Form()] = None,
    seller_phone: Annotated[Optional[str], Form()] = None,
    buyer_address: Annotated[Optional[str], Form()] = None,
    buyer_email: Annotated[Optional[str], Form()] = None,
    item_id: List[str] = Form([]),
    origin: List[str] = Form([]),
    unit: List[str] = Form([]),
):
    invoice_no = require_text("Invoice No", invoice_no)
    account_id = _account_id(request)
    current = public_packing(_owned_packing(packing_no, account_id))
    _owned_invoice(invoice_no, account_id)
    origin = origin if isinstance(origin, list) else []
    unit = unit if isinstance(unit, list) else []
    seller = require_text("Seller", seller)
    buyer = require_text("Buyer", buyer)
    items = []

    for i in range(len(item_name)):
        if not item_name[i].strip():
            continue

        try:
            quantity_value = float(quantity[i] or 0) if i < len(quantity) else 0
        except (TypeError, ValueError) as exc:
            raise DataValidationError(
                "Quantity", "Quantity must be a number.",
                "Enter a numeric quantity, then save again.",
            ) from exc
        if quantity_value.is_integer():
            quantity_value = int(quantity_value)

        items.append({
            "name": item_name[i],
            "quantity": quantity_value,
            "hs_code": hs_code[i] if i < len(hs_code) else "",
            "origin": origin[i] if i < len(origin) else "",
            "unit": unit[i] if i < len(unit) else "",
            "carton": carton[i] if i < len(carton) else "",
            "net_weight": net_weight[i] if i < len(net_weight) else "",
            "gross_weight": gross_weight[i] if i < len(gross_weight) else "",
        })

    require_items(items)
    from app import product as product_module
    product_module.enrich_items_from_products(items, account_id)
    assign_item_ids(items, item_id, current.get("items", []))
    def replace_packing(packing_lists):
        for packing in packing_lists:
            if (packing.get("packing_no") != packing_no
                    or str(packing.get("account_id", "") or "").strip() != account_id):
                continue
            packing["invoice_no"] = invoice_no
            packing["seller"] = seller
            if seller_address is not None:
                packing["seller_address"] = seller_address
            if seller_email is not None:
                packing["seller_email"] = seller_email
            if seller_phone is not None:
                packing["seller_phone"] = seller_phone
            packing["buyer"] = buyer
            if buyer_address is not None:
                packing["buyer_address"] = buyer_address
            if buyer_email is not None:
                packing["buyer_email"] = buyer_email
            packing["items"] = items
            return
        raise HTTPException(status_code=404, detail="Packing List not found")
    locked_json_mutation(PACKING_FILE, [], replace_packing, list)
    from app.audit_log import record_request_audit
    record_request_audit(request, "Update", "Packing List", packing_no, path=PACKING_FILE.with_name("audit_log.json"))

    shipment_no = direct_document_shipment_no("packing_no", packing_no, account_id)
    return RedirectResponse(
        url=shipment_detail_redirect_url(shipment_no, account_id, "/packing-list"), status_code=303,
    )


@router.get("/packing-delete/{packing_no}")
def delete_packing(packing_no: str, request: Request):
    _owned_packing(packing_no, _account_id(request))
    from app.archive import render_archive_page
    return render_archive_page("Packing List", packing_no, f"/packing-delete/{packing_no}", "/packing-list")

@router.post("/packing-delete/{packing_no}")
def confirm_delete_packing(packing_no: str, request: Request):
    account_id = _account_id(request)
    from app.archive import archive_document
    return archive_document(request, "packing", packing_no, "/packing-list")
    _owned_packing(packing_no, account_id)
    dependencies = find_dependencies("Packing List", packing_no, account_id)
    if dependencies:
        return render_delete_page("Packing List", packing_no, f"/packing-delete/{packing_no}", "/packing-list", dependencies, status_code=409)
    def remove(records):
        index = next((index for index, record in enumerate(records)
                      if isinstance(record, dict)
                      and str(record.get("packing_no", "") or "").strip() == packing_no
                      and str(record.get("account_id", "") or "").strip() == account_id), None)
        if index is None:
            raise HTTPException(status_code=404, detail="Packing List not found")
        records.pop(index)
    locked_json_mutation(PACKING_FILE, [], remove, list)
    return RedirectResponse("/packing-list", status_code=303)

@router.get("/packing-form")
def packing_form():
    return HTMLResponse(form_page("Packing Form", button("Go to New Packing Page", "/packing-page"), subtitle="Use /packing-page for the new Packing UI."))


@router.post("/packing-list/pdf")
def preview_packing_list_pdf(request: Request, payload: dict = Body(...)):
    account_id = _account_id(request)
    _owned_invoice(require_text("Invoice No", payload.get("invoice_no", "")), account_id)
    company = load_account_company(account_id, ACCOUNT_COMPANIES_FILE)
    public_payload = public_packing(payload)
    buyer = _buyer_master(account_id, public_payload.get("buyer", ""))
    return create_packing_list_pdf(public_payload, company, buyer)


preview_packing_list_pdf.__name__ = "create_packing_list_pdf"


def create_packing_list_pdf(payload, company=None, buyer_master=None):
    payload = public_packing(payload)
    company = company if isinstance(company, dict) else load_company()
    buyer_master = buyer_master if isinstance(buyer_master, dict) else {}

    packing_no = payload.get("packing_no") or "-"
    invoice_no = payload.get("invoice_no", "")
    today = datetime.now().strftime("%Y-%m-%d")

    buyer = payload.get("buyer", "")
    buyer_address = payload.get("buyer_address") or buyer_master.get("address", "")
    buyer_email = payload.get("buyer_email") or buyer_master.get("email", "")
    seller = payload.get("seller") or company.get("name", "")
    seller_address = payload.get("seller_address") or company.get("address", "")
    seller_email = payload.get("seller_email") or company.get("email", "")
    seller_phone = payload.get("seller_phone") or company.get("phone", "")

    items = payload.get("items", [])

    buffer = BytesIO()
    ensure_pdf_fonts()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4

    pdf.setTitle(f"Packing List {packing_no}")

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

    def draw_document_header():
        pdf.setFillColor(colors.HexColor("#111827"))
        pdf.rect(0, height - 90, width, 90, fill=1, stroke=0)

        pdf.setFillColor(colors.white)
        pdf.setFont(TP_UNICODE_BOLD, 24)
        pdf.drawString(45, height - 55, "PACKING LIST")

        pdf.setFont(TP_UNICODE, 9)
        pdf.drawRightString(width - 45, height - 38, "Trade Paper AI")
        pdf.drawRightString(width - 45, height - 55, "Automated Trade Document")

        pdf.setFillColor(colors.black)
        pdf.setFont(TP_UNICODE_BOLD, 11)
        pdf.drawString(45, height - 125, f"Packing No: {packing_no}")
        pdf.drawString(45, height - 143, f"Invoice No: {invoice_no}")
        pdf.drawString(45, height - 161, f"Date: {today}")

        pdf.setStrokeColor(colors.HexColor("#D1D5DB"))
        pdf.setFillColor(colors.HexColor("#F9FAFB"))
        pdf.roundRect(45, height - 260, 240, 80, 8, fill=1)
        pdf.roundRect(310, height - 260, 240, 80, 8, fill=1)

        pdf.setFillColor(colors.HexColor("#111827"))
        pdf.setFont(TP_UNICODE_BOLD, 11)
        pdf.drawString(60, height - 197, "SELLER")
        pdf.drawString(325, height - 197, "BUYER")

        pdf.setFont(TP_UNICODE, 9)
        pdf.drawString(60, height - 220, fit_text(seller, 210, font_size=9))
        pdf.drawString(60, height - 235, fit_text(seller_address, 210, font_size=9))
        seller_contact = " · ".join(value for value in (seller_email, seller_phone) if value)
        pdf.drawString(60, height - 250, fit_text(seller_contact, 210, font_size=9))
        pdf.drawString(325, height - 220, fit_text(buyer, 210, font_size=9))
        pdf.drawString(325, height - 235, fit_text(buyer_address, 210, font_size=9))
        pdf.drawString(325, height - 250, fit_text(buyer_email, 210, font_size=9))

    def draw_table_header():
        header_y = height - 315

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

    def start_table_page():
        draw_document_header()
        return draw_table_header()

    def draw_signature_footer():
        pdf.setFillColor(colors.black)
        pdf.setFont(TP_UNICODE, 10)
        pdf.drawString(45, 115, "Authorized Signature:")
        pdf.line(170, 115, 330, 115)

        pdf.setFillColor(colors.HexColor("#6B7280"))
        pdf.setFont(TP_UNICODE, 8)
        pdf.drawString(45, 60, "This document was generated by Trade Paper AI.")
        pdf.drawString(45, 45, "For trade documentation automation.")

    y = start_table_page()

    total_carton = 0
    total_net_weight = 0.0
    total_gross_weight = 0.0

    item_count = len(items)

    for index, item in enumerate(items, start=1):
        quantity = item["quantity"] if "quantity" in item else ""
        carton = item.get("carton", "")
        net_weight = item.get("net_weight", "")
        gross_weight = item.get("gross_weight", "")

        try:
            total_carton += int(float(carton or 0))
        except:
            pass

        try:
            total_net_weight += float(net_weight or 0)
        except:
            pass

        try:
            total_gross_weight += float(gross_weight or 0)
        except:
            pass

        is_last_row = index == item_count
        if is_last_row:
            required_bottom = row_min_bottom + summary_h + summary_gap + row_h
        else:
            required_bottom = row_min_bottom

        if y < required_bottom:
            pdf.showPage()
            y = start_table_page()

        pdf.rect(table_x, y, table_w, row_h, fill=0)

        pdf.drawString(52, y + 9, str(index))
        pdf.drawString(80, y + 9, fit_text(item.get("name", ""), 135))
        pdf.drawRightString(235, y + 9, str(quantity))
        pdf.drawString(270, y + 9, str(item.get("hs_code", "")))
        pdf.drawRightString(370, y + 9, str(carton))
        pdf.drawRightString(455, y + 9, str(net_weight))
        pdf.drawRightString(540, y + 9, str(gross_weight))

        y -= row_h

    summary_x = table_right - summary_w
    summary_top = y - summary_gap
    summary_bottom = summary_top - summary_h

    if summary_bottom < row_min_bottom:
        pdf.showPage()
        y = start_table_page()
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
    pdf.drawString(text_x, text_y - line_gap, f"Total Net Weight: {total_net_weight:g}")
    pdf.drawString(text_x, text_y - line_gap * 2, f"Total Gross Weight: {total_gross_weight:g}")

    draw_signature_footer()

    pdf.showPage()
    pdf.save()
    buffer.seek(0)

    return Response(
        content=buffer.getvalue(),
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{packing_no}.pdf"'
        },
    )


@router.get("/packing-list-pdf/{packing_no}")
def packing_list_pdf(packing_no: str, request: Request):
    account_id = _account_id(request)
    company = load_account_company(account_id, ACCOUNT_COMPANIES_FILE)
    record = public_packing(_owned_packing(packing_no, account_id))
    buyer = _buyer_master(account_id, record.get("buyer", ""))
    set_pdf_export_record(request, record)
    return create_packing_list_pdf(record, company, buyer)
