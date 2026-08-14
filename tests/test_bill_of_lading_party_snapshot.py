from __future__ import annotations

import json

import pytest
from fastapi import HTTPException
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
        "shipper_phone": "",
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
        if field == "shipper_phone":
            continue
        assert payload[field] == value
    assert payload["shipper_phone"] == "999-9999"

    new_html = bill.bl_form(_request("account-a"), packing_no="PK-001").body.decode()
    assert new_html.count("Imported from previous document") == 2
    assert '<details class="tp-imported-section" data-imported-section="party">' in new_html
    assert '<details class="tp-imported-section" data-imported-section="cargo">' in new_html
    assert '<details class="tp-imported-section" data-imported-section="party" open>' not in new_html
    assert new_html.index('data-imported-section="party"') < new_html.index('name="shipper"')
    assert new_html.index('data-imported-section="cargo"') < new_html.index('id="items_area"')

    bill.save_bl(_request("account-a"), **_form(snapshot))
    stored = json.loads(bl_file.read_text(encoding="utf-8"))[0]
    for field, value in snapshot.items():
        assert stored[field] == value

    packing.update({
        "seller": "Changed Packing Shipper",
        "seller_address": "Changed Packing Address",
        "seller_email": "changed-packing@example.com",
        "seller_phone": "added-after-snapshot",
        "buyer": "Changed Packing Consignee",
    })
    api = bill.bl_data(stored["bl_no"], _request("account-a", f"/bl-data/{stored['bl_no']}"))
    for field, value in snapshot.items():
        assert api[field] == value
    assert api["shipper_phone"] == ""
    assert "account_id" not in api

    edit_html = bill.edit_bl(stored["bl_no"], _request("account-a")).body.decode()
    assert edit_html.count("Imported from previous document") == 2
    assert '<details class="tp-imported-section" data-imported-section="party">' in edit_html
    assert '<details class="tp-imported-section" data-imported-section="cargo">' in edit_html
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
        if value:
            assert value.encode() in pdf.body
    assert b"Changed Master Address" not in pdf.body
    assert b"Changed Buyer Address" not in pdf.body
    assert b"added-after-snapshot" not in pdf.body


def test_legacy_bill_api_edit_pdf_share_fallback_and_isolate_account(tmp_path, monkeypatch):
    bl_file = tmp_path / "bills_of_lading.json"
    users_file = tmp_path / "users.json"
    bl_file.write_text(json.dumps([{
        "account_id": "account-a", "bl_no": "BL-LEGACY",
        "packing_no": "PK-LEGACY", "invoice_no": "INV-LEGACY",
        "items": [],
    }]), encoding="utf-8")
    users_file.write_text(json.dumps([
        {"account_id": "account-a"}, {"account_id": "account-b"},
    ]), encoding="utf-8")
    packing = {
        "packing_no": "PK-LEGACY", "invoice_no": "INV-LEGACY",
        "seller": "Packing Shipper", "seller_address": "Packing Shipper Address",
        "buyer": "Packing Consignee", "buyer_address": "Packing Consignee Address",
    }
    invoice = {
        "invoice_no": "INV-LEGACY", "seller_email": "invoice-shipper@example.com",
        "buyer_email": "invoice-consignee@example.com",
    }
    monkeypatch.setattr(bill, "BL_FILE", bl_file)
    monkeypatch.setattr(bill, "USERS_FILE", users_file)
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
    resolved = bill.resolve_party_snapshot({"packing_no": "PK-LEGACY", "invoice_no": "INV-LEGACY"}, "account-a")
    assert resolved["shipper"] == "Packing Shipper"
    assert resolved["shipper_address"] == "Packing Shipper Address"
    assert resolved["shipper_email"] == "invoice-shipper@example.com"
    assert resolved["shipper_phone"] == "333-4444"
    assert resolved["consignee"] == "Packing Consignee"
    assert resolved["consignee_address"] == "Packing Consignee Address"
    assert resolved["consignee_email"] == "invoice-consignee@example.com"
    api = bill.bl_data("BL-LEGACY", _request("account-a", "/bl-data/BL-LEGACY"))
    for field in ("shipper", "shipper_address", "shipper_email", "shipper_phone", "consignee", "consignee_address", "consignee_email"):
        assert api[field] == resolved[field]
    assert "account_id" not in api
    edit_html = bill.edit_bl("BL-LEGACY", _request("account-a")).body.decode()
    for field in ("shipper", "shipper_address", "shipper_email", "shipper_phone"):
        assert f'name="{field}" value="{resolved[field]}"' in edit_html
    monkeypatch.setattr(rl_config, "pageCompression", 0)
    pdf = bill.bl_pdf("BL-LEGACY", _request("account-a", "/bl-pdf/BL-LEGACY"))
    for value in resolved.values():
        if isinstance(value, str) and value:
            assert value.encode() in pdf.body
    assert b"account_id" not in pdf.body
    with pytest.raises(HTTPException) as denied:
        bill.bl_data("BL-LEGACY", _request("account-b"))
    assert denied.value.status_code == 404


def test_booking_driven_bl_create_snapshot_isolation_edit_and_success(tmp_path, monkeypatch):
    bl_file = tmp_path / "bills_of_lading.json"
    bl_file.write_text("[]\n", encoding="utf-8")
    monkeypatch.setattr(bill, "BL_FILE", bl_file)
    bookings = {
        "A": [{"booking_record_no": "BK-A", "booking_reference": "REF-A", "shipment_no": "SHP-A",
               "si_no": "SI-A", "packing_no": "PK-A", "invoice_no": "INV-A"}],
        "B": [{"booking_record_no": "BK-B", "booking_reference": "REF-B", "shipment_no": "SHP-B",
               "si_no": "SI-B", "packing_no": "PK-B", "invoice_no": "INV-B"}],
    }
    snapshot = {
        "shipment_no": "SHP-A", "si_no": "SI-A", "packing_no": "PK-A", "invoice_no": "INV-A",
        "exporter_name": "Booking Exporter", "exporter_address": "Exporter Address",
        "consignee_name": "Booking Consignee", "consignee_address": "Consignee Address",
        "carrier": "Booking Carrier", "vessel": "Booking Vessel", "voyage_no": "V-100",
        "port_of_loading": "Busan", "port_of_discharge": "Long Beach",
        "items": [{"name": "Booking Cargo", "quantity": "5", "hs_code": "847130",
                   "carton": "2", "net_weight": "40", "gross_weight": "44"}],
        "total_carton": "2", "total_net_weight": "40", "total_gross_weight": "44",
    }
    monkeypatch.setattr(bill, "load_bookings", lambda account_id: bookings.get(account_id, []))
    import app.booking_confirmation as booking_module
    monkeypatch.setattr(booking_module, "resolve_booking_snapshot", lambda record, account_id: snapshot if account_id == "A" else {})
    monkeypatch.setattr(bill, "validate_bl_links", lambda *args, **kwargs: None)
    monkeypatch.setattr(bill, "resolve_party_snapshot", lambda record, account_id: record)
    monkeypatch.setattr(bill, "shipment_context_redirect_url", lambda *args: "/shipment/SHP-A")

    html = bill.bl_form(_request("A"), booking_record_no="BK-A").body.decode()
    assert 'name="booking_record_no" required' in html
    assert "BK-A · REF-A" in html and "BK-B" not in html
    assert all(value in html for value in ("SHP-A", "SI-A", "PK-A", "INV-A", "Booking Exporter", "Booking Cargo"))
    assert 'name="shipper" value="Booking Exporter" readonly' in html
    assert 'name="item_name" value="Booking Cargo" placeholder="Item Name" readonly' in html
    with pytest.raises(HTTPException) as denied:
        bill.bl_form(_request("B"), booking_record_no="BK-A")
    assert denied.value.status_code == 404

    response = bill.save_bl(
        _request("A"), booking_record_no="BK-A", shipment_no="SHP-A", si_no="SI-A",
        packing_no="PK-A", invoice_no="INV-A", shipper="Booking Exporter",
        consignee="Booking Consignee", notify_party="", carrier="Ocean Line", vessel="Vessel User",
        voyage_no="V-200", port_of_loading="Busan", port_of_discharge="LA",
        place_of_receipt="Seoul", place_of_delivery="LA", freight_term="Prepaid",
        bl_date="2026-08-12", item_name=["Booking Cargo"], quantity=["5"],
        hs_code=["847130"], carton=["2"], net_weight=["40"], gross_weight=["44"],
        item_id=[], total_carton="2", total_net_weight="40", total_gross_weight="44",
        shipper_address="Exporter Address", shipper_email="", shipper_phone="",
        consignee_address="Consignee Address", consignee_email="",
    )
    success = response.body.decode()
    assert "Continue to Certificate of Origin →" in success
    assert "/co-form?bl_no=BL-001&amp;shipment_no=SHP-A" in success
    stored = json.loads(bl_file.read_text())[0]
    assert stored["bl_no"] == "BL-001" and stored["booking_record_no"] == "BK-A"
    assert stored["carrier"] == "Ocean Line" and stored["freight_term"] == "Prepaid"
    edit = bill.edit_bl("BL-001", _request("A")).body.decode()
    assert 'name="booking_record_no" value="BK-A"' in edit
    assert 'onchange="selectBooking(this.value)"' not in edit
