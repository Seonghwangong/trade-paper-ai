import json
from datetime import datetime

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app import shipment


def _request(account):
    return Request({
        "type": "http", "method": "POST", "path": "/shipment/SHP-001/duplicate",
        "headers": [], "trade_paper_user": {"account_id": account, "email": f"{account}@example.com"},
    })


def test_duplicate_shipment_number_date_snapshot_actions_and_isolation(tmp_path, monkeypatch):
    path = tmp_path / "shipments.json"
    users = tmp_path / "users.json"
    source = {
        "account_id": "A", "shipment_no": "SHP-001", "shipment_date": "2025-01-01",
        "shipment_name": "Reusable Export", "customer": "ABC Trading", "buyer": "Samsung",
        "status": "Delivered", "invoice_no": "INV-016", "packing_no": "PK-028", "si_no": "SI-009",
        "bl_no": "BL-004", "co_no": "CO-003", "inspection_no": "IC-001",
        "insurance_no": "INS-001", "weight_no": "WT-001",
        "shipper": "Account Company", "shipper_address": "Busan", "consignee": "Samsung",
        "items": [{"name": "Laptop", "hs_code": "847130", "quantity": 10, "origin": "KR"}],
        "total_carton": "2", "container_no": "CONT-OLD", "actual_arrival": "2025-01-10",
    }
    other = {"account_id": "B", "shipment_no": "SHP-002", "shipment_name": "Private"}
    path.write_text(json.dumps([source, other]), encoding="utf-8")
    users.write_text(json.dumps([{"account_id": "A"}, {"account_id": "B"}]), encoding="utf-8")
    monkeypatch.setattr(shipment, "SHIPMENT_FILE", path)
    monkeypatch.setattr(shipment, "USERS_FILE", users)

    response = shipment.duplicate_shipment("SHP-001", _request("A"))
    assert response.status_code == 303 and response.headers["location"] == "/edit-shipment/SHP-003"
    records = json.loads(path.read_text(encoding="utf-8"))
    duplicate = records[-1]
    assert duplicate["shipment_no"] == "SHP-003"
    assert duplicate["shipment_date"] == datetime.now().strftime("%Y-%m-%d")
    assert duplicate["status"] == "Draft"
    for field in ("customer", "buyer", "invoice_no", "packing_no", "si_no", "shipper", "shipper_address", "consignee", "items", "total_carton"):
        assert duplicate[field] == source[field]
    for field in ("bl_no", "co_no", "inspection_no", "insurance_no", "weight_no", "container_no", "actual_arrival"):
        assert duplicate[field] == ""
    assert records[0] == source and records[1] == other
    audit = json.loads((tmp_path / "audit_log.json").read_text(encoding="utf-8"))
    assert audit[-1]["action"] == "Create" and audit[-1]["document_no"] == "SHP-003"

    list_html = shipment.shipment_list(_request("A")).body.decode()
    assert '/shipment/SHP-001/duplicate' in list_html and "Private" not in list_html
    with pytest.raises(HTTPException) as denied:
        shipment.duplicate_shipment("SHP-002", _request("A"))
    assert denied.value.status_code == 404
