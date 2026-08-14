from starlette.requests import Request

import app.product as product


def _request(account_id):
    return Request({
        "type": "http",
        "method": "GET",
        "path": "/product-suggestions",
        "raw_path": b"/product-suggestions",
        "query_string": b"",
        "headers": [],
        "client": ("127.0.0.1", 1),
        "server": ("testserver", 80),
        "trade_paper_user": {"account_id": account_id},
    })


def test_product_autocomplete_typo_suggestion_and_account_isolation(monkeypatch):
    products = {
        "account-a": [
            {"name": "Laptop", "hs_code": "847130", "origin": "KR", "unit": "PCS"},
            {"name": "Laptop Bag", "hs_code": "420212", "origin": "CN", "unit": "PCS"},
            {"name": "Laptop Charger", "hs_code": "850440", "origin": "JP", "unit": "PCS"},
        ],
        "account-b": [
            {"name": "Private Laptop", "hs_code": "SECRET", "origin": "US", "unit": "SET"},
        ],
    }
    monkeypatch.setattr(product, "load_products", lambda account_id: products.get(account_id, []))

    partial = product.product_suggestions(_request("account-a"), "lap")
    assert [entry["name"] for entry in partial] == ["Laptop", "Laptop Bag", "Laptop Charger"]
    assert all(entry["hs_code"] != "SECRET" for entry in partial)

    typo = product.product_suggestions(_request("account-a"), "laptpo")
    assert typo[0]["name"] == "Laptop"
    assert product.product_suggestions(_request("account-b"), "lap")[0]["name"] == "Private Laptop"


def test_product_enrichment_fills_only_missing_editable_values(monkeypatch):
    monkeypatch.setattr(product, "load_products", lambda account_id: [{
        "name": "Laptop", "hs_code": "847130", "origin": "KR", "unit": "PCS",
    }] if account_id == "account-a" else [])
    items = [
        {"name": "Laptop", "hs_code": "", "origin": "", "unit": ""},
        {"name": "Laptop", "hs_code": "USER-HS", "origin": "JP", "unit": "SET"},
        {"name": "Unknown", "hs_code": "", "origin": "", "unit": ""},
    ]

    assert product.enrich_items_from_products(items, "account-a") == [
        {"name": "Laptop", "hs_code": "847130", "origin": "KR", "unit": "PCS"},
        {"name": "Laptop", "hs_code": "USER-HS", "origin": "JP", "unit": "SET"},
        {"name": "Unknown", "hs_code": "", "origin": "", "unit": ""},
    ]
    isolated = [{"name": "Laptop", "hs_code": "", "origin": "", "unit": ""}]
    product.enrich_items_from_products(isolated, "account-b")
    assert isolated[0]["hs_code"] == ""


def test_product_master_form_and_list_include_editable_unit(monkeypatch):
    monkeypatch.setattr(product, "owned_product_entries", lambda account_id: [(0, {
        "name": "Laptop", "hs_code": "847130", "unit_price": "850", "origin": "KR", "unit": "PCS",
    })])
    form = product.product_form().body.decode()
    listing = product.product_list(_request("account-a")).body.decode()

    assert 'name="unit"' in form
    assert ">Unit</th>" in listing
    assert ">PCS</td>" in listing
