from __future__ import annotations

import json

from reportlab import rl_config
from starlette.requests import Request

from app import bill_of_lading as bill


def _request(account_id, path="/bl-list"):
    return Request({
        "type": "http",
        "method": "GET",
        "path": path,
        "headers": [],
        "trade_paper_user": {
            "account_id": account_id,
            "company": "Snapshot Shipper",
            "email": "owner@example.com",
        },
    })


def _form(snapshot):
    return {
        "shipment_no": "",
        "packing_no": "PK-001",
        "invoice_no": "INV-001",
        "shipper": snapshot["shipper"],
        "shipper_address": snapshot["shipper_address"],
        "shipper_email": snapshot["shipper_email"],
        "shipper_phone": snapshot["shipper_phone"],
        "consignee": snapshot["consignee"],
        "consignee_address": snapshot["consignee_address"],
        "consignee_email": snapshot["consignee_email"],
        "notify_party": "Notify Party",
        "vessel": "CODEX Vessel",
        "voyage_no": "V-001",
        "port_of_loading": "Busan",
        "port_of_discharge": "Los Angeles",
        "place_of_delivery": "Los Angeles",
        "bl_date": "2026-08-05",
        "item_name": ["Snapshot Product"],
        "quantity": ["4"],
        "hs_code": ["847130"],
        "carton": ["2"],
        "net_weight": ["40"],
        "gross_weight": ["44"],
        "total_carton": "2",
        "total_net_weight": "40",
        "total_gross_weight": "44",
    }


def test_bill_snapshot_create_edit_update_and_pdf_precedence(tmp_path, monkeypatch):
    bl_file = tmp_path / "bills_of_lading.json"
    users_file = tmp_path / "users.json"
    bl_file.write_text("[]\n", encoding="utf-8")
    users_file.write_text(json.dumps([{"account_id": "account-a"}]), encoding="utf-8")
    snapshot = {
        "shipper": "Snapshot Shipper",
        "shipper_address": "Snapshot Shipper Address",
        "shipper_email": "shipper-snapshot@example.com",
        "shipper_phone": "111-2222",
        "consignee": "Snapshot Consignee",
        "consignee_address": "Snapshot Consignee Address",
        "consignee_email": "consignee-snapshot@example.com",
    }
    packing = {
        "packing_no": "PK-001",
        "invoice_no": "INV-001",
        "seller": snapshot["shipper"],
        "seller_address": snapshot["shipper_address"],
        "seller_email": snapshot["shipper_email"],
        "seller_phone": snapshot["shipper_phone"],
        "buyer": snapshot["consignee"],
        "buyer_address": snapshot["consignee_address"],
        "buyer_email": snapshot["consignee_email"],
        "items": [{
            "name": "Snapshot Product", "quantity": 4, "hs_code": "847130",
            "carton": "2", "net_weight": "40", "gross_weight": "44",
        }],
    }
    invoice = {
        "invoice_no": "INV-001", "seller": "Changed Invoice Shipper",
        "buyer": "Changed Invoice Consignee", "items": [],
    }
    monkeypatch.setattr(bill, "BL_FILE", bl_file)
    monkeypatch.setattr(bill, "USERS_FILE", users_file)
    monkeypatch.setattr(bill, "load_packing_lists", lambda account_id: [packing] if account_id == "account-a" else [])
    monkeypatch.setattr(bill.invoice_module, "load_invoices", lambda account_id: [invoice] if account_id == "account-a" else [])
    monkeypatch.setattr(bill, "load_account_company", lambda account_id, path: {
        "name": "Changed Master Shipper", "address": "Changed Master Address",
        "email": "changed-master@example.com", "phone": "999-9999",
    })
    monkeypatch.setattr(bill.buyer_module, "load_buyers", lambda account_id: [{
        "name": snapshot["consignee"], "address": "Changed Buyer Address",
        "email": "changed-buyer@example.com",
    }])

    payload = bill.payload_from_packing("PK-001", "account-a")
    for field, value in snapshot.items():
        assert payload[field] == value

    bill.save_bl(_request("account-a"), **_form(snapshot))
    stored = json.loads(bl_file.read_text(encoding="utf-8"))[0]
    for field, value in snapshot.items():
        assert stored[field] == value

    edit_html = bill.edit_bl(stored["bl_no"], _request("account-a")).body.decode()
    for field, value in snapshot.items():
        assert f'name="{field}" value="{value}"' in edit_html

    update_form = _form(snapshot)
    update_form.pop("shipment_no")
    bill.update_bl(stored["bl_no"], _request("account-a"), **update_form)
    updated = json.loads(bl_file.read_text(encoding="utf-8"))[0]
    for field, value in snapshot.items():
        assert updated[field] == value

    monkeypatch.setattr(rl_config, "pageCompression", 0)
    pdf = bill.create_bl_pdf(_request("account-a", "/bl/pdf"), updated)
    for value in snapshot.values():
        assert value.encode() in pdf.body
    assert b"Changed Master Address" not in pdf.body
    assert b"Changed Buyer Address" not in pdf.body


def test_legacy_bill_fallback_order_is_packing_then_invoice_then_master(monkeypatch):
    packing = {
        "packing_no": "PK-LEGACY", "invoice_no": "INV-LEGACY",
        "seller": "Packing Shipper", "seller_address": "Packing Shipper Address",
        "buyer": "Packing Consignee", "buyer_address": "Packing Consignee Address",
    }
    invoice = {
        "invoice_no": "INV-LEGACY", "seller_email": "invoice-shipper@example.com",
        "buyer_email": "invoice-consignee@example.com",
    }
    monkeypatch.setattr(bill, "load_packing_lists", lambda account_id: [packing])
    monkeypatch.setattr(bill.invoice_module, "load_invoices", lambda account_id: [invoice])
    monkeypatch.setattr(bill, "load_account_company", lambda account_id, path: {
        "name": "Master Shipper", "address": "Master Shipper Address",
        "email": "master-shipper@example.com", "phone": "333-4444",
    })
    monkeypatch.setattr(bill.buyer_module, "load_buyers", lambda account_id: [{
        "name": "Packing Consignee", "address": "Master Consignee Address",
        "email": "master-consignee@example.com",
    }])

    resolved = bill.resolve_party_snapshot({
        "packing_no": "PK-LEGACY", "invoice_no": "INV-LEGACY",
    }, "account-a")
    assert resolved["shipper"] == "Packing Shipper"
    assert resolved["shipper_address"] == "Packing Shipper Address"
    assert resolved["shipper_email"] == "invoice-shipper@example.com"
    assert resolved["shipper_phone"] == "333-4444"
    assert resolved["consignee"] == "Packing Consignee"
    assert resolved["consignee_address"] == "Packing Consignee Address"
    assert resolved["consignee_email"] == "invoice-consignee@example.com"
