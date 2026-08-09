from __future__ import annotations

import json

import pytest
from fastapi import HTTPException
from reportlab import rl_config
from starlette.requests import Request

from app import container_management as container


def _request(account_id):
    return Request({
        "type": "http", "method": "GET", "path": "/container", "headers": [],
        "trade_paper_user": {"account_id": account_id},
    })


def _form():
    return {
        "container_date": "2026-08-08", "shipment_no": "SHP-001",
        "packing_no": "PK-001", "bl_no": "", "invoice_no": "INV-001",
        "container_no": "CONT-001", "seal_no": "SEAL-001",
        "container_type": "40HC", "carrier": "Carrier", "vessel": "Vessel",
        "voyage_no": "V-1", "etd": "2026-08-10", "eta": "2026-08-20",
        "port_of_loading": "Busan", "port_of_discharge": "LA",
        "place_of_delivery": "Los Angeles", "loading_place": "Busan CY",
        "remarks": "Keep dry", "item_name": ["Snapshot Cargo"],
        "hs_code": ["847130"], "quantity": ["4"], "carton": ["2"],
        "net_weight": ["40"], "gross_weight": ["44"],
        "total_carton": "2", "total_net_weight": "40",
        "total_gross_weight": "44", "booking_no": "BOOK-001",
        "country_of_origin": "KR", "destination_country": "US",
    }


def test_container_snapshot_empty_legacy_update_and_account_isolation(tmp_path, monkeypatch):
    container_file = tmp_path / "containers.json"
    users_file = tmp_path / "users.json"
    container_file.write_text("[]\n", encoding="utf-8")
    users_file.write_text(json.dumps([
        {"account_id": "A"}, {"account_id": "B"},
    ]), encoding="utf-8")

    items = [{
        "name": "Snapshot Cargo", "hs_code": "847130", "quantity": "4",
        "carton": "2", "net_weight": "40", "gross_weight": "44",
    }]
    shipment = {
        "shipment_no": "SHP-001", "si_no": "SI-001", "packing_no": "PK-001",
        "invoice_no": "INV-001", "shipper": "Upstream Exporter",
        "shipper_address": "Snapshot Exporter Address",
        "shipper_email": "initial-upstream@example.com", "shipper_phone": "111-2222",
        "consignee": "Upstream Consignee", "consignee_address": "Snapshot Consignee Address",
        "consignee_email": "snapshot-consignee@example.com", "booking_no": "UPSTREAM-BOOK",
        "country_of_origin": "UPSTREAM-ORIGIN", "destination_country": "UPSTREAM-DEST",
        "items": items,
    }
    instruction = {
        "si_no": "SI-001", "shipment_no": "SHP-001", "packing_no": "PK-001",
        "invoice_no": "INV-001", "exporter_address": "SI Exporter Address",
        "items": items,
    }
    packing = {
        "packing_no": "PK-001", "invoice_no": "INV-001", "seller": "Packing Exporter",
        "buyer": "Packing Consignee", "items": items,
    }
    invoice = {"invoice_no": "INV-001", "seller": "Invoice Exporter", "buyer": "Invoice Consignee"}

    monkeypatch.setattr(container, "CONTAINER_FILE", container_file)
    monkeypatch.setattr(container, "USERS_FILE", users_file)
    monkeypatch.setattr(container, "load_shipments", lambda account: [shipment] if account == "A" else [])
    monkeypatch.setattr(container, "load_shipping_instructions", lambda account: [instruction] if account == "A" else [])
    monkeypatch.setattr(container, "load_packing_lists", lambda account: [packing] if account == "A" else [])
    monkeypatch.setattr(container.invoice_module, "load_invoices", lambda account: [invoice] if account == "A" else [])
    monkeypatch.setattr(container, "load_bills_of_lading", lambda account: [])
    monkeypatch.setattr(container.buyer_module, "load_buyers", lambda account: [])
    monkeypatch.setattr(container, "load_account_company", lambda account, path: {
        "name": f"Company {account}", "email": f"company-{account}@example.com",
    })

    container.save_container(
        _request("A"), exporter_email="", exporter_address=None,
        exporter_phone=None, exporter_name="Snapshot Exporter",
        consignee_name="Snapshot Consignee", consignee_address=None,
        consignee_email=None, **_form(),
    )
    stored = json.loads(container_file.read_text(encoding="utf-8"))[0]
    assert stored["shipment_no"] == "SHP-001"
    assert stored["exporter_name"] == "Snapshot Exporter"
    assert stored["exporter_address"] == "Snapshot Exporter Address"
    assert "exporter_email" in stored and stored["exporter_email"] == ""
    assert stored["consignee_email"] == "snapshot-consignee@example.com"
    assert stored["booking_no"] == "BOOK-001"
    assert {k: v for k, v in stored["items"][0].items() if k != "item_id"} == items[0]
    stored_item_id = stored["items"][0]["item_id"]
    assert stored_item_id.startswith("ITEM-")

    shipment.update({
        "shipper": "CHANGED UPSTREAM EXPORTER",
        "shipper_address": "CHANGED UPSTREAM ADDRESS",
        "shipper_email": "added-after-snapshot@example.com",
        "consignee_email": "changed-consignee@example.com",
        "items": [{**items[0], "name": "CHANGED UPSTREAM CARGO"}],
    })
    edit = container.edit_container("CON-001", _request("A")).body.decode()
    assert 'name="exporter_email" value=""' in edit
    assert "Snapshot Exporter Address" in edit
    assert "added-after-snapshot@example.com" not in edit
    assert "CHANGED UPSTREAM CARGO" not in edit

    api = container.container_data("CON-001", _request("A"))
    assert api["exporter_email"] == ""
    assert api["items"][0]["name"] == "Snapshot Cargo"
    assert "account_id" not in api
    detail = container.container_detail("CON-001", _request("A")).body.decode()
    assert "Snapshot Exporter Address" in detail
    assert "added-after-snapshot@example.com" not in detail

    update = _form()
    for field in (
        "item_name", "hs_code", "quantity", "carton", "net_weight", "gross_weight",
        "total_carton", "total_net_weight", "total_gross_weight", "booking_no",
        "country_of_origin", "destination_country",
    ):
        update.pop(field)
    container.update_container("CON-001", _request("A"), **update)
    updated = json.loads(container_file.read_text(encoding="utf-8"))[0]
    assert updated["booking_no"] == "BOOK-001"
    assert updated["exporter_email"] == ""
    assert updated["exporter_address"] == "Snapshot Exporter Address"
    assert {k: v for k, v in updated["items"][0].items() if k != "item_id"} == items[0]
    assert updated["items"][0]["item_id"] == stored_item_id
    assert updated["total_gross_weight"] == "44"

    monkeypatch.setattr(rl_config, "pageCompression", 0)
    pdf = container.container_pdf("CON-001", _request("A"))
    assert b"Snapshot Exporter" in pdf.body
    assert b"CHANGED UPSTREAM EXPORTER" not in pdf.body
    assert b"added-after-snapshot@example.com" not in pdf.body
    assert b"account_id" not in pdf.body

    legacy = container.resolve_container_snapshot({
        "container_record_no": "CON-LEGACY", "shipment_no": "SHP-001",
    }, "A")
    assert legacy["exporter_address"] == "CHANGED UPSTREAM ADDRESS"
    assert legacy["exporter_email"] == "added-after-snapshot@example.com"
    assert legacy["items"][0]["name"] == "CHANGED UPSTREAM CARGO"

    isolated = container.resolve_container_snapshot({
        "container_record_no": "CON-B", "shipment_no": "SHP-001",
    }, "B")
    assert isolated["exporter_name"] == "Company B"
    assert isolated["exporter_email"] == "company-B@example.com"
    with pytest.raises(HTTPException) as denied:
        container.container_data("CON-001", _request("B"))
    assert denied.value.status_code == 404
