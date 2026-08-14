import json

import pytest
from fastapi import HTTPException
from fastapi.responses import Response
from starlette.requests import Request

from app import document_email, email_delivery, invoice, shipment


def _request(account="A", method="GET"):
    return Request({
        "type": "http", "method": method, "path": "/send-email/invoice/INV-001",
        "headers": [], "trade_paper_user": {"account_id": account},
    })


def _records(account):
    return [{
        "account_id": "A", "invoice_no": "INV-001", "buyer_email": "buyer@example.com",
        "shipment_no": "SHP-001", "seller": "Seller", "buyer": "Buyer", "items": [],
    }] if account == "A" else []


def test_email_form_suggests_buyer_and_blocks_other_account(monkeypatch):
    monkeypatch.setattr(invoice, "owned_invoice_records", _records)
    html = document_email.email_form("invoice", "INV-001", _request()).body.decode()
    assert 'value="buyer@example.com"' in html
    assert 'value="Commercial Invoice INV-001"' in html
    assert "INV-001.pdf" in html and "Send Email" in html
    with pytest.raises(HTTPException) as denied:
        document_email.email_form("invoice", "INV-001", _request("B"))
    assert denied.value.status_code == 404


@pytest.mark.parametrize("delivered, expected", [(True, "Success"), (False, "Failed")])
def test_send_attaches_pdf_and_records_result(tmp_path, monkeypatch, delivered, expected):
    history = tmp_path / "email_history.json"
    history.write_text("[]\n", encoding="utf-8")
    monkeypatch.setattr(document_email, "HISTORY_FILE", history)
    monkeypatch.setattr(invoice, "owned_invoice_records", _records)
    monkeypatch.setattr(invoice, "invoice_pdf", lambda number, request: Response(b"%PDF invoice", media_type="application/pdf"))
    captured = []
    monkeypatch.setattr(email_delivery, "deliver_email", lambda message: captured.append(message) or delivered)
    result = document_email.send_document_email(
        "invoice", "INV-001", _request(method="POST"),
        "changed@example.com", "Custom subject", "Please review.",
    )
    assert expected in result.body.decode()
    assert captured[0].recipient == "changed@example.com"
    assert captured[0].attachments[0].filename == "INV-001.pdf"
    assert captured[0].attachments[0].content == b"%PDF invoice"
    rows = json.loads(history.read_text(encoding="utf-8"))
    assert rows[0]["status"] == expected and rows[0]["shipment_no"] == "SHP-001"
    assert "body" not in rows[0] and "attachment" not in rows[0]
    audit = json.loads((tmp_path / "audit_log.json").read_text(encoding="utf-8"))
    assert audit[0]["action"] == "Send Email"
    assert audit[0]["document_type"] == "Commercial Invoice" and audit[0]["document_no"] == "INV-001"
    assert "recipient" not in audit[0] and "subject" not in audit[0] and "body" not in audit[0]


def test_invalid_recipient_does_not_send_or_write(tmp_path, monkeypatch):
    history = tmp_path / "email_history.json"
    monkeypatch.setattr(document_email, "HISTORY_FILE", history)
    monkeypatch.setattr(invoice, "owned_invoice_records", _records)
    monkeypatch.setattr(email_delivery, "deliver_email", lambda message: pytest.fail("must not send"))
    response = document_email.send_document_email(
        "invoice", "INV-001", _request(method="POST"), "invalid", "Subject", "Body",
    )
    assert response.status_code == 200 and "Enter a valid recipient" in response.body.decode()
    assert not history.exists()


def test_smtp_message_contains_pdf_attachment():
    message = email_delivery.DeliveryMessage(
        recipient="buyer@example.com", subject="Invoice", text_body="Attached",
        html_body="<p>Attached</p>", purpose="document_delivery",
        attachments=(email_delivery.EmailAttachment("INV-001.pdf", b"%PDF", "application/pdf"),),
    )
    mime = email_delivery._smtp_message(message, {
        "TRADE_PAPER_EMAIL_FROM_ADDRESS": "sender@example.com",
        "TRADE_PAPER_EMAIL_FROM_NAME": "Trade Paper AI",
        "TRADE_PAPER_EMAIL_REPLY_TO": "reply@example.com",
    })
    attachments = list(mime.iter_attachments())
    assert len(attachments) == 1
    assert attachments[0].get_filename() == "INV-001.pdf"
    assert attachments[0].get_payload(decode=True) == b"%PDF"


def test_shipment_detail_displays_account_scoped_email_history(monkeypatch):
    record = {"account_id": "A", "shipment_no": "SHP-001", "status": "Draft", "items": []}
    monkeypatch.setattr(shipment, "find_shipment", lambda number, account: record if account == "A" else None)
    monkeypatch.setattr(shipment, "load_workflow_datasets", lambda account: {
        **{item["file"].name: [] for item in shipment.DOCUMENTS},
        **{item["file"].name: [] for item in shipment.OPERATIONAL_RECORDS},
        "certificates_of_origin.json": [], "bills_of_lading.json": [],
    })
    monkeypatch.setattr(document_email, "shipment_email_history", lambda number, account: [{
        "sent_at": "2026-08-12T00:00:00+00:00", "document_no": "INV-001",
        "recipient": "buyer@example.com", "subject": "Invoice", "status": "Success",
    }])
    html = shipment.shipment_detail("SHP-001", _request()).body.decode()
    assert "Email Delivery History" in html
    assert "buyer@example.com" in html and "Success" in html
    assert "/send-email/document-package/SHP-001" in html


def test_admin_email_readiness_is_secret_free(monkeypatch):
    monkeypatch.setattr(email_delivery, "email_readiness", lambda: {"backend": "SMTP", "configuration": "Ready"})
    html = document_email.email_readiness_admin(_request()).body.decode()
    assert "Email Backend" in html and "SMTP" in html and "Ready" in html
    assert "username" not in html.casefold() and "password" not in html.casefold()
