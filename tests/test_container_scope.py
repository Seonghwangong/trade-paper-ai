import json

import pytest
from fastapi import HTTPException
from starlette.requests import Request

import app.container_management as container
import app.customs_declaration as customs
import app.invoice as invoice
import app.main as main
import app.packing as packing
import app.product as product
import app.shipment as shipment
from app.account_container import ensure_legacy_container_ownership
from app.validation import DataValidationError


def _request(account_id, path="/container-list"):
    return Request({
        "type": "http", "method": "GET", "scheme": "http",
        "path": path, "raw_path": path.encode(), "query_string": b"",
        "headers": [], "client": ("127.0.0.1", 1), "server": ("testserver", 80),
        "trade_paper_user": {
            "account_id": account_id, "company": f"Company {account_id}",
            "email": f"{account_id}@example.com",
        },
    })


def _form(packing_no, invoice_no, container_no):
    return {
        "container_date": "2026-08-01", "shipment_no": "",
        "packing_no": packing_no, "bl_no": "", "invoice_no": invoice_no,
        "container_no": container_no, "seal_no": "SEAL", "container_type": "40HC",
        "carrier": "Carrier", "vessel": "Vessel", "voyage_no": "V001",
        "etd": "2026-08-10", "eta": "2026-08-20", "port_of_loading": "Busan",
        "port_of_discharge": "LA", "place_of_delivery": "LA",
        "loading_place": "Warehouse", "remarks": "Scope test",
        "item_name": ["Product"], "hs_code": ["1234"], "quantity": ["2"],
        "carton": ["1"], "net_weight": ["10"], "gross_weight": ["12"],
        "total_carton": "1", "total_net_weight": "10", "total_gross_weight": "12",
    }


def test_legacy_container_migration_is_idempotent_and_backed_up(tmp_path):
    container_file = tmp_path / "containers.json"
    users_file = tmp_path / "users.json"
    original = [{"container_record_no": "CON-001", "container_no": "LEGACY"}]
    container_file.write_text(json.dumps(original, indent=2) + "\n", encoding="utf-8")
    users_file.write_text(json.dumps([{"account_id": "legacy-account"}]), encoding="utf-8")
    first = ensure_legacy_container_ownership(container_file, users_file)
    first_bytes = container_file.read_bytes()
    second = ensure_legacy_container_ownership(container_file, users_file)
    assert first[0]["account_id"] == "legacy-account"
    assert second == first and container_file.read_bytes() == first_bytes
    assert json.loads((tmp_path / "containers.backup.json").read_text()) == original


def test_container_scope_crud_pdf_search_dashboard_and_customs_source(tmp_path, monkeypatch):
    users_file = tmp_path / "users.json"
    container_file = tmp_path / "containers.json"
    packing_file = tmp_path / "packing_lists.json"
    invoice_file = tmp_path / "invoices.json"
    shipment_file = tmp_path / "shipments.json"
    product_file = tmp_path / "products.json"
    users_file.write_text(json.dumps([
        {"account_id": "account-a"}, {"account_id": "account-b"},
    ]), encoding="utf-8")
    container_file.write_text("[]\n", encoding="utf-8")
    packing_file.write_text(json.dumps([
        {"account_id": "account-a", "packing_no": "PK-A", "invoice_no": "INV-A", "items": []},
        {"account_id": "account-b", "packing_no": "PK-B", "invoice_no": "INV-B", "items": []},
    ]), encoding="utf-8")
    invoice_file.write_text(json.dumps([
        {"account_id": "account-a", "invoice_no": "INV-A", "items": []},
        {"account_id": "account-b", "invoice_no": "INV-B", "items": []},
    ]), encoding="utf-8")
    shipment_file.write_text("[]\n", encoding="utf-8")
    product_file.write_text("[]\n", encoding="utf-8")
    bl_file = tmp_path / "bills_of_lading.json"
    bl_file.write_text("[]\n", encoding="utf-8")

    monkeypatch.setattr(container, "CONTAINER_FILE", container_file)
    monkeypatch.setattr(container, "USERS_FILE", users_file)
    monkeypatch.setattr(container, "BL_FILE", bl_file)
    monkeypatch.setattr(packing, "PACKING_FILE", packing_file)
    monkeypatch.setattr(packing, "USERS_FILE", users_file)
    monkeypatch.setattr(invoice, "INVOICE_FILE", invoice_file)
    monkeypatch.setattr(invoice, "USERS_FILE", users_file)
    monkeypatch.setattr(shipment, "SHIPMENT_FILE", shipment_file)
    monkeypatch.setattr(shipment, "USERS_FILE", users_file)
    monkeypatch.setattr(product, "PRODUCT_FILE", product_file)
    monkeypatch.setattr(product, "USERS_FILE", users_file)
    monkeypatch.setattr(container, "find_dependencies", lambda module, identifier, account_id: [])

    container.save_container(_request("account-a"), **_form("PK-A", "INV-A", "CONT-A"))
    container.save_container(_request("account-b"), **_form("PK-B", "INV-B", "CONT-B"))
    raw = json.loads(container_file.read_text())
    assert [record["container_record_no"] for record in raw] == ["CON-001", "CON-002"]
    assert [record["account_id"] for record in raw] == ["account-a", "account-b"]
    assert "account_id" not in container.container_data("CON-001", _request("account-a"))

    with pytest.raises(DataValidationError):
        container.save_container(_request("account-a"), **_form("PK-B", "INV-B", "STOLEN"))
    list_a = container.container_list(_request("account-a")).body.decode()
    list_b = container.container_list(_request("account-b")).body.decode()
    assert "CON-001" in list_a and "CON-002" not in list_a
    assert "CON-002" in list_b and "CON-001" not in list_b
    assert "CON-001" in container.container_list(_request("account-a"), "CONT-A").body.decode()
    assert "CON-002" not in container.container_list(_request("account-a"), "CONT-B").body.decode()

    container.update_container("CON-001", _request("account-a"), **_form("PK-A", "INV-A", "CONT-A-UPDATED"))
    assert container.container_data("CON-001", _request("account-a"))["container_no"] == "CONT-A-UPDATED"
    assert "CON-001" in container.container_detail("CON-001", _request("account-a")).body.decode()
    saved_pdf = container.container_pdf("CON-001", _request("account-a"))
    assert saved_pdf.status_code == 200 and saved_pdf.body.startswith(b"%PDF")
    preview = container.create_container_pdf(_request("account-a", "/container/pdf"), {
        **container.container_data("CON-001", _request("account-a")), "account_id": "forged",
    })
    assert preview.body.startswith(b"%PDF") and b"account_id" not in preview.body

    for action in (
        lambda: container.edit_container("CON-002", _request("account-a")),
        lambda: container.container_detail("CON-002", _request("account-a")),
        lambda: container.container_data("CON-002", _request("account-a")),
        lambda: container.delete_container("CON-002", _request("account-a")),
        lambda: container.confirm_delete_container("CON-002", _request("account-a")),
        lambda: container.container_pdf("CON-002", _request("account-a")),
    ):
        with pytest.raises(HTTPException) as denied:
            action()
        assert denied.value.status_code == 404

    source_a = customs.customs_source_container("CON-001", _request("account-a", "/customs-source/container/CON-001"))
    assert source_a["container_no"] == "CONT-A-UPDATED" and "account_id" not in source_a
    with pytest.raises(HTTPException) as denied:
        customs.customs_source_container("CON-002", _request("account-a"))
    assert denied.value.status_code == 404
    monkeypatch.setattr(customs, "load_customs", lambda account_id: [])
    monkeypatch.setattr(customs.booking_module, "load_bookings", lambda account_id: [])
    form_a = customs.customs_form(_request("account-a", "/customs-form")).body.decode()
    form_b = customs.customs_form(_request("account-b", "/customs-form")).body.decode()
    assert "CON-001" in form_a and "CON-002" not in form_a
    assert "CON-002" in form_b and "CON-001" not in form_b

    monkeypatch.setattr(main, "load_account_company", lambda account_id, path: {})
    monkeypatch.setattr(main, "dashboard_list", lambda filename: [])
    monkeypatch.setattr(main.buyer_module, "load_buyers", lambda account_id: [])
    monkeypatch.setattr(main.product_module, "load_products", lambda account_id: [])
    monkeypatch.setattr(main.invoice_module, "load_invoices", lambda account_id: [])
    monkeypatch.setattr(main.packing_module, "load_packing_lists", lambda account_id: [])
    monkeypatch.setattr(main.shipping_instruction_module, "load_shipping_instructions", lambda account_id: [])
    monkeypatch.setattr(main.booking_module, "load_bookings", lambda account_id: [])
    monkeypatch.setattr(main.shipment_module, "load_shipments", lambda account_id: [])
    search_a = main.global_search(_request("account-a", "/search"), "CON-").body.decode()
    search_b = main.global_search(_request("account-b", "/search"), "CON-").body.decode()
    assert "CON-001" in search_a and "CON-002" not in search_a
    assert "CON-002" in search_b and "CON-001" not in search_b
    dashboard_a = main.home(_request("account-a", "/")).body.decode()
    dashboard_b = main.home(_request("account-b", "/")).body.decode()
    assert "Container Management</h3>\n<div class=\"document-count\">1" in dashboard_a
    assert "Container Management</h3>\n<div class=\"document-count\">1" in dashboard_b

    container.confirm_delete_container("CON-001", _request("account-a"))
    assert container.load_containers("account-a") == []
    assert container.load_containers("account-b")[0]["container_record_no"] == "CON-002"
