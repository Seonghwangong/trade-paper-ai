from __future__ import annotations

from datetime import datetime, timezone
from email.utils import parseaddr
import html
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse

from app import auth, email_delivery
from app.storage import data_path, locked_json_mutation


router = APIRouter()
HISTORY_FILE = data_path("email_history.json")

DOCUMENT_TYPES = {
    "invoice": ("Commercial Invoice", "invoice_no"),
    "packing": ("Packing List", "packing_no"),
    "shipping-instruction": ("Shipping Instruction", "si_no"),
    "booking": ("Booking Confirmation", "booking_record_no"),
    "bill-of-lading": ("Bill of Lading", "bl_no"),
    "certificate-of-origin": ("Certificate of Origin", "co_no"),
    "document-package": ("Document Package", "shipment_no"),
}


def html_attr(value):
    return html.escape(str(value or ""), quote=True)


def html_text(value):
    return html.escape(str(value or ""))


def _account_id(request: Request) -> str:
    user = request.scope.get("trade_paper_user") or {}
    value = user.get("account_id")
    if not value:
        raise HTTPException(status_code=401, detail="Login required")
    return str(value)


def _document(document_type: str, document_no: str, account_id: str):
    if document_type not in DOCUMENT_TYPES:
        raise HTTPException(status_code=404, detail="Document type not found")
    from app import invoice, packing, shipping_instruction, booking_confirmation
    from app import bill_of_lading, certificate_of_origin, shipment
    loaders = {
        "invoice": invoice.owned_invoice_records,
        "packing": packing.owned_packing_records,
        "shipping-instruction": shipping_instruction.owned_shipping_instruction_records,
        "booking": booking_confirmation.owned_booking_records,
        "bill-of-lading": bill_of_lading.owned_bill_of_lading_records,
        "certificate-of-origin": certificate_of_origin.owned_certificate_records,
        "document-package": shipment.owned_shipment_records,
    }
    label, identifier = DOCUMENT_TYPES[document_type]
    record = next(
        (item for item in loaders[document_type](account_id) if str(item.get(identifier, "")) == document_no),
        None,
    )
    if record is None:
        raise HTTPException(status_code=404, detail=f"{label} not found")
    return label, identifier, record


def _linked_shipment_no(record: dict, document_type: str, account_id: str) -> str:
    if document_type == "document-package":
        return str(record.get("shipment_no", ""))
    direct = str(record.get("shipment_no", "") or "")
    if direct:
        return direct
    from app import shipment
    _, identifier = DOCUMENT_TYPES[document_type]
    value = str(record.get(identifier, ""))
    field = {
        "invoice": "invoice_no", "packing": "packing_no",
        "shipping-instruction": "si_no", "booking": "booking_record_no",
        "bill-of-lading": "bl_no", "certificate-of-origin": "co_no",
    }.get(document_type)
    if field:
        match = next((item for item in shipment.owned_shipment_records(account_id) if str(item.get(field, "")) == value), None)
        if match:
            return str(match.get("shipment_no", ""))
    return ""


def _recipient(record: dict, document_type: str, account_id: str) -> str:
    for field in ("buyer_email", "consignee_email", "importer_email", "email"):
        if str(record.get(field, "") or "").strip():
            return str(record[field]).strip()
    shipment_no = _linked_shipment_no(record, document_type, account_id)
    if shipment_no:
        from app import shipment
        linked = next((item for item in shipment.owned_shipment_records(account_id) if item.get("shipment_no") == shipment_no), {})
        return str(linked.get("consignee_email", "") or "").strip()
    return ""


def _attachment(document_type: str, document_no: str, request: Request):
    from app import invoice, packing, shipping_instruction, booking_confirmation
    from app import bill_of_lading, certificate_of_origin, shipment
    handlers = {
        "invoice": invoice.invoice_pdf,
        "packing": packing.packing_list_pdf,
        "shipping-instruction": shipping_instruction.si_pdf,
        "booking": booking_confirmation.booking_pdf,
        "bill-of-lading": bill_of_lading.bl_pdf,
        "certificate-of-origin": certificate_of_origin.co_pdf,
        "document-package": shipment.download_document_package,
    }
    response = handlers[document_type](document_no, request)
    suffix = "zip" if document_type == "document-package" else "pdf"
    mime = "application/zip" if suffix == "zip" else "application/pdf"
    return email_delivery.EmailAttachment(f"{document_no}.{suffix}", response.body, mime)


def _valid_recipient(value: str) -> bool:
    address = str(value or "").strip()
    return bool(address and "\r" not in address and "\n" not in address and parseaddr(address)[1] == address and "@" in address)


def _form_page(label, document_type, document_no, recipient, subject, body, error=""):
    message = f'<div class="error" role="alert">{html_text(error)}</div>' if error else ""
    return HTMLResponse(f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Send {html_text(label)}</title><style>*{{box-sizing:border-box}}body{{margin:0;padding:32px;background:#F3F4F6;color:#111827;font-family:Arial,sans-serif}}main{{max-width:720px;margin:auto}}form{{background:#fff;padding:28px;border:1px solid #E5E7EB;border-radius:16px}}label{{display:block;font-weight:700;margin:16px 0 7px}}input,textarea{{width:100%;padding:12px;border:1px solid #CBD5E1;border-radius:9px;font:inherit}}textarea{{min-height:170px;resize:vertical}}button,a{{display:inline-flex;margin-top:18px;padding:12px 17px;border:0;border-radius:9px;background:#111827;color:#fff;text-decoration:none;font-weight:800;cursor:pointer}}a{{margin-left:8px;background:#64748B}}.attachment{{padding:12px;background:#F8FAFC;border-radius:9px}}.error{{padding:12px;background:#FEE2E2;color:#991B1B;border-radius:9px}}</style></head><body><main><h1>Send Email</h1><p>{html_text(label)} {html_text(document_no)}</p>{message}<form method="post" action="/send-email/{html_attr(document_type)}/{quote(document_no, safe='')}"><label for="recipient">Recipient</label><input id="recipient" name="recipient" type="email" required value="{html_attr(recipient)}"><label for="subject">Subject</label><input id="subject" name="subject" required value="{html_attr(subject)}"><label for="body">Message</label><textarea id="body" name="body" required>{html_text(body)}</textarea><p class="attachment">Attachment: {html_text(document_no)}.{"zip" if document_type == "document-package" else "pdf"}</p><button type="submit">Send Email</button><a href="javascript:history.back()">Cancel</a></form></main></body></html>''')


@router.get("/send-email/{document_type}/{document_no}")
def email_form(document_type: str, document_no: str, request: Request):
    account_id = _account_id(request)
    label, _, record = _document(document_type, document_no, account_id)
    recipient = _recipient(record, document_type, account_id)
    subject = f"{label} {document_no}"
    body = f"Dear Partner,\n\nPlease find attached {label} {document_no}.\n\nBest regards"
    return _form_page(label, document_type, document_no, recipient, subject, body)


@router.post("/send-email/{document_type}/{document_no}")
def send_document_email(document_type: str, document_no: str, request: Request, recipient: Annotated[str, Form()], subject: Annotated[str, Form()], body: Annotated[str, Form()]):
    account_id = _account_id(request)
    label, _, record = _document(document_type, document_no, account_id)
    recipient, subject, body = recipient.strip(), subject.strip(), body.strip()
    if not _valid_recipient(recipient) or not subject or not body:
        return _form_page(label, document_type, document_no, recipient, subject, body, "Enter a valid recipient, subject, and message.")
    attachment = _attachment(document_type, document_no, request)
    message = email_delivery.DeliveryMessage(
        recipient=recipient, subject=subject, text_body=body,
        html_body=f"<p>{html.escape(body).replace(chr(10), '<br>')}</p>",
        purpose="document_delivery", attachments=(attachment,),
    )
    success = email_delivery.deliver_email(message)
    shipment_no = _linked_shipment_no(record, document_type, account_id)
    entry = {"account_id": account_id, "sent_at": datetime.now(timezone.utc).isoformat(), "document_type": document_type, "document_no": document_no, "shipment_no": shipment_no, "recipient": recipient, "subject": subject, "status": "Success" if success else "Failed"}
    locked_json_mutation(HISTORY_FILE, [], lambda rows: rows.append(entry), list)
    from app.audit_log import record_request_audit
    record_request_audit(request, "Send Email", label, document_no, path=HISTORY_FILE.with_name("audit_log.json"))
    status, detail = ("Success", "The email was sent successfully.") if success else ("Failed", "The email could not be sent. Check the email delivery configuration and try again.")
    return HTMLResponse(f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Email {status}</title><style>body{{margin:0;background:#F3F4F6;font-family:Arial;color:#111827}}main{{min-height:100vh;display:grid;place-items:center}}section{{max-width:580px;padding:34px;background:#fff;border-radius:18px;text-align:center}}a{{display:inline-block;margin-top:16px;padding:12px 16px;background:#111827;color:#fff;text-decoration:none;border-radius:9px;font-weight:bold}}</style></head><body><main><section><h1>{status}</h1><p>{html_text(detail)}</p><a href="/shipment/{html_attr(shipment_no)}"{' style="display:none"' if not shipment_no else ''}>View Shipment</a><a href="/">Dashboard</a></section></main></body></html>''')


def shipment_email_history(shipment_no: str, account_id: str):
    from app.storage import load_json_strict
    return [item for item in load_json_strict(HISTORY_FILE, default=[], expected_type=list) if item.get("account_id") == account_id and item.get("shipment_no") == shipment_no]


@router.get("/admin/email-readiness")
def email_readiness_admin(request: Request):
    auth.require_admin(request)
    readiness = email_delivery.email_readiness()
    return HTMLResponse(f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Email Readiness</title><style>body{{margin:0;background:#F3F4F6;color:#111827;font-family:Arial}}main{{width:min(720px,calc(100% - 32px));margin:40px auto}}section{{padding:28px;background:#fff;border:1px solid #E5E7EB;border-radius:18px}}dl{{display:grid;grid-template-columns:1fr 1fr;gap:14px}}dt{{color:#64748B;font-weight:bold}}dd{{margin:0;font-weight:800}}a{{display:inline-block;margin-top:18px;padding:11px 15px;border-radius:9px;background:#111827;color:#fff;text-decoration:none;font-weight:bold}}</style></head><body><main><h1>Email Readiness</h1><section><dl><dt>Email Backend</dt><dd>{html_text(readiness["backend"])}</dd><dt>Configuration</dt><dd>{html_text(readiness["configuration"])}</dd></dl><p>No credentials or message content are displayed.</p><a href="/">Dashboard</a></section></main></body></html>''')
