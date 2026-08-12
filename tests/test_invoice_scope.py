import json

import pytest
from fastapi import HTTPException
from starlette.requests import Request

import app.invoice as invoice
import app.packing as packing
import app.main as main
from app.account_invoice import ensure_legacy_invoice_ownership


def _request(account_id, path="/invoice-list"):
    return Request({
        "type": "http",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": [],
        "client": ("127.0.0.1", 1),
        "server": ("testserver", 80),
        "trade_paper_user": {
            "account_id": account_id,
            "company": f"Company {account_id}",
            "email": f"{account_id}@example.com",
        },
    })


def _payload(buyer, item, account_id="forged-account"):
    return {
        "account_id": account_id,
        "seller": "Scoped Seller",
        "buyer": buyer,
        "buyer_address": f"{buyer} Address",
        "buyer_email": f"{buyer.lower().replace(' ', '-')}@example.com",
        "currency": "USD",
        "items": [{
            "name": item,
            "hs_code": "123456",
            "quantity": 2,
            "unit_price": 15,
        }],
    }


def test_legacy_invoice_migration_is_idempotent_and_backed_up(tmp_path):
    invoices_file = tmp_path / "invoices.json"
    users_file = tmp_path / "users.json"
    original = [
        {"invoice_no": "INV-001", "seller": "Seller", "buyer": "Buyer", "items": []},
        {"invoice_no": "INV-002", "seller": "Seller", "buyer": "Buyer", "items": []},
    ]
    invoices_file.write_text(json.dumps(original, indent=2) + "\n", encoding="utf-8")
    users_file.write_text(
        json.dumps([{"account_id": "legacy-account", "email": "legacy@example.com"}]),
        encoding="utf-8",
    )

    first = ensure_legacy_invoice_ownership(invoices_file, users_file)
    first_bytes = invoices_file.read_bytes()
    second = ensure_legacy_invoice_ownership(invoices_file, users_file)

    assert [record["account_id"] for record in first] == ["legacy-account", "legacy-account"]
    assert second == first
    assert invoices_file.read_bytes() == first_bytes
    assert json.loads((tmp_path / "invoices.backup.json").read_text(encoding="utf-8")) == original


def test_invoice_crud_search_pdf_and_direct_access_are_account_scoped(tmp_path, monkeypatch):
    invoices_file = tmp_path / "invoices.json"
    users_file = tmp_path / "users.json"
    proformas_file = tmp_path / "proformas.json"
    packing_file = tmp_path / "packing_lists.json"
    buyers_file = tmp_path / "buyers.json"
    products_file = tmp_path / "products.json"
    account_companies_file = tmp_path / "account_companies.json"
    invoices_file.write_text("[]\n", encoding="utf-8")
    proformas_file.write_text("[]\n", encoding="utf-8")
    packing_file.write_text("[]\n", encoding="utf-8")
    buyers_file.write_text("[]\n", encoding="utf-8")
    products_file.write_text("[]\n", encoding="utf-8")
    users_file.write_text(
        json.dumps([
            {"account_id": "account-a", "email": "a@example.com"},
            {"account_id": "account-b", "email": "b@example.com"},
        ]),
        encoding="utf-8",
    )
    account_companies_file.write_text(json.dumps([
        {"account_id": "account-a", "name": "Seller A", "setup_complete": True},
        {"account_id": "account-b", "name": "Seller B", "setup_complete": True},
    ]), encoding="utf-8")
    monkeypatch.setattr(invoice, "INVOICE_FILE", invoices_file)
    monkeypatch.setattr(invoice, "USERS_FILE", users_file)
    monkeypatch.setattr(invoice, "PROFORMA_FILE", proformas_file)
    monkeypatch.setattr(packing, "PACKING_FILE", packing_file)
    monkeypatch.setattr(packing, "USERS_FILE", users_file)
    monkeypatch.setattr(invoice, "ACCOUNT_COMPANIES_FILE", account_companies_file)
    monkeypatch.setattr(invoice, "find_dependencies", lambda module, identifier, account_id: [])

    created_a = invoice.create_invoice(_request("account-a"), _payload("Buyer A", "Product A"))
    created_b = invoice.create_invoice(_request("account-b"), _payload("Buyer B", "Product B"))
    assert created_a["invoice_no"] == "INV-001"
    assert created_b["invoice_no"] == "INV-002"
    assert "account_id" not in created_a and "account_id" not in created_b
    raw = json.loads(invoices_file.read_text(encoding="utf-8"))
    assert [record["account_id"] for record in raw] == ["account-a", "account-b"]

    assert [record["invoice_no"] for record in invoice.invoice_data(_request("account-a"))] == ["INV-001"]
    assert [record["invoice_no"] for record in invoice.invoice_data(_request("account-b"))] == ["INV-002"]
    assert "account_id" not in invoice.invoice_data(_request("account-a"))[0]
    assert "INV-001" in invoice.invoice_list(_request("account-a")).body.decode()
    assert "INV-002" not in invoice.invoice_list(_request("account-a")).body.decode()
    assert "INV-001" in invoice.invoice_list(_request("account-a"), "Buyer A").body.decode()
    assert "INV-002" not in invoice.invoice_list(_request("account-a"), "Buyer B").body.decode()

    packing_file.write_text(json.dumps([{
        "account_id": "account-b", "packing_no": "PK-B", "invoice_no": "INV-001",
        "seller": "Seller B", "buyer": "Buyer B", "items": [],
    }]), encoding="utf-8")
    foreign_only = invoice.invoice_list(_request("account-a")).body.decode()
    assert "/edit-packing/PK-B" not in foreign_only
    assert "/packing-page?invoice_no=INV-001" in foreign_only

    packing_file.write_text(json.dumps([
        {
            "account_id": "account-b", "packing_no": "PK-B", "invoice_no": "INV-001",
            "seller": "Seller B", "buyer": "Buyer B", "items": [],
        },
        {
            "account_id": "account-a", "packing_no": "PK-A", "invoice_no": "INV-001",
            "seller": "Seller A", "buyer": "Buyer A", "items": [],
        },
    ]), encoding="utf-8")
    calls = {"count": 0}
    real_loader = packing.load_packing_lists

    def counted_loader(account_id):
        calls["count"] += 1
        return real_loader(account_id)

    monkeypatch.setattr(packing, "load_packing_lists", counted_loader)
    owned_packing = invoice.invoice_list(_request("account-a")).body.decode()
    assert "/edit-packing/PK-A" in owned_packing
    assert "/edit-packing/PK-B" not in owned_packing
    assert calls["count"] == 1

    invoice.update_invoice(
        "INV-001", _request("account-a"), "Seller A", "USD", "Buyer A Updated",
        "Updated Address", "a@example.com", "Product A", "123456", "3", "20",
    )
    with pytest.raises(HTTPException) as edit_denied:
        invoice.edit_invoice("INV-002", _request("account-a"))
    assert edit_denied.value.status_code == 404
    with pytest.raises(HTTPException) as update_denied:
        invoice.update_invoice(
            "INV-002", _request("account-a"), "Stolen", "USD", "Stolen",
            "", "", "Product", "", "1", "1",
        )
    assert update_denied.value.status_code == 404
    with pytest.raises(HTTPException) as delete_denied:
        invoice.delete_invoice("INV-002", _request("account-a"))
    assert delete_denied.value.status_code == 404
    with pytest.raises(HTTPException) as pdf_denied:
        invoice.invoice_pdf("INV-002", _request("account-a"))
    assert pdf_denied.value.status_code == 404

    pdf = invoice.invoice_pdf("INV-001", _request("account-a"))
    assert pdf.status_code == 200
    assert pdf.media_type == "application/pdf"
    assert pdf.body.startswith(b"%PDF")
    assert pdf.headers["content-disposition"] == 'attachment; filename="INV-001.pdf"'
    assert b"account_id" not in pdf.body
    preview = invoice.preview_invoice_pdf(_request("account-a"), {
        **created_a,
        "account_id": "forged-account",
    })
    assert preview.body.startswith(b"%PDF")
    assert b"account_id" not in preview.body

    monkeypatch.setattr(main.invoice_module, "INVOICE_FILE", invoices_file)
    monkeypatch.setattr(main.invoice_module, "USERS_FILE", users_file)
    monkeypatch.setattr(main.packing_module, "PACKING_FILE", packing_file)
    monkeypatch.setattr(main.packing_module, "USERS_FILE", users_file)
    shipping_file = tmp_path / "shipping_instructions.json"
    shipping_file.write_text("[]\n", encoding="utf-8")
    monkeypatch.setattr(main.shipping_instruction_module, "SI_FILE", shipping_file)
    monkeypatch.setattr(main.shipping_instruction_module, "USERS_FILE", users_file)
    booking_file = tmp_path / "booking_confirmations.json"
    booking_file.write_text("[]\n", encoding="utf-8")
    monkeypatch.setattr(main.booking_module, "BOOKING_FILE", booking_file)
    monkeypatch.setattr(main.booking_module, "USERS_FILE", users_file)
    monkeypatch.setattr(main.shipment_module, "load_shipments", lambda account_id: [])
    monkeypatch.setattr(main.container_module, "load_containers", lambda account_id: [])
    monkeypatch.setattr(main.buyer_module, "BUYER_FILE", buyers_file)
    monkeypatch.setattr(main.buyer_module, "USERS_FILE", users_file)
    monkeypatch.setattr(main.product_module, "PRODUCT_FILE", products_file)
    monkeypatch.setattr(main.product_module, "USERS_FILE", users_file)
    monkeypatch.setattr(main, "load_account_company", lambda account_id, path: {})
    search_a = main.global_search(_request("account-a", "/search"), "INV").body.decode()
    search_b = main.global_search(_request("account-b", "/search"), "INV").body.decode()
    assert "INV-001" in search_a and "INV-002" not in search_a
    assert "INV-002" in search_b and "INV-001" not in search_b
    assert [record["invoice_no"] for record in main.get_invoices(_request("account-a"))] == ["INV-001"]
    with pytest.raises(HTTPException) as legacy_pdf_denied:
        main.invoice_pdf(1, _request("account-a"))
    assert legacy_pdf_denied.value.status_code == 404

    invoice.confirm_delete_invoice("INV-001", _request("account-a"))
    assert invoice.invoice_data(_request("account-a")) == []
    assert invoice.invoice_data(_request("account-b"))[0]["invoice_no"] == "INV-002"
