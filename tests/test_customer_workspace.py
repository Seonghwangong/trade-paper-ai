import json

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app import buyer, document_email, invoice, shipment


def _request(account="A"):
    return Request({
        "type": "http", "method": "GET", "scheme": "http", "path": "/buyers",
        "raw_path": b"/buyers", "query_string": b"", "headers": [],
        "server": ("test", 80), "client": ("127.0.0.1", 1),
        "trade_paper_user": {"account_id": account},
    })


def test_customer_workspace_metrics_status_links_search_and_isolation(tmp_path, monkeypatch):
    buyers = tmp_path / "buyers.json"
    users = tmp_path / "users.json"
    history = tmp_path / "email_history.json"
    buyers.write_text(json.dumps([
        {"account_id": "A", "name": "Sakura Retail", "email": "buyer@example.com", "country": "JP", "address": "Tokyo", "status": "Customer"},
        {"account_id": "B", "name": "Other Buyer", "email": "other@example.com", "status": "Lead"},
    ]), encoding="utf-8")
    users.write_text(json.dumps([{"account_id": "A"}, {"account_id": "B"}]), encoding="utf-8")
    history.write_text(json.dumps([
        {"account_id": "A", "recipient": "buyer@example.com", "sent_at": "2026-08-12T10:00:00Z", "document_no": "INV-002", "shipment_no": "SHP-002", "status": "Success"},
        {"account_id": "B", "recipient": "buyer@example.com", "sent_at": "2026-08-13T10:00:00Z", "document_no": "SECRET", "shipment_no": "SHP-999", "status": "Success"},
    ]), encoding="utf-8")
    monkeypatch.setattr(buyer, "BUYER_FILE", buyers)
    monkeypatch.setattr(buyer, "USERS_FILE", users)
    monkeypatch.setattr(document_email, "HISTORY_FILE", history)
    monkeypatch.setattr(invoice, "owned_invoice_records", lambda account: [
        {"account_id": "A", "invoice_no": "INV-001", "buyer": "Sakura Retail", "invoice_date": "2026-07-01", "items": [{"quantity": 2, "unit_price": 100}]},
        {"account_id": "A", "invoice_no": "INV-002", "buyer_email": "buyer@example.com", "invoice_date": "2026-08-10", "items": [{"quantity": 3, "unit_price": 50}]},
    ] if account == "A" else [])
    monkeypatch.setattr(shipment, "owned_shipment_records", lambda account: [
        {"account_id": "A", "shipment_no": "SHP-002", "consignee_email": "buyer@example.com", "shipment_date": "2026-08-11"},
        {"account_id": "A", "shipment_no": "SHP-001", "buyer": "Sakura Retail", "shipment_date": "2026-07-02"},
    ] if account == "A" else [])

    metrics = buyer.buyer_workspace_metrics(json.loads(buyers.read_text())[0], "A")
    assert metrics["transaction_count"] == 2
    assert metrics["total_invoice_amount"] == 350
    assert metrics["last_transaction_date"] == "2026-08-11"
    assert metrics["latest_shipment"]["shipment_no"] == "SHP-002"
    assert metrics["latest_email"]["document_no"] == "INV-002"

    html = buyer.buyer_workspace(0, _request()).body.decode()
    assert "Customer" in html and "Transactions</span><strong>2" in html
    assert "USD 350.00" in html and "/shipment/SHP-002" in html
    assert "/shipment/SHP-002#email-history" in html
    assert "SECRET" not in html and "SHP-999" not in html
    listing = buyer.buyer_list(_request(), "saku").body.decode()
    assert "Sakura Retail" in listing and "Other Buyer" not in listing
    assert 'datalist id="buyer-search-options"' in listing
    with pytest.raises(HTTPException) as denied:
        buyer.buyer_workspace(1, _request())
    assert denied.value.status_code == 404


def test_customer_status_create_and_edit_preserves_allowed_value(tmp_path, monkeypatch):
    buyers = tmp_path / "buyers.json"
    users = tmp_path / "users.json"
    buyers.write_text("[]\n", encoding="utf-8")
    users.write_text('[{"account_id":"A"}]', encoding="utf-8")
    monkeypatch.setattr(buyer, "BUYER_FILE", buyers)
    monkeypatch.setattr(buyer, "USERS_FILE", users)
    buyer.save_buyer(_request(), "Lead Buyer", "Address", "lead@example.com", "KR", "Prospect")
    assert json.loads(buyers.read_text())[0]["status"] == "Prospect"
    buyer.update_buyer(0, _request(), "Lead Buyer", "Address", "lead@example.com", "KR", "Customer")
    assert json.loads(buyers.read_text())[0]["status"] == "Customer"
    audit = json.loads((tmp_path / "audit_log.json").read_text(encoding="utf-8"))
    assert [(item["action"], item["document_type"], item["document_no"]) for item in audit] == [
        ("Create", "Buyer", "Lead Buyer"), ("Update", "Buyer", "Lead Buyer"),
    ]
