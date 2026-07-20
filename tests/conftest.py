from __future__ import annotations

import json
from pathlib import Path

import pytest

import app.storage as storage
from app.documents import DOCUMENT_DEFINITIONS
from tests.helpers import file_hashes


@pytest.fixture(scope="session", autouse=True)
def real_data_write_guard():
    before = file_hashes(storage.DATA_DIR)
    before_backups = sorted(path.name for path in storage.DATA_DIR.glob("*.bak"))
    before_temporaries = sorted(path.name for path in storage.DATA_DIR.glob(".*.tmp"))
    yield
    assert file_hashes(storage.DATA_DIR) == before
    assert sorted(path.name for path in storage.DATA_DIR.glob("*.bak")) == before_backups
    assert sorted(path.name for path in storage.DATA_DIR.glob(".*.tmp")) == before_temporaries


@pytest.fixture
def temporary_data(monkeypatch, tmp_path):
    for definition in DOCUMENT_DEFINITIONS:
        path = tmp_path / definition.storage_filename
        value = {} if definition.key == "company" else []
        path.write_text(json.dumps(value, indent=2), encoding="utf-8")
    monkeypatch.setattr(storage, "DATA_DIR", tmp_path)
    return tmp_path


@pytest.fixture
def workflow_fixture(temporary_data, monkeypatch):
    import app.shipment as shipment

    records = {
        "quotations.json": [{"quotation_no": "QT-001", "buyer_name": "Buyer"}],
        "proformas.json": [{"pi_no": "PI-001", "buyer": "Buyer"}],
        "invoices.json": [{"invoice_no": "INV-001", "pi_no": "PI-001", "buyer": "Buyer"}],
        "packing_lists.json": [{"packing_no": "PK-001", "invoice_no": "INV-001", "buyer": "Buyer"}],
        "shipping_instructions.json": [{"si_no": "SI-001", "packing_no": "PK-001", "invoice_no": "INV-001"}],
        "bills_of_lading.json": [{"bl_no": "BL-001", "packing_no": "PK-001", "invoice_no": "INV-001"}],
        "shipments.json": [{
            "shipment_no": "SHP-001", "shipment_name": "QA Shipment", "status": "Inquiry",
            "quotation_no": "QT-001", "pi_no": "PI-001", "invoice_no": "INV-001",
            "packing_no": "PK-001", "si_no": "SI-001", "bl_no": "BL-001",
        }],
        "booking_confirmations.json": [{"booking_record_no": "BK-001", "shipment_no": "SHP-001"}],
        "containers.json": [{"container_record_no": "CON-001", "shipment_no": "SHP-001"}],
        "customs_declarations.json": [{"customs_record_no": "CD-001", "shipment_no": "SHP-001"}],
    }
    for filename, value in records.items():
        (temporary_data / filename).write_text(json.dumps(value, indent=2), encoding="utf-8")

    direct_files = {
        "quotation_no": "quotations.json", "pi_no": "proformas.json", "invoice_no": "invoices.json",
        "packing_no": "packing_lists.json", "si_no": "shipping_instructions.json",
        "bl_no": "bills_of_lading.json", "co_no": "certificates_of_origin.json",
        "inspection_no": "inspection_certificates.json", "insurance_no": "insurance_certificates.json",
        "weight_no": "weight_certificates.json",
    }
    for definition in shipment.DOCUMENTS:
        monkeypatch.setitem(definition, "file", temporary_data / direct_files[definition["field"]])
    operation_files = {
        "booking_record_no": "booking_confirmations.json",
        "container_record_no": "containers.json",
        "customs_record_no": "customs_declarations.json",
    }
    for definition in shipment.OPERATIONAL_RECORDS:
        monkeypatch.setitem(definition, "file", temporary_data / operation_files[definition["key"]])
    monkeypatch.setattr(shipment, "SHIPMENT_FILE", temporary_data / "shipments.json")
    return records["shipments.json"][0]
