import json

import pytest
from fastapi import HTTPException
from starlette.requests import Request

import app.customer as customer
import app.main as main
from app.account_customer import ensure_legacy_customer_ownership


def _request(account_id, path="/customer"):
    return Request({
        "type": "http", "method": "GET", "scheme": "http", "path": path,
        "raw_path": path.encode(), "query_string": b"", "headers": [],
        "client": ("127.0.0.1", 1), "server": ("testserver", 80),
        "trade_paper_user": {
            "account_id": account_id,
            "company": f"Company {account_id}",
            "email": f"{account_id}@example.com",
        },
    })


def _request_without_user(path="/customer"):
    return Request({
        "type": "http", "method": "GET", "scheme": "http", "path": path,
        "raw_path": path.encode(), "query_string": b"", "headers": [],
        "client": ("127.0.0.1", 1), "server": ("testserver", 80),
    })


def test_legacy_customer_migration_is_idempotent_and_backed_up(tmp_path):
    customers_file = tmp_path / "customers.json"
    users_file = tmp_path / "users.json"
    original = [{
        "company": "Legacy Customer", "country": "KR", "address": "Address",
        "email": "legacy@example.com", "phone": "123", "pic": "PIC",
    }]
    customers_file.write_text(json.dumps(original, indent=2) + "\n", encoding="utf-8")
    users_file.write_text(
        json.dumps([{"account_id": "legacy-account", "email": "legacy@example.com"}]),
        encoding="utf-8",
    )

    first = ensure_legacy_customer_ownership(customers_file, users_file)
    first_bytes = customers_file.read_bytes()
    second = ensure_legacy_customer_ownership(customers_file, users_file)

    assert first[0]["account_id"] == "legacy-account"
    assert second == first
    assert customers_file.read_bytes() == first_bytes
    assert json.loads((tmp_path / "customers.backup.json").read_text(encoding="utf-8")) == original


def test_customer_crud_search_api_dashboard_and_direct_access_are_scoped(tmp_path, monkeypatch):
    customers_file = tmp_path / "customers.json"
    users_file = tmp_path / "users.json"
    shipments_file = tmp_path / "shipments.json"
    customers_file.write_text("[]\n", encoding="utf-8")
    shipments_file.write_text("[]\n", encoding="utf-8")
    users_file.write_text(json.dumps([
        {"account_id": "account-a", "email": "a@example.com"},
        {"account_id": "account-b", "email": "b@example.com"},
    ]), encoding="utf-8")
    monkeypatch.setattr(customer, "CUSTOMER_FILE", customers_file)
    monkeypatch.setattr(customer, "USERS_FILE", users_file)

    customer.save_customer(_request("account-a"), "", "Customer A", "KR", "Address A", "a@test", "1", "PIC A")
    customer.save_customer(_request("account-b"), "", "Customer B", "US", "Address B", "b@test", "2", "PIC B")
    raw = json.loads(customers_file.read_text(encoding="utf-8"))
    assert [record["account_id"] for record in raw] == ["account-a", "account-b"]

    assert customer.customer_data(_request("account-a")) == [{
        "company": "Customer A", "country": "KR", "address": "Address A",
        "email": "a@test", "phone": "1", "pic": "PIC A",
    }]
    assert "account_id" not in customer.customer_data(_request("account-a"))[0]
    page_a = customer.customer_page(_request("account-a")).body.decode()
    assert "Customer A" in page_a and "Customer B" not in page_a
    assert "Customer A" in customer.customer_page(_request("account-a"), search="Address A").body.decode()
    assert "Customer B" not in customer.customer_page(_request("account-a"), search="Address B").body.decode()

    customer.save_customer(_request("account-a"), "0", "Customer A Updated", "KR", "New A", "a@test", "1", "PIC A")
    with pytest.raises(HTTPException) as edit_denied:
        customer.customer_page(_request("account-a"), edit=1)
    assert edit_denied.value.status_code == 404
    with pytest.raises(HTTPException) as update_denied:
        customer.save_customer(_request("account-a"), "1", "Stolen", "", "", "", "", "")
    assert update_denied.value.status_code == 404
    with pytest.raises(HTTPException) as delete_denied:
        customer.delete_customer(1, _request("account-a"))
    assert delete_denied.value.status_code == 404
    for denied_operation in (
        lambda: customer.customer_page(_request("account-b"), edit=0),
        lambda: customer.save_customer(_request("account-b"), "0", "Stolen", "", "", "", "", ""),
        lambda: customer.delete_customer(0, _request("account-b")),
        lambda: customer.confirm_delete_customer(0, _request("account-b"), "Customer A Updated"),
    ):
        with pytest.raises(HTTPException) as denied:
            denied_operation()
        assert denied.value.status_code == 404

    before_empty_mutations = customers_file.read_bytes()
    for invalid_request in (_request(""), _request_without_user()):
        with pytest.raises(HTTPException) as missing_create_account:
            customer.save_customer(invalid_request, "", "No Owner", "", "", "", "", "")
        assert missing_create_account.value.status_code == 401
        assert customers_file.read_bytes() == before_empty_mutations
        with pytest.raises(HTTPException) as missing_update_account:
            customer.save_customer(invalid_request, "1", "No Owner Update", "", "", "", "", "")
        assert missing_update_account.value.status_code == 401
        assert customers_file.read_bytes() == before_empty_mutations
        with pytest.raises(HTTPException) as missing_delete_account:
            customer.confirm_delete_customer(1, invalid_request, "Customer B")
        assert missing_delete_account.value.status_code == 401
        assert customers_file.read_bytes() == before_empty_mutations

    search_a = main.global_search_results("Customer", customers=customer.load_customers("account-a"))
    search_b = main.global_search_results("Customer", customers=customer.load_customers("account-b"))
    assert any(result["title"] == "Customer A Updated" for result in search_a)
    assert not any(result["title"] == "Customer B" for result in search_a)
    assert any(result["title"] == "Customer B" for result in search_b)

    monkeypatch.setattr(main.customer_module, "load_customers", customer.load_customers)
    monkeypatch.setattr(main, "load_account_company", lambda account_id, path: {})
    for module, loader in [
        (main.buyer_module, "load_buyers"), (main.product_module, "load_products"),
        (main.quotation_module, "load_quotations"), (main.proforma_module, "load_proformas"),
        (main.invoice_module, "load_invoices"), (main.packing_module, "load_packing_lists"),
        (main.shipment_module, "load_shipments"), (main.shipping_instruction_module, "load_shipping_instructions"),
        (main.booking_module, "load_bookings"), (main.container_module, "load_containers"),
        (main.bill_of_lading_module, "load_bills_of_lading"), (main.certificate_of_origin_module, "load_certificates"),
        (main.inspection_module, "load_inspections"), (main.insurance_module, "load_insurances"),
        (main.weight_module, "load_weights"), (main.customs_module, "load_customs"),
    ]:
        monkeypatch.setattr(module, loader, lambda account_id: [])
    dashboard_a = main.home(_request("account-a", "/")).body.decode()
    dashboard_b = main.home(_request("account-b", "/")).body.decode()
    customer_card = '<a class="summary-card" href="/customer"><h3>Customers</h3><div class="summary-count">1</div>'
    assert customer_card in dashboard_a
    assert customer_card in dashboard_b

    customer.confirm_delete_customer(0, _request("account-a"), "Customer A Updated")
    assert customer.customer_data(_request("account-a")) == []
    assert customer.customer_data(_request("account-b"))[0]["company"] == "Customer B"
    customer.save_customer(_request("account-b"), "0", "Customer B Updated", "US", "New B", "b@test", "2", "PIC B")
    customer.confirm_delete_customer(0, _request("account-b"), "Customer B Updated")
    assert customer.customer_data(_request("account-b")) == []
