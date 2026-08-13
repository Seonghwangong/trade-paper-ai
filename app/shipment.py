from pathlib import Path
from datetime import datetime
from io import BytesIO
from copy import deepcopy
from typing import Annotated, Optional
import html as html_lib
import zipfile
from urllib.parse import quote, urlencode

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from app.pdf_fonts import TP_UNICODE, TP_UNICODE_BOLD, ensure_pdf_fonts, fit_pdf_text

router = APIRouter()

from app.storage import atomic_write_json, data_path, load_json_strict, locked_json_mutation, next_identifier
from app.validation import require_allowed_value, require_consistent_reference, require_existing_reference, require_text
from app.referential_integrity import find_dependencies, render_delete_page
from app.account_shipment import ensure_legacy_shipment_ownership, public_shipment
from app.snapshot import fill_missing_snapshot_fields, set_submitted_snapshot_fields, snapshot_value
from app.export import set_pdf_export_record
from app.auth import USERS_FILE
from app.account_company import load_account_company
from app.routers.company import ACCOUNT_COMPANIES_FILE

SHIPMENT_FILE = data_path("shipments.json")

OPERATIONAL_RECORDS = [
    {
        "label": "Booking Confirmation",
        "file": data_path("booking_confirmations.json"),
        "key": "booking_record_no",
        "view": "/booking/{value}",
        "pdf": "/booking-pdf/{value}",
        "edit": "/edit-booking/{value}",
        "create": "/booking-form?shipment_no={shipment_no}",
    },
    {
        "label": "Container Management",
        "file": data_path("containers.json"),
        "key": "container_record_no",
        "view": "/container/{value}",
        "pdf": "/container-pdf/{value}",
        "edit": "/edit-container/{value}",
        "create": "/container-form?shipment_no={shipment_no}",
    },
    {
        "label": "Customs Declaration",
        "file": data_path("customs_declarations.json"),
        "key": "customs_record_no",
        "view": "/customs/{value}",
        "pdf": "/customs-pdf/{value}",
        "edit": "/edit-customs/{value}",
        "create": "/customs-form?shipment_no={shipment_no}",
    },
]

DOCUMENTS = [
    {
        "label": "Quotation",
        "field": "quotation_no",
        "file": data_path("quotations.json"),
        "key": "quotation_no",
        "pdf": "/quotation-pdf/{value}",
        "edit": "/edit-quotation/{value}",
    },
    {
        "label": "Proforma Invoice",
        "field": "pi_no",
        "file": data_path("proformas.json"),
        "key": "pi_no",
        "pdf": "/proforma-pdf/{value}",
        "edit": "/edit-proforma/{value}",
    },
    {
        "label": "Commercial Invoice",
        "field": "invoice_no",
        "file": data_path("invoices.json"),
        "key": "invoice_no",
        "pdf": "/invoice-pdf/{value}",
        "edit": "/edit-invoice/{value}",
    },
    {
        "label": "Packing List",
        "field": "packing_no",
        "file": data_path("packing_lists.json"),
        "key": "packing_no",
        "pdf": "/packing-list-pdf/{value}",
        "edit": "/edit-packing/{value}",
    },
    {
        "label": "Shipping Instruction",
        "field": "si_no",
        "file": data_path("shipping_instructions.json"),
        "key": "si_no",
        "pdf": "/si-pdf/{value}",
        "edit": "/edit-si/{value}",
    },
    {
        "label": "Bill of Lading",
        "field": "bl_no",
        "file": data_path("bills_of_lading.json"),
        "key": "bl_no",
        "pdf": "/bl-pdf/{value}",
        "edit": "/edit-bl/{value}",
    },
    {
        "label": "Certificate of Origin",
        "field": "co_no",
        "file": data_path("certificates_of_origin.json"),
        "key": "co_no",
        "view": "/co/{value}",
        "pdf": "/co-pdf/{value}",
        "edit": "/edit-co/{value}",
    },
    {
        "label": "Inspection Certificate",
        "field": "inspection_no",
        "file": data_path("inspection_certificates.json"),
        "key": "inspection_no",
        "view": "/inspection/{value}",
        "pdf": "/inspection-pdf/{value}",
        "edit": "/edit-inspection/{value}",
    },
    {
        "label": "Insurance Certificate",
        "field": "insurance_no",
        "file": data_path("insurance_certificates.json"),
        "key": "insurance_no",
        "view": "/insurance/{value}",
        "pdf": "/insurance-pdf/{value}",
        "edit": "/edit-insurance/{value}",
    },
    {
        "label": "Weight Certificate",
        "field": "weight_no",
        "file": data_path("weight_certificates.json"),
        "key": "weight_no",
        "view": "/weight/{value}",
        "pdf": "/weight-pdf/{value}",
        "edit": "/edit-weight/{value}",
    },
]

TRACKING_STATUS_OPTIONS = ["Draft", "Booked", "Loaded", "In Transit", "Arrived", "Delivered", "Completed"]
LEGACY_STATUS_OPTIONS = ["Inquiry", "Quoted", "Confirmed", "In Production", "Ready to Ship", "Shipped"]
STATUS_OPTIONS = [*TRACKING_STATUS_OPTIONS, *LEGACY_STATUS_OPTIONS]
TRACKING_FIELDS = (
    "container_no", "seal_no", "container_type", "etd", "eta",
    "actual_departure", "actual_arrival", "tracking_memo",
)

PARTY_SNAPSHOT_FIELDS = (
    "shipper", "shipper_address", "shipper_email", "shipper_phone",
    "consignee", "consignee_address", "consignee_email",
)
CARGO_SNAPSHOT_FIELDS = ("items", "total_carton", "total_net_weight", "total_gross_weight")

DOCUMENT_PACKAGE_ITEMS = (
    {"label": "Commercial Invoice", "field": "invoice_no", "view": "/invoice-list?search={value}", "edit": "/edit-invoice/{value}", "pdf": "/invoice-pdf/{value}"},
    {"label": "Packing List", "field": "packing_no", "view": "/packing-list?search={value}", "edit": "/edit-packing/{value}", "pdf": "/packing-list-pdf/{value}"},
    {"label": "Shipping Instruction", "field": "si_no", "view": "/si-list?search={value}", "edit": "/edit-si/{value}", "pdf": "/si-pdf/{value}"},
    {"label": "Booking Confirmation", "field": "booking_record_no", "view": "/booking/{value}", "edit": "/edit-booking/{value}", "pdf": "/booking-pdf/{value}"},
    {"label": "Bill of Lading", "field": "bl_no", "view": "/bl-list?search={value}", "edit": "/edit-bl/{value}", "pdf": "/bl-pdf/{value}"},
    {"label": "Certificate of Origin", "field": "co_no", "view": "/co/{value}", "edit": "/edit-co/{value}", "pdf": "/co-pdf/{value}"},
)
EMAIL_DOCUMENT_TYPES = {
    "invoice_no": "invoice", "packing_no": "packing",
    "si_no": "shipping-instruction", "booking_record_no": "booking",
    "bl_no": "bill-of-lading", "co_no": "certificate-of-origin",
}


def html_attr(value):
    return html_lib.escape(str(value or ""), quote=True)


def html_text(value):
    return html_lib.escape(str(value or ""))


def load_json(path, default):
    return load_json_strict(path, default, type(default) if isinstance(default, (list, dict)) else None)


def _account_id(request):
    user = request.scope.get("trade_paper_user") or {}
    return str(user.get("account_id", "") or "").strip()


def load_shipment_records():
    return ensure_legacy_shipment_ownership(SHIPMENT_FILE, USERS_FILE)


def owned_shipment_records(account_id):
    owner = str(account_id or "").strip()
    return [
        record for record in load_shipment_records()
        if isinstance(record, dict)
        and str(record.get("account_id", "") or "").strip() == owner
    ]


def load_shipments(account_id):
    return [public_shipment(record) for record in owned_shipment_records(account_id)]


def save_shipments(records):
    atomic_write_json(SHIPMENT_FILE, records, list)


def next_shipment_no(records):
    return next_identifier(records, "shipment_no", "SHP")


def blank_shipment():
    record = {
        "shipment_no": "",
        "shipment_date": datetime.now().strftime("%Y-%m-%d"),
        "shipment_name": "",
        "customer": "",
        "buyer": "",
        "status": "Draft",
        "remarks": "",
    }
    for doc in DOCUMENTS:
        record[doc["field"]] = ""
    for field in PARTY_SNAPSHOT_FIELDS:
        record[field] = ""
    record.update({"items": [], "total_carton": "", "total_net_weight": "", "total_gross_weight": ""})
    record.update({field: "" for field in TRACKING_FIELDS})
    return record


def _first_record(records, field, value):
    target = str(value or "").strip()
    if not target:
        return {}
    return next(
        (record for record in records
         if str(record.get(field, "") or "").strip() == target),
        {},
    )


def _numeric_total(items, field):
    total = 0.0
    for item in items or []:
        try:
            total += float(item.get(field, 0) or 0)
        except (TypeError, ValueError):
            pass
    return f"{total:g}" if total else ""


def resolve_shipment_snapshot(record, account_id, bill=None, packing=None, invoice=None, instruction=None, preserve_empty=None):
    """Resolve a read-only Shipment snapshot using account-owned sources only."""
    from app import bill_of_lading as bill_module
    from app import packing as packing_module
    from app import invoice as invoice_module
    from app import shipping_instruction as shipping_instruction_module
    from app import buyer as buyer_module

    resolved = deepcopy(record or {})
    if preserve_empty is None:
        preserve_empty = bool(resolved.get("shipment_no"))
    si_no = str(resolved.get("si_no", "") or "").strip()
    if instruction is None and si_no:
        instruction = _first_record(
            shipping_instruction_module.load_shipping_instructions(account_id), "si_no", si_no,
        )
    instruction = instruction or {}
    bl_no = str(resolved.get("bl_no", "") or "").strip()
    if bill is None:
        bill = _first_record(bill_module.load_bills_of_lading(account_id), "bl_no", bl_no)
    bill = bill or {}

    packing_no = str(snapshot_value(resolved, "packing_no", (
        instruction.get("packing_no", ""), bill.get("packing_no", ""),
    ), preserve_empty=preserve_empty) or "").strip()
    if packing is None:
        packing = _first_record(packing_module.load_packing_lists(account_id), "packing_no", packing_no)
    packing = packing or {}

    invoice_no = str(snapshot_value(resolved, "invoice_no", (
        instruction.get("invoice_no", ""), bill.get("invoice_no", ""), packing.get("invoice_no", ""),
    ), preserve_empty=preserve_empty) or "").strip()
    if invoice is None:
        invoice = _first_record(invoice_module.load_invoices(account_id), "invoice_no", invoice_no)
    invoice = invoice or {}

    company = load_account_company(account_id, ACCOUNT_COMPANIES_FILE)
    consignee_name = (
        resolved.get("consignee") or instruction.get("consignee") or instruction.get("consignee_name") or bill.get("consignee") or packing.get("buyer")
        or invoice.get("buyer") or resolved.get("buyer") or ""
    )
    buyer = next(
        (candidate for candidate in buyer_module.load_buyers(account_id)
         if str(candidate.get("name", "") or "").strip().casefold()
         == str(consignee_name or "").strip().casefold()),
        {},
    )

    fallbacks = {
        "shipper": (instruction.get("shipper"), instruction.get("exporter_name"), bill.get("shipper"), packing.get("seller"), invoice.get("seller"), company.get("name")),
        "shipper_address": (instruction.get("exporter_address"), bill.get("shipper_address"), packing.get("seller_address"), invoice.get("seller_address"), company.get("address")),
        "shipper_email": (instruction.get("exporter_email"), bill.get("shipper_email"), packing.get("seller_email"), invoice.get("seller_email"), company.get("email")),
        "shipper_phone": (instruction.get("exporter_phone"), bill.get("shipper_phone"), packing.get("seller_phone"), invoice.get("seller_phone"), company.get("phone")),
        "consignee": (instruction.get("consignee"), instruction.get("consignee_name"), bill.get("consignee"), packing.get("buyer"), invoice.get("buyer"), buyer.get("name")),
        "consignee_address": (instruction.get("consignee_address"), bill.get("consignee_address"), packing.get("buyer_address"), invoice.get("buyer_address"), buyer.get("address")),
        "consignee_email": (instruction.get("consignee_email"), bill.get("consignee_email"), packing.get("buyer_email"), invoice.get("buyer_email"), buyer.get("email")),
    }
    fill_missing_snapshot_fields(resolved, fallbacks, preserve_empty=preserve_empty)

    if "items" not in resolved or (not preserve_empty and not resolved["items"]):
        resolved["items"] = deepcopy(
            instruction.get("items") or bill.get("items") or packing.get("items") or invoice.get("items") or []
        )
    for field in ("total_carton", "total_net_weight", "total_gross_weight"):
        if field not in resolved or (not preserve_empty and not resolved[field]):
            resolved[field] = instruction.get(field) or bill.get(field) or packing.get(field) or _numeric_total(
                resolved.get("items", []), field.removeprefix("total_")
            )

    resolved["bl_no"] = bl_no
    resolved["packing_no"] = packing_no
    resolved["invoice_no"] = invoice_no
    if ("buyer" not in resolved or (not preserve_empty and not resolved.get("buyer"))) and buyer.get("name"):
        resolved["buyer"] = buyer.get("name", "")
    from app import product as product_module
    product_module.enrich_items_from_products(resolved.get("items", []), account_id)
    return resolved


def _cached_records(path, datasets):
    if datasets is None:
        return None
    return datasets.get(Path(path).name)


def load_workflow_datasets(account_id=None):
    """Load each workflow dataset once for one render operation."""
    datasets = {}
    for descriptor in [*DOCUMENTS, *OPERATIONAL_RECORDS]:
        filename = Path(descriptor["file"]).name
        if filename not in datasets:
            datasets[filename] = load_json(descriptor["file"], [])
    if account_id is not None:
        from app import invoice as invoice_module
        from app import packing as packing_module
        from app import shipping_instruction as shipping_instruction_module
        from app import booking_confirmation as booking_module
        from app import container_management as container_module
        from app import bill_of_lading as bill_of_lading_module
        from app import customs_declaration as customs_module
        from app import certificate_of_origin as certificate_of_origin_module
        from app import inspection_certificate as inspection_module
        from app import insurance_certificate as insurance_module
        from app import weight_certificate as weight_module
        from app import quotation as quotation_module
        from app import proforma as proforma_module
        datasets["invoices.json"] = invoice_module.owned_invoice_records(account_id)
        datasets["packing_lists.json"] = packing_module.owned_packing_records(account_id)
        datasets["shipping_instructions.json"] = shipping_instruction_module.owned_shipping_instruction_records(account_id)
        datasets["booking_confirmations.json"] = booking_module.owned_booking_records(account_id)
        datasets["containers.json"] = container_module.owned_container_records(account_id)
        datasets["bills_of_lading.json"] = bill_of_lading_module.owned_bill_of_lading_records(account_id)
        datasets["customs_declarations.json"] = customs_module.owned_customs_records(account_id)
        datasets["certificates_of_origin.json"] = certificate_of_origin_module.owned_certificate_records(account_id)
        datasets["inspection_certificates.json"] = inspection_module.owned_inspection_records(account_id)
        datasets["insurance_certificates.json"] = insurance_module.owned_insurance_records(account_id)
        datasets["weight_certificates.json"] = weight_module.owned_weight_records(account_id)
        datasets["quotations.json"] = quotation_module.owned_quotation_records(account_id)
        datasets["proformas.json"] = proforma_module.owned_proforma_records(account_id)
    return datasets


def document_records(doc, datasets=None):
    cached = _cached_records(doc["file"], datasets)
    return cached if cached is not None else load_json(doc["file"], [])


def document_options(doc, datasets=None):
    records = document_records(doc, datasets)
    values = []
    for record in records:
        value = str(record.get(doc["key"], "") or "")
        if value:
            values.append(value)
    return sorted(set(values), reverse=True)


def document_exists(doc, value, datasets=None):
    if not value:
        return False
    return any(record.get(doc["key"]) == value for record in document_records(doc, datasets))


def linked_count(record, datasets=None):
    return sum(
        1
        for doc in DOCUMENTS
        if document_exists(doc, record.get(doc["field"], ""), datasets)
    )


def find_shipment(shipment_no, account_id):
    for record in owned_shipment_records(account_id):
        if record.get("shipment_no") == shipment_no:
            return record
    return None


def link_direct_document(shipment_no, field, identifier):
    """Link an existing workflow result without adding fields or changing status."""
    allowed = {"invoice_no", "packing_no", "si_no", "bl_no", "co_no", "inspection_no", "insurance_no", "weight_no"}
    shipment_no = str(shipment_no or "").strip()
    identifier = str(identifier or "").strip()
    if field not in allowed or not shipment_no or not identifier:
        return False
    document = document_by_field(field)
    linked_record = next(
        (
            record for record in document_records(document)
            if str(record.get(document["key"], "") or "").strip() == identifier
        ),
        None,
    ) if document else None
    owner = str((linked_record or {}).get("account_id", "") or "").strip()
    if not owner:
        return False
    linked = {"value": False}
    def update(records):
        for record in records:
            if (str(record.get("shipment_no", "") or "").strip() == shipment_no
                    and str(record.get("account_id", "") or "").strip() == owner):
                record[field] = identifier
                linked["value"] = True
                return
    locked_json_mutation(SHIPMENT_FILE, [], update, list)
    return linked["value"]


def shipment_context_redirect_url(shipment_no, field, identifier, fallback_url):
    if link_direct_document(shipment_no, field, identifier):
        return f'/shipment/{quote(str(shipment_no).strip(), safe="")}'
    return fallback_url


def shipment_detail_redirect_url(shipment_no, account_id, fallback_url):
    normalized = str(shipment_no or "").strip()
    if normalized and find_shipment(normalized, account_id):
        return f'/shipment/{quote(normalized, safe="")}'
    return fallback_url


def direct_document_shipment_no(field, identifier, account_id):
    """Resolve one unambiguous account-owned Shipment for a directly linked document."""
    target = str(identifier or "").strip()
    if field not in {"packing_no", "bl_no"} or not target:
        return ""
    matches = [
        str(record.get("shipment_no", "") or "").strip()
        for record in owned_shipment_records(account_id)
        if str(record.get(field, "") or "").strip() == target
    ]
    unique = {value for value in matches if value}
    return next(iter(unique)) if len(unique) == 1 else ""


def resolve_direct_documents(record, datasets=None):
    resolved = []
    for doc in DOCUMENTS:
        value = str(record.get(doc["field"], "") or "")
        resolved.append({"document": doc, "value": value, "exists": document_exists(doc, value, datasets)})
    return resolved


def reverse_records_for(shipment_no, operational, datasets=None):
    matches = []
    records = _cached_records(operational["file"], datasets)
    for record in records if records is not None else load_json(operational["file"], []):
        value = str(record.get(operational["key"], "") or "")
        if value and record.get("shipment_no") == shipment_no:
            matches.append({"value": value, "record": record})
    return matches


def resolve_operational_records(shipment_no, datasets=None):
    return [
        {"operational": operational, "matches": reverse_records_for(shipment_no, operational, datasets)}
        for operational in OPERATIONAL_RECORDS
    ]


def resolve_document_package(shipment, datasets):
    """Resolve the six customer-facing package documents from one owned Shipment."""
    direct = {entry["document"]["field"]: entry for entry in resolve_direct_documents(shipment, datasets)}
    booking_group = next(
        group for group in resolve_operational_records(shipment.get("shipment_no", ""), datasets)
        if group["operational"]["key"] == "booking_record_no"
    )
    def related(records, key):
        candidates = []
        for record in records:
            if any(
                shipment.get(field) and record.get(field) == shipment.get(field)
                for field in ("shipment_no", "si_no", "packing_no", "invoice_no", "bl_no")
            ):
                candidates.append({"value": str(record.get(key, "") or ""), "record": record})
        candidates = [candidate for candidate in candidates if candidate["value"]]
        return select_operational_match(candidates, shipment)

    booking = select_operational_match(booking_group["matches"], shipment)
    if not booking:
        booking = related(datasets.get("booking_confirmations.json", []), "booking_record_no")
    package = []
    for descriptor in DOCUMENT_PACKAGE_ITEMS:
        field = descriptor["field"]
        if field == "booking_record_no":
            value = booking["value"] if booking else ""
            exists = bool(booking)
        else:
            resolved = direct[field]
            value, exists = resolved["value"], resolved["exists"]
            if not exists and field in {"bl_no", "co_no"}:
                filename = "bills_of_lading.json" if field == "bl_no" else "certificates_of_origin.json"
                inferred = related(datasets.get(filename, []), field)
                if inferred:
                    value, exists = inferred["value"], True
        package.append({**descriptor, "value": value, "exists": exists})
    return package


def render_document_package_page(request, selected_shipment_no=""):
    account_id = _account_id(request)
    shipments = sorted(load_shipments(account_id), key=lambda row: str(row.get("shipment_no", "")), reverse=True)
    options = ['<option value="">Select Shipment</option>']
    for shipment in shipments:
        value = str(shipment.get("shipment_no", "") or "")
        selected = " selected" if value == selected_shipment_no else ""
        label = " · ".join(part for part in (value, str(shipment.get("shipment_name", "") or "")) if part)
        options.append(f'<option value="{html_attr(value)}"{selected}>{html_text(label)}</option>')

    cards = '<div class="empty">Select a Shipment to build its document package.</div>'
    package_actions = ""
    if selected_shipment_no:
        shipment = find_shipment(selected_shipment_no, account_id)
        if not shipment:
            raise HTTPException(status_code=404, detail="Shipment not found")
        package = resolve_document_package(public_shipment(shipment), load_workflow_datasets(account_id))
        rows = []
        for item in package:
            if item["exists"]:
                value = item["value"]
                actions = "".join(
                    f'<a href="{html_attr(item[key].format(value=quote(value, safe="")))}">{label}</a>'
                    for key, label in (("view", "View"), ("edit", "Edit"), ("pdf", "PDF"))
                )
                actions += f'<a href="/send-email/{EMAIL_DOCUMENT_TYPES[item["field"]]}/{quote(value, safe="")}">Send Email</a>'
                status = '<span class="status complete">Complete</span>'
                identifier = html_text(value)
            else:
                actions = ""
                status = '<span class="status missing">Missing</span>'
                identifier = f'{html_text(item["label"])} is missing'
            rows.append(f'''<article class="doc"><div><h2>{html_text(item["label"])}</h2><p>{identifier}</p></div><div>{status}<div class="actions">{actions}</div></div></article>''')
        cards = "".join(rows)
        complete_count = sum(1 for item in package if item["exists"])
        package_actions = f'''<div class="summary"><strong>{complete_count} / {len(package)} documents complete</strong><div><a class="download" href="/shipment/{html_attr(selected_shipment_no)}/package.zip">Download Package (.zip)</a> <a class="download" href="/send-email/document-package/{html_attr(selected_shipment_no)}">Send Email</a></div></div>'''

    return HTMLResponse(f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Document Package</title><style>*{{box-sizing:border-box}}body{{margin:0;padding:40px;background:#F3F4F6;color:#111827;font-family:Arial,sans-serif}}main{{width:min(980px,94%);margin:auto}}.nav{{display:flex;gap:10px;margin-bottom:24px}}.nav a,.download,.actions a{{display:inline-flex;padding:11px 14px;border-radius:9px;background:#111827;color:#fff;text-decoration:none;font-weight:700}}h1{{font-size:44px;margin:0 0 8px}}.sub{{color:#64748B;margin:0 0 26px}}form,.summary,.doc,.empty{{background:#fff;border:1px solid #E5E7EB;border-radius:15px;padding:20px;margin-bottom:15px}}form{{display:flex;gap:12px}}select,button{{min-height:46px;padding:10px 13px;border:1px solid #CBD5E1;border-radius:9px;font-size:16px}}select{{flex:1;background:#fff}}button{{background:#111827;color:#fff;font-weight:700;cursor:pointer}}.summary,.doc{{display:flex;align-items:center;justify-content:space-between;gap:16px}}.doc h2{{margin:0 0 6px;font-size:19px}}.doc p{{margin:0;color:#64748B}}.status{{display:inline-block;padding:6px 9px;border-radius:999px;font-size:12px;font-weight:800}}.complete{{background:#DCFCE7;color:#166534}}.missing{{background:#FEE2E2;color:#991B1B}}.actions{{display:flex;gap:6px;margin-top:9px}}.actions a{{padding:7px 9px;font-size:12px}}@media(max-width:700px){{body{{padding:20px}}form,.summary,.doc{{align-items:stretch;flex-direction:column}}h1{{font-size:34px}}}}</style></head><body><main><div class="nav"><a href="/">Dashboard</a><a href="/shipment-list">Shipment List</a></div><h1>Document Package</h1><p class="sub">All trade documents connected to one Shipment.</p><form action="/document-package" method="get"><select name="shipment_no" required>{''.join(options)}</select><button type="submit">Build Package</button></form>{package_actions}<section>{cards}</section></main></body></html>''')


def operational_count(shipment_no):
    return sum(len(group["matches"]) for group in resolve_operational_records(shipment_no))


def shipment_dependencies(shipment_no, account_id):
    from app import booking_confirmation as booking_module
    owned_bookings = {
        str(record.get("booking_record_no", "") or "").strip()
        for record in booking_module.owned_booking_records(account_id)
    }
    return [
        dependency for dependency in find_dependencies("Shipment", shipment_no, account_id)
        if dependency["module"] != "Booking Confirmation"
        or dependency["identifier"] in owned_bookings
    ]


def required_workflow_progress(shipment, resolved_direct=None, resolved_operations=None):
    if resolved_direct is None:
        resolved_direct = resolve_direct_documents(shipment)
    if resolved_operations is None:
        resolved_operations = resolve_operational_records(shipment.get("shipment_no", ""))

    direct_status = {
        resolved["document"]["field"]: resolved["exists"]
        for resolved in resolved_direct
    }
    operational_status = {
        group["operational"]["key"]: bool(group["matches"])
        for group in resolved_operations
    }
    completed = sum([
        bool(direct_status.get("invoice_no")),
        bool(direct_status.get("packing_no")),
        bool(direct_status.get("si_no")),
        bool(operational_status.get("booking_record_no")),
        bool(direct_status.get("bl_no")),
        bool(operational_status.get("customs_record_no")),
    ])
    total = 6
    return {
        "completed": completed,
        "total": total,
        "percentage": round(completed * 100 / total),
    }


def health_score_label(score):
    if score >= 90:
        return "Excellent"
    if score >= 70:
        return "Good"
    if score >= 40:
        return "Attention"
    return "Critical"


def shipment_health_score(
    shipment,
    resolved_direct=None,
    resolved_operations=None,
    workflow_progress=None,
    next_step=None,
):
    if resolved_direct is None:
        resolved_direct = resolve_direct_documents(shipment)
    if resolved_operations is None:
        resolved_operations = resolve_operational_records(shipment.get("shipment_no", ""))
    if workflow_progress is None:
        workflow_progress = required_workflow_progress(shipment, resolved_direct, resolved_operations)
    if next_step is None:
        next_step = next_step_for_shipment(shipment)

    direct_status = {
        resolved["document"]["field"]: resolved["exists"]
        for resolved in resolved_direct
    }
    operational_status = {
        group["operational"]["key"]: bool(group["matches"])
        for group in resolved_operations
    }
    optional_completed = sum([
        bool(direct_status.get("quotation_no")),
        bool(direct_status.get("pi_no")),
        bool(operational_status.get("container_record_no")),
        bool(direct_status.get("co_no")),
        bool(direct_status.get("inspection_no")),
        bool(direct_status.get("insurance_no")),
        bool(direct_status.get("weight_no")),
    ])
    optional_total = 7
    optional_points = round(optional_completed * 10 / optional_total)
    required_completed = workflow_progress["completed"]
    score = max(0, min(100, required_completed * 15 + optional_points))
    return {
        "score": score,
        "label": health_score_label(score),
        "required_completed": required_completed,
        "required_total": 6,
        "optional_completed": optional_completed,
        "optional_total": optional_total,
        "workflow_complete": bool(next_step["is_complete"]),
    }


def document_by_field(field):
    return next((doc for doc in DOCUMENTS if doc["field"] == field), None)


def validate_shipment_values(record, account_id, datasets=None):
    from app import buyer as buyer_module
    record["shipment_name"] = require_text("Shipment name", record.get("shipment_name", ""))
    record["status"] = require_allowed_value("Shipment status", record.get("status", ""), STATUS_OPTIONS)
    require_existing_reference(
        "Buyer", record.get("buyer", ""), buyer_module.load_buyers(account_id), "name"
    )
    linked = {}
    for doc in DOCUMENTS:
        value = record.get(doc["field"], "")
        linked[doc["field"]] = require_existing_reference(
            doc["label"], value, document_records(doc, datasets), doc["key"]
        )
    packing = linked.get("packing_no")
    if packing:
        require_consistent_reference("Invoice", record.get("invoice_no", ""), packing.get("invoice_no", ""), "selected Packing List")
    instruction = linked.get("si_no")
    if instruction:
        require_consistent_reference("Packing List", record.get("packing_no", ""), instruction.get("packing_no", ""), "selected Shipping Instruction")
    bill = linked.get("bl_no")
    if bill:
        require_consistent_reference("Packing List", record.get("packing_no", ""), bill.get("packing_no", ""), "selected Bill of Lading")
        require_consistent_reference("Invoice", record.get("invoice_no", ""), bill.get("invoice_no", ""), "selected Bill of Lading")
    for field in ["co_no", "inspection_no", "insurance_no", "weight_no"]:
        certificate = linked.get(field)
        if certificate:
            require_consistent_reference("Bill of Lading", record.get("bl_no", ""), certificate.get("bl_no", ""), "selected certificate")


def operational_by_key(key):
    return next((record for record in OPERATIONAL_RECORDS if record["key"] == key), None)


def workflow_url(path, parameters):
    query = urlencode([(key, value) for key, value in parameters if str(value or "")])
    return f"{path}?{query}" if query else path


def select_operational_match(matches, shipment):
    packing_no = str(shipment.get("packing_no", "") or "")
    bl_no = str(shipment.get("bl_no", "") or "")

    def sort_key(match):
        record = match["record"]
        related = bool(
            (packing_no and record.get("packing_no") == packing_no)
            or (bl_no and record.get("bl_no") == bl_no)
        )
        return related, match["value"]

    return max(matches, key=sort_key) if matches else None


def next_step_for_shipment(shipment, resolved_direct=None, resolved_operations=None):
    shipment_no = str(shipment.get("shipment_no", "") or "")
    invoice_no = str(shipment.get("invoice_no", "") or "")
    packing_no = str(shipment.get("packing_no", "") or "")
    si_no = str(shipment.get("si_no", "") or "")
    bl_no = str(shipment.get("bl_no", "") or "")

    direct_status = {
        resolved["document"]["field"]: resolved
        for resolved in (resolved_direct or [])
    }
    operation_status = {
        group["operational"]["key"]: group["matches"]
        for group in (resolved_operations or [])
    }
    def direct_exists(field, value):
        resolved = direct_status.get(field)
        return resolved["exists"] if resolved is not None else document_exists(document_by_field(field), value)
    def operation_matches(key):
        if key in operation_status:
            return operation_status[key]
        return reverse_records_for(shipment_no, operational_by_key(key))

    invoice_exists = direct_exists("invoice_no", invoice_no)
    packing_exists = direct_exists("packing_no", packing_no)
    si_exists = direct_exists("si_no", si_no)
    bl_exists = direct_exists("bl_no", bl_no)

    booking_matches = operation_matches("booking_record_no")
    container_matches = operation_matches("container_record_no")
    customs_matches = operation_matches("customs_record_no")

    if not invoice_exists:
        pi_no = str(shipment.get("pi_no", "") or "")
        pi_exists = direct_exists("pi_no", pi_no)
        create_url = workflow_url("/invoice", [("pi_no", pi_no), ("shipment_no", shipment_no)]) if pi_exists else workflow_url("/invoice", [("shipment_no", shipment_no)])
        return {
            "step_label": "Commercial Invoice",
            "reason": "A Commercial Invoice is required before packing and shipping documents can be prepared.",
            "create_url": create_url,
            "is_complete": False,
        }
    if not packing_exists:
        return {
            "step_label": "Packing List",
            "reason": "A Packing List is required to prepare the shipment's cargo documents.",
            "create_url": workflow_url("/packing-page", [("invoice_no", invoice_no), ("shipment_no", shipment_no)]),
            "is_complete": False,
        }
    if not si_exists:
        return {
            "step_label": "Shipping Instruction",
            "reason": "A Shipping Instruction is required before carrier booking.",
            "create_url": workflow_url("/si-form", [("packing_no", packing_no), ("shipment_no", shipment_no)]),
            "is_complete": False,
        }
    if not booking_matches:
        return {
            "step_label": "Booking Confirmation",
            "reason": "A Booking Confirmation is required to schedule this shipment.",
            "create_url": workflow_url("/booking-form", [
                ("shipment_no", shipment_no), ("si_no", si_no), ("packing_no", packing_no),
            ]),
            "is_complete": False,
        }
    if not bl_exists:
        return {
            "step_label": "Bill of Lading",
            "reason": "A Bill of Lading is required before customs clearance.",
            "create_url": workflow_url("/bl-form", [("packing_no", packing_no), ("shipment_no", shipment_no)]),
            "is_complete": False,
        }
    if not customs_matches:
        booking = select_operational_match(booking_matches, shipment)
        container = select_operational_match(container_matches, shipment)
        return {
            "step_label": "Customs Declaration",
            "reason": "A Customs Declaration is the final required workflow record.",
            "create_url": workflow_url("/customs-form", [
                ("shipment_no", shipment_no),
                ("invoice_no", invoice_no),
                ("packing_no", packing_no),
                ("booking_record_no", booking["value"] if booking else ""),
                ("container_record_no", container["value"] if container else ""),
                ("bl_no", bl_no),
            ]),
            "is_complete": False,
        }
    return {
        "step_label": "Workflow Complete",
        "reason": "All required workflow records are linked.",
        "create_url": "",
        "is_complete": True,
    }


def render_workflow_timeline(resolved_direct, resolved_operations, next_step):
    direct_status = {
        resolved["document"]["field"]: resolved["exists"]
        for resolved in resolved_direct
    }
    operational_status = {
        group["operational"]["key"]: bool(group["matches"])
        for group in resolved_operations
    }
    primary_steps = [
        ("Quotation", direct_status.get("quotation_no", False), True),
        ("Proforma Invoice", direct_status.get("pi_no", False), True),
        ("Commercial Invoice", direct_status.get("invoice_no", False), False),
        ("Packing List", direct_status.get("packing_no", False), False),
        ("Shipping Instruction", direct_status.get("si_no", False), False),
        ("Booking Confirmation", operational_status.get("booking_record_no", False), False),
        ("Bill of Lading", direct_status.get("bl_no", False), False),
        ("Customs Declaration", operational_status.get("customs_record_no", False), False),
    ]

    nodes = []
    for label, exists, optional_when_missing in primary_steps:
        if exists:
            state, marker, status = "complete", "✓", "Complete"
        elif optional_when_missing:
            state, marker, status = "optional", "○", "Optional"
        elif not next_step["is_complete"] and next_step["step_label"] == label:
            state, marker, status = "current", "●", "Current"
        else:
            state, marker, status = "pending", "○", "Pending"
        nodes.append(f"""
<div class="timeline-node {state}" data-step="{html_attr(label)}" data-state="{state}">
<span class="timeline-marker">{marker}</span>
<span class="timeline-label">{html_text(label)}</span>
<span class="timeline-state">{status}</span>
</div>""")
    primary_html = '<span class="timeline-connector" aria-hidden="true">→</span>'.join(nodes)

    optional_steps = [
        ("Container Management", operational_status.get("container_record_no", False)),
        ("Certificate of Origin", direct_status.get("co_no", False)),
        ("Inspection Certificate", direct_status.get("inspection_no", False)),
        ("Insurance Certificate", direct_status.get("insurance_no", False)),
        ("Weight Certificate", direct_status.get("weight_no", False)),
    ]
    optional_html = "".join(
        f"""
<div class="optional-node {'linked' if exists else 'optional'}" data-optional-step="{html_attr(label)}" data-state="{'linked' if exists else 'optional'}">
<span class="timeline-marker">{'✓' if exists else '○'}</span>
<span class="timeline-label">{html_text(label)}</span>
<span class="timeline-state">{'Linked' if exists else 'Optional'}</span>
</div>"""
        for label, exists in optional_steps
    )
    return f"""
<section class="workflow-timeline" aria-labelledby="workflow-timeline-title">
<h2 id="workflow-timeline-title">Workflow Timeline</h2>
<div class="timeline-scroll"><div class="timeline-track">{primary_html}</div></div>
<h3>Optional Documents</h3>
<div class="optional-track">{optional_html}</div>
</section>
"""


def render_relationship_node(label, state, records=None, create_url="", root=False):
    records = records or []
    record_html = ""
    for record in records:
        actions = "".join(
            f'<a href="{html_attr(url)}">{html_text(action)}</a>'
            for action, url in record.get("actions", [])
        )
        record_html += f"""
<div class="relationship-record">
<div class="relationship-identifier">{html_text(record.get('identifier', ''))}</div>
<div class="relationship-actions">{actions}</div>
</div>"""
    if not records and create_url:
        record_html = f'<div class="relationship-actions"><a href="{html_attr(create_url)}">Create</a></div>'
    root_class = " root" if root else ""
    return f"""
<div class="relationship-node {state.lower()}{root_class}" data-relationship-node="{html_attr(label)}" data-state="{state.lower()}">
<div class="relationship-node-head"><strong>{html_text(label)}</strong><span class="relationship-badge">{html_text(state)}</span></div>
{record_html}
</div>"""


def render_document_relationship_graph(shipment, resolved_direct, resolved_operations, workflow_progress, next_step):
    direct = {
        resolved["document"]["field"]: resolved
        for resolved in resolved_direct
    }
    operational = {
        group["operational"]["key"]: group
        for group in resolved_operations
    }

    def valid_direct_value(field):
        resolved = direct[field]
        return resolved["value"] if resolved["exists"] else ""

    def direct_node(field, required, create_url):
        resolved = direct[field]
        doc = resolved["document"]
        if resolved["exists"]:
            value = resolved["value"]
            actions = []
            if doc.get("view"):
                actions.append(("View", doc["view"].format(value=value)))
            actions.extend([
                ("PDF", doc["pdf"].format(value=value)),
                ("Edit", doc["edit"].format(value=value)),
            ])
            return render_relationship_node(doc["label"], "Linked", [{"identifier": value, "actions": actions}])
        return render_relationship_node(doc["label"], "Missing" if required else "Optional", create_url=create_url)

    def operational_node(key, required, create_url):
        group = operational[key]
        descriptor = group["operational"]
        records = []
        for match in group["matches"]:
            value = match["value"]
            records.append({
                "identifier": value,
                "actions": [
                    ("View", descriptor["view"].format(value=value)),
                    ("PDF", descriptor["pdf"].format(value=value)),
                    ("Edit", descriptor["edit"].format(value=value)),
                ],
            })
        state = "Linked" if records else ("Missing" if required else "Optional")
        return render_relationship_node(descriptor["label"], state, records, create_url)

    shipment_no = str(shipment.get("shipment_no", "") or "")
    quotation_no = valid_direct_value("quotation_no")
    pi_no = valid_direct_value("pi_no")
    invoice_no = valid_direct_value("invoice_no")
    packing_no = valid_direct_value("packing_no")
    si_no = valid_direct_value("si_no")
    bl_no = valid_direct_value("bl_no")

    booking_matches = operational["booking_record_no"]["matches"]
    container_matches = operational["container_record_no"]["matches"]
    booking = select_operational_match(booking_matches, shipment)
    container = select_operational_match(container_matches, shipment)

    quotation = direct_node("quotation_no", False, "/quotation-form")
    proforma = direct_node("pi_no", False, workflow_url("/proforma-form", [("quotation_no", quotation_no)]))
    invoice = direct_node("invoice_no", True, workflow_url("/invoice", [("pi_no", pi_no)]))
    packing = direct_node("packing_no", True, workflow_url("/packing-page", [("invoice_no", invoice_no)]))
    shipping_instruction = direct_node("si_no", True, workflow_url("/si-form", [("packing_no", packing_no)]))
    booking_node = operational_node("booking_record_no", True, workflow_url("/booking-form", [
        ("shipment_no", shipment_no), ("si_no", si_no), ("packing_no", packing_no),
    ]))
    container_node = operational_node("container_record_no", False, workflow_url("/container-form", [
        ("shipment_no", shipment_no), ("packing_no", packing_no), ("bl_no", bl_no),
    ]))
    bill_of_lading = direct_node("bl_no", True, workflow_url("/bl-form", [("packing_no", packing_no), ("shipment_no", shipment_no)]))
    certificate = direct_node("co_no", False, workflow_url("/co-form", [("bl_no", bl_no), ("shipment_no", shipment_no)]))
    inspection = direct_node("inspection_no", False, workflow_url("/inspection-form", [("bl_no", bl_no), ("shipment_no", shipment_no)]))
    insurance = direct_node("insurance_no", False, workflow_url("/insurance-form", [("bl_no", bl_no), ("shipment_no", shipment_no)]))
    weight = direct_node("weight_no", False, workflow_url("/weight-form", [("bl_no", bl_no), ("shipment_no", shipment_no)]))
    customs_node = operational_node("customs_record_no", True, workflow_url("/customs-form", [
        ("shipment_no", shipment_no),
        ("invoice_no", invoice_no),
        ("packing_no", packing_no),
        ("booking_record_no", booking["value"] if booking else ""),
        ("container_record_no", container["value"] if container else ""),
        ("bl_no", bl_no),
    ]))
    shipment_root = render_relationship_node("Shipment", "Linked", [{"identifier": shipment_no, "actions": []}], root=True)

    return f"""
<section class="document-relationship" aria-labelledby="document-relationship-title" data-required-completed="{workflow_progress['completed']}" data-workflow-complete="{str(next_step['is_complete']).lower()}">
<div class="relationship-heading"><h2 id="document-relationship-title">Document Relationship</h2><span>{workflow_progress['completed']} / {workflow_progress['total']} required linked</span></div>
<div class="relationship-scroll">
<ul class="relationship-tree"><li>{shipment_root}<ul>
<li>{quotation}</li>
<li>{proforma}</li>
<li>{invoice}</li>
<li>{packing}<ul>
<li>{shipping_instruction}</li>
<li>{booking_node}</li>
<li>{container_node}</li>
<li>{bill_of_lading}<ul>
<li>{certificate}</li>
<li>{inspection}</li>
<li>{insurance}</li>
<li>{weight}</li>
<li>{customs_node}</li>
</ul></li>
</ul></li>
</ul></li></ul>
</div>
</section>
"""


def build_record(
    shipment_no, shipment_date, shipment_name, customer, buyer, status, remarks,
    quotation_no, pi_no, invoice_no, packing_no, si_no, bl_no, co_no,
    inspection_no, insurance_no, weight_no,
):
    return {
        "shipment_no": shipment_no,
        "shipment_date": shipment_date,
        "shipment_name": shipment_name,
        "customer": customer,
        "buyer": buyer,
        "status": status,
        "remarks": remarks,
        "quotation_no": quotation_no,
        "pi_no": pi_no,
        "invoice_no": invoice_no,
        "packing_no": packing_no,
        "si_no": si_no,
        "bl_no": bl_no,
        "co_no": co_no,
        "inspection_no": inspection_no,
        "insurance_no": insurance_no,
        "weight_no": weight_no,
    }


def select_html(name, selected, options, placeholder):
    html = [f'<select name="{html_attr(name)}">']
    html.append(f'<option value="">{html_text(placeholder)}</option>')
    for value in options:
        checked = " selected" if value == selected else ""
        html.append(f'<option value="{html_attr(value)}"{checked}>{html_text(value)}</option>')
    html.append("</select>")
    return "".join(html)


def render_form(record, action, title, button_text, show_shipment_no=False, datasets=None, create_mode=False):
    shipment_no_input = ""
    if show_shipment_no:
        shipment_no_input = f'<input type="text" name="shipment_no" value="{html_attr(record.get("shipment_no", ""))}" placeholder="Shipment No" readonly>'

    status_options = "".join(
        f'<option value="{html_attr(option)}"{" selected" if record.get("status") == option else ""}>{html_text(option)}</option>'
        for option in STATUS_OPTIONS
    )

    si_doc = document_by_field("si_no")
    si_options = document_options(si_doc, datasets)
    selected_si = str(record.get("si_no", "") or "")
    if create_mode:
        si_picker = select_html("si_no", selected_si, si_options, "Select Shipping Instruction").replace(
            '<select name="si_no">', '<select id="shipment-si" name="si_no" required onchange="if(this.value)location.href=\'/shipment-form?si_no=\'+encodeURIComponent(this.value)">',
        )
    else:
        si_picker = (
            f'<input type="hidden" name="si_no" value="{html_attr(selected_si)}">'
            f'<select id="shipment-si" disabled><option selected>{html_text(selected_si or "No Shipping Instruction")}</option></select>'
        )

    document_fields = ""
    for doc in DOCUMENTS:
        value = record.get(doc["field"], "")
        readonly_select = (
            f'<input type="hidden" name="{html_attr(doc["field"])}" value="{html_attr(value)}">'
            f'<select disabled aria-label="{html_attr(doc["label"])}"><option selected>{html_text(value or "Not linked")}</option></select>'
            if doc["field"] != "si_no" else
            f'<select disabled aria-label="{html_attr(doc["label"])}"><option selected>{html_text(value or "Not linked")}</option></select>'
        )
        document_fields += f"""
<div>
<label>{html_text(doc["label"])}</label>
{readonly_select}
</div>
"""

    cargo_rows = "".join(
        f"<tr><td>{html_text(item.get('name', ''))}</td>"
        f"<td>{html_text(item.get('quantity', ''))}</td>"
        f"<td>{html_text(item.get('hs_code', ''))}</td>"
        f"<td>{html_text(item.get('carton', ''))}</td>"
        f"<td>{html_text(item.get('net_weight', ''))}</td>"
        f"<td>{html_text(item.get('gross_weight', ''))}</td></tr>"
        for item in record.get("items", [])
        if isinstance(item, dict)
    ) or '<tr><td colspan="6">No cargo snapshot available.</td></tr>'

    html = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Shipment Management</title>
<style>
body{font-family:Arial,sans-serif;background:#f3f4f6;padding:40px;color:#111827;}
.container{max-width:1080px;margin:auto;background:white;padding:35px;border-radius:16px;box-shadow:0 12px 35px rgba(15,23,42,.08);}
h1{text-align:center;font-size:46px;margin:8px 0 10px;}
.sub{text-align:center;color:#6B7280;margin-bottom:35px;}
.nav-row{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:25px;}
.card{border:1px solid #E5E7EB;border-radius:16px;padding:25px;margin-bottom:25px;background:#fff;}
.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px;}
label{display:block;font-weight:bold;margin-bottom:7px;color:#374151;}
input,select,textarea{width:100%;padding:14px;border:1px solid #D1D5DB;border-radius:10px;font-size:16px;box-sizing:border-box;background:white;}
textarea{min-height:100px;resize:vertical;}
button{padding:15px 18px;background:#111827;color:white;border:none;border-radius:12px;font-size:16px;cursor:pointer;}
.small{min-width:170px;}
.full{width:100%;margin-top:10px;font-size:18px;}
@media(max-width:820px){body{padding:18px}.grid{grid-template-columns:1fr}h1{font-size:34px}}
</style>
</head>
<body>
<div class="container">
<div class="nav-row">
<a href="/"><button class="small" type="button">Dashboard</button></a>
<a href="/shipment-list"><button class="small" type="button">Shipment List</button></a>
</div>
<h1>__TITLE__</h1>
<p class="sub">Group trade documents under one shipment project without duplicating document data</p>

<form action="__ACTION__" method="post">
<div class="card"><h2>Shipping Instruction <span aria-hidden="true">*</span></h2>__SI_PICKER__</div>
<div class="card">
<h2>Shipment Information</h2>
<div class="grid">
__SHIPMENT_NO_INPUT__
<div><label>Shipment Date</label><input type="date" name="shipment_date" value="__SHIPMENT_DATE__"></div>
<div><label>Shipment Name</label><input type="text" name="shipment_name" value="__SHIPMENT_NAME__" placeholder="Shipment Name"></div>
<div><label>Customer</label><input type="text" name="customer" value="__CUSTOMER__" placeholder="Customer"></div>
<div><label>Buyer</label><input type="text" name="buyer" value="__BUYER__" placeholder="Buyer"></div>
<div><label>Status</label><select name="status">__STATUS_OPTIONS__</select></div>
</div>
<br>
<label>Remarks</label>
<textarea name="remarks" placeholder="Remarks">__REMARKS__</textarea>
</div>

<div class="card">
<h2>Party Snapshot</h2>
<div class="grid">
<div><label>Shipper</label><input type="text" name="shipper" value="__SHIPPER__" readonly></div>
<div><label>Shipper Address</label><input type="text" name="shipper_address" value="__SHIPPER_ADDRESS__" readonly></div>
<div><label>Shipper Email</label><input type="email" name="shipper_email" value="__SHIPPER_EMAIL__" readonly></div>
<div><label>Shipper Phone</label><input type="text" name="shipper_phone" value="__SHIPPER_PHONE__" readonly></div>
<div><label>Consignee</label><input type="text" name="consignee" value="__CONSIGNEE__" readonly></div>
<div><label>Consignee Address</label><input type="text" name="consignee_address" value="__CONSIGNEE_ADDRESS__" readonly></div>
<div><label>Consignee Email</label><input type="email" name="consignee_email" value="__CONSIGNEE_EMAIL__" readonly></div>
</div>
</div>

<div class="card">
<h2>Cargo Snapshot</h2>
<div style="overflow-x:auto">
<table style="width:100%;border-collapse:collapse">
<thead><tr><th>Item</th><th>Quantity</th><th>HS Code</th><th>Carton</th><th>Net Weight</th><th>Gross Weight</th></tr></thead>
<tbody>__CARGO_ROWS__</tbody>
</table>
</div>
</div>

<div class="card">
<h2>Document References</h2>
<div class="grid">
__DOCUMENT_FIELDS__
</div>
</div>

<button class="full" type="submit">__BUTTON_TEXT__</button>
</form>
</div>
</body>
</html>
"""
    replacements = {
        "__TITLE__": html_text(title),
        "__ACTION__": html_attr(action),
        "__SI_PICKER__": si_picker,
        "__SHIPMENT_NO_INPUT__": shipment_no_input,
        "__SHIPMENT_DATE__": html_attr(record.get("shipment_date", "")),
        "__SHIPMENT_NAME__": html_attr(record.get("shipment_name", "")),
        "__CUSTOMER__": html_attr(record.get("customer", "")),
        "__BUYER__": html_attr(record.get("buyer", "")),
        "__SHIPPER__": html_attr(record.get("shipper", "")),
        "__SHIPPER_ADDRESS__": html_attr(record.get("shipper_address", "")),
        "__SHIPPER_EMAIL__": html_attr(record.get("shipper_email", "")),
        "__SHIPPER_PHONE__": html_attr(record.get("shipper_phone", "")),
        "__CONSIGNEE__": html_attr(record.get("consignee", "")),
        "__CONSIGNEE_ADDRESS__": html_attr(record.get("consignee_address", "")),
        "__CONSIGNEE_EMAIL__": html_attr(record.get("consignee_email", "")),
        "__CARGO_ROWS__": cargo_rows,
        "__STATUS_OPTIONS__": status_options,
        "__REMARKS__": html_text(record.get("remarks", "")),
        "__DOCUMENT_FIELDS__": document_fields,
        "__BUTTON_TEXT__": html_text(button_text),
    }
    for key, value in replacements.items():
        html = html.replace(key, value)
    return HTMLResponse(html)


@router.get("/shipment-list", response_class=HTMLResponse)
def shipment_list(request: Request, search: str = ""):
    shipments = sorted(load_shipments(_account_id(request)), key=lambda record: record.get("shipment_no", ""), reverse=True)
    if search:
        term = search.lower()
        shipments = [
            record for record in shipments
            if term in str(record.get("shipment_no", "")).lower()
            or term in str(record.get("shipment_name", "")).lower()
            or term in str(record.get("customer", "")).lower()
            or term in str(record.get("buyer", "")).lower()
            or term in str(record.get("status", "")).lower()
        ]

    rows = ""
    for record in shipments:
        shipment_no = record.get("shipment_no", "")
        progress = f"{linked_count(record)} / {len(DOCUMENTS)}"
        rows += f"""
<tr>
<td>{html_text(shipment_no)}</td>
<td>{html_text(record.get('shipment_name', ''))}</td>
<td>{html_text(record.get('buyer', '') or record.get('customer', ''))}</td>
<td><span class="pill">{html_text(record.get('status', ''))}</span></td>
<td>{html_text(progress)}</td>
<td><a class="link" href="/shipment/{html_attr(shipment_no)}">View</a></td>
<td><a class="link" href="/shipment/{html_attr(shipment_no)}/package">Package</a></td>
<td><a class="link" href="/edit-shipment/{html_attr(shipment_no)}">Edit</a></td>
<td><a class="danger" href="/delete-shipment/{html_attr(shipment_no)}">Delete</a></td>
</tr>
"""

    html = f"""
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Shipments</title>
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
.pill{{background:#E5E7EB;color:#111827;padding:7px 10px;border-radius:999px;font-weight:bold;font-size:13px;}}
</style>
</head>
<body>
<div class="container">
<h1>Shipment Management</h1>
<p class="sub">Track each shipment project and its linked trade documents</p>
<div class="toolbar">
<div class="nav">
<a class="btn" href="/">Dashboard</a>
<a class="btn" href="/shipment-form">+ New Shipment</a>
</div>
<form class="search" action="/shipment-list" method="get">
<input type="text" name="search" value="{html_attr(search)}" placeholder="Search shipment, customer, buyer, status">
<button type="submit">Search</button>
<a class="btn reset" href="/shipment-list">Reset</a>
</form>
</div>
<div class="count">Total Shipments: {len(shipments)}</div>
<div class="table-wrap">
<table>
<thead>
<tr>
<th>Shipment No</th><th>Shipment Name</th><th>Buyer / Customer</th><th>Status</th><th>Linked Direct Documents</th><th>View</th><th>Package</th><th>Edit</th><th>Delete</th>
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


@router.get("/shipment-form", response_class=HTMLResponse)
def shipment_form(request: Request, bl_no: str = "", si_no: str = ""):
    account_id = _account_id(request)
    datasets = load_workflow_datasets(account_id)
    record = blank_shipment()
    record["shipment_no"] = next_shipment_no(load_shipment_records())
    instruction = _first_record(datasets.get("shipping_instructions.json", []), "si_no", si_no)
    if instruction:
        record["si_no"] = instruction.get("si_no", "")
        record = resolve_shipment_snapshot(
            record, account_id, instruction=instruction, preserve_empty=False,
        )
    elif bl_no:
        record["bl_no"] = bl_no
        record = resolve_shipment_snapshot(record, account_id, preserve_empty=False)
    return render_form(
        record, "/shipment", "New Shipment", "Save Shipment",
        show_shipment_no=True, datasets=datasets, create_mode=True,
    )


@router.get("/document-package", response_class=HTMLResponse)
def document_package(request: Request, shipment_no: str = ""):
    return render_document_package_page(request, str(shipment_no or "").strip())


@router.get("/shipment/{shipment_no}/package", response_class=HTMLResponse)
def shipment_document_package(shipment_no: str, request: Request):
    return render_document_package_page(request, shipment_no)


def tracking_suggestions(shipment, account_id):
    """Suggest only missing values from owned operational records; never persist automatically."""
    datasets = load_workflow_datasets(account_id)
    booking = select_operational_match(
        reverse_records_for(shipment.get("shipment_no", ""), OPERATIONAL_RECORDS[0], datasets), shipment,
    )
    containers = reverse_records_for(shipment.get("shipment_no", ""), OPERATIONAL_RECORDS[1], datasets)
    container = select_operational_match(containers, shipment)
    bill = _first_record(datasets.get("bills_of_lading.json", []), "bl_no", shipment.get("bl_no", ""))
    booking_record = (booking or {}).get("record", {})
    container_record = (container or {}).get("record", {})
    return {
        "container_no": container_record.get("container_no", ""),
        "seal_no": container_record.get("seal_no", ""),
        "container_type": container_record.get("container_type", ""),
        "etd": booking_record.get("etd") or bill.get("etd", ""),
        "eta": booking_record.get("eta") or bill.get("eta", ""),
    }


@router.get("/shipment/{shipment_no}/tracking", response_class=HTMLResponse)
def edit_shipment_tracking(shipment_no: str, request: Request):
    account_id = _account_id(request)
    owned = find_shipment(shipment_no, account_id)
    if not owned:
        raise HTTPException(status_code=404, detail="Shipment not found")
    record = public_shipment(owned)
    suggestions = tracking_suggestions(record, account_id)
    values = {field: record.get(field, "") or suggestions.get(field, "") for field in TRACKING_FIELDS}
    statuses = "".join(
        f'<option value="{html_attr(status)}"{" selected" if status == record.get("status") else ""}>{html_text(status)}</option>'
        for status in TRACKING_STATUS_OPTIONS
    )
    fields = "".join(
        f'<div><label>{html_text(label)}</label><input aria-label="{html_attr(label)}" type="{input_type}" name="{name}" value="{html_attr(values[name])}"></div>'
        for name, label, input_type in (
            ("container_no", "Container No", "text"), ("seal_no", "Seal No", "text"),
            ("container_type", "Container Type", "text"), ("etd", "ETD", "date"),
            ("eta", "ETA", "date"), ("actual_departure", "Actual Departure", "date"),
            ("actual_arrival", "Actual Arrival", "date"),
        )
    )
    return HTMLResponse(f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Edit Shipment Tracking</title><style>*{{box-sizing:border-box}}body{{margin:0;padding:40px;background:#F3F4F6;color:#111827;font-family:Arial,sans-serif}}main{{width:min(850px,94%);margin:auto;background:#fff;padding:30px;border-radius:16px}}.nav{{display:flex;gap:10px;margin-bottom:22px}}a,button{{padding:12px 16px;border:0;border-radius:9px;background:#111827;color:#fff;text-decoration:none;font-weight:700}}h1{{margin:0 0 8px}}.sub{{color:#64748B;margin-bottom:24px}}.grid{{display:grid;grid-template-columns:1fr 1fr;gap:15px}}label{{display:block;font-weight:700;margin-bottom:7px}}input,select,textarea{{width:100%;padding:12px;border:1px solid #CBD5E1;border-radius:9px;font:inherit}}textarea{{min-height:120px}}.memo{{margin-top:15px}}button{{width:100%;margin-top:20px;font-size:16px;cursor:pointer}}@media(max-width:700px){{body{{padding:18px}}.grid{{grid-template-columns:1fr}}}}</style></head><body><main><div class="nav"><a href="/shipment/{html_attr(shipment_no)}">Back to Shipment</a></div><h1>Edit Tracking</h1><p class="sub">{html_text(shipment_no)} · suggested empty values remain fully editable.</p><form action="/shipment/{html_attr(shipment_no)}/tracking" method="post"><div class="grid"><div><label>Shipment Status</label><select aria-label="Shipment Status" name="status" required>{statuses}</select></div>{fields}</div><div class="memo"><label>Tracking Memo</label><textarea aria-label="Tracking Memo" name="tracking_memo">{html_text(values["tracking_memo"])}</textarea></div><button type="submit">Save Tracking</button></form></main></body></html>''')


@router.post("/shipment/{shipment_no}/tracking")
def update_shipment_tracking(
    shipment_no: str, request: Request, status: str = Form("Draft"),
    container_no: str = Form(""), seal_no: str = Form(""), container_type: str = Form(""),
    etd: str = Form(""), eta: str = Form(""), actual_departure: str = Form(""),
    actual_arrival: str = Form(""), tracking_memo: str = Form(""),
):
    account_id = _account_id(request)
    if find_shipment(shipment_no, account_id) is None:
        raise HTTPException(status_code=404, detail="Shipment not found")
    status = require_allowed_value("Shipment status", status, TRACKING_STATUS_OPTIONS)
    submitted = {
        "container_no": container_no, "seal_no": seal_no, "container_type": container_type,
        "etd": etd, "eta": eta, "actual_departure": actual_departure,
        "actual_arrival": actual_arrival, "tracking_memo": tracking_memo,
    }
    def replace(records):
        for record in records:
            if record.get("shipment_no") == shipment_no and str(record.get("account_id", "") or "").strip() == account_id:
                record["status"] = status
                record.update(submitted)
                return
        raise HTTPException(status_code=404, detail="Shipment not found")
    locked_json_mutation(SHIPMENT_FILE, [], replace, list)
    return RedirectResponse(f"/shipment/{quote(shipment_no, safe='')}", status_code=303)


@router.get("/shipment/{shipment_no}/package.zip")
def download_document_package(shipment_no: str, request: Request):
    account_id = _account_id(request)
    owned = find_shipment(shipment_no, account_id)
    if not owned:
        raise HTTPException(status_code=404, detail="Shipment not found")
    package = resolve_document_package(public_shipment(owned), load_workflow_datasets(account_id))
    from app import invoice as invoice_module
    from app import packing as packing_module
    from app import shipping_instruction as si_module
    from app import booking_confirmation as booking_module
    from app import bill_of_lading as bl_module
    from app import certificate_of_origin as co_module
    pdf_handlers = {
        "invoice_no": invoice_module.invoice_pdf,
        "packing_no": packing_module.packing_list_pdf,
        "si_no": si_module.si_pdf,
        "booking_record_no": booking_module.booking_pdf,
        "bl_no": bl_module.bl_pdf,
        "co_no": co_module.co_pdf,
    }
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for item in package:
            if not item["exists"]:
                continue
            value = item["value"]
            response = pdf_handlers[item["field"]](value, request)
            archive.writestr(f"{value}.pdf", response.body)
    return Response(
        buffer.getvalue(), media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{html_attr(shipment_no)}-document-package.zip"'},
    )


def shipment_success_response(shipment_no, si_no, packing_no):
    booking_url = workflow_url("/booking-form", [
        ("shipment_no", shipment_no), ("si_no", si_no), ("packing_no", packing_no),
    ])
    return HTMLResponse(f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Shipment Saved</title><style>*{{box-sizing:border-box}}body{{margin:0;background:#F3F4F6;color:#111827;font-family:Arial,sans-serif}}main{{min-height:100vh;display:grid;place-items:center;padding:24px}}.card{{width:min(580px,100%);padding:34px;border:1px solid #E5E7EB;border-radius:18px;background:#fff;text-align:center;box-shadow:0 14px 34px rgba(15,23,42,.09)}}h1{{margin:0 0 10px}}p{{color:#475569}}.actions{{display:flex;justify-content:center;gap:10px;flex-wrap:wrap;margin-top:20px}}a{{display:inline-flex;min-height:46px;align-items:center;padding:11px 16px;border-radius:10px;background:#E5E7EB;color:#111827;text-decoration:none;font-weight:800}}a.primary{{background:#111827;color:#fff}}</style></head><body><main><section class="card"><h1>Shipment Saved</h1><p>✓ {html_text(shipment_no)} was created successfully.</p><div class="actions"><a class="primary" href="{html_attr(booking_url)}">Continue to Booking →</a><a href="/shipment/{html_attr(shipment_no)}">View Shipment</a></div></section></main></body></html>""")


@router.post("/shipment")
def save_shipment(
    request: Request,
    shipment_date: str = Form(""),
    shipment_name: str = Form(""),
    customer: str = Form(""),
    buyer: str = Form(""),
    status: str = Form("Draft"),
    remarks: str = Form(""),
    quotation_no: str = Form(""),
    pi_no: str = Form(""),
    invoice_no: str = Form(""),
    packing_no: str = Form(""),
    si_no: str = Form(""),
    bl_no: str = Form(""),
    co_no: str = Form(""),
    inspection_no: str = Form(""),
    insurance_no: str = Form(""),
    weight_no: str = Form(""),
    shipper: Annotated[Optional[str], Form()] = None,
    shipper_address: Annotated[Optional[str], Form()] = None,
    shipper_email: Annotated[Optional[str], Form()] = None,
    shipper_phone: Annotated[Optional[str], Form()] = None,
    consignee: Annotated[Optional[str], Form()] = None,
    consignee_address: Annotated[Optional[str], Form()] = None,
    consignee_email: Annotated[Optional[str], Form()] = None,
):
    account_id = _account_id(request)
    datasets = load_workflow_datasets(account_id)
    require_existing_reference(
        "Shipping Instruction", si_no,
        document_records(document_by_field("si_no"), datasets), "si_no", required=True,
    )
    saved = {}
    def add_shipment(shipments):
        record = build_record(
        next_identifier(shipments, "shipment_no", "SHP"), shipment_date, shipment_name, customer, buyer,
        status, remarks, quotation_no, pi_no, invoice_no, packing_no, si_no,
        bl_no, co_no, inspection_no, insurance_no, weight_no,
        )
        set_submitted_snapshot_fields(record, {
            "shipper": shipper, "shipper_address": shipper_address,
            "shipper_email": shipper_email, "shipper_phone": shipper_phone,
            "consignee": consignee, "consignee_address": consignee_address,
            "consignee_email": consignee_email,
        })
        record = resolve_shipment_snapshot(record, account_id)
        validate_shipment_values(record, account_id, datasets)
        record["account_id"] = account_id
        shipments.append(record)
        saved.update({"shipment_no": record["shipment_no"], "si_no": si_no, "packing_no": packing_no})
    locked_json_mutation(SHIPMENT_FILE, [], add_shipment, list)
    return shipment_success_response(saved["shipment_no"], saved["si_no"], saved["packing_no"])


@router.get("/shipment/{shipment_no}", response_class=HTMLResponse)
def shipment_detail(shipment_no: str, request: Request):
    account_id = _account_id(request)
    owned_shipment = find_shipment(shipment_no, account_id)
    if not owned_shipment:
        raise HTTPException(status_code=404, detail="Shipment not found")
    shipment = public_shipment(owned_shipment)
    tracking_rows = "".join(
        f'<div><div class="label">{html_text(label)}</div><div class="value">{html_text(shipment.get(field, "") or "-")}</div></div>'
        for field, label in (
            ("container_no", "Container No"), ("seal_no", "Seal No"),
            ("container_type", "Container Type"), ("etd", "ETD"), ("eta", "ETA"),
            ("actual_departure", "Actual Departure"), ("actual_arrival", "Actual Arrival"),
        )
    )

    workflow_datasets = load_workflow_datasets(account_id)
    from app.document_email import shipment_email_history
    email_history = list(reversed(shipment_email_history(shipment_no, account_id)))
    email_history_rows = "".join(
        f'<tr><td>{html_text(item.get("sent_at", ""))}</td><td>{html_text(item.get("document_no", ""))}</td><td>{html_text(item.get("recipient", ""))}</td><td>{html_text(item.get("subject", ""))}</td><td>{html_text(item.get("status", ""))}</td></tr>'
        for item in email_history
    ) or '<tr><td colspan="5">No email delivery history.</td></tr>'
    cards = ""
    resolved_direct = resolve_direct_documents(shipment, workflow_datasets)
    for resolved in resolved_direct:
        doc = resolved["document"]
        value = resolved["value"]
        exists = resolved["exists"]
        status = "Linked" if exists else "Missing"
        badge_class = "linked" if exists else "missing"
        actions = ""
        if exists:
            view_action = ""
            if doc.get("view"):
                view_action = f'<a href="{html_attr(doc["view"].format(value=value))}">View</a>'
            actions = f"""
<div class="actions">
{view_action}
<a href="{html_attr(doc['pdf'].format(value=value))}">PDF</a>
<a href="{html_attr(doc['edit'].format(value=value))}">Edit</a>
<a href="/send-email/{EMAIL_DOCUMENT_TYPES.get(doc['field'], '')}/{quote(value, safe='')}">Send Email</a>
</div>
"""
        cards += f"""
<div class="doc-card">
<div class="doc-title">{html_text(doc["label"])}</div>
<div class="doc-no">{html_text(value if exists else "-")}</div>
<span class="badge {badge_class}">{status}</span>
{actions}
</div>
"""

    operational_cards = ""
    resolved_operations = resolve_operational_records(shipment_no, workflow_datasets)
    for group in resolved_operations:
        operational = group["operational"]
        matches = group["matches"]
        if matches:
            record_rows = ""
            for match in matches:
                value = match["value"]
                record_rows += f"""
<div class="operational-record">
<div class="doc-no">{html_text(value)}</div>
<span class="badge linked">Linked</span>
<div class="actions">
<a href="{html_attr(operational['view'].format(value=value))}">View</a>
<a href="{html_attr(operational['pdf'].format(value=value))}">PDF</a>
<a href="{html_attr(operational['edit'].format(value=value))}">Edit</a>
{f'<a href="/send-email/{EMAIL_DOCUMENT_TYPES[operational["field"]]}/{quote(value, safe="")}">Send Email</a>' if operational.get("field") in EMAIL_DOCUMENT_TYPES else ''}
</div>
</div>
"""
        else:
            record_rows = f"""
<div class="doc-no">-</div>
<span class="badge missing">Missing</span>
<div class="actions">
<a href="{html_attr(operational['create'].format(shipment_no=shipment_no))}">Create</a>
</div>
"""
        operational_cards += f"""
<div class="doc-card operational-card">
<div class="doc-title">{html_text(operational['label'])}</div>
{record_rows}
</div>
"""

    linked_operations = sum(len(group["matches"]) for group in resolved_operations)
    workflow_progress = required_workflow_progress(shipment, resolved_direct, resolved_operations)
    next_step = next_step_for_shipment(shipment, resolved_direct, resolved_operations)
    health_score = shipment_health_score(
        shipment, resolved_direct, resolved_operations, workflow_progress, next_step
    )
    health_colors = {
        "Excellent": "#166534",
        "Good": "#1D4ED8",
        "Attention": "#92400E",
        "Critical": "#991B1B",
    }
    health_color = health_colors[health_score["label"]]
    workflow_timeline = render_workflow_timeline(resolved_direct, resolved_operations, next_step)
    relationship_graph = render_document_relationship_graph(
        shipment, resolved_direct, resolved_operations, workflow_progress, next_step
    )
    if next_step["is_complete"]:
        next_step_card = f"""
<section class="next-step complete">
<div class="next-kicker">Workflow Status</div>
<h2><span class="complete-check" aria-hidden="true">✓</span>{html_text(next_step['step_label'])}</h2>
<p>{html_text(next_step['reason'])}</p>
</section>
"""
    else:
        next_step_card = f"""
<section class="next-step">
<div>
<div class="next-kicker">Next Step</div>
<h2>{html_text(next_step['step_label'])}</h2>
<p>{html_text(next_step['reason'])}</p>
</div>
<a class="next-action" href="{html_attr(next_step['create_url'])}">Create</a>
</section>
"""

    html = f"""
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html_text(shipment_no)}</title>
<style>
body{{font-family:Arial,sans-serif;background:#f3f4f6;padding:40px;color:#111827;}}
.container{{width:94%;max-width:1180px;margin:auto;}}
.nav-row{{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:25px;}}
button,.btn{{display:inline-block;padding:13px 18px;background:#111827;color:white;border:none;border-radius:12px;text-decoration:none;font-size:15px;cursor:pointer;}}
.header{{background:#111827;color:white;border-radius:16px;padding:30px;box-shadow:0 12px 35px rgba(15,23,42,.12);}}
.header h1{{font-size:42px;margin:0 0 8px 0;}}
.meta{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px;margin-top:22px;}}
.meta div{{background:#1F2937;border-radius:12px;padding:14px;}}
.label{{color:#CBD5E1;font-size:13px;margin-bottom:5px;}}
.value{{font-weight:bold;}}
.workflow-progress-value{{display:flex;align-items:center;justify-content:space-between;gap:10px;font-weight:bold;}}
.progress-track{{display:block;height:6px;margin-top:9px;background:#374151;border-radius:999px;overflow:hidden;}}
.progress-fill{{display:block;height:100%;border-radius:999px;background:#3B82F6;}}
.progress-fill.complete{{background:#22C55E;}}
.health-score-line{{display:flex;align-items:baseline;justify-content:space-between;gap:10px;font-weight:bold;}}
.health-score-number{{font-size:19px;}}
.health-score-label{{font-size:13px;}}
.health-score-detail{{display:flex;gap:10px;flex-wrap:wrap;margin-top:7px;color:#CBD5E1;font-size:12px;}}
.health-track{{display:block;height:5px;margin-top:9px;background:#374151;border-radius:999px;overflow:hidden;}}
.health-fill{{display:block;height:100%;border-radius:999px;}}
.remarks{{margin-top:14px;background:#1F2937;border-radius:12px;padding:14px;}}
.tracking{{margin-top:24px;background:white;border:1px solid #E5E7EB;border-radius:16px;padding:24px;box-shadow:0 10px 25px rgba(15,23,42,.07)}}
.tracking-head{{display:flex;align-items:center;justify-content:space-between;gap:15px;margin-bottom:18px}}.tracking-head h2{{margin:0}}
.tracking-grid{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}}.tracking-grid>div{{padding:14px;border:1px solid #E5E7EB;border-radius:11px;background:#F8FAFC}}.tracking .label{{color:#64748B}}.tracking-status{{display:inline-flex;padding:8px 13px;border-radius:999px;background:#DBEAFE;color:#1E3A8A;font-size:17px;font-weight:800}}.tracking-memo{{margin-top:13px;padding:14px;border-radius:11px;background:#F8FAFC;white-space:pre-wrap}}
.next-step{{display:flex;align-items:center;justify-content:space-between;gap:22px;margin-top:24px;padding:24px 26px;background:#111827;color:white;border-radius:16px;box-shadow:0 12px 30px rgba(15,23,42,.14);}}
.next-step h2{{font-size:28px;margin:5px 0 8px;}}
.next-step p{{color:#D1D5DB;margin:0;line-height:1.5;}}
.next-kicker{{color:#93C5FD;text-transform:uppercase;letter-spacing:.1em;font-size:12px;font-weight:bold;}}
.next-action{{display:inline-block;min-width:120px;text-align:center;background:white;color:#111827;text-decoration:none;padding:13px 18px;border-radius:10px;font-weight:bold;}}
.next-step.complete{{display:block;background:#111827;border:1px solid #374151;}}
.next-step.complete .next-kicker{{color:#9CA3AF;}}
.next-step.complete h2{{display:flex;align-items:center;gap:10px;}}
.next-step.complete p{{color:#D1D5DB;}}
.complete-check{{display:inline-flex;align-items:center;justify-content:center;width:25px;height:25px;border-radius:999px;background:#DCFCE7;color:#166534;font-size:16px;line-height:1;}}
.workflow-timeline{{margin-top:24px;background:white;border:1px solid #E5E7EB;border-radius:16px;padding:24px;box-shadow:0 10px 25px rgba(15,23,42,.07);}}
.workflow-timeline h2{{font-size:26px;margin:0 0 20px;}}
.workflow-timeline h3{{font-size:17px;margin:24px 0 13px;color:#374151;}}
.timeline-scroll{{max-width:100%;overflow-x:auto;padding:2px 2px 10px;}}
.timeline-track{{display:flex;align-items:center;min-width:max-content;}}
.timeline-node{{display:grid;grid-template-columns:auto 1fr;column-gap:9px;row-gap:4px;align-items:center;width:172px;min-height:92px;padding:15px;border:1px solid #D1D5DB;border-radius:13px;background:#F9FAFB;}}
.timeline-marker{{grid-row:1/3;font-size:18px;font-weight:bold;}}
.timeline-label{{font-size:14px;font-weight:bold;line-height:1.25;}}
.timeline-state{{font-size:12px;color:#6B7280;}}
.timeline-connector{{padding:0 9px;color:#9CA3AF;font-size:19px;}}
.timeline-node.complete{{background:#F8FAFC;border-color:#BBF7D0;}}
.timeline-node.complete .timeline-marker{{color:#166534;}}
.timeline-node.current{{background:#111827;border-color:#2563EB;color:white;box-shadow:0 0 0 3px #DBEAFE;}}
.timeline-node.current .timeline-marker{{color:#60A5FA;}}
.timeline-node.current .timeline-state{{color:#BFDBFE;}}
.timeline-node.pending,.timeline-node.optional{{color:#6B7280;background:#F3F4F6;}}
.optional-track{{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:11px;}}
.optional-node{{display:grid;grid-template-columns:auto 1fr;column-gap:8px;row-gap:3px;align-items:center;min-width:0;padding:13px;border:1px solid #D1D5DB;border-radius:12px;background:#F9FAFB;}}
.optional-node .timeline-marker{{grid-row:1/3;}}
.optional-node.linked{{border-color:#BBF7D0;}}
.optional-node.linked .timeline-marker{{color:#166534;}}
.optional-node.optional{{color:#6B7280;background:#F3F4F6;}}
.document-relationship{{margin-top:24px;background:white;border:1px solid #E5E7EB;border-radius:16px;padding:24px;box-shadow:0 10px 25px rgba(15,23,42,.07);}}
.relationship-heading{{display:flex;align-items:center;justify-content:space-between;gap:16px;margin-bottom:20px;}}
.relationship-heading h2{{font-size:26px;margin:0;}}
.relationship-heading span{{color:#6B7280;font-size:13px;font-weight:bold;}}
.relationship-scroll{{max-width:100%;overflow-x:auto;padding:2px 4px 14px;}}
.relationship-tree,.relationship-tree ul{{list-style:none;margin:0;padding-left:28px;position:relative;}}
.relationship-tree{{padding-left:0;min-width:1080px;}}
.relationship-tree ul::before{{content:"";position:absolute;left:10px;top:0;bottom:22px;border-left:1px solid #CBD5E1;}}
.relationship-tree li{{position:relative;padding:9px 0 0 28px;}}
.relationship-tree>li{{padding-left:0;}}
.relationship-tree li::before{{content:"";position:absolute;left:10px;top:34px;width:18px;border-top:1px solid #CBD5E1;}}
.relationship-tree>li::before{{display:none;}}
.relationship-node{{width:250px;background:white;border:1px solid #D1D5DB;border-left:4px solid #DC2626;border-radius:12px;padding:13px;}}
.relationship-node.linked{{border-left-color:#16A34A;}}
.relationship-node.optional{{border-left-color:#9CA3AF;background:#F9FAFB;}}
.relationship-node.root{{background:#111827;color:white;border-color:#111827;width:280px;}}
.relationship-node-head{{display:flex;align-items:flex-start;justify-content:space-between;gap:9px;}}
.relationship-badge{{flex:none;padding:5px 8px;border-radius:999px;background:#FEE2E2;color:#991B1B;font-size:11px;font-weight:bold;}}
.relationship-node.linked .relationship-badge{{background:#DCFCE7;color:#166534;}}
.relationship-node.optional .relationship-badge{{background:#E5E7EB;color:#4B5563;}}
.relationship-node.root .relationship-badge{{background:#374151;color:white;}}
.relationship-record{{border-top:1px solid #E5E7EB;margin-top:10px;padding-top:9px;}}
.relationship-node.root .relationship-record{{border-top-color:#374151;}}
.relationship-identifier{{font-size:13px;font-weight:bold;word-break:break-word;}}
.relationship-actions{{display:flex;gap:6px;flex-wrap:wrap;margin-top:8px;}}
.relationship-actions a{{background:#111827;color:white;text-decoration:none;padding:6px 8px;border-radius:7px;font-size:11px;font-weight:bold;}}
.relationship-node.root .relationship-actions a{{background:white;color:#111827;}}
.docs{{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:16px;margin-top:24px;}}
.doc-card{{background:white;border:1px solid #E5E7EB;border-radius:16px;padding:22px;box-shadow:0 10px 25px rgba(15,23,42,.07);}}
.doc-title{{font-size:17px;font-weight:bold;margin-bottom:14px;}}
.doc-no{{font-size:24px;font-weight:bold;margin-bottom:12px;color:#111827;}}
.badge{{display:inline-block;padding:7px 10px;border-radius:999px;font-size:13px;font-weight:bold;}}
.linked{{background:#DCFCE7;color:#166534;}}
.missing{{background:#FEE2E2;color:#991B1B;}}
.actions{{display:flex;gap:10px;margin-top:16px;}}
.actions a{{flex:1;text-align:center;background:#111827;color:white;text-decoration:none;padding:10px;border-radius:10px;font-weight:bold;}}
.section-title{{margin:34px 0 0;font-size:26px;}}
.operational-record+.operational-record{{border-top:1px solid #E5E7EB;margin-top:18px;padding-top:18px;}}
.email-history{{margin-top:24px;background:#fff;border:1px solid #E5E7EB;border-radius:16px;padding:24px;overflow:auto}}.email-history table{{width:100%;border-collapse:collapse}}.email-history th,.email-history td{{padding:10px;border-bottom:1px solid #E5E7EB;text-align:left;font-size:13px}}
@media(max-width:780px){{body{{padding:18px}}.meta{{grid-template-columns:1fr}}.header h1{{font-size:32px}}.next-step{{align-items:flex-start;flex-direction:column}}.optional-track{{grid-template-columns:1fr 1fr}}}}
@media(max-width:780px){{.tracking-grid{{grid-template-columns:1fr}}.tracking-head{{align-items:flex-start;flex-direction:column}}}}
@media(max-width:480px){{.optional-track{{grid-template-columns:1fr}}}}
@media(max-width:780px){{.relationship-heading{{align-items:flex-start;flex-direction:column}}.relationship-tree{{min-width:0}}.relationship-tree,.relationship-tree ul{{padding-left:20px}}.relationship-tree ul::before{{left:6px}}.relationship-tree li{{padding-left:20px}}.relationship-tree li::before{{left:6px;width:14px}}.relationship-node,.relationship-node.root{{width:100%;max-width:100%}}}}
</style>
</head>
<body>
<div class="container">
<div class="nav-row">
<a class="btn" href="/">Dashboard</a>
<a class="btn" href="/shipment-list">Shipment List</a>
<a class="btn" href="/edit-shipment/{html_attr(shipment_no)}">Edit Shipment</a>
<a class="btn" href="/shipment-pdf/{html_attr(shipment_no)}">PDF</a>
<a class="btn" href="/shipment/{html_attr(shipment_no)}/package">Document Package</a>
<a class="btn" href="/send-email/document-package/{html_attr(shipment_no)}">Send Package</a>
</div>
<div class="header">
<h1>{html_text(shipment.get("shipment_no", ""))}</h1>
<div>{html_text(shipment.get("shipment_name", ""))}</div>
<div class="meta">
<div><div class="label">Date</div><div class="value">{html_text(shipment.get("shipment_date", ""))}</div></div>
<div><div class="label">Customer</div><div class="value">{html_text(shipment.get("customer", ""))}</div></div>
<div><div class="label">Buyer</div><div class="value">{html_text(shipment.get("buyer", ""))}</div></div>
<div><div class="label">Status</div><div class="value">{html_text(shipment.get("status", ""))}</div></div>
<div><div class="label">Linked Direct Documents</div><div class="value">{linked_count(shipment, workflow_datasets)} / {len(DOCUMENTS)}</div></div>
<div><div class="label">Operational Records</div><div class="value">{linked_operations} linked</div></div>
<div><div class="label">Workflow Progress</div><span class="workflow-progress-value"><span>{workflow_progress['completed']} / {workflow_progress['total']}</span><span>{workflow_progress['percentage']}%</span></span><span class="progress-track" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="{workflow_progress['percentage']}"><span class="progress-fill{' complete' if workflow_progress['percentage'] == 100 else ''}" style="width:{workflow_progress['percentage']}%"></span></span></div>
<div><div class="label">Health Score</div><span class="health-score-line"><span class="health-score-number">{health_score['score']} / 100</span><span class="health-score-label" style="color:{health_color}">{health_score['label']}</span></span><span class="health-score-detail"><span>Required: {health_score['required_completed']} / {health_score['required_total']}</span><span>Optional: {health_score['optional_completed']} / {health_score['optional_total']}</span></span><span class="health-track" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="{health_score['score']}"><span class="health-fill" style="width:{health_score['score']}%;background:{health_color}"></span></span></div>
</div>
<div class="remarks"><div class="label">Remarks</div><div>{html_text(shipment.get("remarks", ""))}</div></div>
</div>
<section class="tracking"><div class="tracking-head"><div><h2>Tracking Information</h2><div class="tracking-status">{html_text(shipment.get("status", "Draft"))}</div></div><a class="btn" href="/shipment/{html_attr(shipment_no)}/tracking">Edit Tracking</a></div><div class="tracking-grid">{tracking_rows}</div><div class="tracking-memo"><div class="label">Tracking Memo</div>{html_text(shipment.get("tracking_memo", "") or "-")}</div></section>
<section class="email-history"><h2>Email Delivery History</h2><table><thead><tr><th>Sent At</th><th>Document</th><th>Recipient</th><th>Subject</th><th>Result</th></tr></thead><tbody>{email_history_rows}</tbody></table></section>
{next_step_card}
{workflow_timeline}
{relationship_graph}
<div class="docs">{cards}</div>
<h2 class="section-title">Operational Records</h2>
<div class="docs operational-docs">{operational_cards}</div>
</div>
</body>
</html>
"""
    return HTMLResponse(html)


def draw_pdf_text(pdf, text, x, y, max_width=390, font=TP_UNICODE, size=10):
    value = str(text or "")
    lines = []
    while value and pdf.stringWidth(value, font, size) > max_width:
        split_at = len(value)
        while split_at > 1 and pdf.stringWidth(value[:split_at], font, size) > max_width:
            split_at -= 1
        whitespace = value.rfind(" ", 0, split_at + 1)
        if whitespace > 0:
            split_at = whitespace
        lines.append(value[:split_at])
        value = value[split_at:].lstrip()
    lines.append(value)
    pdf.setFont(font, size)
    for line in lines:
        pdf.drawString(x, y, line)
        y -= 14
    return y


@router.get("/shipment-pdf/{shipment_no}")
def shipment_pdf(shipment_no: str, request: Request):
    account_id = _account_id(request)
    owned_shipment = find_shipment(shipment_no, account_id)
    if not owned_shipment:
        raise HTTPException(status_code=404, detail="Shipment not found")
    shipment = public_shipment(owned_shipment)
    shipment = resolve_shipment_snapshot(shipment, account_id)
    set_pdf_export_record(request, shipment)

    buffer = BytesIO()
    ensure_pdf_fonts()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4
    navy = colors.HexColor("#111827")
    muted = colors.HexColor("#6B7280")

    def header():
        pdf.setFillColor(navy)
        pdf.rect(0, height - 82, width, 82, fill=1, stroke=0)
        pdf.setFillColor(colors.white)
        pdf.setFont(TP_UNICODE_BOLD, 22)
        pdf.drawString(42, height - 50, "SHIPMENT SUMMARY")
        return height - 112

    def ensure_space(y, needed=48):
        if y < needed:
            pdf.showPage()
            return header()
        return y

    y = header()
    pdf.setFillColor(navy)
    for label, value in [
        ("Shipment No", shipment.get("shipment_no", "")),
        ("Shipment Date", shipment.get("shipment_date", "")),
        ("Shipment Name", shipment.get("shipment_name", "")),
        ("Customer", shipment.get("customer", "")),
        ("Buyer", shipment.get("buyer", "")),
        ("Status", shipment.get("status", "")),
        ("Remarks", shipment.get("remarks", "")),
    ]:
        y = ensure_space(y)
        pdf.setFont(TP_UNICODE_BOLD, 10)
        pdf.drawString(42, y, f"{label}:")
        pdf.setFont(TP_UNICODE, 10)
        y = draw_pdf_text(pdf, value, 145, y, font=TP_UNICODE, size=10)
        y -= 3

    y -= 10
    y = ensure_space(y, 120)
    pdf.setFont(TP_UNICODE_BOLD, 14)
    pdf.drawString(42, y, "Party Snapshot")
    y -= 24
    for label, value in [
        ("Shipper", shipment.get("shipper", "")),
        ("Shipper Address", shipment.get("shipper_address", "")),
        ("Shipper Email", shipment.get("shipper_email", "")),
        ("Shipper Phone", shipment.get("shipper_phone", "")),
        ("Consignee", shipment.get("consignee", "")),
        ("Consignee Address", shipment.get("consignee_address", "")),
        ("Consignee Email", shipment.get("consignee_email", "")),
    ]:
        y = ensure_space(y)
        pdf.setFont(TP_UNICODE_BOLD, 9)
        pdf.drawString(42, y, f"{label}:")
        pdf.setFont(TP_UNICODE, 9)
        y = draw_pdf_text(pdf, value, 165, y, font=TP_UNICODE, size=9)
        y -= 2

    y -= 10
    y = ensure_space(y, 90)
    pdf.setFont(TP_UNICODE_BOLD, 14)
    pdf.drawString(42, y, "Cargo Snapshot")
    y -= 22
    pdf.setFont(TP_UNICODE_BOLD, 8)
    pdf.drawString(42, y, "Item")
    pdf.drawString(220, y, "Qty")
    pdf.drawString(270, y, "HS Code")
    pdf.drawString(350, y, "Carton")
    pdf.drawString(410, y, "Net")
    pdf.drawString(480, y, "Gross")
    y -= 15
    for item in shipment.get("items", []):
        if not isinstance(item, dict):
            continue
        y = ensure_space(y)
        pdf.setFont(TP_UNICODE, 8)
        pdf.drawString(42, y, fit_pdf_text(pdf, item.get("name", ""), 165, TP_UNICODE, 8))
        pdf.drawString(220, y, str(item.get("quantity", "")))
        pdf.drawString(270, y, str(item.get("hs_code", "")))
        pdf.drawString(350, y, str(item.get("carton", "")))
        pdf.drawString(410, y, str(item.get("net_weight", "")))
        pdf.drawString(480, y, str(item.get("gross_weight", "")))
        y -= 15
    y = ensure_space(y)
    pdf.setFont(TP_UNICODE_BOLD, 8)
    pdf.drawString(350, y, f"Totals: {shipment.get('total_carton', '')} cartons")
    pdf.drawString(430, y, f"N {shipment.get('total_net_weight', '')} / G {shipment.get('total_gross_weight', '')}")

    y -= 28
    y = ensure_space(y, 80)
    pdf.setFont(TP_UNICODE_BOLD, 14)
    pdf.drawString(42, y, "Direct Document Status")
    y -= 24
    pdf.setFont(TP_UNICODE_BOLD, 9)
    pdf.drawString(42, y, "Document")
    pdf.drawString(245, y, "Record No")
    pdf.drawString(430, y, "Status")
    y -= 15
    workflow_datasets = load_workflow_datasets(account_id)
    for resolved in resolve_direct_documents(shipment, workflow_datasets):
        y = ensure_space(y)
        pdf.setFont(TP_UNICODE, 9)
        pdf.drawString(42, y, resolved["document"]["label"])
        pdf.drawString(245, y, resolved["value"] or "-")
        pdf.drawString(430, y, "Linked" if resolved["exists"] else "Missing")
        y -= 16

    y -= 12
    y = ensure_space(y, 80)
    pdf.setFont(TP_UNICODE_BOLD, 14)
    pdf.drawString(42, y, "Operational Records")
    y -= 24
    pdf.setFont(TP_UNICODE_BOLD, 9)
    pdf.drawString(42, y, "Record Type")
    pdf.drawString(245, y, "Record No")
    pdf.drawString(430, y, "Status")
    y -= 15
    for group in resolve_operational_records(shipment_no, workflow_datasets):
        matches = group["matches"] or [{"value": "-"}]
        for match in matches:
            y = ensure_space(y)
            pdf.setFont(TP_UNICODE, 9)
            pdf.drawString(42, y, group["operational"]["label"])
            pdf.drawString(245, y, match["value"])
            pdf.drawString(430, y, "Linked" if group["matches"] else "Missing")
            y -= 16

    pdf.setFillColor(muted)
    pdf.setFont(TP_UNICODE, 8)
    pdf.drawCentredString(width / 2, 24, "Generated by Trade Paper AI")
    pdf.save()
    buffer.seek(0)
    return Response(
        content=buffer.getvalue(),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{shipment_no}.pdf"'},
    )


@router.get("/edit-shipment/{shipment_no}", response_class=HTMLResponse)
def edit_shipment(shipment_no: str, request: Request):
    account_id = _account_id(request)
    for record in load_shipments(account_id):
        if record.get("shipment_no") == shipment_no:
            record = resolve_shipment_snapshot(record, account_id)
            return render_form(
                record,
                f"/update-shipment/{html_attr(shipment_no)}",
                "Edit Shipment",
                "Update Shipment",
                show_shipment_no=True,
                datasets=load_workflow_datasets(account_id),
                create_mode=False,
            )
    raise HTTPException(status_code=404, detail="Shipment not found")


@router.post("/update-shipment/{shipment_no}")
def update_shipment(
    shipment_no: str,
    request: Request,
    shipment_date: str = Form(""),
    shipment_name: str = Form(""),
    customer: str = Form(""),
    buyer: str = Form(""),
    status: str = Form("Draft"),
    remarks: str = Form(""),
    quotation_no: str = Form(""),
    pi_no: str = Form(""),
    invoice_no: str = Form(""),
    packing_no: str = Form(""),
    si_no: str = Form(""),
    bl_no: str = Form(""),
    co_no: str = Form(""),
    inspection_no: str = Form(""),
    insurance_no: str = Form(""),
    weight_no: str = Form(""),
    shipper: Annotated[Optional[str], Form()] = None,
    shipper_address: Annotated[Optional[str], Form()] = None,
    shipper_email: Annotated[Optional[str], Form()] = None,
    shipper_phone: Annotated[Optional[str], Form()] = None,
    consignee: Annotated[Optional[str], Form()] = None,
    consignee_address: Annotated[Optional[str], Form()] = None,
    consignee_email: Annotated[Optional[str], Form()] = None,
):
    account_id = _account_id(request)
    current = find_shipment(shipment_no, account_id)
    if current is None:
        raise HTTPException(status_code=404, detail="Shipment not found")
    datasets = load_workflow_datasets(account_id)
    def replace_shipment(shipments):
        for index, record in enumerate(shipments):
            if (record.get("shipment_no") != shipment_no
                    or str(record.get("account_id", "") or "").strip() != account_id):
                continue
            updated = build_record(
                shipment_no, shipment_date, shipment_name, customer, buyer,
                status, remarks, quotation_no, pi_no, invoice_no, packing_no,
                si_no, bl_no, co_no, inspection_no, insurance_no, weight_no,
            )
            supplied_snapshot = {
                "shipper": shipper,
                "shipper_address": shipper_address,
                "shipper_email": shipper_email,
                "shipper_phone": shipper_phone,
                "consignee": consignee,
                "consignee_address": consignee_address,
                "consignee_email": consignee_email,
            }
            for field, value in supplied_snapshot.items():
                updated[field] = current.get(field, "") if value is None else value
            for field in CARGO_SNAPSHOT_FIELDS:
                updated[field] = deepcopy(current.get(field, [] if field == "items" else ""))
            for field in TRACKING_FIELDS:
                updated[field] = current.get(field, "")
            updated = resolve_shipment_snapshot(updated, account_id)
            validate_shipment_values(updated, account_id, datasets)
            updated["account_id"] = account_id
            shipments[index] = updated
            return
        raise HTTPException(status_code=404, detail="Shipment not found")
    locked_json_mutation(SHIPMENT_FILE, [], replace_shipment, list)
    return RedirectResponse("/shipment-list", status_code=303)


@router.get("/delete-shipment/{shipment_no}")
def delete_shipment(shipment_no: str, request: Request):
    account_id = _account_id(request)
    if find_shipment(shipment_no, account_id) is None:
        raise HTTPException(status_code=404, detail="Shipment not found")
    return render_delete_page(
        "Shipment", shipment_no, f"/delete-shipment/{shipment_no}",
        "/shipment-list", shipment_dependencies(shipment_no, account_id),
    )

@router.post("/delete-shipment/{shipment_no}")
def confirm_delete_shipment(shipment_no: str, request: Request):
    account_id = _account_id(request)
    if find_shipment(shipment_no, account_id) is None:
        raise HTTPException(status_code=404, detail="Shipment not found")
    dependencies = shipment_dependencies(shipment_no, account_id)
    if dependencies:
        return render_delete_page(
            "Shipment", shipment_no, f"/delete-shipment/{shipment_no}",
            "/shipment-list", dependencies, status_code=409,
        )
    def remove(records):
        index = next((index for index, record in enumerate(records)
                      if isinstance(record, dict)
                      and str(record.get("shipment_no", "") or "").strip() == shipment_no
                      and str(record.get("account_id", "") or "").strip() == account_id), None)
        if index is None:
            raise HTTPException(status_code=404, detail="Shipment not found")
        records.pop(index)
    locked_json_mutation(SHIPMENT_FILE, [], remove, list)
    return RedirectResponse("/shipment-list", status_code=303)


@router.get("/shipment-data/{shipment_no}")
def shipment_data(shipment_no: str, request: Request):
    account_id = _account_id(request)
    record = find_shipment(shipment_no, account_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Shipment not found")
    return public_shipment(resolve_shipment_snapshot(record, account_id))
