import json

import pytest
from fastapi import HTTPException
from starlette.requests import Request

import app.bill_of_lading as bill
import app.invoice as invoice
import app.packing as packing
import app.product as product
import app.referential_integrity as referential_integrity
import app.weight_certificate as weight
from app.account_weight import ensure_legacy_weight_ownership
from app.validation import DataValidationError


def _request(account_id, path="/weight-list"):
    return Request({"type": "http", "method": "GET", "scheme": "http", "path": path,
                    "raw_path": path.encode(), "query_string": b"", "headers": [],
                    "client": ("127.0.0.1", 1), "server": ("testserver", 80),
                    "trade_paper_user": {"account_id": account_id, "company": account_id,
                                         "email": f"{account_id}@example.com"}})


def _form(suffix):
    return {"shipment_no": "", "weight_date": "2026-08-01", "bl_no": f"BL-{suffix}",
            "packing_no": f"PK-{suffix}", "invoice_no": f"INV-{suffix}", "exporter": "Seller",
            "consignee": "Buyer", "transport_details": "Vessel V001", "port_of_loading": "Busan",
            "port_of_discharge": "LA", "weighing_place": "Busan", "weighing_method": "Scale",
            "remarks": "Scope", "item_name": ["Widget"], "hs_code": ["1234"], "quantity": ["2"],
            "carton": ["1"], "net_weight": ["10"], "gross_weight": ["12"],
            "total_net_weight": "10", "total_gross_weight": "12"}


def test_legacy_weight_migration_is_idempotent_and_backed_up(tmp_path):
    source = tmp_path / "weight_certificates.json"; users = tmp_path / "users.json"
    original = [{"weight_no": "WT-001", "bl_no": "BL-001"}]
    source.write_text(json.dumps(original, indent=2) + "\n"); users.write_text(json.dumps([{"account_id": "legacy"}]))
    first = ensure_legacy_weight_ownership(source, users); first_bytes = source.read_bytes()
    second = ensure_legacy_weight_ownership(source, users)
    assert first[0]["account_id"] == "legacy" and second == first and source.read_bytes() == first_bytes
    assert json.loads((tmp_path / "weight_certificates.backup.json").read_text()) == original


def test_weight_scope_crud_sources_pdf_and_dependencies(tmp_path, monkeypatch):
    users = tmp_path / "users.json"; users.write_text(json.dumps([{"account_id": "A"}, {"account_id": "B"}]))
    weight_file = tmp_path / "weight_certificates.json"; weight_file.write_text("[]\n")
    invoice_file = tmp_path / "invoices.json"; packing_file = tmp_path / "packing_lists.json"
    bl_file = tmp_path / "bills_of_lading.json"; product_file = tmp_path / "products.json"
    invoice_file.write_text(json.dumps([{"account_id": x, "invoice_no": f"INV-{x}", "items": []} for x in "AB"]))
    packing_file.write_text(json.dumps([{"account_id": x, "packing_no": f"PK-{x}", "invoice_no": f"INV-{x}", "items": []} for x in "AB"]))
    bl_file.write_text(json.dumps([{"account_id": x, "bl_no": f"BL-{x}", "packing_no": f"PK-{x}", "invoice_no": f"INV-{x}", "shipper": "Seller", "consignee": "Buyer", "items": [{"name": "Widget", "hs_code": "", "quantity": "2", "net_weight": "10", "gross_weight": "12"}]} for x in "AB"]))
    product_file.write_text(json.dumps([{"account_id": x, "name": "Widget", "hs_code": f"HS-{x}"} for x in "AB"]))
    for module, attr, path in [(weight, "WEIGHT_FILE", weight_file), (invoice, "INVOICE_FILE", invoice_file), (packing, "PACKING_FILE", packing_file), (bill, "BL_FILE", bl_file), (product, "PRODUCT_FILE", product_file)]:
        monkeypatch.setattr(module, attr, path)
        if hasattr(module, "USERS_FILE"): monkeypatch.setattr(module, "USERS_FILE", users)
    monkeypatch.setattr(weight, "USERS_FILE", users); monkeypatch.setattr(weight, "find_dependencies", lambda module, identifier, account_id: [])

    assert weight.payload_from_bl("BL-A", "A")["items"][0]["hs_code"] == "HS-A"
    assert weight.payload_from_bl("BL-B", "A")["bl_no"] == ""
    weight.save_weight(_request("A"), **_form("A")); weight.save_weight(_request("B"), **_form("B"))
    raw = json.loads(weight_file.read_text()); assert [row["account_id"] for row in raw] == ["A", "B"]
    assert "WT-001" in weight.weight_list(_request("A")).body.decode() and "WT-002" not in weight.weight_list(_request("A")).body.decode()
    assert "account_id" not in weight.weight_data("WT-001", _request("A"))
    assert weight.weight_detail("WT-001", _request("A")).status_code == 200
    assert weight.weight_pdf("WT-001", _request("A")).body.startswith(b"%PDF")
    preview = weight.create_weight_pdf(_request("A", "/weight/pdf"), {**weight.weight_data("WT-001", _request("A")), "account_id": "forged"})
    assert preview.body.startswith(b"%PDF") and b"account_id" not in preview.body
    with pytest.raises(DataValidationError): weight.save_weight(_request("A"), **_form("B"))

    shipment_file = tmp_path / "shipments.json"; shipment_file.write_text(json.dumps([{"account_id": "A", "shipment_no": "SHP-A", "weight_no": "WT-001"}, {"account_id": "B", "shipment_no": "SHP-B", "weight_no": "WT-001"}]))
    original_data_path = referential_integrity.data_path
    mapping = {"weight_certificates.json": weight_file, "shipments.json": shipment_file}
    monkeypatch.setattr(referential_integrity, "data_path", lambda filename: mapping.get(filename, original_data_path(filename)))
    assert [item["identifier"] for item in referential_integrity.find_dependencies("Weight Certificate", "WT-001", "A")] == ["SHP-A"]

    for action in [lambda: weight.edit_weight("WT-002", _request("A")), lambda: weight.weight_detail("WT-002", _request("A")), lambda: weight.weight_data("WT-002", _request("A")), lambda: weight.delete_weight("WT-002", _request("A")), lambda: weight.confirm_delete_weight("WT-002", _request("A")), lambda: weight.weight_pdf("WT-002", _request("A"))]:
        with pytest.raises(HTTPException) as denied: action()
        assert denied.value.status_code == 404
    weight.confirm_delete_weight("WT-001", _request("A"))
    assert weight.load_weights("A") == [] and weight.load_weights("B")[0]["weight_no"] == "WT-002"
