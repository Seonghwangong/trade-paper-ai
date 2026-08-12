import json

import pytest
from fastapi import HTTPException
from starlette.requests import Request

import app.buyer as buyer
import app.product as product
import app.proforma as proforma
import app.quotation as quotation
import app.referential_integrity as referential_integrity
from app.account_quotation import ensure_legacy_quotation_ownership
from app.validation import DataValidationError


def _request(account_id, path="/quotation-list"):
    return Request({"type": "http", "method": "GET", "scheme": "http", "path": path,
                    "raw_path": path.encode(), "query_string": b"", "headers": [],
                    "client": ("127.0.0.1", 1), "server": ("testserver", 80),
                    "trade_paper_user": {"account_id": account_id, "company": account_id,
                                         "email": f"{account_id}@example.com"}})


def _form(suffix):
    return {"buyer_name": f"Buyer {suffix}", "buyer_address": f"Address {suffix}",
            "buyer_email": f"{suffix}@buyer.test", "seller": f"Seller {suffix}",
            "valid_until": "2026-09-01", "currency": "USD", "item_name": [f"Product {suffix}"],
            "hs_code": [f"HS-{suffix}"], "qty": ["2"], "unit_price": ["10"], "amount": ["20"]}


def test_legacy_quotation_migration_is_idempotent_and_backed_up(tmp_path):
    source = tmp_path / "quotations.json"; users = tmp_path / "users.json"
    original = [{"quotation_no": "QT-001", "buyer_name": "Buyer"}]
    source.write_text(json.dumps(original, indent=2) + "\n"); users.write_text(json.dumps([{"account_id": "legacy"}]))
    first = ensure_legacy_quotation_ownership(source, users); first_bytes = source.read_bytes()
    second = ensure_legacy_quotation_ownership(source, users)
    assert first[0]["account_id"] == "legacy" and second == first and source.read_bytes() == first_bytes
    assert json.loads((tmp_path / "quotations.backup.json").read_text()) == original


def test_quotation_scope_crud_pdf_sources_conversion_and_dependencies(tmp_path, monkeypatch):
    users = tmp_path / "users.json"; users.write_text(json.dumps([{"account_id": "A"}, {"account_id": "B"}]))
    quote_file = tmp_path / "quotations.json"; quote_file.write_text("[]\n")
    buyer_file = tmp_path / "buyers.json"; product_file = tmp_path / "products.json"
    companies_file = tmp_path / "account_companies.json"
    buyer_file.write_text(json.dumps([{"account_id": x, "name": f"Buyer {x}", "address": f"Address {x}", "email": f"{x}@buyer.test"} for x in "AB"]))
    product_file.write_text(json.dumps([{"account_id": x, "name": f"Product {x}", "hs_code": f"HS-{x}", "unit_price": "10"} for x in "AB"]))
    companies_file.write_text(json.dumps([{"account_id": x, "name": f"Seller {x}", "address": f"Seller Address {x}", "email": f"{x}@seller.test"} for x in "AB"]))
    monkeypatch.setattr(quotation, "QUOTATION_FILE", quote_file); monkeypatch.setattr(quotation, "USERS_FILE", users)
    monkeypatch.setattr(quotation, "ACCOUNT_COMPANIES_FILE", companies_file)
    monkeypatch.setattr(buyer, "BUYER_FILE", buyer_file); monkeypatch.setattr(buyer, "USERS_FILE", users)
    monkeypatch.setattr(product, "PRODUCT_FILE", product_file); monkeypatch.setattr(product, "USERS_FILE", users)
    monkeypatch.setattr(quotation, "find_dependencies", lambda module, identifier, account_id: [])

    quotation.save_quotation(_request("A"), **_form("A")); quotation.save_quotation(_request("B"), **_form("B"))
    raw = json.loads(quote_file.read_text()); assert [row["account_id"] for row in raw] == ["A", "B"]
    assert "QT-001" in quotation.quotation_list(_request("A")).body.decode() and "QT-002" not in quotation.quotation_list(_request("A")).body.decode()
    assert quotation.edit_quotation("QT-001", _request("A")).status_code == 200
    assert quotation.quotation_pdf("QT-001", _request("A")).body.startswith(b"%PDF")
    preview_payload = {**quotation.load_quotations("A")[0], "account_id": "forged"}
    assert quotation.create_quotation_pdf(_request("A", "/quotation/pdf"), preview_payload).body.startswith(b"%PDF")
    assert all("account_id" not in record for record in quotation.load_quotations("A"))
    with pytest.raises(DataValidationError): quotation.save_quotation(_request("A"), **_form("B"))

    monkeypatch.setattr(proforma, "QUOTATION_FILE", quote_file)
    assert "Buyer A" in proforma.proforma_form(_request("A", "/proforma-form"), "QT-001").body.decode()
    assert "Buyer B" not in proforma.proforma_form(_request("A", "/proforma-form"), "QT-002").body.decode()

    shipment_file = tmp_path / "shipments.json"; shipment_file.write_text(json.dumps([{"account_id": "A", "shipment_no": "SHP-A", "quotation_no": "QT-001"}, {"account_id": "B", "shipment_no": "SHP-B", "quotation_no": "QT-001"}]))
    original_data_path = referential_integrity.data_path
    mapping = {"quotations.json": quote_file, "shipments.json": shipment_file}
    monkeypatch.setattr(referential_integrity, "data_path", lambda filename: mapping.get(filename, original_data_path(filename)))
    assert [item["identifier"] for item in referential_integrity.find_dependencies("Quotation", "QT-001", "A")] == ["SHP-A"]

    for action in [lambda: quotation.edit_quotation("QT-002", _request("A")), lambda: quotation.delete_quotation("QT-002", _request("A")), lambda: quotation.confirm_delete_quotation("QT-002", _request("A")), lambda: quotation.quotation_pdf("QT-002", _request("A"))]:
        with pytest.raises(HTTPException) as denied: action()
        assert denied.value.status_code == 404
    quotation.confirm_delete_quotation("QT-001", _request("A"))
    assert quotation.load_quotations("A") == [] and quotation.load_quotations("B")[0]["quotation_no"] == "QT-002"
