import json

from starlette.requests import Request

import app.routers.company as company_router
import app.main as main
from app.account_company import (
    company_setup_complete,
    ensure_account_companies,
    load_account_company,
)


def _request(account_id):
    return Request({
        "type": "http",
        "method": "GET",
        "scheme": "http",
        "path": "/company-data",
        "raw_path": b"/company-data",
        "query_string": b"",
        "headers": [],
        "client": ("127.0.0.1", 1),
        "server": ("testserver", 80),
        "trade_paper_user": {
            "account_id": account_id,
            "company": "Untrusted display value",
            "email": f"{account_id}@example.com",
        },
    })


def test_company_records_are_scoped_by_authenticated_account(tmp_path, monkeypatch):
    accounts_file = tmp_path / "account_companies.json"
    legacy_file = tmp_path / "company.json"
    legacy_file.write_text("{}\n", encoding="utf-8")
    users = [
        {"account_id": "account-a", "company": "Company A"},
        {"account_id": "account-b", "company": "Company B"},
    ]
    ensure_account_companies(users, accounts_file, legacy_file)
    monkeypatch.setattr(company_router, "ACCOUNT_COMPANIES_FILE", accounts_file)

    saved_a = company_router.save_company(
        _request("account-a"),
        {"account_id": "account-b", "name": "Scoped A", "address": "Address A"},
    )
    saved_b = company_router.save_company(
        _request("account-b"),
        {"name": "Scoped B", "address": "Address B"},
    )

    assert saved_a["name"] == "Scoped A"
    assert saved_b["name"] == "Scoped B"
    assert company_router.get_company_data(_request("account-a"))["address"] == "Address A"
    assert company_router.get_company_data(_request("account-b"))["address"] == "Address B"
    assert load_account_company("account-a", accounts_file)["name"] == "Scoped A"
    assert load_account_company("account-b", accounts_file)["name"] == "Scoped B"
    assert company_setup_complete("account-a", accounts_file)
    assert company_setup_complete("account-b", accounts_file)
    records = json.loads(accounts_file.read_text(encoding="utf-8"))
    assert {record["account_id"] for record in records} == {"account-a", "account-b"}
    monkeypatch.setattr(main.company_module, "ACCOUNT_COMPANIES_FILE", accounts_file)
    buyers_file = tmp_path / "buyers.json"
    products_file = tmp_path / "products.json"
    invoices_file = tmp_path / "invoices.json"
    packing_file = tmp_path / "packing_lists.json"
    shipping_file = tmp_path / "shipping_instructions.json"
    booking_file = tmp_path / "booking_confirmations.json"
    users_file = tmp_path / "users.json"
    buyers_file.write_text("[]\n", encoding="utf-8")
    products_file.write_text("[]\n", encoding="utf-8")
    invoices_file.write_text("[]\n", encoding="utf-8")
    packing_file.write_text("[]\n", encoding="utf-8")
    shipping_file.write_text("[]\n", encoding="utf-8")
    booking_file.write_text("[]\n", encoding="utf-8")
    users_file.write_text("[]\n", encoding="utf-8")
    monkeypatch.setattr(main.buyer_module, "BUYER_FILE", buyers_file)
    monkeypatch.setattr(main.buyer_module, "USERS_FILE", users_file)
    monkeypatch.setattr(main.product_module, "PRODUCT_FILE", products_file)
    monkeypatch.setattr(main.product_module, "USERS_FILE", users_file)
    monkeypatch.setattr(main.invoice_module, "INVOICE_FILE", invoices_file)
    monkeypatch.setattr(main.invoice_module, "USERS_FILE", users_file)
    monkeypatch.setattr(main.packing_module, "PACKING_FILE", packing_file)
    monkeypatch.setattr(main.packing_module, "USERS_FILE", users_file)
    monkeypatch.setattr(main.shipping_instruction_module, "SI_FILE", shipping_file)
    monkeypatch.setattr(main.shipping_instruction_module, "USERS_FILE", users_file)
    monkeypatch.setattr(main.booking_module, "BOOKING_FILE", booking_file)
    monkeypatch.setattr(main.booking_module, "USERS_FILE", users_file)
    monkeypatch.setattr(main.shipment_module, "load_shipments", lambda account_id: [])
    monkeypatch.setattr(main.container_module, "load_containers", lambda account_id: [])
    search_a = main.global_search(_request("account-a"), "").body.decode()
    search_b = main.global_search(_request("account-b"), "").body.decode()
    assert "Scoped A" in search_a and "Scoped B" not in search_a
    assert "Scoped B" in search_b and "Scoped A" not in search_b


def test_single_legacy_company_is_preserved_for_existing_account(tmp_path):
    accounts_file = tmp_path / "account_companies.json"
    legacy_file = tmp_path / "company.json"
    legacy = {
        "name": "Legacy Trade Company",
        "address": "Legacy Address",
        "email": "legacy@example.com",
        "phone": "123",
    }
    legacy_file.write_text(json.dumps(legacy), encoding="utf-8")

    ensure_account_companies(
        [{"account_id": "legacy-account", "company": "Old Login Company"}],
        accounts_file,
        legacy_file,
    )

    assert load_account_company("legacy-account", accounts_file) == legacy
    assert company_setup_complete("legacy-account", accounts_file)
    assert json.loads(legacy_file.read_text(encoding="utf-8")) == legacy
