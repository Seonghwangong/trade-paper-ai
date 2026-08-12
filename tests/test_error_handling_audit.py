import json

import pytest
from starlette.requests import Request

import app.invoice as invoice
import app.packing as packing
from app.validation import DataValidationError


def _request(account_id):
    return Request({
        "type": "http", "method": "POST", "scheme": "http", "path": "/",
        "raw_path": b"/", "query_string": b"", "headers": [],
        "client": ("127.0.0.1", 1), "server": ("testserver", 80),
        "trade_paper_user": {"account_id": account_id},
    })


def test_packing_update_invalid_quantity_is_validation_error(tmp_path, monkeypatch):
    packing_file = tmp_path / "packing_lists.json"
    invoice_file = tmp_path / "invoices.json"
    users_file = tmp_path / "users.json"
    users_file.write_text(json.dumps([{"account_id": "account-a"}]), encoding="utf-8")
    packing_file.write_text(json.dumps([{
        "account_id": "account-a", "packing_no": "PK-001",
        "invoice_no": "INV-001", "seller": "Seller", "buyer": "Buyer",
        "items": [{"name": "Cargo", "quantity": 1}],
    }]), encoding="utf-8")
    invoice_file.write_text(json.dumps([{
        "account_id": "account-a", "invoice_no": "INV-001",
    }]), encoding="utf-8")
    monkeypatch.setattr(packing, "PACKING_FILE", packing_file)
    monkeypatch.setattr(packing, "USERS_FILE", users_file)
    monkeypatch.setattr(packing.invoice_module, "INVOICE_FILE", invoice_file)
    monkeypatch.setattr(packing.invoice_module, "USERS_FILE", users_file)

    with pytest.raises(DataValidationError) as error:
        packing.update_packing(
            "PK-001", _request("account-a"), invoice_no="INV-001",
            seller="Seller", buyer="Buyer", item_name=["Cargo"],
            quantity=["not-a-number"], hs_code=[""], carton=[""],
            net_weight=[""], gross_weight=[""], item_id=[""],
        )
    assert error.value.field == "Quantity"


@pytest.mark.parametrize("items", (
    [],
    [{"name": "Cargo", "quantity": "not-a-number", "unit_price": "10"}],
    ["Cargo"],
))
def test_invoice_pdf_bad_cargo_is_validation_error(items):
    with pytest.raises(DataValidationError):
        invoice.create_invoice_pdf({
            "invoice_no": "INV-001", "seller": "Seller", "buyer": "Buyer",
            "currency": "USD", "items": items,
        })
