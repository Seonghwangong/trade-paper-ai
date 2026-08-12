import json

import pytest
from fastapi import HTTPException
from starlette.requests import Request

import app.buyer as buyer
import app.invoice as invoice
import app.product as product
import app.proforma as proforma
import app.quotation as quotation
import app.referential_integrity as referential_integrity
from app.account_proforma import ensure_legacy_proforma_ownership
from app.validation import DataValidationError


def _request(account_id, path="/proforma-list"):
    return Request({"type": "http", "method": "GET", "scheme": "http", "path": path,
                    "raw_path": path.encode(), "query_string": b"", "headers": [],
                    "client": ("127.0.0.1", 1), "server": ("testserver", 80),
                    "trade_paper_user": {"account_id": account_id, "company": account_id,
                                         "email": f"{account_id}@example.com"}})


def _form(suffix):
    return {"seller": f"Seller {suffix}", "buyer": f"Buyer {suffix}",
            "buyer_address": f"Address {suffix}", "buyer_email": f"{suffix}@buyer.test",
            "pi_date": "2026-08-02", "currency": "USD", "total_amount": "20",
            "item_name": [f"Product {suffix}"], "hs_code": [f"HS-{suffix}"],
            "qty": ["2"], "unit_price": ["10"], "amount": ["20"]}


def test_legacy_proforma_migration_is_idempotent_and_backed_up(tmp_path):
    source = tmp_path / "proformas.json"; users = tmp_path / "users.json"
    original = [{"pi_no": "PI-001", "buyer": "Buyer"}]
    source.write_text(json.dumps(original, indent=2) + "\n"); users.write_text(json.dumps([{"account_id": "legacy"}]))
    first = ensure_legacy_proforma_ownership(source, users); first_bytes = source.read_bytes()
    second = ensure_legacy_proforma_ownership(source, users)
    assert first[0]["account_id"] == "legacy" and second == first and source.read_bytes() == first_bytes
    assert json.loads((tmp_path / "proformas.backup.json").read_text()) == original


def test_proforma_scope_crud_api_pdf_invoice_and_dependencies(tmp_path, monkeypatch):
    users = tmp_path / "users.json"; users.write_text(json.dumps([{"account_id": "A"}, {"account_id": "B"}]))
    proforma_file = tmp_path / "proformas.json"; proforma_file.write_text("[]\n")
    quote_file = tmp_path / "quotations.json"
    quote_file.write_text(json.dumps([{"account_id": x, "quotation_no": f"QT-{x}", "seller": f"Seller {x}", "buyer_name": f"Buyer {x}", "buyer_address": f"Address {x}", "buyer_email": f"{x}@buyer.test", "currency": "USD", "items": [{"name": f"Product {x}", "hs_code": f"HS-{x}", "qty": "2", "unit_price": "10", "amount": "20"}]} for x in "AB"]))
    buyer_file = tmp_path / "buyers.json"; product_file = tmp_path / "products.json"; companies_file = tmp_path / "account_companies.json"
    buyer_file.write_text(json.dumps([{"account_id": x, "name": f"Buyer {x}", "address": f"Address {x}", "email": f"{x}@buyer.test"} for x in "AB"]))
    product_file.write_text(json.dumps([{"account_id": x, "name": f"Product {x}", "hs_code": f"HS-{x}", "unit_price": "10"} for x in "AB"]))
    companies_file.write_text(json.dumps([{"account_id": x, "name": f"Seller {x}"} for x in "AB"]))
    monkeypatch.setattr(proforma, "PROFORMA_FILE", proforma_file); monkeypatch.setattr(proforma, "USERS_FILE", users)
    monkeypatch.setattr(proforma, "ACCOUNT_COMPANIES_FILE", companies_file)
    monkeypatch.setattr(quotation, "QUOTATION_FILE", quote_file); monkeypatch.setattr(quotation, "USERS_FILE", users)
    monkeypatch.setattr(buyer, "BUYER_FILE", buyer_file); monkeypatch.setattr(buyer, "USERS_FILE", users)
    monkeypatch.setattr(product, "PRODUCT_FILE", product_file); monkeypatch.setattr(product, "USERS_FILE", users)
    monkeypatch.setattr(proforma, "find_dependencies", lambda module, identifier, account_id: [])

    assert "Buyer A" in proforma.proforma_form(_request("A", "/proforma-form"), "QT-A").body.decode()
    assert "Buyer B" not in proforma.proforma_form(_request("A", "/proforma-form"), "QT-B").body.decode()
    proforma.save_proforma(_request("A"), **_form("A")); proforma.save_proforma(_request("B"), **_form("B"))
    raw = json.loads(proforma_file.read_text()); assert [row["account_id"] for row in raw] == ["A", "B"]
    assert "PI-001" in proforma.proforma_list(_request("A")).body.decode() and "PI-002" not in proforma.proforma_list(_request("A")).body.decode()
    assert "account_id" not in proforma.proforma_data("PI-001", _request("A"))
    assert proforma.edit_proforma("PI-001", _request("A")).status_code == 200
    assert proforma.proforma_pdf("PI-001", _request("A")).body.startswith(b"%PDF")
    preview = {**proforma.proforma_data("PI-001", _request("A")), "account_id": "forged"}
    assert proforma.create_proforma_pdf(_request("A", "/proforma/pdf"), preview).body.startswith(b"%PDF")
    with pytest.raises(DataValidationError): proforma.save_proforma(_request("A"), **_form("B"))

    invoice_file = tmp_path / "invoices.json"; invoice_file.write_text("[]\n")
    monkeypatch.setattr(invoice, "INVOICE_FILE", invoice_file); monkeypatch.setattr(invoice, "USERS_FILE", users)
    invoice.create_invoice(_request("A", "/invoice"), {"pi_no": "PI-001", "seller": "Seller A", "buyer": "Buyer A", "items": [{"name": "Product A"}]})
    with pytest.raises(DataValidationError):
        invoice.create_invoice(_request("A", "/invoice"), {"pi_no": "PI-002", "seller": "Seller A", "buyer": "Buyer A", "items": [{"name": "Product A"}]})

    shipment_file = tmp_path / "shipments.json"; shipment_file.write_text(json.dumps([{"account_id": "A", "shipment_no": "SHP-A", "pi_no": "PI-001"}, {"account_id": "B", "shipment_no": "SHP-B", "pi_no": "PI-001"}]))
    original_data_path = referential_integrity.data_path
    mapping = {"proformas.json": proforma_file, "shipments.json": shipment_file, "invoices.json": invoice_file}
    monkeypatch.setattr(referential_integrity, "data_path", lambda filename: mapping.get(filename, original_data_path(filename)))
    assert all(item["identifier"] != "SHP-B" for item in referential_integrity.find_dependencies("Proforma Invoice", "PI-001", "A"))

    for action in [lambda: proforma.edit_proforma("PI-002", _request("A")), lambda: proforma.proforma_data("PI-002", _request("A")), lambda: proforma.delete_proforma("PI-002", _request("A")), lambda: proforma.confirm_delete_proforma("PI-002", _request("A")), lambda: proforma.proforma_pdf("PI-002", _request("A"))]:
        with pytest.raises(HTTPException) as denied: action()
        assert denied.value.status_code == 404
    proforma.confirm_delete_proforma("PI-001", _request("A"))
    assert proforma.load_proformas("A") == [] and proforma.load_proformas("B")[0]["pi_no"] == "PI-002"
