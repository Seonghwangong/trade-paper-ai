import json

import pytest
from fastapi import HTTPException
from starlette.requests import Request

import app.main as main
import app.product as product
import app.certificate_of_origin as certificate_of_origin
import app.customs_declaration as customs_declaration
import app.inspection_certificate as inspection_certificate
import app.insurance_certificate as insurance_certificate
from app.account_product import ensure_legacy_product_ownership


def _request(account_id, path="/products", query_string=b""):
    return Request({
        "type": "http",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": query_string,
        "headers": [],
        "client": ("127.0.0.1", 1),
        "server": ("testserver", 80),
        "trade_paper_user": {
            "account_id": account_id,
            "company": f"Company {account_id}",
            "email": f"{account_id}@example.com",
        },
    })


def test_legacy_product_migration_is_idempotent_and_backed_up(tmp_path):
    products_file = tmp_path / "products.json"
    users_file = tmp_path / "users.json"
    original = [
        {"name": "Legacy A", "hs_code": "1", "unit_price": "10", "origin": "KR"},
        {"name": "Legacy B", "hs_code": "2", "unit_price": "20", "origin": "US"},
    ]
    products_file.write_text(json.dumps(original, indent=2) + "\n", encoding="utf-8")
    users_file.write_text(
        json.dumps([{"account_id": "legacy-account", "email": "legacy@example.com"}]),
        encoding="utf-8",
    )

    first = ensure_legacy_product_ownership(products_file, users_file)
    first_bytes = products_file.read_bytes()
    second = ensure_legacy_product_ownership(products_file, users_file)

    assert [record["account_id"] for record in first] == ["legacy-account", "legacy-account"]
    assert second == first
    assert products_file.read_bytes() == first_bytes
    assert json.loads((tmp_path / "products.backup.json").read_text(encoding="utf-8")) == original


def test_product_crud_search_and_direct_access_are_account_scoped(tmp_path, monkeypatch):
    products_file = tmp_path / "products.json"
    buyers_file = tmp_path / "buyers.json"
    invoices_file = tmp_path / "invoices.json"
    packing_file = tmp_path / "packing_lists.json"
    shipping_file = tmp_path / "shipping_instructions.json"
    booking_file = tmp_path / "booking_confirmations.json"
    users_file = tmp_path / "users.json"
    products_file.write_text("[]\n", encoding="utf-8")
    buyers_file.write_text("[]\n", encoding="utf-8")
    invoices_file.write_text("[]\n", encoding="utf-8")
    packing_file.write_text("[]\n", encoding="utf-8")
    shipping_file.write_text("[]\n", encoding="utf-8")
    booking_file.write_text("[]\n", encoding="utf-8")
    users_file.write_text(
        json.dumps([
            {"account_id": "account-a", "email": "a@example.com"},
            {"account_id": "account-b", "email": "b@example.com"},
        ]),
        encoding="utf-8",
    )
    monkeypatch.setattr(product, "PRODUCT_FILE", products_file)
    monkeypatch.setattr(product, "USERS_FILE", users_file)
    monkeypatch.setattr(product, "find_soft_warnings", lambda module, name, account_id="": [])

    product.save_product(_request("account-a"), "Product A", "100", "10", "KR")
    product.save_product(_request("account-b"), "Product B", "200", "20", "US")
    raw = json.loads(products_file.read_text(encoding="utf-8"))
    assert [record["account_id"] for record in raw] == ["account-a", "account-b"]

    assert product.product_data(_request("account-a")) == [{
        "name": "Product A", "hs_code": "100", "unit_price": "10", "origin": "KR", "unit": "",
    }]
    assert product.product_data(_request("account-b")) == [{
        "name": "Product B", "hs_code": "200", "unit_price": "20", "origin": "US", "unit": "",
    }]
    assert "account_id" not in product.product_data(_request("account-a"))[0]
    assert "Product A" in product.product_list(_request("account-a")).body.decode()
    assert "Product B" not in product.product_list(_request("account-a")).body.decode()
    assert "Product A" in product.product_list(_request("account-a"), "100").body.decode()
    assert "Product B" not in product.product_list(_request("account-a"), "200").body.decode()

    product.update_product(0, _request("account-a"), "Product A Updated", "101", "11", "KR")
    with pytest.raises(HTTPException) as edit_denied:
        product.edit_product(1, _request("account-a"))
    assert edit_denied.value.status_code == 404
    with pytest.raises(HTTPException) as update_denied:
        product.update_product(1, _request("account-a"), "Stolen", "", "", "")
    assert update_denied.value.status_code == 404
    with pytest.raises(HTTPException) as delete_denied:
        product.delete_product(1, _request("account-a"))
    assert delete_denied.value.status_code == 404

    monkeypatch.setattr(main.product_module, "PRODUCT_FILE", products_file)
    monkeypatch.setattr(main.product_module, "USERS_FILE", users_file)
    monkeypatch.setattr(main.buyer_module, "BUYER_FILE", buyers_file)
    monkeypatch.setattr(main.buyer_module, "USERS_FILE", users_file)
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
    search_a = main.global_search(_request("account-a", "/search"), "Product").body.decode()
    search_b = main.global_search(_request("account-b", "/search"), "Product").body.decode()
    assert "Product A Updated" in search_a and "Product B" not in search_a
    assert "Product B" in search_b and "Product A Updated" not in search_b
    assert 'href="/edit-product/1"' in search_b
    assert 'href="/edit-product/0"' not in search_b
    edit_b = product.edit_product(1, _request("account-b", "/edit-product/1")).body.decode()
    assert "Product B" in edit_b and "Product A Updated" not in edit_b
    with pytest.raises(HTTPException) as other_account_edit:
        product.edit_product(0, _request("account-b", "/edit-product/0"))
    assert other_account_edit.value.status_code == 404

    before_empty_mutations = products_file.read_bytes()
    with pytest.raises(HTTPException) as missing_create_account:
        product.save_product(_request(""), "No Owner", "", "", "")
    assert missing_create_account.value.status_code == 401
    with pytest.raises(HTTPException) as missing_update_account:
        product.update_product(1, _request(""), "No Owner Update", "", "", "")
    assert missing_update_account.value.status_code == 401
    with pytest.raises(HTTPException) as missing_delete_account:
        product.confirm_delete_product(1, _request(""), "Product B")
    assert missing_delete_account.value.status_code == 401
    assert products_file.read_bytes() == before_empty_mutations

    for render in [
        lambda request: certificate_of_origin.co_form(request),
        lambda request: inspection_certificate.inspection_form(request),
        lambda request: insurance_certificate.inspection_form(request),
        lambda request: customs_declaration.customs_form(request),
    ]:
        account_a_form = render(_request("account-a"))
        account_b_form = render(_request("account-b"))
        assert "Product A Updated" in account_a_form.body.decode()
        assert "Product B" not in account_a_form.body.decode()
        assert "Product B" in account_b_form.body.decode()
        assert "Product A Updated" not in account_b_form.body.decode()

    product.confirm_delete_product(0, _request("account-a"), "Product A Updated")
    assert product.product_data(_request("account-a")) == []
    assert product.product_data(_request("account-b"))[0]["name"] == "Product B"
