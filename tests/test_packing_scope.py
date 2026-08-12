import json
import re

import pytest
from fastapi import HTTPException
from starlette.requests import Request

import app.invoice as invoice
import app.main as main
import app.packing as packing
from app.account_packing import ensure_legacy_packing_ownership


def _request(account_id, path="/packing-list"):
    return Request({
        "type": "http", "method": "GET", "scheme": "http",
        "path": path, "raw_path": path.encode(), "query_string": b"",
        "headers": [], "client": ("127.0.0.1", 1), "server": ("testserver", 80),
        "trade_paper_user": {
            "account_id": account_id,
            "company": f"Company {account_id}",
            "email": f"{account_id}@example.com",
        },
    })


def _payload(invoice_no, buyer, account_id="forged-account"):
    return {
        "account_id": account_id,
        "invoice_no": invoice_no,
        "seller": "Scoped Seller",
        "buyer": buyer,
        "items": [{
            "name": f"{buyer} Product", "quantity": 3, "hs_code": "123456",
            "carton": "2", "net_weight": "10", "gross_weight": "12",
        }],
    }


def test_legacy_packing_migration_is_idempotent_and_backed_up(tmp_path):
    packing_file = tmp_path / "packing_lists.json"
    users_file = tmp_path / "users.json"
    original = [
        {"packing_no": "PK-001", "invoice_no": "INV-001", "buyer": "Buyer A"},
        {"packing_no": "PK-002", "invoice_no": "INV-002", "buyer": "Buyer B"},
    ]
    packing_file.write_text(json.dumps(original, indent=2) + "\n", encoding="utf-8")
    users_file.write_text(json.dumps([
        {"account_id": "legacy-account", "email": "legacy@example.com"},
    ]), encoding="utf-8")

    first = ensure_legacy_packing_ownership(packing_file, users_file)
    first_bytes = packing_file.read_bytes()
    second = ensure_legacy_packing_ownership(packing_file, users_file)

    assert [record["account_id"] for record in first] == ["legacy-account", "legacy-account"]
    assert second == first
    assert packing_file.read_bytes() == first_bytes
    assert json.loads((tmp_path / "packing_lists.backup.json").read_text(encoding="utf-8")) == original


def test_packing_crud_search_pdf_invoice_reference_and_dashboard_are_scoped(tmp_path, monkeypatch):
    packing_file = tmp_path / "packing_lists.json"
    invoices_file = tmp_path / "invoices.json"
    users_file = tmp_path / "users.json"
    companies_file = tmp_path / "account_companies.json"
    buyers_file = tmp_path / "buyers.json"
    products_file = tmp_path / "products.json"
    shipping_file = tmp_path / "shipping_instructions.json"
    booking_file = tmp_path / "booking_confirmations.json"
    packing_file.write_text("[]\n", encoding="utf-8")
    invoices_file.write_text(json.dumps([
        {"account_id": "account-a", "invoice_no": "INV-001", "seller": "Seller A", "buyer": "Buyer A", "items": []},
        {"account_id": "account-b", "invoice_no": "INV-002", "seller": "Seller B", "buyer": "Buyer B", "items": []},
    ]), encoding="utf-8")
    users_file.write_text(json.dumps([
        {"account_id": "account-a", "email": "a@example.com"},
        {"account_id": "account-b", "email": "b@example.com"},
    ]), encoding="utf-8")
    companies_file.write_text(json.dumps([
        {"account_id": "account-a", "name": "Seller A", "setup_complete": True},
        {"account_id": "account-b", "name": "Seller B", "setup_complete": True},
    ]), encoding="utf-8")
    buyers_file.write_text("[]\n", encoding="utf-8")
    products_file.write_text("[]\n", encoding="utf-8")
    shipping_file.write_text("[]\n", encoding="utf-8")
    booking_file.write_text("[]\n", encoding="utf-8")

    monkeypatch.setattr(packing, "PACKING_FILE", packing_file)
    monkeypatch.setattr(packing, "USERS_FILE", users_file)
    monkeypatch.setattr(packing, "ACCOUNT_COMPANIES_FILE", companies_file)
    monkeypatch.setattr(invoice, "INVOICE_FILE", invoices_file)
    monkeypatch.setattr(invoice, "USERS_FILE", users_file)
    monkeypatch.setattr(packing, "find_dependencies", lambda module, identifier, account_id: [])

    created_a = packing.create_packing_list(_request("account-a"), _payload("INV-001", "Buyer A"))
    created_b = packing.create_packing_list(_request("account-b"), _payload("INV-002", "Buyer B"))
    assert created_a["packing_no"] == "PK-001"
    assert created_b["packing_no"] == "PK-002"
    assert "account_id" not in created_a and "account_id" not in created_b
    raw = json.loads(packing_file.read_text(encoding="utf-8"))
    assert [record["account_id"] for record in raw] == ["account-a", "account-b"]

    with pytest.raises(HTTPException) as forged_create:
        packing.create_packing_list(_request("account-b"), _payload("INV-001", "Stolen"))
    assert forged_create.value.status_code == 404
    assert [record["packing_no"] for record in packing.load_packing_lists("account-a")] == ["PK-001"]
    assert "account_id" not in packing.load_packing_lists("account-a")[0]
    html_a = packing.packing_list(_request("account-a")).body.decode()
    assert "PK-001" in html_a and "PK-002" not in html_a
    assert "PK-001" in packing.packing_list(_request("account-a"), "Buyer A").body.decode()
    assert "PK-002" not in packing.packing_list(_request("account-a"), "Buyer B").body.decode()

    packing.update_packing(
        "PK-001", _request("account-a"), "INV-001", "Seller A", "Buyer A Updated",
        ["Product A"], ["4"], ["123456"], ["3"], ["11"], ["13"],
    )
    assert packing.load_packing_lists("account-a")[0]["items"][0]["quantity"] == 4
    with pytest.raises(HTTPException) as forged_update:
        packing.update_packing(
            "PK-001", _request("account-a"), "INV-002", "Seller", "Buyer",
            ["Product"], ["1"], [""], [""], [""], [""],
        )
    assert forged_update.value.status_code == 404
    for action in (
        lambda: packing.edit_packing("PK-002", _request("account-a")),
        lambda: packing.delete_packing("PK-002", _request("account-a")),
        lambda: packing.confirm_delete_packing("PK-002", _request("account-a")),
        lambda: packing.packing_list_pdf("PK-002", _request("account-a")),
    ):
        with pytest.raises(HTTPException) as denied:
            action()
        assert denied.value.status_code == 404

    pdf = packing.packing_list_pdf("PK-001", _request("account-a"))
    assert pdf.status_code == 200 and pdf.body.startswith(b"%PDF")
    assert pdf.headers["content-disposition"] == 'attachment; filename="PK-001.pdf"'
    assert b"account_id" not in pdf.body
    preview = packing.preview_packing_list_pdf(_request("account-a"), {
        **created_a, "account_id": "forged-account",
    })
    assert preview.body.startswith(b"%PDF") and b"account_id" not in preview.body

    monkeypatch.setattr(main.packing_module, "PACKING_FILE", packing_file)
    monkeypatch.setattr(main.packing_module, "USERS_FILE", users_file)
    monkeypatch.setattr(main.invoice_module, "INVOICE_FILE", invoices_file)
    monkeypatch.setattr(main.invoice_module, "USERS_FILE", users_file)
    monkeypatch.setattr(main.buyer_module, "BUYER_FILE", buyers_file)
    monkeypatch.setattr(main.buyer_module, "USERS_FILE", users_file)
    monkeypatch.setattr(main.product_module, "PRODUCT_FILE", products_file)
    monkeypatch.setattr(main.product_module, "USERS_FILE", users_file)
    monkeypatch.setattr(main.shipping_instruction_module, "SI_FILE", shipping_file)
    monkeypatch.setattr(main.shipping_instruction_module, "USERS_FILE", users_file)
    monkeypatch.setattr(main.booking_module, "BOOKING_FILE", booking_file)
    monkeypatch.setattr(main.booking_module, "USERS_FILE", users_file)
    monkeypatch.setattr(main.shipment_module, "load_shipments", lambda account_id: [])
    monkeypatch.setattr(main.container_module, "load_containers", lambda account_id: [])
    monkeypatch.setattr(main, "load_account_company", lambda account_id, path: {})
    search_a = main.global_search(_request("account-a", "/search"), "PK").body.decode()
    search_b = main.global_search(_request("account-b", "/search"), "PK").body.decode()
    assert "PK-001" in search_a and "PK-002" not in search_a
    assert "PK-002" in search_b and "PK-001" not in search_b
    dashboard_a = main.home(_request("account-a", "/")).body.decode()
    dashboard_b = main.home(_request("account-b", "/")).body.decode()
    assert re.search(r'href="/packing-list"[^>]*>.*?<strong>1</strong>', dashboard_a, re.S)
    assert re.search(r'href="/packing-list"[^>]*>.*?<strong>1</strong>', dashboard_b, re.S)

    packing.confirm_delete_packing("PK-001", _request("account-a"))
    assert packing.load_packing_lists("account-a") == []
    assert packing.load_packing_lists("account-b")[0]["packing_no"] == "PK-002"
