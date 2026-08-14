import json
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app import admin_dashboard, auth
import app.storage as storage


def _request(admin=True):
    return Request({
        "type": "http", "method": "GET", "scheme": "http", "path": "/admin/dashboard",
        "raw_path": b"/admin/dashboard", "query_string": b"", "headers": [],
        "server": ("test", 80), "client": ("127.0.0.1", 1),
        "trade_paper_user": {"account_id": "A", "email": "admin@example.com", "is_admin": admin},
    })


def _write(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")


def test_admin_dashboard_statistics_recent_activity_email_and_isolation(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DATA_DIR", tmp_path)
    users = tmp_path / "users.json"
    email = tmp_path / "email_history.json"
    billing = tmp_path / "billing_history.json"
    buyers = tmp_path / "buyers.json"
    shipments = tmp_path / "shipments.json"
    monkeypatch.setattr(admin_dashboard, "USERS_FILE", users)
    monkeypatch.setattr(admin_dashboard, "EMAIL_HISTORY_FILE", email)
    monkeypatch.setattr(admin_dashboard, "BILLING_HISTORY_FILE", billing)
    monkeypatch.setattr(admin_dashboard, "BUYERS_FILE", buyers)
    monkeypatch.setattr(admin_dashboard, "SHIPMENTS_FILE", shipments)
    _write(users, [
        {"account_id": "A", "company": "Alpha", "email": "a@example.com", "plan": "Free", "subscription_status": "Trial", "registered_at": "2026-08-13T01:00:00Z"},
        {"account_id": "B", "company": "Beta", "email": "b@example.com", "plan": "Starter", "subscription_status": "Active", "registered_at": "2026-08-12T01:00:00Z"},
        {"account_id": "C", "company": "Closed", "email": "c@example.com", "plan": "Professional", "subscription_status": "Cancelled"},
    ])
    _write(email, [
        {"account_id": "A", "sent_at": "2026-08-13T02:00:00Z", "document_no": "INV-001", "status": "Success"},
        {"account_id": "B", "sent_at": "2026-08-13T03:00:00Z", "document_no": "PK-001", "status": "Failed"},
    ])
    _write(billing, [])
    _write(buyers, [
        {"account_id": "A", "name": "Lead", "status": "Lead"},
        {"account_id": "B", "name": "Prospect", "status": "Prospect"},
        {"account_id": "B", "name": "Customer", "status": "Customer"},
        {"account_id": "C", "name": "Legacy"},
    ])
    _write(shipments, [
        {"account_id": "A", "shipment_no": "SHP-001", "shipment_date": "2026-08-13", "status": "Draft"},
        {"account_id": "B", "shipment_no": "SHP-002", "shipment_date": "2026-08-12", "status": "In Transit"},
        {"account_id": "C", "shipment_no": "SHP-003", "shipment_date": "2026-08-11", "status": "Delivered"},
    ])
    _write(tmp_path / "invoices.json", [
        {"account_id": "A", "invoice_no": "INV-001", "invoice_date": "2026-08-13"},
        {"account_id": "B", "invoice_no": "INV-002", "invoice_date": "2026-08-01"},
    ])
    for definition in admin_dashboard.DOCUMENT_DEFINITIONS:
        path = tmp_path / definition.storage_filename
        if not path.exists():
            _write(path, {} if definition.key == "company" else [])

    metrics = admin_dashboard.admin_dashboard_metrics(datetime(2026, 8, 13, tzinfo=timezone.utc))
    assert metrics["users"] == {"Total Users": 3, "Active Users": 1, "Trial Users": 1, "Paid Users": 1}
    assert metrics["documents_today"] == 2  # invoice and shipment
    assert metrics["documents_month"] == 5
    assert metrics["document_counts"]["Commercial Invoice"] == 2
    assert metrics["revenue"] == {"MRR": 0, "Active Subscription": 1, "Trial Conversion": 0}
    assert metrics["email"]["Success"] == 1 and metrics["email"]["Failed"] == 1
    assert metrics["email"]["recent"][0]["document_no"] == "PK-001"
    assert metrics["shipments"] == {"Draft": 1, "In Transit": 1, "Delivered": 1}
    assert metrics["customers"] == {"Lead": 2, "Prospect": 1, "Customer": 1, "Inactive": 0}
    assert metrics["recent_users"][0]["company"] == "Alpha"
    assert metrics["recent_documents"][0]["identifier"] in {"INV-001", "SHP-001"}

    html = admin_dashboard.admin_dashboard(_request()).body.decode()
    for section in ("Overview", "Documents", "Revenue", "Email", "Shipments", "Customers", "Recent Activity", "Quick Actions"):
        assert f">{section}<" in html
    assert "$0.00" in html and "INV-001" in html and "PK-001" in html
    assert "/company" in html and "/invoice" in html and "/shipment-form" in html
    with pytest.raises(HTTPException) as denied:
        admin_dashboard.admin_dashboard(_request(False))
    assert denied.value.status_code == 403


def test_admin_role_or_environment_allowlist_only():
    assert auth.user_is_admin({"email": "owner@example.com", "role": "admin"}, {})
    assert auth.user_is_admin({"email": "owner@example.com"}, {"TRADE_PAPER_ADMIN_EMAILS": "other@example.com, OWNER@example.com"})
    assert not auth.user_is_admin({"email": "owner@example.com"}, {})
