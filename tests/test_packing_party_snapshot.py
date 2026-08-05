from __future__ import annotations

import json

from reportlab import rl_config
from starlette.requests import Request

from app import packing


def _request(account_id):
    return Request({
        "type": "http",
        "method": "GET",
        "path": "/packing-list",
        "headers": [],
        "trade_paper_user": {
            "account_id": account_id,
            "company": "Snapshot Seller",
            "email": "owner@example.com",
        },
    })


def test_packing_snapshot_create_edit_update_and_pdf_precedence(tmp_path, monkeypatch):
    packing_file = tmp_path / "packing_lists.json"
    invoice_file = tmp_path / "invoices.json"
    users_file = tmp_path / "users.json"
    packing_file.write_text("[]\n", encoding="utf-8")
    invoice_file.write_text(json.dumps([{
        "account_id": "account-a",
        "invoice_no": "INV-001",
        "seller": "Snapshot Seller",
        "seller_address": "Snapshot Seller Address",
        "seller_email": "seller-snapshot@example.com",
        "seller_phone": "111-2222",
        "buyer": "Snapshot Buyer",
        "buyer_address": "Snapshot Buyer Address",
        "buyer_email": "buyer-snapshot@example.com",
        "items": [{"name": "Product", "quantity": 2, "hs_code": "847130", "unit_price": 25}],
    }]), encoding="utf-8")
    users_file.write_text(json.dumps([{"account_id": "account-a"}]), encoding="utf-8")
    monkeypatch.setattr(packing, "PACKING_FILE", packing_file)
    monkeypatch.setattr(packing, "USERS_FILE", users_file)
    monkeypatch.setattr(packing.invoice_module, "INVOICE_FILE", invoice_file)
    monkeypatch.setattr(packing.invoice_module, "USERS_FILE", users_file)
    monkeypatch.setattr(packing, "load_account_company", lambda account_id, path: {
        "name": "Changed Company",
        "address": "Changed Seller Address",
        "email": "changed-seller@example.com",
        "phone": "999-9999",
    })
    monkeypatch.setattr(packing, "_buyer_master", lambda account_id, name: {
        "name": "Snapshot Buyer",
        "address": "Changed Buyer Address",
        "email": "changed-buyer@example.com",
    })

    created = packing.create_packing_list(_request("account-a"), {
        "invoice_no": "INV-001",
        "seller": "Snapshot Seller",
        "buyer": "Snapshot Buyer",
        "items": [{
            "name": "Product", "quantity": 2, "hs_code": "847130",
            "carton": 1, "net_weight": "10", "gross_weight": "12",
        }],
    })
    assert created["seller_address"] == "Snapshot Seller Address"
    assert created["seller_email"] == "seller-snapshot@example.com"
    assert created["seller_phone"] == "111-2222"
    assert created["buyer_address"] == "Snapshot Buyer Address"
    assert created["buyer_email"] == "buyer-snapshot@example.com"

    edit_html = packing.edit_packing(created["packing_no"], _request("account-a")).body.decode()
    for field, value in {
        "seller_address": "Snapshot Seller Address",
        "seller_email": "seller-snapshot@example.com",
        "seller_phone": "111-2222",
        "buyer_address": "Snapshot Buyer Address",
        "buyer_email": "buyer-snapshot@example.com",
    }.items():
        assert f'name="{field}" value="{value}"' in edit_html

    packing.update_packing(
        created["packing_no"],
        _request("account-a"),
        "INV-001",
        "Snapshot Seller",
        "Snapshot Buyer",
        ["Product"],
        ["2"],
        ["847130"],
        ["1"],
        ["10"],
        ["12"],
        seller_address="Snapshot Seller Address",
        seller_email="seller-snapshot@example.com",
        seller_phone="111-2222",
        buyer_address="Snapshot Buyer Address",
        buyer_email="buyer-snapshot@example.com",
    )
    updated = packing.load_packing_lists("account-a")[0]
    assert updated["seller_address"] == "Snapshot Seller Address"
    assert updated["seller_email"] == "seller-snapshot@example.com"
    assert updated["seller_phone"] == "111-2222"
    assert updated["buyer_address"] == "Snapshot Buyer Address"
    assert updated["buyer_email"] == "buyer-snapshot@example.com"

    monkeypatch.setattr(rl_config, "pageCompression", 0)
    pdf = packing.create_packing_list_pdf(
        updated,
        {
            "name": "Changed Company",
            "address": "Changed Seller Address",
            "email": "changed-seller@example.com",
            "phone": "999-9999",
        },
        {
            "name": "Snapshot Buyer",
            "address": "Changed Buyer Address",
            "email": "changed-buyer@example.com",
        },
    )
    for value in (
        b"Snapshot Seller Address",
        b"seller-snapshot@example.com",
        b"111-2222",
        b"Snapshot Buyer Address",
        b"buyer-snapshot@example.com",
    ):
        assert value in pdf.body
    for value in (
        b"Changed Seller Address",
        b"changed-seller@example.com",
        b"999-9999",
        b"Changed Buyer Address",
        b"changed-buyer@example.com",
    ):
        assert value not in pdf.body


def test_legacy_packing_without_snapshot_falls_back_to_current_masters(monkeypatch):
    monkeypatch.setattr(rl_config, "pageCompression", 0)
    pdf = packing.create_packing_list_pdf({
        "packing_no": "PK-LEGACY",
        "invoice_no": "INV-LEGACY",
        "seller": "Legacy Seller",
        "buyer": "Legacy Buyer",
        "items": [{
            "name": "Legacy Product", "quantity": 1, "hs_code": "847130",
            "carton": 1, "net_weight": "5", "gross_weight": "6",
        }],
    }, {
        "name": "Current Company",
        "address": "Current Seller Address",
        "email": "current-seller@example.com",
        "phone": "333-4444",
    }, {
        "name": "Legacy Buyer",
        "address": "Current Buyer Address",
        "email": "current-buyer@example.com",
    })
    for value in (
        b"Current Seller Address",
        b"current-seller@example.com",
        b"333-4444",
        b"Current Buyer Address",
        b"current-buyer@example.com",
    ):
        assert value in pdf.body
