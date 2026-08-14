from __future__ import annotations

import json

from reportlab import rl_config
from starlette.requests import Request

from app import certificate_of_origin as certificate


def _request(account_id):
    return Request({"type": "http", "method": "GET", "path": "/co", "headers": [],
                    "trade_paper_user": {"account_id": account_id}})


def _base_form():
    return {
        "shipment_no": "SHP-001", "co_date": "2026-08-06", "bl_no": "BL-001",
        "invoice_no": "INV-001", "packing_no": "PK-001", "exporter": "Exporter",
        "consignee": "Consignee", "country_of_origin": "KR", "destination_country": "US",
        "transport_details": "Vessel V-1", "port_of_loading": "Busan",
        "port_of_discharge": "LA", "remarks": "Snapshot", "item_name": ["Cargo"],
        "hs_code": ["847130"], "quantity": ["4"], "origin": ["KR"],
        "unit": ["PCS"],
        "carton": ["2"], "net_weight": ["40"], "gross_weight": ["44"],
    }


def test_co_snapshot_create_edit_update_pdf_legacy_and_account_isolation(tmp_path, monkeypatch):
    co_file = tmp_path / "certificates_of_origin.json"
    users_file = tmp_path / "users.json"
    co_file.write_text("[]\n")
    users_file.write_text(json.dumps([{"account_id": "A"}, {"account_id": "B"}]))
    party = {
        "shipper": "Shipment Exporter", "shipper_address": "Exporter Address",
        "shipper_email": "exporter@example.com", "shipper_phone": "111-2222",
        "consignee": "Shipment Consignee", "consignee_address": "Consignee Address",
        "consignee_email": "consignee@example.com",
    }
    items = [{"name": "Cargo", "hs_code": "847130", "quantity": "4", "carton": "2",
              "net_weight": "40", "gross_weight": "44", "origin": "KR", "unit": "PCS"}]
    shipment = {"shipment_no": "SHP-001", "bl_no": "BL-001", "packing_no": "PK-001",
                "invoice_no": "INV-001", **party, "items": items}
    bill = {"bl_no": "BL-001", "booking_record_no": "BK-001", "shipment_no": "SHP-001",
            "si_no": "SI-001", "packing_no": "PK-001", "invoice_no": "INV-001",
            "shipper": "B/L Exporter", "consignee": "B/L Consignee", "place_of_delivery": "US",
            "items": [{**items[0], "name": "B/L Cargo"}]}
    packing = {"packing_no": "PK-001", "invoice_no": "INV-001", "seller_address": "Packing Address"}
    invoice = {"invoice_no": "INV-001", "seller_email": "invoice@example.com"}

    monkeypatch.setattr(certificate, "CO_FILE", co_file)
    monkeypatch.setattr(certificate, "USERS_FILE", users_file)
    monkeypatch.setattr(certificate.shipment_module, "load_shipments", lambda account: [shipment] if account == "A" else [])
    monkeypatch.setattr(certificate, "load_bills_of_lading", lambda account: [bill] if account == "A" else [])
    monkeypatch.setattr(certificate.packing_module, "load_packing_lists", lambda account: [packing] if account == "A" else [])
    monkeypatch.setattr(certificate.invoice_module, "load_invoices", lambda account: [invoice] if account == "A" else [])
    monkeypatch.setattr(certificate.product_module, "load_products", lambda account: [])
    monkeypatch.setattr(certificate.buyer_module, "load_buyers", lambda account: [])
    monkeypatch.setattr(certificate, "load_account_company", lambda account, path: {"name": "Master Exporter"})
    monkeypatch.setattr(certificate, "shipment_context_redirect_url", lambda shipment_no, field, value, fallback: fallback)

    form = certificate.co_form(_request("A"), bl_no="BL-001", shipment_no="SHP-001").body.decode()
    assert 'name="exporter_name" value="Shipment Exporter"' in form
    assert 'name="exporter_address" value="Exporter Address"' in form
    assert 'name="consignee_email" value="consignee@example.com"' in form
    assert 'name="bl_no" required' in form
    assert 'name="booking_record_no" value="BK-001"' in form
    assert 'name="si_no" value="SI-001"' in form
    assert 'name="invoice_no" value="INV-001" placeholder="Commercial Invoice" readonly' in form
    assert 'name="unit" value="PCS"' in form
    assert "BL-001" not in certificate.co_form(_request("B")).body.decode()
    assert all(value in form for value in ("Cargo", "847130", "2", "40", "44"))

    payload = _base_form()
    payload.update({"exporter": "User Exporter", "consignee": "User Importer",
                    "booking_record_no": "BK-001", "si_no": "SI-001",
                    "issuing_authority": "Busan Chamber", "certificate_type": "Preferential"})
    success = certificate.save_co(_request("A"), **payload).body.decode()
    assert "View Certificate" in success and "Back to Dashboard" in success
    stored = json.loads(co_file.read_text())[0]
    assert stored["shipment_no"] == "SHP-001"
    assert stored["exporter_address"] == "Exporter Address"
    assert stored["consignee_email"] == "consignee@example.com"
    assert stored["exporter"] == "User Exporter" and stored["consignee"] == "User Importer"
    assert stored["booking_record_no"] == "BK-001" and stored["si_no"] == "SI-001"
    assert stored["issuing_authority"] == "Busan Chamber"
    assert stored["certificate_type"] == "Preferential"
    assert {k: v for k, v in stored["items"][0].items() if k != "item_id"} == items[0]
    assert stored["items"][0]["item_id"].startswith("ITEM-")
    edit = certificate.edit_co("CO-001", _request("A")).body.decode()
    assert all(value in edit for value in ("Exporter Address", "consignee@example.com", "44"))

    changed_shipment = {**shipment, "shipper_address": "CHANGED", "items": []}
    monkeypatch.setattr(certificate.shipment_module, "load_shipments", lambda account: [changed_shipment] if account == "A" else [])
    certificate.update_co("CO-001", _request("A"), **{key: value for key, value in payload.items() if key != "shipment_no"})
    updated = json.loads(co_file.read_text())[0]
    assert updated["exporter_address"] == "Exporter Address"
    assert updated["items"][0]["gross_weight"] == "44"
    monkeypatch.setattr(rl_config, "pageCompression", 0)
    pdf = certificate.co_pdf("CO-001", _request("A"))
    assert b"Exporter Address" in pdf.body and b"CHANGED" not in pdf.body
    assert b"account_id" not in pdf.body and "account_id" not in certificate.co_data("CO-001", _request("A"))

    legacy = certificate.resolve_co_snapshot({"bl_no": "BL-001"}, "A")
    assert legacy["exporter_name"] == "B/L Exporter"
    assert legacy["exporter_address"] == "Packing Address"
    assert legacy["exporter_email"] == "invoice@example.com"
    assert legacy["items"][0]["name"] == "B/L Cargo"
    assert certificate.resolve_co_snapshot({"bl_no": "BL-001"}, "B")["exporter_name"] == "Master Exporter"
