import json

import pytest
from fastapi import HTTPException
from starlette.requests import Request

import app.bill_of_lading as bill
import app.invoice as invoice
import app.main as main
import app.packing as packing
import app.shipment as shipment
from app.account_bill_of_lading import ensure_legacy_bill_of_lading_ownership
from app.validation import DataValidationError


def _request(account_id, path="/bl-list"):
    return Request({
        "type": "http", "method": "GET", "scheme": "http", "path": path,
        "raw_path": path.encode(), "query_string": b"", "headers": [],
        "client": ("127.0.0.1", 1), "server": ("testserver", 80),
        "trade_paper_user": {"account_id": account_id, "company": account_id,
                             "email": f"{account_id}@example.com"},
    })


def _form(packing_no, invoice_no, shipment_no=""):
    return {
        "shipment_no": shipment_no, "packing_no": packing_no,
        "invoice_no": invoice_no, "shipper": "Seller", "consignee": "Buyer",
        "notify_party": "", "vessel": "Vessel", "voyage_no": "V001",
        "port_of_loading": "Busan", "port_of_discharge": "LA",
        "place_of_delivery": "LA", "bl_date": "2026-08-01",
        "item_name": ["Product"], "quantity": ["2"], "hs_code": ["1234"],
        "carton": ["1"], "net_weight": ["10"], "gross_weight": ["12"],
        "total_carton": "1", "total_net_weight": "10", "total_gross_weight": "12",
    }


def test_legacy_bill_migration_is_idempotent_and_backed_up(tmp_path):
    bl_file = tmp_path / "bills_of_lading.json"
    users_file = tmp_path / "users.json"
    original = [{"bl_no": "BL-001", "packing_no": "PK-001"}]
    bl_file.write_text(json.dumps(original, indent=2) + "\n", encoding="utf-8")
    users_file.write_text(json.dumps([{"account_id": "legacy"}]), encoding="utf-8")
    first = ensure_legacy_bill_of_lading_ownership(bl_file, users_file)
    first_bytes = bl_file.read_bytes()
    second = ensure_legacy_bill_of_lading_ownership(bl_file, users_file)
    assert first[0]["account_id"] == "legacy"
    assert second == first and bl_file.read_bytes() == first_bytes
    assert json.loads((tmp_path / "bills_of_lading.backup.json").read_text()) == original


def test_bill_scope_crud_api_pdf_sources_and_shipment_discovery(tmp_path, monkeypatch):
    users_file = tmp_path / "users.json"
    bl_file = tmp_path / "bills_of_lading.json"
    packing_file = tmp_path / "packing_lists.json"
    invoice_file = tmp_path / "invoices.json"
    shipment_file = tmp_path / "shipments.json"
    users_file.write_text(json.dumps([{"account_id": "a"}, {"account_id": "b"}]))
    bl_file.write_text("[]\n")
    packing_file.write_text(json.dumps([
        {"account_id": "a", "packing_no": "PK-A", "invoice_no": "INV-A", "items": []},
        {"account_id": "b", "packing_no": "PK-B", "invoice_no": "INV-B", "items": []},
    ]))
    invoice_file.write_text(json.dumps([
        {"account_id": "a", "invoice_no": "INV-A", "items": []},
        {"account_id": "b", "invoice_no": "INV-B", "items": []},
    ]))
    shipment_file.write_text(json.dumps([
        {"account_id": "a", "shipment_no": "SHP-A", "packing_no": "PK-A", "invoice_no": "INV-A", "bl_no": "BL-001"},
        {"account_id": "b", "shipment_no": "SHP-B", "packing_no": "PK-B", "invoice_no": "INV-B", "bl_no": "BL-002"},
    ]))
    monkeypatch.setattr(bill, "BL_FILE", bl_file)
    monkeypatch.setattr(bill, "USERS_FILE", users_file)
    monkeypatch.setattr(packing, "PACKING_FILE", packing_file)
    monkeypatch.setattr(packing, "USERS_FILE", users_file)
    monkeypatch.setattr(invoice, "INVOICE_FILE", invoice_file)
    monkeypatch.setattr(invoice, "USERS_FILE", users_file)
    monkeypatch.setattr(shipment, "SHIPMENT_FILE", shipment_file)
    monkeypatch.setattr(shipment, "USERS_FILE", users_file)
    monkeypatch.setattr(bill, "find_dependencies", lambda module, identifier, account_id: [])

    bill.save_bl(_request("a"), **_form("PK-A", "INV-A", "SHP-A"))
    bill.save_bl(_request("b"), **_form("PK-B", "INV-B", "SHP-B"))
    assert [r["account_id"] for r in json.loads(bl_file.read_text())] == ["a", "b"]
    assert "BL-001" in bill.bl_list(_request("a")).body.decode()
    assert "BL-002" not in bill.bl_list(_request("a")).body.decode()
    assert "account_id" not in bill.bl_data("BL-001", _request("a"))
    assert bill.bl_pdf("BL-001", _request("a")).body.startswith(b"%PDF")
    preview = bill.create_bl_pdf(_request("a", "/bl/pdf"), {**bill.bl_data("BL-001", _request("a")), "account_id": "forged"})
    assert preview.body.startswith(b"%PDF") and b"account_id" not in preview.body
    search_a = main.global_search_results("BL-", bills_of_lading=bill.load_bills_of_lading("a"))
    search_b = main.global_search_results("BL-", bills_of_lading=bill.load_bills_of_lading("b"))
    assert any(result["identifier"] == "BL-001" for result in search_a)
    assert not any(result["identifier"] == "BL-002" for result in search_a)
    assert any(result["identifier"] == "BL-002" for result in search_b)
    with pytest.raises(DataValidationError):
        bill.save_bl(_request("a"), **_form("PK-B", "INV-B"))
    for action in (
        lambda: bill.edit_bl("BL-002", _request("a")),
        lambda: bill.bl_data("BL-002", _request("a")),
        lambda: bill.delete_bl("BL-002", _request("a")),
        lambda: bill.confirm_delete_bl("BL-002", _request("a")),
        lambda: bill.bl_pdf("BL-002", _request("a")),
    ):
        with pytest.raises(HTTPException) as denied:
            action()
        assert denied.value.status_code == 404

    datasets_a = shipment.load_workflow_datasets("a")
    datasets_b = shipment.load_workflow_datasets("b")
    assert [r["bl_no"] for r in datasets_a["bills_of_lading.json"]] == ["BL-001"]
    assert [r["bl_no"] for r in datasets_b["bills_of_lading.json"]] == ["BL-002"]
