import json

import pytest
from fastapi import HTTPException
from starlette.requests import Request

import app.bill_of_lading as bill
import app.certificate_of_origin as certificate
import app.invoice as invoice
import app.packing as packing
import app.product as product
import app.referential_integrity as referential_integrity
import app.shipment as shipment
from app.account_certificate_of_origin import ensure_legacy_certificate_of_origin_ownership
from app.validation import DataValidationError


def _request(account_id, path="/co-list"):
    return Request({"type": "http", "method": "GET", "scheme": "http", "path": path,
                    "raw_path": path.encode(), "query_string": b"", "headers": [],
                    "client": ("127.0.0.1", 1), "server": ("testserver", 80),
                    "trade_paper_user": {"account_id": account_id, "company": account_id,
                                         "email": f"{account_id}@example.com"}})


def _form(suffix):
    return {"shipment_no": "", "co_date": "2026-08-01", "bl_no": f"BL-{suffix}",
            "invoice_no": f"INV-{suffix}", "packing_no": f"PK-{suffix}",
            "exporter": "Seller", "consignee": "Buyer", "country_of_origin": "Korea",
            "destination_country": "USA", "transport_details": "Vessel V001",
            "port_of_loading": "Busan", "port_of_discharge": "LA", "remarks": "Scope",
            "item_name": ["Widget"], "hs_code": ["1234"], "quantity": ["2"],
            "origin": ["Korea"]}


def test_legacy_certificate_migration_is_idempotent_and_backed_up(tmp_path):
    co_file = tmp_path / "certificates_of_origin.json"; users = tmp_path / "users.json"
    original = [{"co_no": "CO-001", "bl_no": "BL-001"}]
    co_file.write_text(json.dumps(original, indent=2) + "\n"); users.write_text(json.dumps([{"account_id": "legacy"}]))
    first = ensure_legacy_certificate_of_origin_ownership(co_file, users)
    first_bytes = co_file.read_bytes(); second = ensure_legacy_certificate_of_origin_ownership(co_file, users)
    assert first[0]["account_id"] == "legacy"
    assert second == first and co_file.read_bytes() == first_bytes
    assert json.loads((tmp_path / "certificates_of_origin.backup.json").read_text()) == original


def test_certificate_scope_crud_sources_pdf_and_shipment_discovery(tmp_path, monkeypatch):
    users = tmp_path / "users.json"; users.write_text(json.dumps([{"account_id": "A"}, {"account_id": "B"}]))
    co_file = tmp_path / "certificates_of_origin.json"; co_file.write_text("[]\n")
    invoice_file = tmp_path / "invoices.json"; packing_file = tmp_path / "packing_lists.json"
    bl_file = tmp_path / "bills_of_lading.json"; product_file = tmp_path / "products.json"
    invoice_file.write_text(json.dumps([{"account_id": x, "invoice_no": f"INV-{x}", "items": []} for x in "AB"]))
    packing_file.write_text(json.dumps([{"account_id": x, "packing_no": f"PK-{x}", "invoice_no": f"INV-{x}", "items": []} for x in "AB"]))
    bl_file.write_text(json.dumps([{"account_id": x, "bl_no": f"BL-{x}", "packing_no": f"PK-{x}", "invoice_no": f"INV-{x}", "shipper": "Seller", "consignee": "Buyer", "items": [{"name": "Widget", "hs_code": "1234", "quantity": "2"}]} for x in "AB"]))
    product_file.write_text(json.dumps([{"account_id": "A", "name": "Widget", "hs_code": "1234", "origin": "Korea"}, {"account_id": "B", "name": "Widget", "hs_code": "1234", "origin": "Japan"}]))
    for module, attr, path in [(certificate, "CO_FILE", co_file), (invoice, "INVOICE_FILE", invoice_file), (packing, "PACKING_FILE", packing_file), (bill, "BL_FILE", bl_file), (product, "PRODUCT_FILE", product_file)]:
        monkeypatch.setattr(module, attr, path)
        if hasattr(module, "USERS_FILE"): monkeypatch.setattr(module, "USERS_FILE", users)
    monkeypatch.setattr(certificate, "USERS_FILE", users)
    monkeypatch.setattr(certificate, "find_dependencies", lambda module, identifier, account_id: [])

    assert certificate.payload_from_bl("BL-A", product.load_products("A"), "A")["items"][0]["origin"] == "Korea"
    assert certificate.payload_from_bl("BL-B", product.load_products("B"), "B")["items"][0]["origin"] == "Japan"
    assert certificate.payload_from_bl("BL-B", product.load_products("A"), "A")["bl_no"] == ""
    certificate.save_co(_request("A"), **_form("A")); certificate.save_co(_request("B"), **_form("B"))
    raw = json.loads(co_file.read_text()); assert [row["account_id"] for row in raw] == ["A", "B"]
    assert "CO-001" in certificate.co_list(_request("A")).body.decode() and "CO-002" not in certificate.co_list(_request("A")).body.decode()
    assert "account_id" not in certificate.co_data("CO-001", _request("A"))
    assert certificate.co_detail("CO-001", _request("A")).status_code == 200
    assert certificate.co_pdf("CO-001", _request("A")).body.startswith(b"%PDF")
    preview = certificate.create_co_pdf(_request("A", "/co/pdf"), {**certificate.co_data("CO-001", _request("A")), "account_id": "forged"})
    assert preview.body.startswith(b"%PDF") and b"account_id" not in preview.body
    with pytest.raises(DataValidationError): certificate.save_co(_request("A"), **_form("B"))

    shipment_file = tmp_path / "shipments.json"
    shipment_file.write_text(json.dumps([
        {"account_id": "A", "shipment_no": "SHP-A", "co_no": "CO-001"},
        {"account_id": "B", "shipment_no": "SHP-B", "co_no": "CO-001"},
    ]))
    original_data_path = referential_integrity.data_path
    mapping = {"certificates_of_origin.json": co_file, "shipments.json": shipment_file}
    monkeypatch.setattr(referential_integrity, "data_path", lambda filename: mapping.get(filename, original_data_path(filename)))
    dependencies = referential_integrity.find_dependencies("Certificate of Origin", "CO-001", "A")
    assert [item["identifier"] for item in dependencies] == ["SHP-A"]
    for action in [lambda: certificate.edit_co("CO-002", _request("A")), lambda: certificate.co_detail("CO-002", _request("A")), lambda: certificate.co_data("CO-002", _request("A")), lambda: certificate.delete_co("CO-002", _request("A")), lambda: certificate.confirm_delete_co("CO-002", _request("A")), lambda: certificate.co_pdf("CO-002", _request("A"))]:
        with pytest.raises(HTTPException) as denied: action()
        assert denied.value.status_code == 404

    assert [r["co_no"] for r in certificate.owned_certificate_records("A")] == ["CO-001"]
    certificate.confirm_delete_co("CO-001", _request("A"))
    assert certificate.load_certificates("A") == [] and certificate.load_certificates("B")[0]["co_no"] == "CO-002"
