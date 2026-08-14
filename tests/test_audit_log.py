import json
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app import audit_log


def _request(account="A", email="owner@example.com", admin=False, path="/audit-log"):
    return Request({
        "type": "http", "method": "GET", "scheme": "http", "path": path,
        "raw_path": path.encode(), "query_string": b"", "headers": [],
        "server": ("test", 80), "client": ("127.0.0.1", 1),
        "trade_paper_user": {"account_id": account, "email": email, "is_admin": admin},
    })


def test_audit_records_only_safe_fields_and_supports_search(tmp_path, monkeypatch):
    path = tmp_path / "audit_log.json"
    monkeypatch.setattr(audit_log, "AUDIT_FILE", path)
    audit_log.record_audit("A", "owner@example.com", "Create", "Commercial Invoice", "INV-001", datetime(2026, 8, 13, 1, tzinfo=timezone.utc))
    audit_log.record_audit("A", "staff@example.com", "Send Email", "Packing List", "PK-001", datetime(2026, 8, 12, 1, tzinfo=timezone.utc))
    audit_log.record_audit("B", "other@example.com", "Update", "Shipment", "SHP-999", datetime(2026, 8, 13, 2, tzinfo=timezone.utc))

    stored = json.loads(path.read_text(encoding="utf-8"))
    assert set(stored[0]) == set(audit_log.ALLOWED_FIELDS)
    serialized = json.dumps(stored).casefold()
    for secret in ("password", "reset_token", "smtp_password", "email_body", "attachment"):
        assert secret not in serialized
    assert [row["document_no"] for row in audit_log.query_audit("A")] == ["INV-001", "PK-001"]
    assert [row["document_no"] for row in audit_log.query_audit("A", date="2026-08-13")] == ["INV-001"]
    assert [row["document_no"] for row in audit_log.query_audit("A", user="staff")] == ["PK-001"]
    assert [row["document_no"] for row in audit_log.query_audit("A", document="invoice")] == ["INV-001"]


def test_account_and_admin_audit_pages_enforce_isolation(tmp_path, monkeypatch):
    path = tmp_path / "audit_log.json"
    monkeypatch.setattr(audit_log, "AUDIT_FILE", path)
    audit_log.record_audit("A", "a@example.com", "Create", "Buyer", "Alpha")
    audit_log.record_audit("B", "b@example.com", "Create", "Buyer", "Beta")

    account_html = audit_log.account_audit_log(_request(), document="Buyer").body.decode()
    assert "Alpha" in account_html and "Beta" not in account_html
    assert 'type="date"' in account_html and "Search user" in account_html and "Search document" in account_html

    with pytest.raises(HTTPException) as denied:
        audit_log.admin_audit_log(_request(), "", "", "")
    assert denied.value.status_code == 403
    admin_html = audit_log.admin_audit_log(_request(admin=True, path="/admin/audit-log"), "", "", "Buyer").body.decode()
    assert "Alpha" in admin_html and "Beta" in admin_html


def test_request_audit_uses_only_authenticated_identity(tmp_path):
    path = tmp_path / "audit_log.json"
    request = _request(email="safe@example.com")
    audit_log.record_request_audit(request, "Update", "Product", "Laptop", path=path)
    assert json.loads(path.read_text(encoding="utf-8"))[0] == {
        "time": json.loads(path.read_text(encoding="utf-8"))[0]["time"],
        "account_id": "A", "user": "safe@example.com", "action": "Update",
        "document_type": "Product", "document_no": "Laptop",
    }
