from __future__ import annotations

import json

from reportlab import rl_config
from starlette.requests import Request

from app import booking_confirmation as booking


def _request(account_id):
    return Request({"type": "http", "method": "GET", "path": "/booking", "headers": [],
                    "trade_paper_user": {"account_id": account_id}})


def _form():
    return {
        "booking_date": "2026-08-06", "shipment_no": "SHP-001", "si_no": "SI-001",
        "packing_no": "PK-001", "bl_no": "BL-001", "invoice_no": "INV-001",
        "booking_no": "BOOK-001", "carrier": "Carrier", "vessel": "Vessel", "voyage_no": "V-1",
        "container_type": "40HC", "container_count": "1", "etd": "2026-08-10", "eta": "2026-08-20",
        "port_of_loading": "Busan", "port_of_discharge": "LA", "place_of_delivery": "LA",
        "cut_off_date": "2026-08-08", "loading_place": "Warehouse", "remarks": "Snapshot",
        "country_of_origin": "KR", "destination_country": "US", "item_name": ["Cargo"],
        "hs_code": ["847130"], "quantity": ["4"], "origin": ["KR"], "carton": ["2"],
        "net_weight": ["40"], "gross_weight": ["44"], "total_carton": "2",
        "total_net_weight": "40", "total_gross_weight": "44",
    }


def test_booking_snapshot_create_edit_update_pdf_legacy_and_isolation(tmp_path, monkeypatch):
    booking_file = tmp_path / "booking_confirmations.json"
    users_file = tmp_path / "users.json"
    booking_file.write_text("[]\n")
    users_file.write_text(json.dumps([{"account_id": "A"}, {"account_id": "B"}]))
    party = {
        "shipper": "Shipment Exporter", "shipper_address": "Exporter Address",
        "shipper_email": "exporter@example.com", "shipper_phone": "111-2222",
        "consignee": "Shipment Consignee", "consignee_address": "Consignee Address",
        "consignee_email": "consignee@example.com",
    }
    items = [{"name": "Cargo", "hs_code": "847130", "quantity": "4", "origin": "KR",
              "carton": "2", "net_weight": "40", "gross_weight": "44"}]
    shipment = {"shipment_no": "SHP-001", "si_no": "SI-001", "bl_no": "BL-001",
                "packing_no": "PK-001", "invoice_no": "INV-001", "country_of_origin": "KR",
                "destination_country": "US", **party, "items": items}
    bill = {"bl_no": "BL-001", "packing_no": "PK-001", "invoice_no": "INV-001",
            "shipper": "B/L Exporter", "consignee": "B/L Consignee", "place_of_delivery": "US",
            "items": [{**items[0], "name": "B/L Cargo"}]}
    packing = {"packing_no": "PK-001", "invoice_no": "INV-001", "seller_address": "Packing Address"}
    invoice = {"invoice_no": "INV-001", "seller_email": "invoice@example.com"}
    instruction = {"si_no": "SI-001", "shipment_no": "SHP-001", "packing_no": "PK-001",
                   "invoice_no": "INV-001", "items": items}

    monkeypatch.setattr(booking, "BOOKING_FILE", booking_file)
    monkeypatch.setattr(booking, "USERS_FILE", users_file)
    monkeypatch.setattr(booking, "load_shipments", lambda account: [shipment] if account == "A" else [])
    monkeypatch.setattr(booking, "load_shipping_instructions", lambda account: [instruction] if account == "A" else [])
    monkeypatch.setattr(booking, "load_bills_of_lading", lambda account: [bill] if account == "A" else [])
    monkeypatch.setattr(booking, "load_packing_lists", lambda account: [packing] if account == "A" else [])
    monkeypatch.setattr(booking.invoice_module, "owned_invoice_records", lambda account: [invoice] if account == "A" else [])
    monkeypatch.setattr(booking.buyer_module, "load_buyers", lambda account: [])
    monkeypatch.setattr(booking, "load_account_company", lambda account, path: {"name": "Master Exporter"})

    form = booking.booking_form(_request("A"), shipment_no="SHP-001", si_no="SI-001",
                                packing_no="PK-001", bl_no="BL-001").body.decode()
    assert all(value in form for value in ("Shipment Exporter", "Exporter Address", "Cargo", "847130", "44"))
    assert form.count("Imported from previous document") == 2
    assert '<details class="tp-imported-section" data-imported-section="party">' in form
    assert '<details class="tp-imported-section" data-imported-section="cargo">' in form
    assert '<details class="tp-imported-section" data-imported-section="party" open>' not in form
    assert form.index('data-imported-section="party"') < form.index('name="exporter_name"')
    assert form.index('data-imported-section="cargo"') < form.index('id="items"')
    assert '<label>Shipment *</label><select required aria-required="true" onchange="selectShipment(this.value)" name="shipment_no">' in form
    assert 'name="si_no" value="SI-001"' in form and 'name="packing_no" value="PK-001"' in form
    assert 'name="invoice_no" value="INV-001"' in form
    assert 'name="exporter_name" value="Shipment Exporter" placeholder="Exporter Name" readonly' in form
    assert 'name="item_name" value="Cargo" placeholder="Item" readonly' in form
    payload = _form()
    response = booking.save_booking(_request("A"), booking_reference="CARRIER-REF-001", **payload)
    success = response.body.decode()
    assert "Continue to Bill of Lading →" in success
    assert "/bl-form?packing_no=PK-001&amp;shipment_no=SHP-001" in success
    stored = json.loads(booking_file.read_text())[0]
    assert stored["booking_no"] == "BK-001" and stored["shipment_no"] == "SHP-001"
    assert stored["booking_reference"] == "CARRIER-REF-001"
    assert stored["exporter_address"] == "Exporter Address"
    assert stored["consignee_email"] == "consignee@example.com"
    assert {k: v for k, v in stored["items"][0].items() if k != "item_id"} == items[0]
    assert stored["items"][0]["item_id"].startswith("ITEM-")
    edit = booking.edit_booking("BK-001", _request("A")).body.decode()
    assert '<details class="tp-imported-section" data-imported-section="party">' in edit
    assert '<details class="tp-imported-section" data-imported-section="cargo">' in edit
    assert all(value in edit for value in ("Exporter Address", "consignee@example.com", "44"))
    assert '<input type="hidden" name="shipment_no" value="SHP-001">' in edit
    assert 'onchange="selectShipment(this.value)"' not in edit

    changed = {**shipment, "shipper_address": "CHANGED", "items": []}
    monkeypatch.setattr(booking, "load_shipments", lambda account: [changed] if account == "A" else [])
    omitted = {key: value for key, value in payload.items()
               if key not in {"origin", "carton", "net_weight", "gross_weight"}}
    booking.update_booking("BK-001", _request("A"), **omitted)
    updated = json.loads(booking_file.read_text())[0]
    assert updated["exporter_address"] == "Exporter Address"
    assert updated["items"][0]["gross_weight"] == "44"
    monkeypatch.setattr(rl_config, "pageCompression", 0)
    pdf = booking.booking_pdf("BK-001", _request("A"))
    assert b"Exporter Address" in pdf.body and b"CHANGED" not in pdf.body
    api = booking.booking_data("BK-001", _request("A"))
    assert "account_id" not in api and b"account_id" not in pdf.body

    legacy = booking.resolve_booking_snapshot({"shipment_no": "SHP-001"}, "A")
    assert legacy["exporter_name"] == "Shipment Exporter"
    legacy_bl = booking.resolve_booking_snapshot({"bl_no": "BL-001"}, "A")
    assert legacy_bl["exporter_name"] == "B/L Exporter"
    assert legacy_bl["exporter_address"] == "Packing Address"
    assert legacy_bl["exporter_email"] == "invoice@example.com"
    assert legacy_bl["items"][0]["name"] == "B/L Cargo"
    assert booking.resolve_booking_snapshot({"bl_no": "BL-001"}, "B")["exporter_name"] == "Master Exporter"
