import json
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from fastapi import HTTPException
from starlette.requests import Request
from starlette.responses import Response

from app import document_email, email_delivery, email_pair, invoice, packing

REAL_INVOICE_PDF = invoice.create_invoice_pdf
REAL_PACKING_PDF = packing.create_packing_list_pdf

def request(account="A", method="GET", lang="en", headers=()):
    return Request({"type": "http", "method": method, "path": "/email-pair/prepare/PL-1",
                    "scheme": "https", "server": ("test", 443), "query_string": f"lang={lang}".encode(),
                    "headers": list(headers), "trade_paper_user": {"account_id": account}})


@pytest.fixture
def pair(tmp_path, monkeypatch):
    source = {"account_id": "A", "invoice_no": "INV-1", "buyer": "Buyer", "buyer_email": "buyer@example.com",
              "items": [{"name": "Shirt", "quantity": 100, "unit": "PCS", "unit_price": 8}], "currency": "USD"}
    packed = {**source, "packing_no": "PL-1", "items": [{**source["items"][0], "carton": 5, "net_weight": 20, "gross_weight": 25}]}
    company = {"name": "Seller"}
    monkeypatch.setattr(email_pair, "REVIEW_DB", tmp_path / "reviews.sqlite3")
    monkeypatch.setattr(document_email, "HISTORY_FILE", tmp_path / "email_history.json")
    monkeypatch.setattr(invoice, "owned_invoice_records", lambda account: [source] if account == "A" else [])
    monkeypatch.setattr(packing, "owned_packing_records", lambda account: [packed] if account == "A" else [])
    monkeypatch.setattr(invoice, "load_account_company", lambda *args: company)
    monkeypatch.setattr(packing, "_buyer_master", lambda *args: {})
    monkeypatch.setattr(document_email, "_linked_shipment_no", lambda *args: "SHP-1")
    monkeypatch.setattr(invoice, "create_invoice_pdf", lambda *args: Response(b"%PDF frozen invoice", media_type="application/pdf"))
    monkeypatch.setattr(packing, "create_packing_list_pdf", lambda *args: Response(b"%PDF frozen packing", media_type="application/pdf"))
    monkeypatch.setattr(email_delivery, "deliver_email", lambda message: pytest.fail("Unexpected email"))
    return source, packed, company


def prepare():
    response = email_pair.prepare_pair("PL-1", request(method="POST"))
    assert response.status_code == 303
    return response.headers["location"].split("/")[-1].split("?")[0]


def send(token, **kwargs):
    return email_pair.send_pair(token, request(method="POST"), "buyer@example.com", "Documents", "Please review.", **kwargs)


def test_prepare_is_private_and_does_not_send_and_preview_uses_frozen_bytes(pair, monkeypatch):
    token = prepare()
    monkeypatch.setattr(invoice, "create_invoice_pdf", lambda *args: pytest.fail("Must not regenerate"))
    monkeypatch.setattr(packing, "create_packing_list_pdf", lambda *args: pytest.fail("Must not regenerate"))
    html = email_pair.review_pair(token, request(lang="ko")).body.decode()
    assert "두 PDF 확인 후 함께 보내기" in html and html.count('<iframe title=') == 2
    assert "새 사본 준비" in html and "checked" not in html
    for index, content in [(0, b"%PDF frozen invoice"), (1, b"%PDF frozen packing")]:
        response = email_pair.review_pdf(token, index, request())
        assert response.body == content
        assert response.headers["cache-control"] == "private, no-store"
        assert "inline" in response.headers["content-disposition"]
    assert not document_email.HISTORY_FILE.exists()


def test_exact_reviewed_bytes_are_sent_together_and_receipt_lists_both(pair, monkeypatch):
    token = prepare()
    previews = [email_pair.review_pdf(token, i, request()).body for i in range(2)]
    monkeypatch.setattr(invoice, "create_invoice_pdf", lambda *args: pytest.fail("Must not regenerate"))
    captured = []
    monkeypatch.setattr(email_delivery, "deliver_email", lambda message: captured.append(message) or True)
    response = send(token, confirmed="yes")
    assert response.status_code == 303
    assert [a.content for a in captured[0].attachments] == previews
    assert [a.filename for a in captured[0].attachments] == ["Invoice-INV-1.pdf", "PackingList-PL-1.pdf"]
    assert captured[0].retry_transient is False
    receipt = document_email.email_result(token, request()).body.decode()
    assert "Invoice-INV-1.pdf" in receipt and "PackingList-PL-1.pdf" in receipt
    assert "does not confirm arrival" in receipt
    assert send(token, confirmed="yes").status_code == 303
    assert len(captured) == 1
    history = json.loads(document_email.HISTORY_FILE.read_text())
    assert len(history) == 1 and len(history[0]["attachments"]) == 2
    assert "body" not in history[0]


@pytest.mark.parametrize("operation", ["prepare", "review", "pdf", "send"])
def test_other_account_cannot_access_pair(pair, operation):
    token = prepare()
    with pytest.raises(HTTPException) as denied:
        if operation == "prepare": email_pair.prepare_pair("PL-1", request("B", "POST"))
        elif operation == "review": email_pair.review_pair(token, request("B"))
        elif operation == "pdf": email_pair.review_pdf(token, 0, request("B"))
        else: email_pair.send_pair(token, request("B", "POST"), "x@example.com", "X", "X", "yes")
    assert denied.value.status_code == 404


@pytest.mark.parametrize("changed", ["invoice", "packing", "company"])
def test_edit_after_review_blocks_send_without_changing_preview(pair, changed):
    token = prepare()
    target = pair[{"invoice": 0, "packing": 1, "company": 2}[changed]]
    target["name" if changed == "company" else "buyer"] = "Changed"
    response = send(token, confirmed="yes")
    assert response.status_code == 409
    assert "Source details changed" in response.body.decode()
    assert email_pair.review_pdf(token, 0, request()).body == b"%PDF frozen invoice"


def test_mismatched_fields_and_missing_source_do_not_prepare(pair):
    pair[1]["items"][0]["quantity"] = 99
    response = email_pair.prepare_pair("PL-1", request(method="POST"))
    assert "Shared fields differ" in response.body.decode()
    assert not email_pair.REVIEW_DB.exists()
    pair[1]["invoice_no"] = "missing"
    with pytest.raises(HTTPException):
        email_pair.prepare_pair("PL-1", request(method="POST"))


def test_expiry_deletes_snapshot_and_blocks_delivery(pair, monkeypatch):
    token = prepare()
    now = email_pair.time.time()
    monkeypatch.setattr(email_pair.time, "time", lambda: now + 3601)
    with pytest.raises(HTTPException) as expired:
        send(token, confirmed="yes")
    assert expired.value.status_code == 404
    with email_pair._database() as db:
        assert db.execute("SELECT COUNT(*) FROM reviews").fetchone()[0] == 0


def test_confirmation_and_valid_fields_required(pair):
    token = prepare()
    assert send(token).status_code == 400
    response = email_pair.send_pair(token, request(method="POST"), "bad", "Subject\r\nOther", "<script>", "yes")
    assert response.status_code == 400 and "&lt;script&gt;" in response.body.decode()
    assert not document_email.HISTORY_FILE.exists()


def test_ambiguous_failure_cannot_retry_same_review(pair, monkeypatch):
    token = prepare()
    calls = []
    monkeypatch.setattr(email_delivery, "deliver_email", lambda message: calls.append(message) or False)
    assert send(token, confirmed="yes").status_code == 502
    assert send(token, confirmed="yes").status_code == 409
    assert len(calls) == 1


def test_concurrent_requests_claim_only_one_delivery(pair, monkeypatch):
    token = prepare()
    entered, release = Event(), Event()
    calls = []
    def deliver(message):
        calls.append(message)
        entered.set()
        assert release.wait(5)
        return True
    monkeypatch.setattr(email_delivery, "deliver_email", deliver)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(send, token, confirmed="yes")
        assert entered.wait(5)
        try:
            assert send(token, confirmed="yes").status_code == 409
        finally:
            release.set()
        assert first.result().status_code == 303
    assert len(calls) == 1


def test_cross_site_post_is_rejected(pair):
    with pytest.raises(HTTPException) as denied:
        email_pair.prepare_pair("PL-1", request(method="POST", headers=[(b"origin", b"https://foreign.example")]))
    assert denied.value.status_code == 403


def test_pdf_export_error_creates_no_review(pair, monkeypatch):
    monkeypatch.setattr(packing, "create_packing_list_pdf", lambda *args: Response(b"error", media_type="text/html"))
    assert email_pair.prepare_pair("PL-1", request(method="POST")).status_code == 400
    assert not email_pair.REVIEW_DB.exists()


def test_real_pdf_generators_prepare_readable_pair(pair, monkeypatch):
    monkeypatch.setattr(invoice, "create_invoice_pdf", REAL_INVOICE_PDF)
    monkeypatch.setattr(packing, "create_packing_list_pdf", REAL_PACKING_PDF)
    token = prepare()
    for index in (0, 1):
        pdf = email_pair.review_pdf(token, index, request()).body
        assert pdf.startswith(b"%PDF") and len(pdf) > 1000


def test_invalid_slot_and_removed_document_cannot_leak_pdf(pair, monkeypatch):
    token = prepare()
    with pytest.raises(HTTPException):
        email_pair.review_pdf(token, -1, request())
    monkeypatch.setattr(packing, "owned_packing_records", lambda account: [])
    with pytest.raises(HTTPException):
        email_pair.review_pdf(token, 0, request())


def test_bounded_review_count_and_size(pair, monkeypatch):
    for _ in range(10):
        prepare()
    with pytest.raises(HTTPException) as limit:
        prepare()
    assert limit.value.status_code == 429
    monkeypatch.setattr(email_pair, "MAX_PAIR_BYTES", 1)
    with pytest.raises(HTTPException) as size:
        prepare()
    assert size.value.status_code == 413


def test_review_pdf_keeps_inline_disposition_through_middleware(pair):
    import asyncio
    from app.ui import ReleaseFooterMiddleware
    token = prepare()
    response = email_pair.review_pdf(token, 0, request())
    async def app(scope, receive, send):
        await response(scope, receive, send)
    messages = []
    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}
    async def send_message(message):
        messages.append(message)
    scope = dict(request().scope, path=f"/email-pair/pdf/{token}/0")
    asyncio.run(ReleaseFooterMiddleware(app)(scope, receive, send_message))
    headers = dict(next(m for m in messages if m["type"] == "http.response.start")["headers"])
    assert headers[b"content-disposition"].startswith(b"inline;")
    assert b"Invoice-INV-1.pdf" in headers[b"content-disposition"]
    assert headers[b"cache-control"] == b"private, no-store"
    assert b"".join(m.get("body", b"") for m in messages) == response.body

