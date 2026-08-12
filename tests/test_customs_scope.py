import json

import pytest
from fastapi import HTTPException
from starlette.requests import Request

import app.bill_of_lading as bill
import app.booking_confirmation as booking
import app.container_management as container
import app.customs_declaration as customs
import app.invoice as invoice
import app.packing as packing
import app.product as product
import app.referential_integrity as referential_integrity
import app.shipment as shipment
from app.account_customs import ensure_legacy_customs_ownership
from app.validation import DataValidationError


def _request(account_id, path="/customs-list"):
    return Request({"type": "http", "method": "GET", "scheme": "http", "path": path,
                    "raw_path": path.encode(), "query_string": b"", "headers": [],
                    "client": ("127.0.0.1", 1), "server": ("testserver", 80),
                    "trade_paper_user": {"account_id": account_id, "company": account_id,
                                         "email": f"{account_id}@example.com"}})


def _form(suffix):
    return {
        "customs_date": "2026-08-01", "declaration_no": f"DEC-{suffix}",
        "shipment_no": f"SHP-{suffix}", "booking_record_no": f"BK-{suffix}",
        "invoice_no": f"INV-{suffix}", "packing_no": f"PK-{suffix}",
        "container_record_no": f"CON-{suffix}", "bl_no": f"BL-{suffix}",
        "exporter": "Seller", "consignee": "Buyer", "country_of_origin": "",
        "destination_country": "USA", "port_of_loading": "Busan",
        "port_of_discharge": "LA", "vessel": "Vessel", "voyage_no": "V001",
        "container_no": "CONT", "seal_no": "SEAL", "customs_office": "Office",
        "declaration_type": "Export", "incoterms": "FOB", "currency": "USD",
        "total_invoice_value": "20", "remarks": "Scope", "item_name": ["Widget"],
        "hs_code": ["1234"], "quantity": ["2"], "unit_price": ["10"],
        "amount": ["20"], "origin": [""], "net_weight": ["10"],
        "gross_weight": ["12"], "total_quantity": "2", "total_net_weight": "10",
        "total_gross_weight": "12", "total_amount": "20",
    }


def test_legacy_customs_migration_is_idempotent_and_backed_up(tmp_path):
    customs_file = tmp_path / "customs_declarations.json"
    users_file = tmp_path / "users.json"
    original = [{"customs_record_no": "CD-001", "declaration_no": "LEGACY"}]
    customs_file.write_text(json.dumps(original, indent=2) + "\n")
    users_file.write_text(json.dumps([{"account_id": "legacy"}]))
    first = ensure_legacy_customs_ownership(customs_file, users_file)
    first_bytes = customs_file.read_bytes()
    second = ensure_legacy_customs_ownership(customs_file, users_file)
    assert first[0]["account_id"] == "legacy"
    assert second == first and customs_file.read_bytes() == first_bytes
    assert json.loads((tmp_path / "customs_declarations.backup.json").read_text()) == original


def test_customs_account_scope_crud_sources_pdf_and_origin(tmp_path, monkeypatch):
    users = tmp_path / "users.json"; users.write_text(json.dumps([{"account_id": "A"}, {"account_id": "B"}]))
    files = {name: tmp_path / name for name in ["customs_declarations.json", "shipments.json", "booking_confirmations.json", "invoices.json", "packing_lists.json", "containers.json", "bills_of_lading.json", "products.json"]}
    files["customs_declarations.json"].write_text("[]\n")
    for name, key, prefix in [
        ("shipments.json", "shipment_no", "SHP"), ("booking_confirmations.json", "booking_record_no", "BK"),
        ("invoices.json", "invoice_no", "INV"), ("packing_lists.json", "packing_no", "PK"),
        ("containers.json", "container_record_no", "CON"), ("bills_of_lading.json", "bl_no", "BL")]:
        rows = []
        for owner in ["A", "B"]:
            row = {"account_id": owner, key: f"{prefix}-{owner}", "shipment_no": f"SHP-{owner}",
                   "invoice_no": f"INV-{owner}", "packing_no": f"PK-{owner}", "items": []}
            rows.append(row)
        files[name].write_text(json.dumps(rows))
    files["products.json"].write_text(json.dumps([
        {"account_id": "A", "name": "Widget", "hs_code": "1234", "origin": "Korea"},
        {"account_id": "B", "name": "Widget", "hs_code": "1234", "origin": "Japan"},
    ]))
    for module, file_attr, filename in [(customs, "CUSTOMS_FILE", "customs_declarations.json"), (shipment, "SHIPMENT_FILE", "shipments.json"), (booking, "BOOKING_FILE", "booking_confirmations.json"), (invoice, "INVOICE_FILE", "invoices.json"), (packing, "PACKING_FILE", "packing_lists.json"), (container, "CONTAINER_FILE", "containers.json"), (bill, "BL_FILE", "bills_of_lading.json"), (product, "PRODUCT_FILE", "products.json")]:
        monkeypatch.setattr(module, file_attr, files[filename])
        if hasattr(module, "USERS_FILE"):
            monkeypatch.setattr(module, "USERS_FILE", users)
    monkeypatch.setattr(customs, "USERS_FILE", users)

    customs.save_customs_record(_request("A"), **_form("A"))
    customs.save_customs_record(_request("B"), **_form("B"))
    raw = json.loads(files["customs_declarations.json"].read_text())
    assert [row["account_id"] for row in raw] == ["A", "B"]
    assert raw[0]["items"][0]["origin"] == "Korea" and raw[1]["items"][0]["origin"] == "Japan"
    assert "CD-001" in customs.customs_list(_request("A")).body.decode()
    assert "CD-002" not in customs.customs_list(_request("A")).body.decode()
    assert "account_id" not in customs.customs_data("CD-001", _request("A"))
    assert customs.customs_detail("CD-001", _request("A")).status_code == 200
    assert customs.customs_pdf("CD-001", _request("A")).body.startswith(b"%PDF")
    preview = customs.create_customs_pdf(_request("A", "/customs/pdf"), {**customs.customs_data("CD-001", _request("A")), "account_id": "forged"})
    assert preview.body.startswith(b"%PDF") and b"account_id" not in preview.body
    original_data_path = referential_integrity.data_path
    monkeypatch.setattr(
        referential_integrity,
        "data_path",
        lambda filename: files.get(filename, original_data_path(filename)),
    )
    dependencies = referential_integrity.find_dependencies("Commercial Invoice", "INV-A", "A")
    assert [item["identifier"] for item in dependencies if item["module"] == "Customs Declaration"] == ["CD-001"]
    with pytest.raises(DataValidationError):
        customs.save_customs_record(_request("A"), **_form("B"))
    for action in [lambda: customs.edit_customs("CD-002", _request("A")), lambda: customs.customs_detail("CD-002", _request("A")), lambda: customs.customs_data("CD-002", _request("A")), lambda: customs.delete_customs("CD-002", _request("A")), lambda: customs.confirm_delete_customs("CD-002", _request("A")), lambda: customs.customs_pdf("CD-002", _request("A"))]:
        with pytest.raises(HTTPException) as denied:
            action()
        assert denied.value.status_code == 404
    customs.confirm_delete_customs("CD-001", _request("A"))
    assert customs.load_customs("A") == [] and customs.load_customs("B")[0]["customs_record_no"] == "CD-002"
