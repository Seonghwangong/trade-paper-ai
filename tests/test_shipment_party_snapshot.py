from __future__ import annotations

import json

from reportlab import rl_config
from starlette.requests import Request

from app import bill_of_lading as bill
from app import buyer, invoice, packing, shipment, shipping_instruction


def _request(account_id, path="/shipment-list"):
    return Request({
        "type": "http", "method": "GET", "path": path, "headers": [],
        "trade_paper_user": {
            "account_id": account_id, "company": f"Company {account_id}",
            "email": f"{account_id}@example.com",
        },
    })


def _form(bl_no="BL-001"):
    return {
        "shipment_date": "2026-08-05", "shipment_name": "Snapshot Shipment",
        "customer": "", "buyer": "Snapshot Consignee", "status": "Inquiry",
        "remarks": "Snapshot test", "quotation_no": "", "pi_no": "",
        "invoice_no": "INV-001", "packing_no": "PK-001", "si_no": "SI-001",
        "bl_no": bl_no, "co_no": "", "inspection_no": "",
        "insurance_no": "", "weight_no": "",
    }


def _datasets(bl_record, packing_record, invoice_record, si_record=None):
    datasets = {descriptor["file"].name: [] for descriptor in [*shipment.DOCUMENTS, *shipment.OPERATIONAL_RECORDS]}
    datasets["bills_of_lading.json"] = [bl_record]
    datasets["packing_lists.json"] = [packing_record]
    datasets["invoices.json"] = [invoice_record]
    datasets["shipping_instructions.json"] = [si_record] if si_record else []
    return datasets


def test_shipment_snapshot_create_edit_update_pdf_and_account_isolation(tmp_path, monkeypatch):
    shipment_file = tmp_path / "shipments.json"
    users_file = tmp_path / "users.json"
    shipment_file.write_text("[]\n", encoding="utf-8")
    users_file.write_text(json.dumps([{"account_id": "account-a"}, {"account_id": "account-b"}]), encoding="utf-8")
    snapshot = {
        "shipper": "Snapshot Shipper",
        "shipper_address": "Snapshot Shipper Address",
        "shipper_email": "shipper-snapshot@example.com",
        "shipper_phone": "111-2222",
        "consignee": "Snapshot Consignee",
        "consignee_address": "Snapshot Consignee Address",
        "consignee_email": "consignee-snapshot@example.com",
    }
    items = [{
        "name": "Snapshot Product", "quantity": 4, "hs_code": "847130",
        "carton": "2", "net_weight": "40", "gross_weight": "44",
    }]
    bl_record = {
        "bl_no": "BL-001", "packing_no": "PK-001", "invoice_no": "INV-001",
        **snapshot, "items": items, "total_carton": "2",
        "total_net_weight": "40", "total_gross_weight": "44",
    }
    packing_record = {"packing_no": "PK-001", "invoice_no": "INV-001", "items": items}
    invoice_record = {"invoice_no": "INV-001", "buyer": snapshot["consignee"], "items": items}
    si_record = {
        "si_no": "SI-001", "packing_no": "PK-001", "invoice_no": "INV-001",
        "shipper": snapshot["shipper"],
        "exporter_address": snapshot["shipper_address"],
        "exporter_email": snapshot["shipper_email"],
        "exporter_phone": snapshot["shipper_phone"],
        "consignee": snapshot["consignee"],
        "consignee_address": snapshot["consignee_address"],
        "consignee_email": snapshot["consignee_email"],
        "items": items, "total_carton": "2",
        "total_net_weight": "40", "total_gross_weight": "44",
    }

    monkeypatch.setattr(shipment, "SHIPMENT_FILE", shipment_file)
    monkeypatch.setattr(shipment, "USERS_FILE", users_file)
    monkeypatch.setattr(bill, "load_bills_of_lading", lambda account_id: [bl_record] if account_id == "account-a" else [])
    monkeypatch.setattr(packing, "load_packing_lists", lambda account_id: [packing_record] if account_id == "account-a" else [])
    monkeypatch.setattr(invoice, "load_invoices", lambda account_id: [invoice_record] if account_id == "account-a" else [])
    monkeypatch.setattr(shipping_instruction, "load_shipping_instructions", lambda account_id: [si_record] if account_id == "account-a" else [])
    monkeypatch.setattr(buyer, "load_buyers", lambda account_id: [{"name": snapshot["consignee"]}] if account_id == "account-a" else [])
    monkeypatch.setattr(shipment, "load_account_company", lambda account_id, path: {
        "name": "Changed Master", "address": "Changed Master Address",
        "email": "changed-master@example.com", "phone": "999-9999",
    })
    monkeypatch.setattr(shipment, "load_workflow_datasets", lambda account_id=None: _datasets(bl_record, packing_record, invoice_record, si_record))

    create_html = shipment.shipment_form(_request("account-a", "/shipment-form"), si_no="SI-001").body.decode()
    for field, value in snapshot.items():
        assert f'name="{field}" value="{value}"' in create_html
    assert "Snapshot Product" in create_html and "847130" in create_html

    shipment.save_shipment(_request("account-a", "/shipment"), **_form())
    stored = json.loads(shipment_file.read_text(encoding="utf-8"))[0]
    for field, value in snapshot.items():
        assert stored[field] == value
    assert stored["items"] == items
    assert stored["total_carton"] == "2"
    assert stored["total_net_weight"] == "40"
    assert stored["total_gross_weight"] == "44"

    edit_html = shipment.edit_shipment(stored["shipment_no"], _request("account-a")).body.decode()
    for field, value in snapshot.items():
        assert f'name="{field}" value="{value}"' in edit_html
    assert "Snapshot Product" in edit_html

    changed_bl = {**bl_record, "shipper_address": "Changed Upstream Address", "items": []}
    monkeypatch.setattr(bill, "load_bills_of_lading", lambda account_id: [changed_bl] if account_id == "account-a" else [])
    shipment.update_shipment(stored["shipment_no"], _request("account-a"), **_form())
    updated = json.loads(shipment_file.read_text(encoding="utf-8"))[0]
    for field, value in snapshot.items():
        assert updated[field] == value
    assert updated["items"] == items

    monkeypatch.setattr(rl_config, "pageCompression", 0)
    pdf = shipment.shipment_pdf(stored["shipment_no"], _request("account-a", "/shipment-pdf"))
    for value in snapshot.values():
        assert value.encode() in pdf.body
    assert b"Snapshot Product" in pdf.body and b"847130" in pdf.body
    assert b"Changed Upstream Address" not in pdf.body

    assert shipment.resolve_shipment_snapshot({"bl_no": "BL-001"}, "account-b")["shipper"] == "Changed Master"


def test_legacy_shipment_fallback_order(tmp_path, monkeypatch):
    bill_record = {
        "bl_no": "BL-LEGACY", "packing_no": "PK-LEGACY", "invoice_no": "INV-LEGACY",
        "shipper": "B/L Shipper", "consignee": "B/L Consignee",
        "items": [{"name": "B/L Cargo", "quantity": 2, "carton": 1}],
    }
    packing_record = {
        "packing_no": "PK-LEGACY", "invoice_no": "INV-LEGACY",
        "seller_address": "Packing Address", "buyer_address": "Packing Buyer Address",
    }
    invoice_record = {
        "invoice_no": "INV-LEGACY", "seller_email": "invoice-seller@example.com",
        "buyer_email": "invoice-buyer@example.com",
    }
    monkeypatch.setattr(bill, "load_bills_of_lading", lambda account_id: [bill_record])
    monkeypatch.setattr(packing, "load_packing_lists", lambda account_id: [packing_record])
    monkeypatch.setattr(invoice, "load_invoices", lambda account_id: [invoice_record])
    monkeypatch.setattr(buyer, "load_buyers", lambda account_id: [{"name": "B/L Consignee"}])
    monkeypatch.setattr(shipment, "load_account_company", lambda account_id, path: {
        "name": "Master Shipper", "address": "Master Address",
        "email": "master@example.com", "phone": "333-4444",
    })
    shipment_file = tmp_path / "shipments.json"
    users_file = tmp_path / "users.json"
    shipment_file.write_text(json.dumps([{
        "shipment_no": "SHP-LEGACY", "shipment_date": "2026-08-05",
        "shipment_name": "Legacy Shipment", "buyer": "B/L Consignee",
        "status": "Inquiry", "bl_no": "BL-LEGACY",
    }]), encoding="utf-8")
    users_file.write_text(json.dumps([{"account_id": "account-a"}]), encoding="utf-8")
    monkeypatch.setattr(shipment, "SHIPMENT_FILE", shipment_file)
    monkeypatch.setattr(shipment, "USERS_FILE", users_file)
    monkeypatch.setattr(shipment, "load_workflow_datasets", lambda account_id=None: _datasets(bill_record, packing_record, invoice_record))

    resolved = shipment.resolve_shipment_snapshot({
        "shipment_no": "SHP-LEGACY", "bl_no": "BL-LEGACY",
    }, "account-a")
    assert resolved["shipper"] == "B/L Shipper"
    assert resolved["shipper_address"] == "Packing Address"
    assert resolved["shipper_email"] == "invoice-seller@example.com"
    assert resolved["shipper_phone"] == "333-4444"
    assert resolved["consignee"] == "B/L Consignee"
    assert resolved["consignee_address"] == "Packing Buyer Address"
    assert resolved["consignee_email"] == "invoice-buyer@example.com"
    assert resolved["items"][0]["name"] == "B/L Cargo"

    monkeypatch.setattr(rl_config, "pageCompression", 0)
    pdf = shipment.shipment_pdf("SHP-LEGACY", _request("account-a", "/shipment-pdf"))
    for value in (
        b"B/L Shipper", b"Packing Address", b"invoice-seller@example.com",
        b"333-4444", b"B/L Consignee", b"Packing Buyer Address",
        b"invoice-buyer@example.com", b"B/L Cargo",
    ):
        assert value in pdf.body
    migrated = json.loads(shipment_file.read_text(encoding="utf-8"))[0]
    assert migrated["account_id"] == "account-a"
    assert "shipper_address" not in migrated
