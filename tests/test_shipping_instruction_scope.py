import json
import re

import pytest
from fastapi import HTTPException
from starlette.requests import Request

import app.main as main
import app.packing as packing
import app.shipping_instruction as shipping_instruction
from app.account_shipping_instruction import ensure_legacy_shipping_instruction_ownership
from app.validation import DataValidationError


def _request(account_id, path="/si-list"):
    return Request({
        "type": "http", "method": "GET", "scheme": "http",
        "path": path, "raw_path": path.encode(), "query_string": b"",
        "headers": [], "client": ("127.0.0.1", 1), "server": ("testserver", 80),
        "trade_paper_user": {
            "account_id": account_id, "company": f"Company {account_id}",
            "email": f"{account_id}@example.com",
        },
    })


def _form(packing_no, invoice_no, consignee):
    return {
        "shipment_no": "", "si_date": "2026-08-01", "packing_no": packing_no,
        "invoice_no": invoice_no, "shipper": "Scoped Shipper", "consignee": consignee,
        "notify_party": "Notify", "carrier": "Carrier", "vessel": "Vessel",
        "voyage_no": "V001", "port_of_loading": "Busan",
        "port_of_discharge": "LA", "place_of_delivery": "LA",
        "shipping_marks": "Marks", "freight_terms": "Prepaid",
        "special_instructions": "Handle carefully", "item_name": ["Product"],
        "hs_code": ["123456"], "quantity": ["3"], "carton": ["2"],
        "net_weight": ["10"], "gross_weight": ["12"], "total_carton": "2",
        "total_net_weight": "10", "total_gross_weight": "12",
    }


def test_legacy_shipping_instruction_migration_is_idempotent_and_backed_up(tmp_path):
    si_file = tmp_path / "shipping_instructions.json"
    users_file = tmp_path / "users.json"
    original = [{"si_no": "SI-001", "packing_no": "PK-001", "invoice_no": "INV-001"}]
    si_file.write_text(json.dumps(original, indent=2) + "\n", encoding="utf-8")
    users_file.write_text(json.dumps([
        {"account_id": "legacy-account", "email": "legacy@example.com"},
    ]), encoding="utf-8")

    first = ensure_legacy_shipping_instruction_ownership(si_file, users_file)
    first_bytes = si_file.read_bytes()
    second = ensure_legacy_shipping_instruction_ownership(si_file, users_file)

    assert first[0]["account_id"] == "legacy-account"
    assert second == first
    assert si_file.read_bytes() == first_bytes
    assert json.loads((tmp_path / "shipping_instructions.backup.json").read_text()) == original


def test_shipping_instruction_scope_reference_crud_pdf_search_and_dashboard(tmp_path, monkeypatch):
    si_file = tmp_path / "shipping_instructions.json"
    packing_file = tmp_path / "packing_lists.json"
    users_file = tmp_path / "users.json"
    buyers_file = tmp_path / "buyers.json"
    products_file = tmp_path / "products.json"
    invoices_file = tmp_path / "invoices.json"
    booking_file = tmp_path / "booking_confirmations.json"
    si_file.write_text("[]\n", encoding="utf-8")
    packing_file.write_text(json.dumps([
        {"account_id": "account-a", "packing_no": "PK-001", "invoice_no": "INV-001", "seller": "A", "buyer": "A", "items": []},
        {"account_id": "account-b", "packing_no": "PK-002", "invoice_no": "INV-002", "seller": "B", "buyer": "B", "items": []},
    ]), encoding="utf-8")
    users_file.write_text(json.dumps([
        {"account_id": "account-a", "email": "a@example.com"},
        {"account_id": "account-b", "email": "b@example.com"},
    ]), encoding="utf-8")
    buyers_file.write_text("[]\n", encoding="utf-8")
    products_file.write_text("[]\n", encoding="utf-8")
    invoices_file.write_text("[]\n", encoding="utf-8")
    booking_file.write_text("[]\n", encoding="utf-8")

    monkeypatch.setattr(shipping_instruction, "SI_FILE", si_file)
    monkeypatch.setattr(shipping_instruction, "USERS_FILE", users_file)
    monkeypatch.setattr(packing, "PACKING_FILE", packing_file)
    monkeypatch.setattr(packing, "USERS_FILE", users_file)
    monkeypatch.setattr(shipping_instruction, "find_dependencies", lambda module, identifier, account_id: [])

    form_a = shipping_instruction.si_form(_request("account-a", "/si-form")).body.decode()
    assert '<option value="PK-001">PK-001</option>' in form_a
    assert "PK-002" not in form_a
    selected_a = shipping_instruction.si_form(
        _request("account-a", "/si-form"), packing_no="PK-001",
    ).body.decode()
    assert '<option value="PK-001" selected>PK-001</option>' in selected_a
    stolen = shipping_instruction.si_form(
        _request("account-a", "/si-form"), packing_no="PK-002",
    ).body.decode()
    assert "PK-002" not in stolen
    assert '<option value="" selected>' not in stolen

    shipping_instruction.save_si(_request("account-a"), **_form("PK-001", "INV-001", "Consignee A"))
    shipping_instruction.save_si(_request("account-b"), **_form("PK-002", "INV-002", "Consignee B"))
    assert [record["si_no"] for record in shipping_instruction.load_shipping_instructions("account-a")] == ["SI-001"]
    assert [record["si_no"] for record in shipping_instruction.load_shipping_instructions("account-b")] == ["SI-002"]
    raw = json.loads(si_file.read_text())
    assert [record["account_id"] for record in raw] == ["account-a", "account-b"]
    assert "account_id" not in shipping_instruction.si_data("SI-001", _request("account-a"))

    with pytest.raises(DataValidationError):
        shipping_instruction.save_si(_request("account-b"), **_form("PK-001", "INV-001", "Stolen"))
    html_a = shipping_instruction.si_list(_request("account-a")).body.decode()
    assert "SI-001" in html_a and "SI-002" not in html_a
    assert "SI-001" in shipping_instruction.si_list(_request("account-a"), "Consignee A").body.decode()
    assert "SI-002" not in shipping_instruction.si_list(_request("account-a"), "Consignee B").body.decode()

    update_a = _form("PK-001", "INV-001", "Consignee A Updated")
    update_a.pop("shipment_no")
    shipping_instruction.update_si("SI-001", _request("account-a"), **update_a)
    assert shipping_instruction.si_data("SI-001", _request("account-a"))["consignee"] == "Consignee A Updated"
    with pytest.raises(DataValidationError):
        forged_update = _form("PK-002", "INV-002", "Stolen")
        forged_update.pop("shipment_no")
        shipping_instruction.update_si("SI-001", _request("account-a"), **forged_update)
    for action in (
        lambda: shipping_instruction.edit_si("SI-002", _request("account-a")),
        lambda: shipping_instruction.si_data("SI-002", _request("account-a")),
        lambda: shipping_instruction.delete_si("SI-002", _request("account-a")),
        lambda: shipping_instruction.confirm_delete_si("SI-002", _request("account-a")),
        lambda: shipping_instruction.si_pdf("SI-002", _request("account-a")),
    ):
        with pytest.raises(HTTPException) as denied:
            action()
        assert denied.value.status_code == 404

    pdf = shipping_instruction.si_pdf("SI-001", _request("account-a"))
    assert pdf.status_code == 200 and pdf.body.startswith(b"%PDF")
    assert pdf.headers["content-disposition"] == "attachment; filename=SI-001.pdf"
    preview_payload = {**shipping_instruction.si_data("SI-001", _request("account-a")), "account_id": "forged"}
    preview = shipping_instruction.create_si_pdf(_request("account-a"), preview_payload)
    assert preview.body.startswith(b"%PDF") and b"account_id" not in preview.body

    monkeypatch.setattr(main.shipping_instruction_module, "SI_FILE", si_file)
    monkeypatch.setattr(main.shipping_instruction_module, "USERS_FILE", users_file)
    monkeypatch.setattr(main.packing_module, "PACKING_FILE", packing_file)
    monkeypatch.setattr(main.packing_module, "USERS_FILE", users_file)
    monkeypatch.setattr(main.invoice_module, "INVOICE_FILE", invoices_file)
    monkeypatch.setattr(main.invoice_module, "USERS_FILE", users_file)
    monkeypatch.setattr(main.buyer_module, "BUYER_FILE", buyers_file)
    monkeypatch.setattr(main.buyer_module, "USERS_FILE", users_file)
    monkeypatch.setattr(main.product_module, "PRODUCT_FILE", products_file)
    monkeypatch.setattr(main.product_module, "USERS_FILE", users_file)
    monkeypatch.setattr(main, "load_account_company", lambda account_id, path: {})
    monkeypatch.setattr(main.booking_module, "BOOKING_FILE", booking_file)
    monkeypatch.setattr(main.booking_module, "USERS_FILE", users_file)
    monkeypatch.setattr(main.shipment_module, "load_shipments", lambda account_id: [])
    monkeypatch.setattr(main.container_module, "load_containers", lambda account_id: [])
    search_a = main.global_search(_request("account-a", "/search"), "SI-").body.decode()
    search_b = main.global_search(_request("account-b", "/search"), "SI-").body.decode()
    assert "SI-001" in search_a and "SI-002" not in search_a
    assert "SI-002" in search_b and "SI-001" not in search_b
    dashboard_a = main.home(_request("account-a", "/")).body.decode()
    dashboard_b = main.home(_request("account-b", "/")).body.decode()
    assert re.search(r'<h3>Shipping Instruction</h3>\s*<div class="document-count">1</div>', dashboard_a)
    assert re.search(r'<h3>Shipping Instruction</h3>\s*<div class="document-count">1</div>', dashboard_b)

    shipping_instruction.confirm_delete_si("SI-001", _request("account-a"))
    assert shipping_instruction.load_shipping_instructions("account-a") == []
    assert shipping_instruction.load_shipping_instructions("account-b")[0]["si_no"] == "SI-002"
