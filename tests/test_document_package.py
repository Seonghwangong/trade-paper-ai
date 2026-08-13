from io import BytesIO
import zipfile

import pytest
from fastapi import HTTPException
from fastapi.responses import Response
from starlette.requests import Request

from app import shipment


def _request(account_id):
    return Request({
        "type": "http", "method": "GET", "path": "/document-package", "headers": [],
        "trade_paper_user": {"account_id": account_id},
    })


def _shipment():
    return {
        "shipment_no": "SHP-001", "shipment_name": "Package Test",
        "invoice_no": "INV-001", "packing_no": "PK-001", "si_no": "SI-001",
        "bl_no": "BL-001", "co_no": "",
    }


def _datasets():
    return {
        "invoices.json": [{"invoice_no": "INV-001"}],
        "packing_lists.json": [{"packing_no": "PK-001"}],
        "shipping_instructions.json": [{"si_no": "SI-001"}],
        "booking_confirmations.json": [{"booking_record_no": "BK-001", "shipment_no": "SHP-001", "packing_no": "PK-001"}],
        "bills_of_lading.json": [{"bl_no": "BL-001"}],
        "certificates_of_origin.json": [],
        "quotations.json": [], "proformas.json": [], "containers.json": [],
        "customs_declarations.json": [], "inspection_certificates.json": [],
        "insurance_certificates.json": [], "weight_certificates.json": [],
    }


def test_document_package_resolves_status_actions_and_account_isolation(monkeypatch):
    record = _shipment()
    monkeypatch.setattr(shipment, "load_shipments", lambda account: [record] if account == "A" else [])
    monkeypatch.setattr(shipment, "find_shipment", lambda number, account: record if account == "A" and number == "SHP-001" else None)
    monkeypatch.setattr(shipment, "load_workflow_datasets", lambda account: _datasets())

    package = shipment.resolve_document_package(record, _datasets())
    assert [item["exists"] for item in package] == [True, True, True, True, True, False]
    assert package[3]["value"] == "BK-001"

    selector = shipment.document_package(_request("A")).body.decode()
    assert "SHP-001 · Package Test" in selector
    html = shipment.shipment_document_package("SHP-001", _request("A")).body.decode()
    assert "5 / 6 documents complete" in html
    assert "Certificate of Origin is missing" in html
    assert "/booking/BK-001" in html and "/edit-booking/BK-001" in html
    assert "/invoice-pdf/INV-001" in html and "/booking-pdf/BK-001" in html
    assert "/shipment/SHP-001/package.zip" in html
    assert "SHP-001" not in shipment.document_package(_request("B")).body.decode()
    with pytest.raises(HTTPException) as denied:
        shipment.shipment_document_package("SHP-001", _request("B"))
    assert denied.value.status_code == 404


def test_document_package_zip_contains_each_completed_pdf(monkeypatch):
    record = _shipment()
    monkeypatch.setattr(shipment, "find_shipment", lambda number, account: record if account == "A" else None)
    monkeypatch.setattr(shipment, "load_workflow_datasets", lambda account: _datasets())

    from app import invoice, packing, shipping_instruction, booking_confirmation, bill_of_lading, certificate_of_origin
    for module, name in (
        (invoice, "invoice_pdf"), (packing, "packing_list_pdf"),
        (shipping_instruction, "si_pdf"), (booking_confirmation, "booking_pdf"),
        (bill_of_lading, "bl_pdf"), (certificate_of_origin, "co_pdf"),
    ):
        monkeypatch.setattr(module, name, lambda identifier, request: Response(b"%PDF package"))

    response = shipment.download_document_package("SHP-001", _request("A"))
    assert response.media_type == "application/zip"
    with zipfile.ZipFile(BytesIO(response.body)) as archive:
        assert sorted(archive.namelist()) == ["BK-001.pdf", "BL-001.pdf", "INV-001.pdf", "PK-001.pdf", "SI-001.pdf"]
        assert all(archive.read(name).startswith(b"%PDF") for name in archive.namelist())

    with pytest.raises(HTTPException) as denied:
        shipment.download_document_package("SHP-001", _request("B"))
    assert denied.value.status_code == 404
