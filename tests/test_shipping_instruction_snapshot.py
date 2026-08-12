from __future__ import annotations

import json

import pytest
from fastapi import HTTPException
from reportlab import rl_config
from starlette.requests import Request

from app import shipping_instruction as shipping


def _request(account_id):
    return Request({
        "type": "http", "method": "GET", "path": "/si", "headers": [],
        "trade_paper_user": {"account_id": account_id},
    })


def _form():
    return {
        "shipment_no": "SHP-001", "si_date": "2026-08-08",
        "packing_no": "PK-001", "invoice_no": "INV-001",
        "shipper": "Snapshot Exporter", "consignee": "Snapshot Consignee",
        "notify_party": "Notify", "carrier": "Carrier", "vessel": "Vessel",
        "voyage_no": "V-1", "port_of_loading": "Busan",
        "port_of_discharge": "LA", "place_of_delivery": "Los Angeles",
        "shipping_marks": "Marks", "freight_terms": "Prepaid",
        "special_instructions": "Keep dry", "item_name": ["Snapshot Cargo"],
        "hs_code": ["847130"], "quantity": ["4"], "carton": ["2"],
        "net_weight": ["40"], "gross_weight": ["44"],
        "total_carton": "2", "total_net_weight": "40", "total_gross_weight": "44",
        "booking_no": "BOOK-001", "country_of_origin": "KR",
        "destination_country": "US",
    }


def test_shipping_instruction_snapshot_empty_legacy_and_account_isolation(tmp_path, monkeypatch):
    si_file = tmp_path / "shipping_instructions.json"
    users_file = tmp_path / "users.json"
    si_file.write_text("[]\n", encoding="utf-8")
    users_file.write_text(json.dumps([
        {"account_id": "A"}, {"account_id": "B"},
    ]), encoding="utf-8")

    items = [{
        "name": "Snapshot Cargo", "hs_code": "847130", "quantity": "4",
        "carton": "2", "net_weight": "40", "gross_weight": "44",
    }]
    shipment = {
        "shipment_no": "SHP-001", "packing_no": "PK-001", "invoice_no": "INV-001",
        "shipper": "Upstream Exporter", "shipper_address": "Snapshot Exporter Address",
        "shipper_email": "initial-upstream@example.com", "shipper_phone": "111-2222",
        "consignee": "Upstream Consignee", "consignee_address": "Snapshot Consignee Address",
        "consignee_email": "snapshot-consignee@example.com", "booking_no": "UPSTREAM-BOOK",
        "country_of_origin": "UPSTREAM-ORIGIN", "destination_country": "UPSTREAM-DEST",
        "items": items,
    }
    packing = {
        "packing_no": "PK-001", "invoice_no": "INV-001", "seller": "Packing Exporter",
        "buyer": "Packing Consignee", "items": items,
    }
    invoice = {"invoice_no": "INV-001", "seller": "Invoice Exporter", "buyer": "Invoice Consignee"}

    monkeypatch.setattr(shipping, "SI_FILE", si_file)
    monkeypatch.setattr(shipping, "USERS_FILE", users_file)
    monkeypatch.setattr(shipping, "load_shipments", lambda account: [shipment] if account == "A" else [])
    monkeypatch.setattr(shipping, "load_packing_lists", lambda account: [packing] if account == "A" else [])
    monkeypatch.setattr(shipping, "load_invoices", lambda account: [invoice] if account == "A" else [])
    monkeypatch.setattr(shipping.buyer_module, "load_buyers", lambda account: [])
    monkeypatch.setattr(shipping, "load_account_company", lambda account, path: {
        "name": f"Company {account}", "email": f"company-{account}@example.com",
    })
    monkeypatch.setattr(shipping, "valid_shipment_context", lambda value: "")

    new_html = shipping.si_form(_request("A"), packing_no="PK-001", shipment_no="SHP-001").body.decode()
    assert '<details class="tp-imported-section" data-imported-section="party">' in new_html
    assert '<details class="tp-imported-section" data-imported-section="cargo">' in new_html
    assert '<details class="tp-imported-section" data-imported-section="party" open>' not in new_html
    assert '<select id="packing_no" name="packing_no" aria-label="Packing No">' in new_html
    assert '<option value="PK-001" selected>PK-001</option>' in new_html
    assert '"invoice_no": "INV-001"' in new_html
    assert '"name": "Snapshot Cargo"' in new_html

    shipping.save_si(
        _request("A"), exporter_email="", exporter_address=None,
        exporter_phone=None, consignee_address=None, consignee_email=None,
        **_form(),
    )
    stored = json.loads(si_file.read_text(encoding="utf-8"))[0]
    assert stored["shipment_no"] == "SHP-001"
    assert stored["exporter_name"] == "Snapshot Exporter"
    assert stored["exporter_address"] == "Snapshot Exporter Address"
    assert "exporter_email" in stored and stored["exporter_email"] == ""
    assert stored["consignee_email"] == "snapshot-consignee@example.com"
    assert stored["booking_no"] == "BOOK-001"
    assert {k: v for k, v in stored["items"][0].items() if k != "item_id"} == items[0]
    assert stored["items"][0]["item_id"].startswith("ITEM-")

    shipment.update({
        "shipper_address": "CHANGED UPSTREAM ADDRESS",
        "shipper_email": "added-after-snapshot@example.com",
        "consignee_email": "changed-consignee@example.com",
        "items": [{**items[0], "name": "CHANGED UPSTREAM CARGO"}],
    })
    edit = shipping.edit_si("SI-001", _request("A")).body.decode()
    assert '<option value="PK-001" selected>PK-001</option>' in edit
    assert edit.count("Imported from previous document") == 2
    assert '<details class="tp-imported-section" data-imported-section="party">' in edit
    assert '<details class="tp-imported-section" data-imported-section="cargo">' in edit
    assert '<details class="tp-imported-section" data-imported-section="party" open>' not in edit
    assert edit.index('data-imported-section="party"') < edit.index('name="shipper"')
    assert edit.index('data-imported-section="cargo"') < edit.index('id="items"')
    assert 'name="exporter_email" value=""' in edit
    assert "Snapshot Exporter Address" in edit
    assert "added-after-snapshot@example.com" not in edit
    assert "CHANGED UPSTREAM CARGO" not in edit

    api = shipping.si_data("SI-001", _request("A"))
    assert api["exporter_email"] == ""
    assert api["items"][0]["name"] == "Snapshot Cargo"
    assert "account_id" not in api

    update = _form()
    update.pop("shipment_no")
    for field in ("booking_no", "country_of_origin", "destination_country"):
        update.pop(field)
    shipping.update_si("SI-001", _request("A"), **update)
    updated = json.loads(si_file.read_text(encoding="utf-8"))[0]
    assert updated["shipment_no"] == "SHP-001"
    assert updated["booking_no"] == "BOOK-001"
    assert updated["exporter_email"] == ""
    assert updated["exporter_address"] == "Snapshot Exporter Address"

    monkeypatch.setattr(rl_config, "pageCompression", 0)
    pdf = shipping.si_pdf("SI-001", _request("A"))
    assert b"Snapshot Exporter Address" in pdf.body
    assert b"added-after-snapshot@example.com" not in pdf.body
    assert b"account_id" not in pdf.body

    legacy = shipping.resolve_si_snapshot({
        "si_no": "SI-LEGACY", "shipment_no": "SHP-001",
    }, "A")
    assert legacy["exporter_address"] == "CHANGED UPSTREAM ADDRESS"
    assert legacy["exporter_email"] == "added-after-snapshot@example.com"
    assert legacy["items"][0]["name"] == "CHANGED UPSTREAM CARGO"

    isolated = shipping.resolve_si_snapshot({
        "si_no": "SI-B", "shipment_no": "SHP-001",
    }, "B")
    assert isolated["exporter_name"] == "Company B"
    assert isolated["exporter_email"] == "company-B@example.com"
    with pytest.raises(HTTPException) as denied:
        shipping.si_data("SI-001", _request("B"))
    assert denied.value.status_code == 404
