import json

from starlette.requests import Request

import app.buyer as buyer
import app.packing as packing
import app.product as product


def _request(account_id, path):
    return Request({
        "type": "http", "method": "GET", "scheme": "http", "path": path,
        "raw_path": path.encode(), "query_string": b"", "headers": [],
        "client": ("127.0.0.1", 1), "server": ("testserver", 80),
        "trade_paper_user": {"account_id": account_id},
    })


def test_buyer_list_advances_only_accounts_with_a_completed_buyer(tmp_path, monkeypatch):
    users_file = tmp_path / "users.json"
    buyer_file = tmp_path / "buyers.json"
    users_file.write_text(json.dumps([
        {"account_id": "account-a"}, {"account_id": "account-b"},
    ]), encoding="utf-8")
    buyer_file.write_text(json.dumps([
        {"account_id": "account-a", "name": "Buyer A"},
    ]), encoding="utf-8")
    monkeypatch.setattr(buyer, "BUYER_FILE", buyer_file)
    monkeypatch.setattr(buyer, "USERS_FILE", users_file)

    html_a = buyer.buyer_list(_request("account-a", "/buyers")).body.decode()
    html_b = buyer.buyer_list(_request("account-b", "/buyers")).body.decode()
    assert 'href="/product-form"' in html_a and "Next: Add Product" in html_a
    assert "Next: Add Product" not in html_b


def test_product_list_advances_only_accounts_with_a_completed_product(tmp_path, monkeypatch):
    users_file = tmp_path / "users.json"
    product_file = tmp_path / "products.json"
    users_file.write_text(json.dumps([
        {"account_id": "account-a"}, {"account_id": "account-b"},
    ]), encoding="utf-8")
    product_file.write_text(json.dumps([
        {"account_id": "account-a", "name": "Product A"},
    ]), encoding="utf-8")
    monkeypatch.setattr(product, "PRODUCT_FILE", product_file)
    monkeypatch.setattr(product, "USERS_FILE", users_file)

    html_a = product.product_list(_request("account-a", "/products")).body.decode()
    html_b = product.product_list(_request("account-b", "/products")).body.decode()
    assert 'href="/invoice"' in html_a and "Next: Create Invoice" in html_a
    assert "Next: Create Invoice" not in html_b


def test_packing_list_exposes_an_explicit_pdf_action(monkeypatch):
    monkeypatch.setattr(packing, "load_packing_lists", lambda account_id: [{
        "packing_no": "PK-001", "invoice_no": "INV-001", "items": [],
    }])
    html = packing.packing_list(_request("account-a", "/packing-list")).body.decode()
    assert "Download PDF" in html
    assert 'href="/packing-list-pdf/PK-001"' in html
