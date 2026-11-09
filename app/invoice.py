from io import BytesIO
from app.document_dates import new_document_date, saved_document_date
from fastapi import APIRouter, Body, Response, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.lib import colors
from app.pdf_fonts import TP_UNICODE, TP_UNICODE_BOLD, ensure_pdf_fonts, fit_pdf_text
import html as html_lib
import os
import math
import hashlib
import json
from urllib.parse import urlencode, quote
from typing import Annotated, Optional

from app.storage import data_path, load_json_strict, locked_json_mutation, next_identifier
from app.audit_log import record_request_audit
from app.validation import DataValidationError, require_existing_reference, require_items, require_text
from app.referential_integrity import find_dependencies, render_delete_page
from app.shipment import link_direct_document
from app.ui import badge, button, form_css, form_footer, metadata, navigation_footer, page_shell, search_toolbar, section_card, table
from app.account_invoice import ensure_legacy_invoice_ownership, public_invoice
from app.auth import USERS_FILE
from app.account_company import load_account_company
from app.routers.company import ACCOUNT_COMPANIES_FILE
from app import proforma as proforma_module
from app import product as product_module
from app import buyer as buyer_module
from app.export import set_pdf_export_record
from app.company_signature import signature_for_invoice, signature_edit_control, normalize_signature
from reportlab.lib.utils import ImageReader
import base64

COMPANY_FILE = data_path("company.json")
INVOICE_FILE = data_path("invoices.json")
PROFORMA_FILE = data_path("proformas.json")

router = APIRouter()

def _invoice_number(field, value):
    try:
        if isinstance(value, bool):
            raise ValueError("Boolean is not a quantity or price")
        number = float(value if value is not None and value != "" else 0)
        if not math.isfinite(number) or number < 0:
            raise ValueError("Expected a finite non-negative number")
    except (ValueError, TypeError, OverflowError) as exc:
        raise DataValidationError(field, f"{field} must be a finite number of zero or more.",
                                  f"Enter a valid {field.lower()}, then try again.") from exc
    return int(number) if number.is_integer() else number


def _account_id(request):
    user = request.scope.get("trade_paper_user") or {}
    account_id = str(user.get("account_id", "") or "").strip()
    if not account_id:
        raise HTTPException(status_code=401, detail="Authenticated account is required")
    return account_id


def load_invoice_records():
    return ensure_legacy_invoice_ownership(INVOICE_FILE, USERS_FILE)


def owned_invoice_records(account_id):
    owner = str(account_id or "").strip()
    return [
        record
        for record in load_invoice_records()
        if isinstance(record, dict) and str(record.get("account_id", "") or "").strip() == owner and not record.get("archived_at")
    ]


def load_invoices(account_id):
    return [public_invoice(record) for record in owned_invoice_records(account_id)]


def _owned_invoice(invoice_no, account_id):
    target = str(invoice_no or "").strip()
    owner = str(account_id or "").strip()
    record = next(
        (
            invoice
            for invoice in load_invoice_records()
            if isinstance(invoice, dict)
            and str(invoice.get("invoice_no", "") or "").strip() == target
            and str(invoice.get("account_id", "") or "").strip() == owner
        ),
        None,
    )
    if record is None:
        raise HTTPException(status_code=404, detail="Invoice not found")
    return record


def load_proformas(account_id):
    return proforma_module.load_proformas(account_id)


@router.post("/invoice")
def create_invoice(request: Request, payload: dict = Body(...)):
    record = dict(payload)
    record.pop("account_id", None)
    record.pop("signature_image", None)
    include_signature = record.pop("include_signature", False) is True
    record["account_id"] = _account_id(request)
    record["invoice_date"] = new_document_date(record.get("invoice_date"), "Invoice date")
    shipment_no = str(record.pop("shipment_no", "") or "").strip()
    record["seller"] = require_text("Seller", record.get("seller", ""))
    record["buyer"] = require_text("Buyer", record.get("buyer", ""))
    if include_signature:
        record["signature_image"] = signature_for_invoice(_account_id(request), record["seller"])
    company = load_account_company(_account_id(request), ACCOUNT_COMPANIES_FILE)
    # An imported supplier invoice may name a different seller. Never attach
    # this account's address/contact to that other company implicitly.
    seller_company = company if str(company.get('name', '')).strip().casefold() == record['seller'].casefold() else {}
    buyer = next(
        (item for item in buyer_module.load_buyers(_account_id(request))
         if str(item.get("name", "") or "").strip().casefold() == record["buyer"].strip().casefold()),
        {},
    )
    for field, value in {
        "seller_address": seller_company.get("address", ""), "seller_email": seller_company.get("email", ""),
        "seller_phone": seller_company.get("phone", ""), "buyer_address": buyer.get("address", ""),
        "buyer_email": buyer.get("email", ""),
    }.items():
        if not str(record.get(field, "") or "").strip():
            record[field] = value
    require_items(record.get("items", []))
    for item in record["items"]:
        if not isinstance(item, dict):
            raise DataValidationError("Items", "Each item must contain structured details.", "Add the item again, then save.")
        item["quantity"] = _invoice_number("Quantity", item.get("quantity", 0))
        item["unit_price"] = _invoice_number("Unit price", item.get("unit_price", 0))
    product_module.enrich_items_from_products(record.get("items", []), _account_id(request))
    require_existing_reference("Proforma Invoice", record.get("pi_no", ""), load_proformas(_account_id(request)), "pi_no")
    def add_invoice(invoices):
        record["invoice_no"] = next_identifier(invoices, "invoice_no", "INV")
        invoices.append(record)
    locked_json_mutation(INVOICE_FILE, [], add_invoice, list)
    link_direct_document(shipment_no, "invoice_no", record["invoice_no"])
    record_request_audit(request, "Create", "Commercial Invoice", record["invoice_no"], path=INVOICE_FILE.with_name("audit_log.json"))
    return public_invoice(record)

@router.get("/invoice-data")
def invoice_data(request: Request):
    return load_invoices(_account_id(request))

@router.get("/invoice-pdf/{invoice_no}")
def invoice_pdf(invoice_no: str, request: Request):
    account_id = _account_id(request)
    company = load_account_company(account_id, ACCOUNT_COMPANIES_FILE)
    record = public_invoice(_owned_invoice(invoice_no, account_id))
    set_pdf_export_record(request, record)
    return create_invoice_pdf(
        record,
        company,
    )

@router.post("/invoice/pdf")
def preview_invoice_pdf(request: Request, payload: dict = Body(...)):
    company = load_account_company(_account_id(request), ACCOUNT_COMPANIES_FILE)
    return create_invoice_pdf(payload, company)


preview_invoice_pdf.__name__ = "create_invoice_pdf"


def create_invoice_pdf(payload, company=None):
    payload = public_invoice(payload)
    company = company if isinstance(company, dict) else load_json_strict(COMPANY_FILE, {}, dict)

    currency = str(payload.get("currency") or "USD").strip().upper() or "USD"
    invoice_no = payload.get("invoice_no", "INV-001")
    document_date = saved_document_date(payload, "invoice_date")

    buyer = payload.get("buyer", "Unknown Buyer")
    buyer_address = payload.get("buyer_address") or ""
    buyer_email = payload.get("buyer_email") or ""

    seller = payload.get("seller") or company.get("name") or "Unknown Seller"
    seller_address = (payload.get("seller_address") if "seller_address" in payload else company.get("address")) or ""
    seller_email = (payload.get("seller_email") if "seller_email" in payload else company.get("email")) or ""
    seller_phone = payload.get("seller_phone") if "seller_phone" in payload else company.get("phone") or ""

    items = require_items(payload.get("items", []))
    if any(not isinstance(item, dict) for item in items):
        raise DataValidationError(
            "Items", "Each cargo item must contain structured item details.",
            "Add the cargo item again, then retry the PDF.",
        )

    pdf_number = _invoice_number

    total = sum(
        pdf_number("Quantity", item.get("quantity", 0))
        * pdf_number("Unit price", item.get("unit_price", 0))
        for item in items
    )

    buffer = BytesIO()
    ensure_pdf_fonts()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4

    pdf.setTitle(f"Invoice {invoice_no}")

    table_x = 45
    table_w = 505
    table_right = table_x + table_w
    table_header_h = 28
    row_h = 30
    row_min_bottom = 190 if payload.get("signature_image") else 145
    total_w = 225
    total_h = 45
    total_gap = 20

    def draw_document_header():
        pdf.setFillColor(colors.HexColor("#111827"))
        pdf.rect(0, height - 90, width, 90, fill=1, stroke=0)

        pdf.setFillColor(colors.white)
        pdf.setFont(TP_UNICODE_BOLD, 24)
        pdf.drawString(45, height - 55, "COMMERCIAL INVOICE")

        pdf.setFont(TP_UNICODE, 9)
        pdf.drawRightString(width - 45, height - 38, "Trade Paper AI")
        pdf.drawRightString(width - 45, height - 55, "Automated Trade Document")

        pdf.setFillColor(colors.black)
        pdf.setFont(TP_UNICODE_BOLD, 11)
        pdf.drawString(45, height - 125, f"Invoice No: {invoice_no}")
        pdf.drawString(45, height - 145, f"Date: {document_date}")

        pdf.setStrokeColor(colors.HexColor("#D1D5DB"))
        pdf.setFillColor(colors.HexColor("#F9FAFB"))
        pdf.roundRect(45, height - 260, 240, 80, 8, fill=1)
        pdf.roundRect(310, height - 260, 240, 80, 8, fill=1)

        pdf.setFillColor(colors.HexColor("#111827"))
        pdf.setFont(TP_UNICODE_BOLD, 11)
        pdf.drawString(60, height - 197, "SELLER")
        pdf.drawString(325, height - 197, "BUYER")

        pdf.setFont(TP_UNICODE, 9)
        pdf.drawString(60, height - 220, fit_pdf_text(pdf, seller, 210, TP_UNICODE, 9))
        pdf.drawString(60, height - 235, fit_pdf_text(pdf, seller_address, 210, TP_UNICODE, 9))
        seller_contact = " · ".join(value for value in (seller_email, seller_phone) if value)
        pdf.drawString(60, height - 250, fit_pdf_text(pdf, seller_contact, 210, TP_UNICODE, 9))

        pdf.drawString(325, height - 220, fit_pdf_text(pdf, buyer, 210, TP_UNICODE, 9))
        pdf.drawString(325, height - 235, fit_pdf_text(pdf, buyer_address, 210, TP_UNICODE, 9))
        pdf.drawString(325, height - 250, fit_pdf_text(pdf, buyer_email, 210, TP_UNICODE, 9))

    def draw_table_header():
        header_y = height - 315

        pdf.setFillColor(colors.HexColor("#E5E7EB"))
        pdf.rect(table_x, header_y, table_w, table_header_h, fill=1, stroke=0)

        pdf.setFillColor(colors.black)
        pdf.setFont(TP_UNICODE_BOLD, 10)
        pdf.drawString(60, header_y + 10, "Item")
        pdf.drawString(245, header_y + 10, "HS Code")
        pdf.drawRightString(350, header_y + 10, "Qty")
        pdf.drawRightString(445, header_y + 10, "Unit Price")
        pdf.drawRightString(540, header_y + 10, "Total")

        pdf.setStrokeColor(colors.HexColor("#D1D5DB"))
        pdf.setFont(TP_UNICODE, 10)
        return header_y - table_header_h

    def start_table_page():
        draw_document_header()
        return draw_table_header()

    def draw_signature_footer():
        pdf.setFillColor(colors.black)
        pdf.setFont(TP_UNICODE, 10)
        pdf.drawString(45, 115, "Authorized Signature:")
        pdf.line(170, 115, 330, 115)
        if payload.get("signature_image"):
            image = normalize_signature(payload["signature_image"])
            pdf.drawImage(ImageReader(BytesIO(base64.b64decode(image.split(',', 1)[1]))),
                          175, 118, width=150, height=58, preserveAspectRatio=True, anchor='sw', mask='auto')

        pdf.setFillColor(colors.HexColor("#6B7280"))
        pdf.setFont(TP_UNICODE, 8)
        pdf.drawString(45, 60, "This document was generated by Trade Paper AI.")
        pdf.drawString(45, 45, "For trade documentation automation.")

    y = start_table_page()

    item_count = len(items)

    for index, item in enumerate(items, start=1):
        quantity = pdf_number("Quantity", item.get("quantity", 0))
        unit_price = pdf_number("Unit price", item.get("unit_price", 0))
        line_total = quantity * unit_price

        is_last_row = index == item_count
        if is_last_row:
            required_bottom = row_min_bottom + total_h + total_gap + row_h
        else:
            required_bottom = row_min_bottom

        if y < required_bottom:
            pdf.showPage()
            y = start_table_page()

        pdf.rect(table_x, y, table_w, row_h, fill=0)
        pdf.drawString(60, y + 11, fit_pdf_text(pdf, item["name"], 170, TP_UNICODE, 10))
        pdf.drawString(245, y + 11, str(item.get("hs_code", "")))
        pdf.drawRightString(350, y + 11, str(quantity))
        pdf.drawRightString(445, y + 11, f"{currency} {unit_price:,.2f}")
        pdf.drawRightString(540, y + 11, f"{currency} {line_total:,.2f}")
        y -= row_h

    total_x = table_right - total_w
    total_top = y - total_gap
    total_bottom = total_top - total_h

    if total_bottom < row_min_bottom:
        pdf.showPage()
        y = start_table_page()
        total_top = y - total_gap
        total_bottom = total_top - total_h

    pdf.setFillColor(colors.HexColor("#111827"))
    pdf.roundRect(total_x, total_bottom, total_w, total_h, 8, fill=1, stroke=0)

    pdf.setFillColor(colors.white)
    pdf.setFont(TP_UNICODE_BOLD, 13)
    pdf.drawRightString(total_x + total_w - 20, total_bottom + 17, f"TOTAL: {currency} {total:,.2f}")

    draw_signature_footer()

    pdf.showPage()
    pdf.save()
    buffer.seek(0)

    return Response(
        content=buffer.getvalue(),
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{invoice_no}.pdf"'
        }
    )
def _invoice_revision(invoice):
    """Detect any saved change while an invoice editor is open."""
    payload = json.dumps(public_invoice(invoice), sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@router.get("/edit-invoice/{invoice_no}")
def edit_invoice(invoice_no: str, request: Request):
    account_id = _account_id(request)
    inv = public_invoice(_owned_invoice(invoice_no, account_id))
    company = load_account_company(account_id, ACCOUNT_COMPANIES_FILE)
    from app.packing import owned_packing_records
    linked = [record for record in owned_packing_records(account_id)
              if record.get("invoice_no") == invoice_no and record.get("packing_no")]
    lang = "ko" if request.query_params.get("lang", request.cookies.get("tp_language", "en")) == "ko" else "en"
    # Never guess which packing document to review when an invoice has several.
    continue_url = (f"/email-pair/prepare/{quote(str(linked[0]['packing_no']), safe='')}?lang={lang}"
                    if len(linked) == 1 else "")
    escape = lambda value: html_lib.escape(str(value if value is not None else ""), quote=True)
    items = inv.get("items") or [{}]
    cards = []
    fields = [("name", "item_name", "Item Name"), ("hs_code", "hs_code", "HS Code"),
              ("origin", "origin", "Country of Origin"), ("unit", "unit", "Unit"),
              ("quantity", "quantity", "Quantity"), ("unit_price", "unit_price", "Unit Price")]
    for index, item in enumerate(items):
        inputs = []
        for key, legacy_name, label in fields:
            name = legacy_name if index == 0 else "extra_" + key
            prefix = {"name": "item", "hs_code": "hs", "origin": "origin", "unit": "unit", "quantity": "qty", "unit_price": "price"}[key]
            field_id = f"{prefix}{index + 1}"
            numeric = key in ("quantity", "unit_price")
            attrs = 'type="number" step="any" min="0"' if numeric else 'type="text"'
            required = ' required' if key in ("name", "quantity", "unit_price") else ''
            inputs.append(f'<div><label for="{field_id}">{label}</label>'
                          f'<input {attrs} id="{field_id}" name="{name}" value="{escape(item.get(key, ""))}"'
                          f' data-field="{key}"{required}></div>')
        cards.append(f'<section class="card invoice-item"><h2><span>Item</span> {index + 1}</h2>'
                     '<label>Saved product<select class="saved-product"><option value="">Select Product Master</option></select></label>'
                     '<div class="grid">' + ''.join(inputs) + '</div></section>')
    party_fields = []
    for key, label, fallback in [
        ("currency", "Currency", "USD"), ("seller", "Seller", ""),
        ("seller_address", "Seller Address", company.get("address", "")),
        ("seller_email", "Seller Email", company.get("email", "")),
        ("seller_phone", "Seller Phone", company.get("phone", "")),
        ("buyer", "Buyer", ""), ("buyer_address", "Buyer Address", ""), ("buyer_email", "Buyer Email", ""),
    ]:
        value = inv.get(key, fallback)
        party_fields.append((label, f'<input type="text" name="{key}" value="{escape(value)}" placeholder="{label}">'))
    content = f"""
<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Edit Invoice</title>
<style>{form_css()}
.invoice-item .grid{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}}
.invoice-item .grid>div:first-child{{grid-column:1/-1}}
.invoice-item label{{display:block;margin-bottom:6px}}
@media(max-width:480px){{.invoice-item .grid{{grid-template-columns:1fr}}}}
</style></head><body><div class="container">
{navigation_footer("/invoice-list", "← Invoice List", state="Editing")}
<h1>Edit Invoice</h1><p class="sub">Review and update every item before saving.</p>
<form id="invoice-edit-form" data-managed-submit="true" data-continue-url="{escape(continue_url)}" action="/update-invoice/{escape(invoice_no)}" method="post">
<input type="hidden" name="revision" value="{_invoice_revision(inv)}">
{section_card("Invoice Information", metadata(party_fields))}
{''.join(cards)}
{signature_edit_control()}
<div class="total" id="total" aria-live="polite"></div>
<p>After saving, review the Packing List and download updated PDFs. Previously downloaded files stay unchanged.</p>
<p id="edit-error" role="alert" hidden></p>
{form_footer("/invoice-list", "Update Invoice")}
</form></div><script src="/static/invoice-edit.js?v=20261109-pair-review" defer></script></body></html>
"""
    return HTMLResponse(content)


@router.post("/update-invoice/{invoice_no}")
def update_invoice(
    invoice_no: str,
    request: Request,
    seller: str = Form(""),
    currency: str = Form(""),
    buyer: str = Form(""),
    buyer_address: str = Form(""),
    buyer_email: str = Form(""),
    item_name: str = Form(""),
    hs_code: str = Form(""),
    quantity: str = Form(""),
    unit_price: str = Form(""),
    origin: str = Form(""),
    unit: str = Form(""),
    seller_address: Annotated[Optional[str], Form()] = None,
    seller_email: Annotated[Optional[str], Form()] = None,
    seller_phone: Annotated[Optional[str], Form()] = None,
    revision: Annotated[Optional[str], Form()] = None,
    signature_action: Annotated[Optional[str], Form()] = None,
    extra_name: Annotated[Optional[list[str]], Form()] = None,
    extra_hs_code: Annotated[Optional[list[str]], Form()] = None,
    extra_quantity: Annotated[Optional[list[str]], Form()] = None,
    extra_unit_price: Annotated[Optional[list[str]], Form()] = None,
    extra_origin: Annotated[Optional[list[str]], Form()] = None,
    extra_unit: Annotated[Optional[list[str]], Form()] = None,
):
    seller = require_text("Seller", seller)
    buyer = require_text("Buyer", buyer)
    require_items([item_name])
    account_id = _account_id(request)
    if signature_action not in (None, "preserve", "use", "remove"):
        raise DataValidationError("Signature", "Choose a signature option.", "Reopen this invoice and select the signature to use.")
    selected_signature = signature_for_invoice(account_id, seller) if signature_action == "use" else None
    origin = origin if isinstance(origin, str) else ""
    unit = unit if isinstance(unit, str) else ""
    def replace_invoice(invoices):
        for inv in invoices:
            if (
                inv.get("invoice_no") != invoice_no
                or str(inv.get("account_id", "") or "").strip() != account_id
            ):
                continue
            existing_items = inv.get("items", [])
            extra_fields = {"name": extra_name, "hs_code": extra_hs_code, "quantity": extra_quantity,
                            "unit_price": extra_unit_price, "origin": extra_origin, "unit": extra_unit}
            if revision is None and any(value is not None for value in extra_fields.values()):
                raise HTTPException(status_code=409, detail="Reopen this Invoice before editing all items.")
            if revision is not None and revision != _invoice_revision(inv):
                raise HTTPException(status_code=409, detail="This Invoice changed while you were editing. Keep your changes for reference and reopen the latest Invoice before saving.")
            if revision is not None:
                count = max(0, len(existing_items) - 1)
                if any(len(value or []) != count for value in extra_fields.values()):
                    raise DataValidationError("Items", "The submitted item list is incomplete.", "Reopen the Invoice and review all items before saving.")
            updates = [{"name": require_text("Item Name", item_name), "hs_code": hs_code,
                        "quantity": quantity, "unit_price": unit_price, "origin": origin, "unit": unit}]
            if revision is not None:
                updates.extend({key: values[index] for key, values in extra_fields.items()}
                               for index in range(max(0, len(existing_items) - 1)))
            updated_items = [dict(item) for item in existing_items] or [{}]
            for index, values in enumerate(updates):
                values["name"] = require_text(f"Item {index + 1} name", values["name"])
                values["quantity"] = _invoice_number(f"Item {index + 1} quantity", values["quantity"])
                values["unit_price"] = _invoice_number(f"Item {index + 1} unit price", values["unit_price"])
                updated_items[index].update(values)

            if signature_action == "use":
                inv["signature_image"] = selected_signature
            elif signature_action == "remove" or str(inv.get("seller", "")).strip().casefold() != seller.casefold():
                inv.pop("signature_image", None)
            inv["seller"] = seller
            if seller_address is not None:
                inv["seller_address"] = seller_address
            if seller_email is not None:
                inv["seller_email"] = seller_email
            if seller_phone is not None:
                inv["seller_phone"] = seller_phone
            inv["currency"] = currency
            inv["buyer"] = buyer
            inv["buyer_address"] = buyer_address
            inv["buyer_email"] = buyer_email
            inv["items"] = updated_items
            return
        raise HTTPException(status_code=404, detail="Invoice not found")
    locked_json_mutation(INVOICE_FILE, [], replace_invoice, list)
    record_request_audit(request, "Update", "Commercial Invoice", invoice_no, path=INVOICE_FILE.with_name("audit_log.json"))

    return RedirectResponse(url="/invoice-list", status_code=303)     
@router.get("/delete-invoice/{invoice_no}")

def delete_invoice(invoice_no: str, request: Request):
    _owned_invoice(invoice_no, _account_id(request))
    from app.archive import render_archive_page
    return render_archive_page("Commercial Invoice", invoice_no, f"/delete-invoice/{invoice_no}", "/invoice-list")

@router.post("/delete-invoice/{invoice_no}")
def confirm_delete_invoice(invoice_no: str, request: Request):
    account_id = _account_id(request)
    from app.archive import archive_document
    return archive_document(request, "invoice", invoice_no, "/invoice-list")
    dependencies = find_dependencies("Commercial Invoice", invoice_no, account_id)
    if dependencies:
        return render_delete_page(
            "Commercial Invoice",
            invoice_no,
            f"/delete-invoice/{invoice_no}",
            "/invoice-list",
            dependencies,
            status_code=409,
        )

    def remove(invoices):
        index = next(
            (
                index
                for index, invoice in enumerate(invoices)
                if isinstance(invoice, dict)
                and str(invoice.get("invoice_no", "") or "").strip() == invoice_no
                and str(invoice.get("account_id", "") or "").strip() == account_id
            ),
            None,
        )
        if index is None:
            raise HTTPException(status_code=404, detail="Invoice not found")
        invoices.pop(index)

    locked_json_mutation(INVOICE_FILE, [], remove, list)
    return RedirectResponse("/invoice-list", status_code=303)
@router.get("/invoice-list")
def invoice_list(request: Request, search: str = ""):

    if not os.path.exists(INVOICE_FILE):
        return HTMLResponse("<h1>No Invoices</h1>")

    account_id = _account_id(request)
    invoices = load_invoices(account_id)
    from app import packing as packing_module
    packing_by_invoice = {}
    for packing in packing_module.load_packing_lists(account_id):
        invoice_no = str(packing.get("invoice_no", "") or "").strip()
        packing_no = str(packing.get("packing_no", "") or "").strip()
        if invoice_no and packing_no:
            packing_by_invoice.setdefault(invoice_no, packing_no)

    valid_invoices = [
        inv for inv in invoices
        if inv.get("invoice_no")
    ]

    valid_invoices = sorted(
        valid_invoices,
        key=lambda inv: inv.get("invoice_no", ""),
        reverse=True
    )

    if search:
        search_lower = search.lower()

        valid_invoices = [
            inv for inv in valid_invoices
            if search_lower in inv.get("invoice_no", "").lower()
            or search_lower in inv.get("buyer", "").lower()
            or search_lower in inv.get("seller", "").lower()
            or search_lower in str(inv.get("items", "")).lower()
        ]

    rows = []
    for inv in valid_invoices:
        packing_no = packing_by_invoice.get(str(inv.get("invoice_no", "") or "").strip(), "")
        packing_exists = bool(packing_no)
        packing_href = f"/edit-packing/{packing_no}" if packing_exists else f"/packing-page?invoice_no={inv.get('invoice_no', '')}"
        items = inv.get("items", [])
        item_names = "<br>".join(html_lib.escape(str(item.get("name", "") or "")) for item in items)

        total = sum(
            item.get("quantity", 0) * item.get("unit_price", 0)
            for item in items
        )

        invoice_no = str(inv.get("invoice_no", "") or "")
        rows.append([
            badge(invoice_no), html_lib.escape(str(inv.get("seller", "") or "")),
            html_lib.escape(str(inv.get("buyer", "") or "")), item_names, f"{html_lib.escape(str(inv.get('currency') or 'USD').strip().upper() or 'USD')} {total:g}",
            button("PDF", f"/invoice-pdf/{invoice_no}", "secondary"),
            button("Send Email", f"/send-email/invoice/{invoice_no}", "secondary"),
            button("Created" if packing_exists else "Packing", packing_href, "secondary"),
            button("Edit", f"/edit-invoice/{invoice_no}", "secondary") + " " + button("Repeat order", "/import-document?" + urlencode({'repeat_invoice': invoice_no}), "secondary"),
            button("Delete", f"/delete-invoice/{invoice_no}", "danger"),
        ])
    content = search_toolbar(button("+ New Invoice", "/invoice"), button("← Dashboard", "/", "secondary"), action="/invoice-list", value=search, placeholder="Search invoice, buyer, seller or item", reset_url="/invoice-list", count_label=f"Total Invoices : {len(valid_invoices)}")
    content += table(["Invoice No", "Seller", "Buyer", "Product", "Total", "PDF", "Email", "Packing", "Edit", "Delete"], rows)
    return HTMLResponse(page_shell("Invoice List", content, subtitle="Manage all invoice documents"))
