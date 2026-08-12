from datetime import date

from starlette.requests import Request

from app import storage
from app.export import pdf_export_filename, set_pdf_export_record


def test_pdf_filename_uses_only_supplied_authorized_record(monkeypatch):
    calls = []

    def forbidden_storage_read(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("PDF filename generation must not read global storage")

    monkeypatch.setattr(storage, "load_json_strict", forbidden_storage_read)

    filename = pdf_export_filename(
        "/invoice-pdf/INV-001",
        "INV-001.pdf",
        {"invoice_no": "INV-001", "buyer": "Account A Buyer", "seller": "Account A Seller"},
    )

    assert filename == f"INV-{date.today().year}-001_Account_A_Buyer_Account_A_Seller.pdf"
    assert calls == []


def test_same_identifier_cannot_select_other_account_metadata():
    account_a = {"invoice_no": "INV-001", "buyer": "Alpha Buyer", "seller": "Alpha Seller"}
    account_b = {"invoice_no": "INV-001", "buyer": "Beta Buyer", "seller": "Beta Seller"}

    filename_a = pdf_export_filename("/invoice-pdf/INV-001", "INV-001.pdf", account_a)
    filename_b = pdf_export_filename("/invoice-pdf/INV-001", "INV-001.pdf", account_b)

    assert "Alpha_Buyer_Alpha_Seller" in filename_a
    assert "Beta_Buyer" not in filename_a
    assert "Beta_Buyer_Beta_Seller" in filename_b
    assert "Alpha_Buyer" not in filename_b


def test_pdf_record_is_request_scoped_and_fallback_is_preserved():
    request = Request({"type": "http", "method": "GET", "path": "/invoice-pdf/INV-001", "headers": []})
    record = {"invoice_no": "INV-001", "buyer": "Buyer"}

    set_pdf_export_record(request, record)

    assert request.scope["trade_paper_pdf_record"] is record
    assert pdf_export_filename("/unregistered-pdf/INV-001", "legacy invoice.pdf", record) == "legacy_invoice.pdf"
