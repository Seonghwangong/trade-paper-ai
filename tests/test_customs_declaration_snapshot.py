from __future__ import annotations

import json

from reportlab import rl_config
from starlette.requests import Request

from app import customs_declaration as customs


def _request(account_id):
    return Request({"type": "http", "method": "GET", "path": "/customs", "headers": [],
                    "trade_paper_user": {"account_id": account_id}})


def _form():
    return {
        "customs_date": "2026-08-06", "declaration_no": "DEC-001", "shipment_no": "SHP-001",
        "booking_record_no": "BK-001", "invoice_no": "INV-001", "packing_no": "PK-001",
        "container_record_no": "CON-001", "bl_no": "BL-001", "exporter": "Shipment Exporter",
        "consignee": "Shipment Consignee", "country_of_origin": "KR", "destination_country": "US",
        "port_of_loading": "Busan", "port_of_discharge": "LA", "vessel": "Vessel",
        "voyage_no": "V-1", "container_no": "CONT", "seal_no": "SEAL", "customs_office": "Busan",
        "declaration_type": "Export", "incoterms": "FOB", "currency": "USD",
        "total_invoice_value": "40", "remarks": "Snapshot", "item_name": ["Cargo"],
        "hs_code": ["847130"], "quantity": ["4"], "unit_price": ["10"], "amount": ["40"],
        "origin": ["KR"], "carton": ["2"], "net_weight": ["40"], "gross_weight": ["44"],
        "total_quantity": "4", "total_net_weight": "40", "total_gross_weight": "44", "total_amount": "40",
    }


def test_customs_snapshot_create_edit_update_pdf_legacy_and_isolation(tmp_path, monkeypatch):
    customs_file = tmp_path / "customs_declarations.json"
    users_file = tmp_path / "users.json"
    customs_file.write_text("[]\n")
    users_file.write_text(json.dumps([{"account_id": "A"}, {"account_id": "B"}]))
    party = {
        "shipper": "Shipment Exporter", "shipper_address": "Exporter Address",
        "shipper_email": "exporter@example.com", "shipper_phone": "111-2222",
        "consignee": "Shipment Consignee", "consignee_address": "Consignee Address",
        "consignee_email": "consignee@example.com",
    }
    items = [{"name": "Cargo", "hs_code": "847130", "quantity": "4", "unit_price": "10",
              "amount": "40", "origin": "KR", "carton": "2", "net_weight": "40", "gross_weight": "44"}]
    shipment = {"shipment_no": "SHP-001", "invoice_no": "INV-001", "packing_no": "PK-001",
                "bl_no": "BL-001", "country_of_origin": "KR", "destination_country": "US",
                **party, "items": items}
    bill = {"bl_no": "BL-001", "packing_no": "PK-001", "invoice_no": "INV-001",
            "shipper": "B/L Exporter", "consignee": "B/L Consignee", "items": [{**items[0], "name": "B/L Cargo"}]}
    packing = {"packing_no": "PK-001", "invoice_no": "INV-001", "seller_address": "Packing Address"}
    invoice = {"invoice_no": "INV-001", "seller_email": "invoice@example.com"}
    booking = {"booking_record_no": "BK-001", "shipment_no": "SHP-001"}
    container = {"container_record_no": "CON-001", "shipment_no": "SHP-001"}

    monkeypatch.setattr(customs, "CUSTOMS_FILE", customs_file)
    monkeypatch.setattr(customs, "USERS_FILE", users_file)
    monkeypatch.setattr(customs, "load_shipments", lambda account: [shipment] if account == "A" else [])
    monkeypatch.setattr(customs, "load_bills_of_lading", lambda account: [bill] if account == "A" else [])
    monkeypatch.setattr(customs, "load_packing_lists", lambda account: [packing] if account == "A" else [])
    monkeypatch.setattr(customs, "load_invoices", lambda account: [invoice] if account == "A" else [])
    monkeypatch.setattr(customs, "load_bookings", lambda account: [booking] if account == "A" else [])
    monkeypatch.setattr(customs, "load_containers", lambda account: [container] if account == "A" else [])
    monkeypatch.setattr(customs, "load_products", lambda account: [])
    monkeypatch.setattr(customs.product_module, "load_products", lambda account: [])
    monkeypatch.setattr(customs.buyer_module, "load_buyers", lambda account: [])
    monkeypatch.setattr(customs, "load_account_company", lambda account, path: {"name": "Master Exporter"})

    form = customs.customs_form(_request("A"), shipment_no="SHP-001").body.decode()
    assert all(value in form for value in ("Shipment Exporter", "Exporter Address", "Cargo", "847130", "44"))
    payload = _form()
    customs.save_customs_record(_request("A"), exporter_email="", **payload)
    stored = json.loads(customs_file.read_text())[0]
    assert stored["exporter_address"] == "Exporter Address"
    assert stored["consignee_email"] == "consignee@example.com"
    assert "exporter_email" in stored and stored["exporter_email"] == ""
    assert {k: v for k, v in stored["items"][0].items() if k != "item_id"} == items[0]
    assert stored["items"][0]["item_id"].startswith("ITEM-")
    edit = customs.edit_customs("CD-001", _request("A")).body.decode()
    assert all(value in edit for value in ("Exporter Address", "consignee@example.com", "44"))

    changed = {
        **shipment,
        "shipper_address": "CHANGED",
        "shipper_email": "added-after-snapshot@example.com",
        "items": [],
    }
    monkeypatch.setattr(customs, "load_shipments", lambda account: [changed] if account == "A" else [])
    edit_after_upstream_change = customs.edit_customs("CD-001", _request("A")).body.decode()
    assert 'name="exporter_email" value=""' in edit_after_upstream_change
    assert "added-after-snapshot@example.com" not in edit_after_upstream_change
    omitted = {key: value for key, value in payload.items() if key != "carton"}
    customs.update_customs("CD-001", _request("A"), **omitted)
    updated = json.loads(customs_file.read_text())[0]
    assert updated["exporter_address"] == "Exporter Address"
    assert updated["items"][0]["carton"] == "2"
    monkeypatch.setattr(rl_config, "pageCompression", 0)
    pdf = customs.customs_pdf("CD-001", _request("A"))
    assert b"Exporter Address" in pdf.body and b"CHANGED" not in pdf.body
    api = customs.customs_data("CD-001", _request("A"))
    assert api["exporter_email"] == ""
    assert b"added-after-snapshot@example.com" not in pdf.body
    assert "account_id" not in api and b"account_id" not in pdf.body

    legacy = customs.resolve_customs_snapshot({"shipment_no": "SHP-001"}, "A")
    assert legacy["exporter_name"] == "Shipment Exporter"
    assert legacy["exporter_email"] == "added-after-snapshot@example.com"
    legacy_bl = customs.resolve_customs_snapshot({"bl_no": "BL-001"}, "A")
    assert legacy_bl["exporter_name"] == "B/L Exporter"
    assert legacy_bl["exporter_address"] == "Packing Address"
    assert legacy_bl["exporter_email"] == "invoice@example.com"
    assert legacy_bl["items"][0]["name"] == "B/L Cargo"
    assert customs.resolve_customs_snapshot({"bl_no": "BL-001"}, "B")["exporter_name"] == "Master Exporter"
