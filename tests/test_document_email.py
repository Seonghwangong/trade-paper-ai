import json

import pytest
from fastapi import HTTPException
from fastapi.responses import Response
from starlette.requests import Request

from app import document_email, email_delivery, invoice, shipment


def _request(account="A", method="GET", admin=False):
    return Request({
        "type": "http", "method": method, "path": "/send-email/invoice/INV-001",
        "headers": [], "trade_paper_user": {"account_id": account, "is_admin": admin},
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
    if delivered:
        assert result.status_code == 303
        receipt = result.headers["location"].rsplit("/", 1)[1]
        assert "Email submitted" in document_email.email_result(receipt, _request()).body.decode()
    else:
        assert result.status_code == 502
        assert "Sending failed" in result.body.decode()
        assert 'value="changed@example.com"' in result.body.decode()
        assert "Please review." in result.body.decode()
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
    assert response.status_code == 400 and "Enter a valid recipient" in response.body.decode()
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
    html = document_email.email_readiness_admin(_request(admin=True)).body.decode()
    assert "Email Backend" in html and "SMTP" in html and "Ready" in html
    assert "username" not in html.casefold() and "password" not in html.casefold()
    with pytest.raises(HTTPException) as denied:
        document_email.email_readiness_admin(_request())
    assert denied.value.status_code == 403


def test_admin_email_readiness_identifies_resend_without_secrets(monkeypatch):
    monkeypatch.setattr(email_delivery, "email_readiness", lambda: {
        "backend": "API", "provider": "Resend", "sender_domain": "Not Verified",
        "configuration": "Not Ready",
    })
    html = document_email.email_readiness_admin(_request(admin=True)).body.decode()
    assert "API Provider" in html and "Resend" in html
    assert "Sender Domain" in html and "Not Verified" in html
    assert "api key" not in html.casefold() and "recipient" not in html.casefold()


def test_success_refresh_is_read_only_and_receipt_is_account_scoped(tmp_path, monkeypatch):
    from fastapi import FastAPI
    import asyncio
    from urllib.parse import urlencode
    monkeypatch.setattr(document_email, "HISTORY_FILE", tmp_path / "history.json")
    monkeypatch.setattr(invoice, "owned_invoice_records", _records)
    monkeypatch.setattr(invoice, "invoice_pdf", lambda number, request: Response(b"%PDF test", media_type="application/pdf"))
    calls = []
    monkeypatch.setattr(email_delivery, "deliver_email", lambda message: calls.append(message) or True)
    app = FastAPI()
    app.include_router(document_email.router)
    async def request(path, method="GET", data=None, account="A"):
        payload = urlencode(data or {}).encode()
        messages = []
        async def receive():
            return {"type": "http.request", "body": payload, "more_body": False}
        async def send(message):
            messages.append(message)
        scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
                 "method": method, "scheme": "http", "path": path, "raw_path": path.encode(),
                 "query_string": b"", "root_path": "", "server": ("test", 80), "client": ("test", 1),
                 "headers": [(b"content-type", b"application/x-www-form-urlencoded")],
                 "trade_paper_user": {"account_id": account}}
        await app(scope, receive, send)
        start = next(message for message in messages if message["type"] == "http.response.start")
        body = b"".join(message.get("body", b"") for message in messages).decode()
        return start["status"], dict(start["headers"]), body
    status, headers, _ = asyncio.run(request("/send-email/invoice/INV-001", "POST", {
        "recipient": "test@example.com", "subject": "Review <test>", "body": "Keep this message",
    }))
    assert status == 303
    receipt_url = headers[b"location"].decode()
    assert receipt_url.startswith("/email-result/")
    for _ in range(2):
        status, _, body = asyncio.run(request(receipt_url))
        assert status == 200
        assert "Review &lt;test&gt;" in body
        assert "does not confirm arrival" in body
    assert len(calls) == 1
    assert asyncio.run(request(receipt_url, account="B"))[0] == 404
    assert asyncio.run(request("/email-result/missing"))[0] == 404


@pytest.mark.parametrize("status, mime, payload", [(400, "text/html", b"error"), (200, "text/html", b"error"), (200, "application/pdf", b"")])
def test_invalid_attachment_never_sends_and_keeps_message(tmp_path, monkeypatch, status, mime, payload):
    monkeypatch.setattr(document_email, "HISTORY_FILE", tmp_path / "history.json")
    monkeypatch.setattr(invoice, "owned_invoice_records", _records)
    monkeypatch.setattr(invoice, "invoice_pdf", lambda number, request: Response(payload, status_code=status, media_type=mime))
    monkeypatch.setattr(email_delivery, "deliver_email", lambda message: pytest.fail("must not send"))
    response = document_email.send_document_email("invoice", "INV-001", _request(method="POST"),
                                                 "test@example.com", "Keep subject", "Keep body <safe>")
    assert response.status_code == 400
    assert "Attachment could not be created" in response.body.decode()
    assert "Keep body &lt;safe&gt;" in response.body.decode()
    assert not document_email.HISTORY_FILE.exists()


def test_pdf_validation_failure_keeps_form(monkeypatch):
    from app.validation import DataValidationError
    monkeypatch.setattr(invoice, "owned_invoice_records", _records)
    def invalid_pdf(number, request):
        raise DataValidationError("Quantity", "Quantity is invalid.", "Correct the quantity.")
    monkeypatch.setattr(invoice, "invoice_pdf", invalid_pdf)
    monkeypatch.setattr(email_delivery, "deliver_email", lambda message: pytest.fail("must not send"))
    response = document_email.send_document_email("invoice", "INV-001", _request(method="POST"),
                                                 "test@example.com", "Keep subject", "Keep body")
    assert response.status_code == 400
    assert "Correct the quantity" in response.body.decode() and "Keep body" in response.body.decode()


def test_email_form_has_encoded_preview_and_safe_return_links():
    body = document_email._form_page("Invoice", "invoice", 'INV/?<x>', "", "", "").body.decode()
    assert '/invoice-pdf/INV%2F%3F%3Cx%3E' in body
    assert 'href="/invoice-list"' in body
    assert 'target="_blank" rel="noopener"' in body
    assert 'method="post"' in body
    assert 'javascript:history.back()' not in body


def test_multiline_subject_does_not_send(monkeypatch):
    monkeypatch.setattr(invoice, "owned_invoice_records", _records)
    monkeypatch.setattr(email_delivery, "deliver_email", lambda message: pytest.fail("must not send"))
    response = document_email.send_document_email("invoice", "INV-001", _request(method="POST"),
                                                 "test@example.com", "Subject\r\nOther header", "Body")
    assert response.status_code == 400


def test_email_result_requires_account_before_reading_history(monkeypatch):
    monkeypatch.setattr(document_email, "load_json_strict", lambda *args, **kwargs: pytest.fail("must not read history"))
    with pytest.raises(HTTPException) as denied:
        document_email.email_result("receipt", _request(account=""))
    assert denied.value.status_code == 401
