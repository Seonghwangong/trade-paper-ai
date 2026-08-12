import json

import pytest
from fastapi import HTTPException
from starlette.requests import Request

import app.bill_of_lading as bill
import app.inspection_certificate as inspection
import app.invoice as invoice
import app.packing as packing
import app.product as product
import app.referential_integrity as referential_integrity
from app.account_inspection import ensure_legacy_inspection_ownership
from app.validation import DataValidationError


def _request(account_id, path="/inspection-list"):
    return Request({"type": "http", "method": "GET", "scheme": "http", "path": path,
                    "raw_path": path.encode(), "query_string": b"", "headers": [],
                    "client": ("127.0.0.1", 1), "server": ("testserver", 80),
                    "trade_paper_user": {"account_id": account_id, "company": account_id,
                                         "email": f"{account_id}@example.com"}})


def _form(suffix):
    return {"shipment_no": "", "inspection_date": "2026-08-01", "bl_no": f"BL-{suffix}",
            "packing_no": f"PK-{suffix}", "invoice_no": f"INV-{suffix}", "exporter": "Seller",
            "consignee": "Buyer", "inspection_company": "Inspector", "inspection_location": "Busan",
            "inspection_result": "Passed", "remarks": "Scope", "port_of_loading": "Busan",
            "port_of_discharge": "LA", "transport_details": "Vessel V001",
            "item_name": ["Widget"], "hs_code": ["1234"], "quantity": ["2"]}


def test_legacy_inspection_migration_is_idempotent_and_backed_up(tmp_path):
    source = tmp_path / "inspection_certificates.json"; users = tmp_path / "users.json"
    original = [{"inspection_no": "IC-001", "bl_no": "BL-001"}]
    source.write_text(json.dumps(original, indent=2) + "\n"); users.write_text(json.dumps([{"account_id": "legacy"}]))
    first = ensure_legacy_inspection_ownership(source, users); first_bytes = source.read_bytes()
    second = ensure_legacy_inspection_ownership(source, users)
    assert first[0]["account_id"] == "legacy" and second == first and source.read_bytes() == first_bytes
    assert json.loads((tmp_path / "inspection_certificates.backup.json").read_text()) == original


def test_inspection_scope_crud_sources_pdf_and_dependencies(tmp_path, monkeypatch):
    users = tmp_path / "users.json"; users.write_text(json.dumps([{"account_id": "A"}, {"account_id": "B"}]))
    inspection_file = tmp_path / "inspection_certificates.json"; inspection_file.write_text("[]\n")
    invoice_file = tmp_path / "invoices.json"; packing_file = tmp_path / "packing_lists.json"
    bl_file = tmp_path / "bills_of_lading.json"; product_file = tmp_path / "products.json"
    invoice_file.write_text(json.dumps([{"account_id": x, "invoice_no": f"INV-{x}", "items": []} for x in "AB"]))
    packing_file.write_text(json.dumps([{"account_id": x, "packing_no": f"PK-{x}", "invoice_no": f"INV-{x}", "items": []} for x in "AB"]))
    bl_file.write_text(json.dumps([{"account_id": x, "bl_no": f"BL-{x}", "packing_no": f"PK-{x}", "invoice_no": f"INV-{x}", "shipper": "Seller", "consignee": "Buyer", "items": [{"name": "Widget", "hs_code": "1234", "quantity": "2"}]} for x in "AB"]))
    product_file.write_text(json.dumps([{"account_id": x, "name": "Widget", "hs_code": "1234"} for x in "AB"]))
    for module, attr, path in [(inspection, "INSPECTION_FILE", inspection_file), (invoice, "INVOICE_FILE", invoice_file), (packing, "PACKING_FILE", packing_file), (bill, "BL_FILE", bl_file), (product, "PRODUCT_FILE", product_file)]:
        monkeypatch.setattr(module, attr, path)
        if hasattr(module, "USERS_FILE"): monkeypatch.setattr(module, "USERS_FILE", users)
    monkeypatch.setattr(inspection, "USERS_FILE", users); monkeypatch.setattr(inspection, "find_dependencies", lambda module, identifier, account_id: [])

    assert inspection.payload_from_bl("BL-A", product.load_products("A"), "A")["bl_no"] == "BL-A"
    assert inspection.payload_from_bl("BL-B", product.load_products("A"), "A")["bl_no"] == ""
    inspection.save_inspection(_request("A"), **_form("A")); inspection.save_inspection(_request("B"), **_form("B"))
    raw = json.loads(inspection_file.read_text()); assert [row["account_id"] for row in raw] == ["A", "B"]
    assert "IC-001" in inspection.inspection_list(_request("A")).body.decode() and "IC-002" not in inspection.inspection_list(_request("A")).body.decode()
    assert "account_id" not in inspection.inspection_data("IC-001", _request("A"))
    assert inspection.inspection_detail("IC-001", _request("A")).status_code == 200
    assert inspection.inspection_pdf("IC-001", _request("A")).body.startswith(b"%PDF")
    preview = inspection.create_inspection_pdf(_request("A", "/inspection/pdf"), {**inspection.inspection_data("IC-001", _request("A")), "account_id": "forged"})
    assert preview.body.startswith(b"%PDF") and b"account_id" not in preview.body
    with pytest.raises(DataValidationError): inspection.save_inspection(_request("A"), **_form("B"))

    shipment_file = tmp_path / "shipments.json"; shipment_file.write_text(json.dumps([{"account_id": "A", "shipment_no": "SHP-A", "inspection_no": "IC-001"}, {"account_id": "B", "shipment_no": "SHP-B", "inspection_no": "IC-001"}]))
    original_data_path = referential_integrity.data_path
    mapping = {"inspection_certificates.json": inspection_file, "shipments.json": shipment_file}
    monkeypatch.setattr(referential_integrity, "data_path", lambda filename: mapping.get(filename, original_data_path(filename)))
    assert [item["identifier"] for item in referential_integrity.find_dependencies("Inspection Certificate", "IC-001", "A")] == ["SHP-A"]

    for action in [lambda: inspection.edit_inspection("IC-002", _request("A")), lambda: inspection.inspection_detail("IC-002", _request("A")), lambda: inspection.inspection_data("IC-002", _request("A")), lambda: inspection.delete_inspection("IC-002", _request("A")), lambda: inspection.confirm_delete_inspection("IC-002", _request("A")), lambda: inspection.inspection_pdf("IC-002", _request("A"))]:
        with pytest.raises(HTTPException) as denied: action()
        assert denied.value.status_code == 404
    inspection.confirm_delete_inspection("IC-001", _request("A"))
    assert inspection.load_inspections("A") == [] and inspection.load_inspections("B")[0]["inspection_no"] == "IC-002"
