import json

import pytest
from fastapi import HTTPException
from starlette.requests import Request

import app.buyer as buyer
import app.main as main
from app.account_buyer import ensure_legacy_buyer_ownership


def _request(account_id, path="/buyers"):
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


def test_legacy_buyer_migration_is_idempotent_and_backed_up(tmp_path):
    buyers_file = tmp_path / "buyers.json"
    users_file = tmp_path / "users.json"
    original = [
        {"name": "Legacy A", "address": "A", "email": "a@example.com", "country": "KR"},
        {"name": "Legacy B", "address": "B", "email": "b@example.com", "country": "US"},
    ]
    buyers_file.write_text(json.dumps(original, indent=2) + "\n", encoding="utf-8")
    users_file.write_text(
        json.dumps([{"account_id": "legacy-account", "email": "legacy@example.com"}]),
        encoding="utf-8",
    )

    first = ensure_legacy_buyer_ownership(buyers_file, users_file)
    first_bytes = buyers_file.read_bytes()
    second = ensure_legacy_buyer_ownership(buyers_file, users_file)

    assert [record["account_id"] for record in first] == ["legacy-account", "legacy-account"]
    assert second == first
    assert buyers_file.read_bytes() == first_bytes
    assert json.loads((tmp_path / "buyers.backup.json").read_text(encoding="utf-8")) == original


def test_buyer_crud_search_and_direct_access_are_account_scoped(tmp_path, monkeypatch):
    buyers_file = tmp_path / "buyers.json"
    users_file = tmp_path / "users.json"
    buyers_file.write_text("[]\n", encoding="utf-8")
    users_file.write_text(
        json.dumps([
            {"account_id": "account-a", "email": "a@example.com"},
            {"account_id": "account-b", "email": "b@example.com"},
        ]),
        encoding="utf-8",
    )
    monkeypatch.setattr(buyer, "BUYER_FILE", buyers_file)
    monkeypatch.setattr(buyer, "USERS_FILE", users_file)
    monkeypatch.setattr(buyer, "find_soft_warnings", lambda module, name, account_id="": [])

    buyer.save_buyer(_request("account-a"), "Buyer A", "Address A", "a@buyer.test", "KR")
    buyer.save_buyer(_request("account-b"), "Buyer B", "Address B", "b@buyer.test", "US")
    raw = json.loads(buyers_file.read_text(encoding="utf-8"))
    assert [record["account_id"] for record in raw] == ["account-a", "account-b"]

    assert buyer.buyer_data(_request("account-a")) == [{
        "name": "Buyer A", "address": "Address A", "email": "a@buyer.test", "country": "KR",
    }]
    assert buyer.buyer_data(_request("account-b")) == [{
        "name": "Buyer B", "address": "Address B", "email": "b@buyer.test", "country": "US",
    }]
    assert "account_id" not in buyer.buyer_data(_request("account-a"))[0]
    assert "Buyer A" in buyer.buyer_list(_request("account-a")).body.decode()
    assert "Buyer B" not in buyer.buyer_list(_request("account-a")).body.decode()

    buyer.update_buyer(0, _request("account-a"), "Buyer A Updated", "New A", "a@buyer.test", "KR")
    with pytest.raises(HTTPException) as edit_denied:
        buyer.edit_buyer(1, _request("account-a"))
    assert edit_denied.value.status_code == 404
    with pytest.raises(HTTPException) as update_denied:
        buyer.update_buyer(1, _request("account-a"), "Stolen", "", "", "")
    assert update_denied.value.status_code == 404
    with pytest.raises(HTTPException) as delete_denied:
        buyer.delete_buyer(1, _request("account-a"))
    assert delete_denied.value.status_code == 404

    monkeypatch.setattr(main.buyer_module, "BUYER_FILE", buyers_file)
    monkeypatch.setattr(main.buyer_module, "USERS_FILE", users_file)
    products_file = tmp_path / "products.json"
    invoices_file = tmp_path / "invoices.json"
    packing_file = tmp_path / "packing_lists.json"
    shipping_file = tmp_path / "shipping_instructions.json"
    booking_file = tmp_path / "booking_confirmations.json"
    products_file.write_text("[]\n", encoding="utf-8")
    invoices_file.write_text("[]\n", encoding="utf-8")
    packing_file.write_text("[]\n", encoding="utf-8")
    shipping_file.write_text("[]\n", encoding="utf-8")
    booking_file.write_text("[]\n", encoding="utf-8")
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
    monkeypatch.setattr(main, "load_account_company", lambda account_id, path: {})
    search_a = main.global_search(_request("account-a", "/search"), "Buyer").body.decode()
    search_b = main.global_search(_request("account-b", "/search"), "Buyer").body.decode()
    assert "Buyer A Updated" in search_a and "Buyer B" not in search_a
    assert "Buyer B" in search_b and "Buyer A Updated" not in search_b

    buyer.confirm_delete_buyer(0, _request("account-a"), "Buyer A Updated")
    assert buyer.buyer_data(_request("account-a")) == []
    assert buyer.buyer_data(_request("account-b"))[0]["name"] == "Buyer B"
