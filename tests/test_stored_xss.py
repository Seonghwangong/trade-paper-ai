import json

import pytest
from starlette.requests import Request

import app.buyer as buyer
import app.main as main
import app.product as product


PAYLOADS = (
    "<script>alert(1)</script>",
    "<img src=x onerror=alert(1)>",
    '"><svg/onload=alert(1)>',
)


def _request(account_id, path="/"):
    return Request({
        "type": "http", "method": "GET", "scheme": "http", "path": path,
        "raw_path": path.encode(), "query_string": b"", "headers": [],
        "client": ("127.0.0.1", 1), "server": ("testserver", 80),
        "trade_paper_user": {"account_id": account_id},
    })


@pytest.fixture
def xss_data(tmp_path, monkeypatch):
    users = tmp_path / "users.json"
    buyers = tmp_path / "buyers.json"
    products = tmp_path / "products.json"
    users.write_text(json.dumps([
        {"account_id": "account-a", "email": "a@example.com"},
        {"account_id": "account-b", "email": "b@example.com"},
    ]), encoding="utf-8")
    buyers.write_text("[]", encoding="utf-8")
    products.write_text("[]", encoding="utf-8")
    for module in (buyer, main.buyer_module):
        monkeypatch.setattr(module, "BUYER_FILE", buyers)
        monkeypatch.setattr(module, "USERS_FILE", users)
    for module in (product, main.product_module):
        monkeypatch.setattr(module, "PRODUCT_FILE", products)
        monkeypatch.setattr(module, "USERS_FILE", users)
    return buyers, products


def _assert_escaped(source, payload):
    assert payload not in source
    assert "&lt;" in source and "&gt;" in source
    assert "&amp;lt;" not in source


@pytest.mark.parametrize("payload", PAYLOADS)
def test_buyer_stored_xss_list_edit_search_and_account_isolation(xss_data, monkeypatch, payload):
    buyer.save_buyer(_request("account-a"), payload, payload, payload, payload)
    buyer.save_buyer(_request("account-b"), "Other Account Buyer", "", "", "")

    list_source = buyer.buyer_list(_request("account-a", "/buyers")).body.decode()
    edit_source = buyer.edit_buyer(0, _request("account-a", "/edit-buyer/0")).body.decode()
    _assert_escaped(list_source, payload)
    _assert_escaped(edit_source, payload)
    assert "Other Account Buyer" not in list_source

    monkeypatch.setattr(main, "global_search_results", lambda *args, **kwargs: [{
        "module": "Buyers", "identifier": payload, "title": payload,
        "subtitle": payload, "search_text": payload, "url": "/buyers",
    }])
    for name in ("load_account_company",):
        monkeypatch.setattr(main, name, lambda *args: {})
    loaders = (
        (main.customer_module, "load_customers"),
        (main.invoice_module, "load_invoices"),
        (main.packing_module, "load_packing_lists"),
        (main.shipping_instruction_module, "load_shipping_instructions"),
        (main.booking_module, "load_bookings"),
        (main.shipment_module, "load_shipments"),
        (main.container_module, "load_containers"),
        (main.bill_of_lading_module, "load_bills_of_lading"),
        (main.customs_module, "load_customs"),
        (main.certificate_of_origin_module, "load_certificates"),
        (main.inspection_module, "load_inspections"),
        (main.insurance_module, "load_insurances"),
        (main.weight_module, "load_weights"),
        (main.quotation_module, "load_quotations"),
        (main.proforma_module, "load_proformas"),
    )
    for module, loader in loaders:
        monkeypatch.setattr(module, loader, lambda *args: [])
    search_source = main.global_search(_request("account-a", "/search"), payload).body.decode()
    _assert_escaped(search_source, payload)


@pytest.mark.parametrize("payload", PAYLOADS)
def test_product_stored_xss_list_edit_search_and_account_isolation(xss_data, payload):
    product.save_product(_request("account-a"), payload, payload, payload, payload)
    product.save_product(_request("account-b"), "Other Account Product", "", "", "")

    list_source = product.product_list(_request("account-a", "/products"), payload).body.decode()
    edit_source = product.edit_product(0, _request("account-a", "/edit-product/0")).body.decode()
    _assert_escaped(list_source, payload)
    _assert_escaped(edit_source, payload)
    assert "Other Account Product" not in list_source


def test_buyer_and_product_unicode_and_literal_entities_are_not_double_escaped(xss_data):
    value = "English 한국어 日本語 中文 &lt;literal&gt;"
    buyer.save_buyer(_request("account-a"), value, value, value, value)
    product.save_product(_request("account-a"), value, value, value, value)
    for source in (
        buyer.buyer_list(_request("account-a")).body.decode(),
        buyer.edit_buyer(0, _request("account-a")).body.decode(),
        product.product_list(_request("account-a")).body.decode(),
        product.edit_product(0, _request("account-a")).body.decode(),
    ):
        assert "English 한국어 日本語 中文" in source
        assert "&amp;lt;literal&amp;gt;" in source
        assert "&amp;amp;lt;" not in source
