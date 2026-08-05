from __future__ import annotations

import json

from reportlab import rl_config
from starlette.requests import Request

from app import invoice


def _request(account_id):
    return Request({
        "type": "http",
        "method": "GET",
        "path": "/invoice",
        "headers": [],
        "trade_paper_user": {
            "account_id": account_id,
            "company": "Snapshot Seller",
            "email": "owner@example.com",
        },
    })


def test_invoice_seller_snapshot_create_edit_update_and_pdf_precedence(tmp_path, monkeypatch):
    invoices_file = tmp_path / "invoices.json"
    users_file = tmp_path / "users.json"
    invoices_file.write_text("[]\n", encoding="utf-8")
    users_file.write_text(json.dumps([{"account_id": "account-a"}]), encoding="utf-8")
    monkeypatch.setattr(invoice, "INVOICE_FILE", invoices_file)
    monkeypatch.setattr(invoice, "USERS_FILE", users_file)
    monkeypatch.setattr(invoice, "load_proformas", lambda account_id: [])
    monkeypatch.setattr(invoice, "load_account_company", lambda account_id, path: {
        "name": "Changed Company",
        "address": "Changed Address",
        "email": "changed@example.com",
        "phone": "999-9999",
    })

    created = invoice.create_invoice(_request("account-a"), {
        "seller": "Snapshot Seller",
        "seller_address": "Snapshot Address",
        "seller_email": "snapshot@example.com",
        "seller_phone": "111-2222",
        "currency": "USD",
        "buyer": "Snapshot Buyer",
        "buyer_address": "Buyer Address",
        "buyer_email": "buyer@example.com",
        "items": [{"name": "Snapshot Product", "hs_code": "847130", "quantity": 2, "unit_price": 25}],
    })
    assert created["seller_address"] == "Snapshot Address"
    assert created["seller_email"] == "snapshot@example.com"
    assert created["seller_phone"] == "111-2222"

    edit_html = invoice.edit_invoice(created["invoice_no"], _request("account-a")).body.decode()
    assert 'name="seller_address" value="Snapshot Address"' in edit_html
    assert 'name="seller_email" value="snapshot@example.com"' in edit_html
    assert 'name="seller_phone" value="111-2222"' in edit_html

    invoice.update_invoice(
        created["invoice_no"],
        _request("account-a"),
        "Snapshot Seller",
        "USD",
        "Snapshot Buyer",
        "Buyer Address",
        "buyer@example.com",
        "Snapshot Product",
        "847130",
        "2",
        "25",
        seller_address="Snapshot Address",
        seller_email="snapshot@example.com",
        seller_phone="111-2222",
    )
    updated = invoice.load_invoices("account-a")[0]
    assert updated["seller_address"] == "Snapshot Address"
    assert updated["seller_email"] == "snapshot@example.com"
    assert updated["seller_phone"] == "111-2222"

    monkeypatch.setattr(rl_config, "pageCompression", 0)
    pdf = invoice.create_invoice_pdf(updated, {
        "name": "Changed Company",
        "address": "Changed Address",
        "email": "changed@example.com",
        "phone": "999-9999",
    })
    assert b"Snapshot Seller" in pdf.body
    assert b"Snapshot Address" in pdf.body
    assert b"snapshot@example.com" in pdf.body
    assert b"111-2222" in pdf.body
    assert b"Changed Company" not in pdf.body
    assert b"Changed Address" not in pdf.body
    assert b"changed@example.com" not in pdf.body
    assert b"999-9999" not in pdf.body


def test_legacy_invoice_without_snapshot_falls_back_to_company_master(monkeypatch):
    monkeypatch.setattr(rl_config, "pageCompression", 0)
    pdf = invoice.create_invoice_pdf({
        "invoice_no": "INV-LEGACY",
        "buyer": "Legacy Buyer",
        "items": [{"name": "Legacy Product", "quantity": 1, "unit_price": 10}],
    }, {
        "name": "Current Company",
        "address": "Current Address",
        "email": "current@example.com",
        "phone": "333-4444",
    })
    assert b"Current Company" in pdf.body
    assert b"Current Address" in pdf.body
    assert b"current@example.com" in pdf.body
    assert b"333-4444" in pdf.body
