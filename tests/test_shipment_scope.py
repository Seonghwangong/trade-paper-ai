import json
import re

import pytest
from fastapi import HTTPException
from starlette.requests import Request

import app.booking_confirmation as booking
import app.bill_of_lading as bill_of_lading
import app.buyer as buyer
import app.invoice as invoice
import app.main as main
import app.packing as packing
import app.shipment as shipment
import app.shipping_instruction as shipping_instruction
from app.account_shipment import ensure_legacy_shipment_ownership
from app.validation import DataValidationError


def _request(account_id, path="/shipment-list"):
    return Request({
        "type": "http", "method": "GET", "scheme": "http",
        "path": path, "raw_path": path.encode(), "query_string": b"",
        "headers": [], "client": ("127.0.0.1", 1), "server": ("testserver", 80),
        "trade_paper_user": {
            "account_id": account_id, "company": f"Company {account_id}",
            "email": f"{account_id}@example.com",
        },
    })


def _form(name, buyer_name, invoice_no, packing_no, si_no):
    return {
        "shipment_date": "2026-08-01", "shipment_name": name,
        "customer": "", "buyer": buyer_name, "status": "Inquiry",
        "remarks": "Scope test", "quotation_no": "", "pi_no": "",
        "invoice_no": invoice_no, "packing_no": packing_no, "si_no": si_no,
        "bl_no": "", "co_no": "", "inspection_no": "",
        "insurance_no": "", "weight_no": "",
    }


def test_legacy_shipment_migration_is_idempotent_and_backed_up(tmp_path):
    shipment_file = tmp_path / "shipments.json"
    users_file = tmp_path / "users.json"
    original = [{"shipment_no": "SHP-001", "shipment_name": "Legacy"}]
    shipment_file.write_text(json.dumps(original, indent=2) + "\n", encoding="utf-8")
    users_file.write_text(json.dumps([
        {"account_id": "legacy-account", "email": "legacy@example.com"},
    ]), encoding="utf-8")

    first = ensure_legacy_shipment_ownership(shipment_file, users_file)
    first_bytes = shipment_file.read_bytes()
    second = ensure_legacy_shipment_ownership(shipment_file, users_file)

    assert first[0]["account_id"] == "legacy-account"
    assert second == first
    assert shipment_file.read_bytes() == first_bytes
    assert json.loads((tmp_path / "shipments.backup.json").read_text()) == original


def test_shipment_scope_crud_sources_workflow_pdf_search_and_dashboard(tmp_path, monkeypatch):
    users_file = tmp_path / "users.json"
    shipment_file = tmp_path / "shipments.json"
    buyer_file = tmp_path / "buyers.json"
    invoice_file = tmp_path / "invoices.json"
    packing_file = tmp_path / "packing_lists.json"
    si_file = tmp_path / "shipping_instructions.json"
    booking_file = tmp_path / "booking_confirmations.json"
    users_file.write_text(json.dumps([
        {"account_id": "account-a", "email": "a@example.com"},
        {"account_id": "account-b", "email": "b@example.com"},
    ]), encoding="utf-8")
    shipment_file.write_text("[]\n", encoding="utf-8")
    buyer_file.write_text(json.dumps([
        {"account_id": "account-a", "name": "Buyer A"},
        {"account_id": "account-b", "name": "Buyer B"},
    ]), encoding="utf-8")
    invoice_file.write_text(json.dumps([
        {"account_id": "account-a", "invoice_no": "INV-A", "buyer": "Buyer A", "items": []},
        {"account_id": "account-b", "invoice_no": "INV-B", "buyer": "Buyer B", "items": []},
    ]), encoding="utf-8")
    packing_file.write_text(json.dumps([
        {"account_id": "account-a", "packing_no": "PK-A", "invoice_no": "INV-A", "items": []},
        {"account_id": "account-b", "packing_no": "PK-B", "invoice_no": "INV-B", "items": []},
    ]), encoding="utf-8")
    si_file.write_text(json.dumps([
        {"account_id": "account-a", "si_no": "SI-A", "packing_no": "PK-A", "invoice_no": "INV-A", "items": []},
        {"account_id": "account-b", "si_no": "SI-B", "packing_no": "PK-B", "invoice_no": "INV-B", "items": []},
    ]), encoding="utf-8")
    booking_file.write_text("[]\n", encoding="utf-8")

    monkeypatch.setattr(shipment, "SHIPMENT_FILE", shipment_file)
    monkeypatch.setattr(shipment, "USERS_FILE", users_file)
    monkeypatch.setattr(buyer, "BUYER_FILE", buyer_file)
    monkeypatch.setattr(buyer, "USERS_FILE", users_file)
    monkeypatch.setattr(invoice, "INVOICE_FILE", invoice_file)
    monkeypatch.setattr(invoice, "USERS_FILE", users_file)
    monkeypatch.setattr(packing, "PACKING_FILE", packing_file)
    monkeypatch.setattr(packing, "USERS_FILE", users_file)
    monkeypatch.setattr(shipping_instruction, "SI_FILE", si_file)
    monkeypatch.setattr(shipping_instruction, "USERS_FILE", users_file)
    monkeypatch.setattr(booking, "BOOKING_FILE", booking_file)
    monkeypatch.setattr(booking, "USERS_FILE", users_file)
    monkeypatch.setattr(booking, "SHIPMENT_FILE", shipment_file)
    direct_files = {
        "invoice_no": invoice_file, "packing_no": packing_file, "si_no": si_file,
    }
    for descriptor in shipment.DOCUMENTS:
        path = direct_files.get(descriptor["field"], tmp_path / descriptor["file"].name)
        if not path.exists():
            path.write_text("[]\n", encoding="utf-8")
        monkeypatch.setitem(descriptor, "file", path)
    for descriptor in shipment.OPERATIONAL_RECORDS:
        path = booking_file if descriptor["key"] == "booking_record_no" else tmp_path / descriptor["file"].name
        if not path.exists():
            path.write_text("[]\n", encoding="utf-8")
        monkeypatch.setitem(descriptor, "file", path)
    monkeypatch.setattr(main.container_module, "CONTAINER_FILE", tmp_path / "containers.json")
    monkeypatch.setattr(main.container_module, "USERS_FILE", users_file)
    monkeypatch.setattr(shipment, "find_dependencies", lambda module, identifier, account_id: [])

    create_a = shipment.shipment_form(_request("account-a", "/shipment-form")).body.decode()
    assert 'value="SI-A"' in create_a and "SI-B" not in create_a
    selected_a = shipment.shipment_form(
        _request("account-a", "/shipment-form"), si_no="SI-A",
    ).body.decode()
    assert '<option value="SI-A" selected>SI-A</option>' in selected_a
    assert 'name="invoice_no" value="INV-A"' in selected_a
    assert 'name="packing_no" value="PK-A"' in selected_a
    assert "SI-B" not in selected_a
    stolen = shipment.shipment_form(
        _request("account-a", "/shipment-form"), si_no="SI-B",
    ).body.decode()
    assert "SI-B" not in stolen

    saved_a = shipment.save_shipment(_request("account-a"), **_form("Shipment A", "Buyer A", "INV-A", "PK-A", "SI-A"))
    saved_html = saved_a.body.decode()
    assert "Continue to Booking →" in saved_html
    assert "/booking-form?shipment_no=SHP-001&amp;si_no=SI-A&amp;packing_no=PK-A" in saved_html
    shipment.save_shipment(_request("account-b"), **_form("Shipment B", "Buyer B", "INV-B", "PK-B", "SI-B"))
    raw = json.loads(shipment_file.read_text())
    assert [record["shipment_no"] for record in raw] == ["SHP-001", "SHP-002"]
    assert [record["account_id"] for record in raw] == ["account-a", "account-b"]
    assert [record["shipment_no"] for record in shipment.load_shipments("account-a")] == ["SHP-001"]
    assert "account_id" not in shipment.shipment_data("SHP-001", _request("account-a"))

    with pytest.raises(DataValidationError):
        shipment.save_shipment(_request("account-a"), **_form("Forged Buyer", "Buyer B", "INV-A", "PK-A", "SI-A"))
    with pytest.raises(DataValidationError):
        shipment.save_shipment(_request("account-a"), **_form("Forged Invoice", "Buyer A", "INV-B", "PK-B", "SI-B"))

    list_a = shipment.shipment_list(_request("account-a")).body.decode()
    list_b = shipment.shipment_list(_request("account-b")).body.decode()
    assert "SHP-001" in list_a and "SHP-002" not in list_a
    assert "SHP-002" in list_b and "SHP-001" not in list_b
    assert "SHP-001" in shipment.shipment_list(_request("account-a"), "Shipment A").body.decode()
    assert "SHP-002" not in shipment.shipment_list(_request("account-a"), "Shipment B").body.decode()

    booking_file.write_text(json.dumps([
        {"account_id": "account-a", "booking_record_no": "BK-A", "shipment_no": "SHP-001"},
        {"account_id": "account-b", "booking_record_no": "BK-B", "shipment_no": "SHP-001"},
    ]), encoding="utf-8")
    detail = shipment.shipment_detail("SHP-001", _request("account-a")).body.decode()
    assert detail.count("Workflow Timeline") == 1
    assert detail.count("Document Relationship") == 1
    assert "INV-A" in detail and "INV-B" not in detail
    assert "PK-A" in detail and "PK-B" not in detail
    assert "SI-A" in detail and "SI-B" not in detail
    assert "BK-A" in detail and "BK-B" not in detail

    shipment.update_shipment("SHP-001", _request("account-a"), **_form("Shipment A Updated", "Buyer A", "INV-A", "PK-A", "SI-A"))
    assert shipment.shipment_data("SHP-001", _request("account-a"))["shipment_name"] == "Shipment A Updated"
    for action in (
        lambda: shipment.shipment_detail("SHP-002", _request("account-a")),
        lambda: shipment.edit_shipment("SHP-002", _request("account-a")),
        lambda: shipment.shipment_data("SHP-002", _request("account-a")),
        lambda: shipment.delete_shipment("SHP-002", _request("account-a")),
        lambda: shipment.confirm_delete_shipment("SHP-002", _request("account-a")),
        lambda: shipment.shipment_pdf("SHP-002", _request("account-a")),
    ):
        with pytest.raises(HTTPException) as denied:
            action()
        assert denied.value.status_code == 404
    pdf = shipment.shipment_pdf("SHP-001", _request("account-a"))
    assert pdf.status_code == 200 and pdf.body.startswith(b"%PDF")

    booking_form_a = booking.booking_form(_request("account-a", "/booking-form")).body.decode()
    booking_form_b = booking.booking_form(_request("account-b", "/booking-form")).body.decode()
    assert "SHP-001" in booking_form_a and "SHP-002" not in booking_form_a
    assert "SHP-002" in booking_form_b and "SHP-001" not in booking_form_b

    monkeypatch.setattr(main, "load_account_company", lambda account_id, path: {})
    monkeypatch.setattr(main, "dashboard_list", lambda filename: [])
    monkeypatch.setattr(main.container_module, "load_containers", lambda account_id: [])
    search_a = main.global_search(_request("account-a", "/search"), "SHP-").body.decode()
    search_b = main.global_search(_request("account-b", "/search"), "SHP-").body.decode()
    assert "SHP-001" in search_a and "SHP-002" not in search_a
    assert "SHP-002" in search_b and "SHP-001" not in search_b
    dashboard_a = main.home(_request("account-a", "/")).body.decode()
    dashboard_b = main.home(_request("account-b", "/")).body.decode()
    assert "Total Shipments<b>1</b>" in dashboard_a
    assert "Total Shipments<b>1</b>" in dashboard_b

    shipment.confirm_delete_shipment("SHP-001", _request("account-a"))
    assert shipment.load_shipments("account-a") == []
    assert shipment.load_shipments("account-b")[0]["shipment_no"] == "SHP-002"


def test_shipment_api_snapshot_contract_legacy_empty_and_upstream_immutability(tmp_path, monkeypatch):
    shipment_file = tmp_path / "shipments.json"
    users_file = tmp_path / "users.json"
    users_file.write_text(json.dumps([
        {"account_id": "account-a"}, {"account_id": "account-b"},
    ]), encoding="utf-8")
    shipment_file.write_text(json.dumps([
        {
            "account_id": "account-a", "shipment_no": "SHP-NEW",
            "shipment_name": "New", "invoice_no": "INV-A",
            "shipper": "Frozen Seller", "shipper_email": "frozen@example.com",
            "items": [{"name": "Frozen Cargo"}],
        },
        {
            "account_id": "account-a", "shipment_no": "SHP-EMPTY",
            "shipment_name": "Empty", "invoice_no": "INV-A",
            "shipper": "", "shipper_email": "", "items": [],
        },
        {
            "account_id": "account-a", "shipment_no": "SHP-LEGACY",
            "shipment_name": "Legacy", "invoice_no": "INV-A",
        },
        {
            "account_id": "account-b", "shipment_no": "SHP-OTHER",
            "shipment_name": "Other", "invoice_no": "INV-B",
        },
    ]), encoding="utf-8")
    upstream = {
        "account-a": [{
            "invoice_no": "INV-A", "seller": "Upstream Seller",
            "seller_email": "upstream@example.com", "items": [{"name": "Upstream Cargo"}],
        }],
        "account-b": [{"invoice_no": "INV-B", "seller": "Other Seller"}],
    }
    monkeypatch.setattr(shipment, "SHIPMENT_FILE", shipment_file)
    monkeypatch.setattr(shipment, "USERS_FILE", users_file)
    monkeypatch.setattr(invoice, "load_invoices", lambda account_id: upstream.get(account_id, []))
    monkeypatch.setattr(packing, "load_packing_lists", lambda account_id: [])
    monkeypatch.setattr(bill_of_lading, "load_bills_of_lading", lambda account_id: [])
    monkeypatch.setattr(buyer, "load_buyers", lambda account_id: [])
    monkeypatch.setattr(shipment, "load_account_company", lambda account_id, path: {})

    current = shipment.shipment_data("SHP-NEW", _request("account-a"))
    assert current["shipper"] == "Frozen Seller"
    assert current["items"] == [{"name": "Frozen Cargo"}]
    assert "account_id" not in current

    empty = shipment.shipment_data("SHP-EMPTY", _request("account-a"))
    assert empty["shipper"] == "" and empty["shipper_email"] == "" and empty["items"] == []

    legacy = shipment.shipment_data("SHP-LEGACY", _request("account-a"))
    assert legacy["shipper"] == "Upstream Seller"
    assert legacy["shipper_email"] == "upstream@example.com"
    assert legacy["items"] == [{"name": "Upstream Cargo"}]

    upstream["account-a"][0].update({
        "seller": "Changed Seller", "seller_email": "changed@example.com",
        "items": [{"name": "Changed Cargo"}],
    })
    unchanged = shipment.shipment_data("SHP-NEW", _request("account-a"))
    pdf_request = _request("account-a", "/shipment-pdf/SHP-NEW")
    shipment.shipment_pdf("SHP-NEW", pdf_request)
    assert unchanged["shipper"] == "Frozen Seller"
    assert unchanged["items"] == [{"name": "Frozen Cargo"}]
    assert pdf_request.scope["trade_paper_pdf_record"] == unchanged

    with pytest.raises(HTTPException) as denied:
        shipment.shipment_data("SHP-OTHER", _request("account-a"))
    assert denied.value.status_code == 404
