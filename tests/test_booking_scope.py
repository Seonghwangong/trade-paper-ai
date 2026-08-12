import json
import re

import pytest
from fastapi import HTTPException
from starlette.requests import Request

import app.booking_confirmation as booking
import app.invoice as invoice
import app.main as main
import app.packing as packing
import app.shipping_instruction as shipping_instruction
import app.shipment as shipment
from app.account_booking import ensure_legacy_booking_ownership
from app.validation import DataValidationError


def _request(account_id, path="/booking-list"):
    return Request({
        "type": "http", "method": "GET", "scheme": "http",
        "path": path, "raw_path": path.encode(), "query_string": b"",
        "headers": [], "client": ("127.0.0.1", 1), "server": ("testserver", 80),
        "trade_paper_user": {
            "account_id": account_id, "company": f"Company {account_id}",
            "email": f"{account_id}@example.com",
        },
    })


def _form(shipment_no, si_no, packing_no, invoice_no, booking_no):
    return {
        "booking_date": "2026-08-01", "shipment_no": shipment_no, "si_no": si_no,
        "packing_no": packing_no, "bl_no": "", "invoice_no": invoice_no,
        "booking_no": booking_no, "carrier": "CODEX Carrier", "vessel": "CODEX Vessel",
        "voyage_no": "V001", "container_type": "40HC", "container_count": "1",
        "etd": "2026-08-10", "eta": "2026-08-20", "port_of_loading": "Busan",
        "port_of_discharge": "LA", "place_of_delivery": "LA",
        "cut_off_date": "2026-08-08", "loading_place": "Warehouse", "remarks": "Scope test",
        "item_name": ["Product"], "hs_code": ["123456"], "quantity": ["3"],
        "carton": ["2"], "net_weight": ["10"], "gross_weight": ["12"],
        "total_carton": "2", "total_net_weight": "10", "total_gross_weight": "12",
    }


def test_legacy_booking_migration_is_idempotent_and_backed_up(tmp_path):
    booking_file = tmp_path / "booking_confirmations.json"
    users_file = tmp_path / "users.json"
    original = [{"booking_record_no": "BK-001", "booking_no": "CARRIER-001"}]
    booking_file.write_text(json.dumps(original, indent=2) + "\n", encoding="utf-8")
    users_file.write_text(json.dumps([
        {"account_id": "legacy-account", "email": "legacy@example.com"},
    ]), encoding="utf-8")

    first = ensure_legacy_booking_ownership(booking_file, users_file)
    first_bytes = booking_file.read_bytes()
    second = ensure_legacy_booking_ownership(booking_file, users_file)

    assert first[0]["account_id"] == "legacy-account"
    assert second == first
    assert booking_file.read_bytes() == first_bytes
    assert json.loads((tmp_path / "booking_confirmations.backup.json").read_text()) == original


def test_booking_scope_sources_crud_pdf_search_and_dashboard(tmp_path, monkeypatch):
    booking_file = tmp_path / "booking_confirmations.json"
    users_file = tmp_path / "users.json"
    shipment_file = tmp_path / "shipments.json"
    si_file = tmp_path / "shipping_instructions.json"
    packing_file = tmp_path / "packing_lists.json"
    invoice_file = tmp_path / "invoices.json"
    bl_file = tmp_path / "bills_of_lading.json"
    buyers_file = tmp_path / "buyers.json"
    products_file = tmp_path / "products.json"
    booking_file.write_text("[]\n", encoding="utf-8")
    users_file.write_text(json.dumps([
        {"account_id": "account-a", "email": "a@example.com"},
        {"account_id": "account-b", "email": "b@example.com"},
    ]), encoding="utf-8")
    shipment_file.write_text(json.dumps([
        {"account_id": "account-a", "shipment_no": "SHP-A", "si_no": "SI-001", "packing_no": "PK-001", "invoice_no": "INV-001", "bl_no": ""},
        {"account_id": "account-b", "shipment_no": "SHP-B", "si_no": "SI-002", "packing_no": "PK-002", "invoice_no": "INV-002", "bl_no": ""},
    ]), encoding="utf-8")
    si_file.write_text(json.dumps([
        {"account_id": "account-a", "si_no": "SI-001", "packing_no": "PK-001", "invoice_no": "INV-001", "items": []},
        {"account_id": "account-b", "si_no": "SI-002", "packing_no": "PK-002", "invoice_no": "INV-002", "items": []},
    ]), encoding="utf-8")
    packing_file.write_text(json.dumps([
        {"account_id": "account-a", "packing_no": "PK-001", "invoice_no": "INV-001", "items": []},
        {"account_id": "account-b", "packing_no": "PK-002", "invoice_no": "INV-002", "items": []},
    ]), encoding="utf-8")
    invoice_file.write_text(json.dumps([
        {"account_id": "account-a", "invoice_no": "INV-001", "seller": "A", "buyer": "A", "items": []},
        {"account_id": "account-b", "invoice_no": "INV-002", "seller": "B", "buyer": "B", "items": []},
    ]), encoding="utf-8")
    bl_file.write_text("[]\n", encoding="utf-8")
    buyers_file.write_text("[]\n", encoding="utf-8")
    products_file.write_text("[]\n", encoding="utf-8")

    monkeypatch.setattr(booking, "BOOKING_FILE", booking_file)
    monkeypatch.setattr(booking, "USERS_FILE", users_file)
    monkeypatch.setattr(booking, "SHIPMENT_FILE", shipment_file)
    monkeypatch.setattr(shipment, "SHIPMENT_FILE", shipment_file)
    monkeypatch.setattr(shipment, "USERS_FILE", users_file)
    monkeypatch.setattr(booking, "BL_FILE", bl_file)
    monkeypatch.setattr(shipping_instruction, "SI_FILE", si_file)
    monkeypatch.setattr(shipping_instruction, "USERS_FILE", users_file)
    monkeypatch.setattr(packing, "PACKING_FILE", packing_file)
    monkeypatch.setattr(packing, "USERS_FILE", users_file)
    monkeypatch.setattr(invoice, "INVOICE_FILE", invoice_file)
    monkeypatch.setattr(invoice, "USERS_FILE", users_file)
    monkeypatch.setattr(booking, "find_dependencies", lambda module, identifier, account_id: [])

    booking.save_booking(_request("account-a"), **_form("SHP-A", "SI-001", "PK-001", "INV-001", "BOOK-A"))
    booking.save_booking(_request("account-b"), **_form("SHP-B", "SI-002", "PK-002", "INV-002", "BOOK-B"))
    assert [record["booking_record_no"] for record in booking.load_bookings("account-a")] == ["BK-001"]
    assert [record["booking_record_no"] for record in booking.load_bookings("account-b")] == ["BK-002"]
    raw = json.loads(booking_file.read_text())
    assert [record["account_id"] for record in raw] == ["account-a", "account-b"]
    assert "account_id" not in booking.booking_data("BK-001", _request("account-a"))

    with pytest.raises(DataValidationError):
        booking.save_booking(_request("account-b"), **_form("SHP-A", "SI-001", "PK-001", "INV-001", "STOLEN"))
    form_b = booking.booking_form(_request("account-b"), shipment_no="SHP-B").body.decode()
    assert "SI-002" in form_b and "SI-001" not in form_b
    assert "PK-002" in form_b and "PK-001" not in form_b
    html_a = booking.booking_list(_request("account-a")).body.decode()
    assert "BK-001" in html_a and "BK-002" not in html_a
    assert "BK-001" in booking.booking_list(_request("account-a"), "BK-001").body.decode()
    assert "Total Bookings: 0" in booking.booking_list(_request("account-a"), "BK-002").body.decode()

    booking.update_booking("BK-001", _request("account-a"), **_form("SHP-A", "SI-001", "PK-001", "INV-001", "BOOK-A-UPDATED"))
    assert booking.booking_data("BK-001", _request("account-a"))["booking_no"] == "BK-001"
    with pytest.raises(DataValidationError):
        booking.update_booking("BK-001", _request("account-a"), **_form("SHP-B", "SI-002", "PK-002", "INV-002", "STOLEN"))
    for action in (
        lambda: booking.edit_booking("BK-002", _request("account-a")),
        lambda: booking.booking_data("BK-002", _request("account-a")),
        lambda: booking.booking_detail("BK-002", _request("account-a")),
        lambda: booking.delete_booking("BK-002", _request("account-a")),
        lambda: booking.confirm_delete_booking("BK-002", _request("account-a")),
        lambda: booking.booking_pdf("BK-002", _request("account-a")),
    ):
        with pytest.raises(HTTPException) as denied:
            action()
        assert denied.value.status_code == 404

    pdf = booking.booking_pdf("BK-001", _request("account-a"))
    assert pdf.status_code == 200 and pdf.body.startswith(b"%PDF")
    preview_payload = {**booking.booking_data("BK-001", _request("account-a")), "account_id": "forged"}
    preview = booking.create_booking_pdf(_request("account-a"), preview_payload)
    assert preview.body.startswith(b"%PDF") and b"account_id" not in preview.body

    monkeypatch.setattr(main.booking_module, "BOOKING_FILE", booking_file)
    monkeypatch.setattr(main.booking_module, "USERS_FILE", users_file)
    monkeypatch.setattr(main.shipment_module, "SHIPMENT_FILE", shipment_file)
    monkeypatch.setattr(main.shipment_module, "USERS_FILE", users_file)
    monkeypatch.setattr(main.container_module, "load_containers", lambda account_id: [])
    monkeypatch.setattr(main.shipping_instruction_module, "SI_FILE", si_file)
    monkeypatch.setattr(main.shipping_instruction_module, "USERS_FILE", users_file)
    monkeypatch.setattr(main.packing_module, "PACKING_FILE", packing_file)
    monkeypatch.setattr(main.packing_module, "USERS_FILE", users_file)
    monkeypatch.setattr(main.invoice_module, "INVOICE_FILE", invoice_file)
    monkeypatch.setattr(main.invoice_module, "USERS_FILE", users_file)
    monkeypatch.setattr(main.buyer_module, "BUYER_FILE", buyers_file)
    monkeypatch.setattr(main.buyer_module, "USERS_FILE", users_file)
    monkeypatch.setattr(main.product_module, "PRODUCT_FILE", products_file)
    monkeypatch.setattr(main.product_module, "USERS_FILE", users_file)
    monkeypatch.setattr(main, "load_account_company", lambda account_id, path: {})
    search_a = main.global_search(_request("account-a", "/search"), "BK-").body.decode()
    search_b = main.global_search(_request("account-b", "/search"), "BK-").body.decode()
    assert "BK-001" in search_a and "BK-002" not in search_a
    assert "BK-002" in search_b and "BK-001" not in search_b
    monkeypatch.setattr(main, "dashboard_list", lambda filename: [])
    dashboard_a = main.home(_request("account-a", "/")).body.decode()
    dashboard_b = main.home(_request("account-b", "/")).body.decode()
    assert re.search(r'<h3>Booking Confirmation</h3>\s*<div class="document-count">1</div>', dashboard_a)
    assert re.search(r'<h3>Booking Confirmation</h3>\s*<div class="document-count">1</div>', dashboard_b)

    booking.confirm_delete_booking("BK-001", _request("account-a"))
    assert booking.load_bookings("account-a") == []
    assert booking.load_bookings("account-b")[0]["booking_record_no"] == "BK-002"
