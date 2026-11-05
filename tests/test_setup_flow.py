import json
from starlette.requests import Request
from app import buyer, product
from app.routers import company
from app.account_company import save_account_company


def request(account="a"):
    return Request({"type":"http", "method":"POST", "scheme":"http", "path":"/save-company", "headers":[], "query_string":b"", "server":("test",80), "client":("127.0.0.1",1), "trade_paper_user":{"account_id":account, "email":f"{account}@example.com"}})


def test_initial_email_suggestion_is_isolated_and_preserves_explicit_empty(tmp_path, monkeypatch):
    path = tmp_path / "accounts.json"
    path.write_text(json.dumps([{"account_id":"a", "name":"A", "email":"", "setup_complete":False}, {"account_id":"b", "name":"B", "email":"contact@example.com", "setup_complete":True}]))
    monkeypatch.setattr(company, "ACCOUNT_COMPANIES_FILE", path)
    assert company.get_company_data(request())["email"] == "a@example.com"
    assert company.get_company_data(request("b"))["email"] == "contact@example.com"
    assert json.loads(path.read_text())[0]["email"] == ""
    save_account_company("a", {"name":"A", "email":""}, path)
    assert company.get_company_data(request())["email"] == ""


def test_company_setup_advances_and_preserves_chosen_task(tmp_path, monkeypatch):
    path = tmp_path / "accounts.json"
    monkeypatch.setattr(company, "ACCOUNT_COMPANIES_FILE", path)
    for target, expected in [("/", "/buyer-form?flow=setup"), ("/onboarding?next=%2F", "/buyer-form?flow=setup"), ("/onboarding?next=%2Fdemo", "/demo"), ("/import-document", "/import-document"), ("https://evil.invalid", "/buyer-form?flow=setup")]:
        path.write_text("[]")
        result = company.save_company(request(), {"name":"A", "next":target})
        assert result["redirect_to"] == expected
        assert result["continue_setup"] is True
    result = company.save_company(request(), {"name":"Updated A", "next":"/"})
    assert result["continue_setup"] is False
    assert result["redirect_to"] == "/"


def test_buyer_setup_continues_and_all_new_products_advance_to_invoice(tmp_path, monkeypatch):
    monkeypatch.setattr(buyer, "BUYER_FILE", tmp_path / "buyers.json")
    monkeypatch.setattr(product, "PRODUCT_FILE", tmp_path / "products.json")
    for flow, expected in [("setup", "/product-form?flow=setup"), ("", "/buyers"), ("https://evil.invalid", "/buyers")]:
        assert buyer.save_buyer(request(), name="Test buyer", address="", email="", country="", status="Lead", flow=flow).headers["location"] == expected
    for flow in ["setup", "", "https://evil.invalid"]:
        assert product.save_product(request(), name="Laptop", hs_code="847130", unit_price="850", origin="KR", unit="PCS", flow=flow).headers["location"] == "/invoice?flow=setup&product=Laptop"
    assert 'name="flow" value="setup"' in buyer.buyer_form(flow="setup").body.decode()
    assert 'name="flow" value="setup"' in product.product_form(flow="setup").body.decode()
    assert 'name="flow" value=""' in buyer.buyer_form().body.decode()
    assert 'data-ui-label="true" value="Lead"' in buyer.buyer_form().body.decode()


def test_product_invoice_redirect_encodes_name_and_saves_values_once(tmp_path, monkeypatch):
    from urllib.parse import parse_qs, urlsplit
    path = tmp_path / "products.json"
    monkeypatch.setattr(product, "PRODUCT_FILE", path)
    name = "테스트 컵 & mug + 350ml/#?"
    response = product.save_product(request(), name=name, hs_code="691110", unit_price="5.00", origin="KR", unit="PCS")
    target = urlsplit(response.headers["location"])
    assert target.path == "/invoice"
    assert parse_qs(target.query) == {"flow": ["setup"], "product": [name]}
    assert json.loads(path.read_text()) == [{"account_id": "a", "name": name, "hs_code": "691110", "unit_price": "5.00", "origin": "KR", "unit": "PCS"}]
