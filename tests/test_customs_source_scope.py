import json

import pytest
from fastapi import HTTPException
from starlette.requests import Request

import app.booking_confirmation as booking
import app.customs_declaration as customs
import app.invoice as invoice
import app.packing as packing
import app.product as product
import app.shipment as shipment
from app.validation import DataValidationError


def _request(account_id, path="/customs-form"):
    return Request({
        "type": "http", "method": "GET", "scheme": "http",
        "path": path, "raw_path": path.encode(), "query_string": b"",
        "headers": [], "client": ("127.0.0.1", 1), "server": ("testserver", 80),
        "trade_paper_user": {
            "account_id": account_id, "company": f"Company {account_id}",
            "email": f"{account_id}@example.com",
        },
    })


def _customs_form():
    return {
        "customs_date": "2026-08-01", "declaration_no": "DECL-A",
        "shipment_no": "SHP-A", "booking_record_no": "BK-A",
        "invoice_no": "INV-A", "packing_no": "PK-A",
        "container_record_no": "", "bl_no": "", "exporter": "Seller A",
        "consignee": "Buyer A", "country_of_origin": "KR",
        "destination_country": "US", "port_of_loading": "Busan",
        "port_of_discharge": "LA", "vessel": "", "voyage_no": "",
        "container_no": "", "seal_no": "", "customs_office": "Busan",
        "declaration_type": "Export", "incoterms": "FOB", "currency": "USD",
        "total_invoice_value": "10", "remarks": "Scoped source test",
        "item_name": ["A"], "hs_code": ["1234"], "quantity": ["1"],
        "unit_price": ["10"], "amount": ["10"], "origin": ["KR"],
        "net_weight": ["1"], "gross_weight": ["2"], "total_quantity": "1",
        "total_net_weight": "1", "total_gross_weight": "2", "total_amount": "10",
    }


def test_customs_owned_sources_api_form_prefill_validation_and_preview(tmp_path, monkeypatch):
    users_file = tmp_path / "users.json"
    invoice_file = tmp_path / "invoices.json"
    packing_file = tmp_path / "packing_lists.json"
    booking_file = tmp_path / "booking_confirmations.json"
    shipment_file = tmp_path / "shipments.json"
    products_file = tmp_path / "products.json"
    customs_file = tmp_path / "customs_declarations.json"
    users_file.write_text(json.dumps([
        {"account_id": "account-a", "email": "a@example.com"},
        {"account_id": "account-b", "email": "b@example.com"},
    ]), encoding="utf-8")
    invoice_file.write_text(json.dumps([
        {"account_id": "account-a", "invoice_no": "INV-A", "seller": "Seller A", "buyer": "Buyer A", "items": [{"name": "A", "quantity": 1}]},
        {"account_id": "account-b", "invoice_no": "INV-B", "seller": "Seller B", "buyer": "Buyer B", "items": [{"name": "B", "quantity": 2}]},
    ]), encoding="utf-8")
    packing_file.write_text(json.dumps([
        {"account_id": "account-a", "packing_no": "PK-A", "invoice_no": "INV-A", "items": [{"name": "A", "quantity": 1}]},
        {"account_id": "account-b", "packing_no": "PK-B", "invoice_no": "INV-B", "items": [{"name": "B", "quantity": 2}]},
    ]), encoding="utf-8")
    booking_file.write_text(json.dumps([
        {"account_id": "account-a", "booking_record_no": "BK-A", "shipment_no": "SHP-A"},
        {"account_id": "account-b", "booking_record_no": "BK-B", "shipment_no": "SHP-B"},
    ]), encoding="utf-8")
    shipment_file.write_text(json.dumps([
        {"account_id": "account-a", "shipment_no": "SHP-A", "invoice_no": "INV-A", "packing_no": "PK-A"},
        {"account_id": "account-b", "shipment_no": "SHP-B", "invoice_no": "INV-B", "packing_no": "PK-B"},
    ]), encoding="utf-8")
    products_file.write_text("[]\n", encoding="utf-8")
    customs_file.write_text("[]\n", encoding="utf-8")

    monkeypatch.setattr(invoice, "INVOICE_FILE", invoice_file)
    monkeypatch.setattr(invoice, "USERS_FILE", users_file)
    monkeypatch.setattr(packing, "PACKING_FILE", packing_file)
    monkeypatch.setattr(packing, "USERS_FILE", users_file)
    monkeypatch.setattr(booking, "BOOKING_FILE", booking_file)
    monkeypatch.setattr(booking, "USERS_FILE", users_file)
    monkeypatch.setattr(shipment, "SHIPMENT_FILE", shipment_file)
    monkeypatch.setattr(shipment, "USERS_FILE", users_file)
    monkeypatch.setattr(product, "PRODUCT_FILE", products_file)
    monkeypatch.setattr(product, "USERS_FILE", users_file)
    monkeypatch.setattr(customs, "CUSTOMS_FILE", customs_file)
    monkeypatch.setattr(customs, "CONTAINER_FILE", tmp_path / "containers.json")
    monkeypatch.setattr(customs.container_module, "load_containers", lambda account_id: [])
    monkeypatch.setattr(customs, "BL_FILE", tmp_path / "bills_of_lading.json")
    monkeypatch.setattr(customs, "PRODUCT_FILE", products_file)
    (tmp_path / "containers.json").write_text("[]\n", encoding="utf-8")
    (tmp_path / "bills_of_lading.json").write_text("[]\n", encoding="utf-8")

    invoice_a = customs.customs_source_invoice("INV-A", _request("account-a"))
    packing_a = customs.customs_source_packing("PK-A", _request("account-a"))
    booking_a = customs.customs_source_booking("BK-A", _request("account-a"))
    assert invoice_a["seller"] == "Seller A" and "account_id" not in invoice_a
    assert packing_a["invoice_no"] == "INV-A" and "account_id" not in packing_a
    assert booking_a["shipment_no"] == "SHP-A" and "account_id" not in booking_a

    for action in (
        lambda: customs.customs_source_invoice("INV-B", _request("account-a")),
        lambda: customs.customs_source_packing("PK-B", _request("account-a")),
        lambda: customs.customs_source_booking("BK-B", _request("account-a")),
    ):
        with pytest.raises(HTTPException) as denied:
            action()
        assert denied.value.status_code == 404

    form_a = customs.customs_form(_request("account-a")).body.decode()
    form_b = customs.customs_form(_request("account-b")).body.decode()
    assert all(value in form_a for value in ["INV-A", "PK-A", "BK-A"])
    assert all(value not in form_a for value in ["INV-B", "PK-B", "BK-B"])
    assert all(value in form_b for value in ["INV-B", "PK-B", "BK-B"])
    assert all(value not in form_b for value in ["INV-A", "PK-A", "BK-A"])

    payload = customs.payload_from_sources("SHP-A", "INV-A", "PK-A", "BK-A", account_id="account-a")
    assert payload["invoice_no"] == "INV-A" and payload["packing_no"] == "PK-A"
    stolen = customs.payload_from_sources("SHP-A", "INV-B", "PK-B", "BK-B", account_id="account-a")
    assert stolen["invoice_no"] == "" and stolen["packing_no"] == "" and stolen["booking_record_no"] == ""

    customs.validate_customs_links("SHP-A", "BK-A", "INV-A", "PK-A", "", "", "account-a")
    for refs in [
        ("SHP-A", "BK-B", "INV-A", "PK-A"),
        ("SHP-A", "BK-A", "INV-B", "PK-A"),
        ("SHP-A", "BK-A", "INV-A", "PK-B"),
    ]:
        with pytest.raises(DataValidationError):
            customs.validate_customs_links(*refs, "", "", "account-a")

    response = customs.save_customs_record(_request("account-a", "/customs"), **_customs_form())
    assert response.status_code == 303
    saved = json.loads(customs_file.read_text())
    assert saved[0]["customs_record_no"] == "CD-001"
    assert saved[0]["invoice_no"] == "INV-A" and saved[0]["packing_no"] == "PK-A"

    preview = customs.create_customs_pdf(_request("account-a", "/customs/pdf"), {
        "customs_record_no": "CD-PREVIEW", "shipment_no": "SHP-A",
        "invoice_no": "INV-A", "packing_no": "PK-A", "booking_record_no": "BK-A",
        "account_id": "forged", "items": [],
    })
    assert preview.status_code == 200 and preview.body.startswith(b"%PDF")
    with pytest.raises(DataValidationError):
        customs.create_customs_pdf(_request("account-a", "/customs/pdf"), {
            "customs_record_no": "CD-STOLEN", "invoice_no": "INV-B", "items": [],
        })
