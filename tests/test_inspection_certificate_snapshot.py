from __future__ import annotations

import json

from reportlab import rl_config
from starlette.requests import Request

from app import inspection_certificate as inspection


def _request(account_id):
    return Request({"type": "http", "method": "GET", "path": "/inspection", "headers": [],
                    "trade_paper_user": {"account_id": account_id}})


def _form():
    return {
        "shipment_no": "SHP-001", "inspection_date": "2026-08-06", "bl_no": "BL-001",
        "packing_no": "PK-001", "invoice_no": "INV-001", "exporter": "Shipment Exporter",
        "consignee": "Shipment Consignee", "inspection_company": "Inspector",
        "inspection_location": "Busan", "inspection_result": "Passed", "remarks": "Snapshot",
        "port_of_loading": "Busan", "port_of_discharge": "LA", "transport_details": "Vessel V-1",
        "country_of_origin": "KR", "destination_country": "US", "item_name": ["Cargo"],
        "hs_code": ["847130"], "quantity": ["4"], "origin": ["KR"], "carton": ["2"],
        "net_weight": ["40"], "gross_weight": ["44"],
    }


def test_inspection_snapshot_create_edit_update_pdf_legacy_and_isolation(tmp_path, monkeypatch):
    inspection_file = tmp_path / "inspection_certificates.json"
    users_file = tmp_path / "users.json"
    inspection_file.write_text("[]\n")
    users_file.write_text(json.dumps([{"account_id": "A"}, {"account_id": "B"}]))
    party = {
        "shipper": "Shipment Exporter", "shipper_address": "Exporter Address",
        "shipper_email": "exporter@example.com", "shipper_phone": "111-2222",
        "consignee": "Shipment Consignee", "consignee_address": "Consignee Address",
        "consignee_email": "consignee@example.com",
    }
    items = [{"name": "Cargo", "hs_code": "847130", "quantity": "4", "origin": "KR",
              "carton": "2", "net_weight": "40", "gross_weight": "44"}]
    shipment = {"shipment_no": "SHP-001", "bl_no": "BL-001", "packing_no": "PK-001",
                "invoice_no": "INV-001", "country_of_origin": "KR", "destination_country": "US",
                **party, "items": items}
    bill = {"bl_no": "BL-001", "packing_no": "PK-001", "invoice_no": "INV-001",
            "shipper": "B/L Exporter", "consignee": "B/L Consignee", "place_of_delivery": "US",
            "items": [{**items[0], "name": "B/L Cargo"}]}
    packing = {"packing_no": "PK-001", "invoice_no": "INV-001", "seller_address": "Packing Address"}
    invoice = {"invoice_no": "INV-001", "seller_email": "invoice@example.com"}

    monkeypatch.setattr(inspection, "INSPECTION_FILE", inspection_file)
    monkeypatch.setattr(inspection, "USERS_FILE", users_file)
    monkeypatch.setattr(inspection.shipment_module, "load_shipments", lambda account: [shipment] if account == "A" else [])
    monkeypatch.setattr(inspection, "load_bills_of_lading", lambda account: [bill] if account == "A" else [])
    monkeypatch.setattr(inspection.packing_module, "load_packing_lists", lambda account: [packing] if account == "A" else [])
    monkeypatch.setattr(inspection.invoice_module, "load_invoices", lambda account: [invoice] if account == "A" else [])
    monkeypatch.setattr(inspection.product_module, "load_products", lambda account: [])
    monkeypatch.setattr(inspection.buyer_module, "load_buyers", lambda account: [])
    monkeypatch.setattr(inspection, "load_account_company", lambda account, path: {"name": "Master Exporter"})
    monkeypatch.setattr(inspection, "shipment_context_redirect_url", lambda *args: args[-1])

    form = inspection.inspection_form(_request("A"), bl_no="BL-001", shipment_no="SHP-001").body.decode()
    assert all(value in form for value in ("Shipment Exporter", "Exporter Address", "Cargo", "847130", "44"))
    payload = _form()
    inspection.save_inspection(_request("A"), **payload)
    stored = json.loads(inspection_file.read_text())[0]
    assert stored["shipment_no"] == "SHP-001"
    assert stored["exporter_address"] == "Exporter Address"
    assert stored["consignee_email"] == "consignee@example.com"
    assert {k: v for k, v in stored["items"][0].items() if k != "item_id"} == items[0]
    assert stored["items"][0]["item_id"].startswith("ITEM-")
    edit = inspection.edit_inspection("IC-001", _request("A")).body.decode()
    assert all(value in edit for value in ("Exporter Address", "consignee@example.com", "44"))

    changed = {**shipment, "shipper_address": "CHANGED", "items": []}
    monkeypatch.setattr(inspection.shipment_module, "load_shipments", lambda account: [changed] if account == "A" else [])
    omitted = {key: value for key, value in payload.items()
               if key not in {"shipment_no", "origin", "carton", "net_weight", "gross_weight"}}
    inspection.update_inspection("IC-001", _request("A"), **omitted)
    updated = json.loads(inspection_file.read_text())[0]
    assert updated["exporter_address"] == "Exporter Address"
    assert updated["items"][0]["gross_weight"] == "44"
    monkeypatch.setattr(rl_config, "pageCompression", 0)
    pdf = inspection.inspection_pdf("IC-001", _request("A"))
    assert b"Exporter Address" in pdf.body and b"CHANGED" not in pdf.body
    api = inspection.inspection_data("IC-001", _request("A"))
    assert "account_id" not in api and b"account_id" not in pdf.body

    legacy = inspection.resolve_inspection_snapshot({"shipment_no": "SHP-001"}, "A")
    assert legacy["exporter_name"] == "Shipment Exporter"
    legacy_bl = inspection.resolve_inspection_snapshot({"bl_no": "BL-001"}, "A")
    assert legacy_bl["exporter_name"] == "B/L Exporter"
    assert legacy_bl["exporter_address"] == "Packing Address"
    assert legacy_bl["exporter_email"] == "invoice@example.com"
    assert legacy_bl["items"][0]["name"] == "B/L Cargo"
    assert inspection.resolve_inspection_snapshot({"bl_no": "BL-001"}, "B")["exporter_name"] == "Master Exporter"
