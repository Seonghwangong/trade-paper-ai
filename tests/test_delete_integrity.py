import json
import ast
from pathlib import Path

import pytest

from app.referential_integrity import (
    DEPENDENCY_REGISTRY,
    SOURCE_META,
    confirmed_identifier_delete,
    find_dependencies,
    identifier_delete_confirmation,
)
from app import shipping_instruction as shipping_instruction_module
from app import main as main_module
from app.validation import DataValidationError


def test_all_hard_dependency_edges(temporary_data):
    tested = 0
    for target_module, edges in DEPENDENCY_REGISTRY.items():
        for source_module, reference_field in edges:
            for filename, *_ in SOURCE_META.values():
                (temporary_data / filename).write_text("[]", encoding="utf-8")
            target_filename, target_identifier_field, *_ = SOURCE_META[target_module]
            (temporary_data / target_filename).write_text(json.dumps([{
                "account_id": "account-a",
                target_identifier_field: "TARGET-001",
            }]), encoding="utf-8")
            filename, identifier_field, title_field, *_ = SOURCE_META[source_module]
            record = {
                "account_id": "account-a",
                identifier_field: "REC-001",
                title_field: "Dependent",
                reference_field: "TARGET-001",
            }
            (temporary_data / filename).write_text(json.dumps([None, {}, record]), encoding="utf-8")
            dependencies = find_dependencies(target_module, "TARGET-001", "account-a")
            assert [(item["module"], item["identifier"]) for item in dependencies] == [(source_module, "REC-001")]
            tested += 1
    assert tested == 49


def test_protected_and_confirmed_deletion(temporary_data):
    invoice_file = temporary_data / "invoices.json"
    packing_file = temporary_data / "packing_lists.json"
    invoice_file.write_text(json.dumps([
        {"account_id": "account-a", "invoice_no": "INV-001"},
        {"account_id": "account-a", "invoice_no": "INV-002"},
    ]), encoding="utf-8")
    packing_file.write_text(json.dumps([{
        "account_id": "account-a", "packing_no": "PK-001", "invoice_no": "INV-001",
    }]), encoding="utf-8")
    before = invoice_file.read_bytes()
    dependencies = find_dependencies("Commercial Invoice", "INV-001", "account-a")
    assert [(item["module"], item["identifier"]) for item in dependencies] == [("Packing List", "PK-001")]
    get_response = identifier_delete_confirmation("Commercial Invoice", "Commercial Invoice", "INV-001", invoice_file, "invoice_no", "/delete-invoice/INV-001", "/invoice-list", "account-a")
    assert get_response.status_code == 200
    assert invoice_file.read_bytes() == before
    blocked = confirmed_identifier_delete("Commercial Invoice", "Commercial Invoice", "INV-001", invoice_file, "invoice_no", "/delete-invoice/INV-001", "/invoice-list", "/invoice-list", "account-a")
    assert blocked.status_code == 409
    assert invoice_file.read_bytes() == before

    packing_file.write_text("[]", encoding="utf-8")
    deleted = confirmed_identifier_delete("Commercial Invoice", "Commercial Invoice", "INV-001", invoice_file, "invoice_no", "/delete-invoice/INV-001", "/invoice-list", "/invoice-list", "account-a")
    assert deleted.status_code == 303
    assert json.loads(invoice_file.read_text()) == [{"account_id": "account-a", "invoice_no": "INV-002"}]


def test_active_dependency_callers_supply_account_id():
    app_dir = Path(__file__).resolve().parents[1] / "app"
    missing = []
    for path in app_dir.glob("*.py"):
        if path.name == "referential_integrity.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
                continue
            if node.func.id != "find_dependencies":
                continue
            has_account = len(node.args) >= 3 or any(
                keyword.arg == "account_id" for keyword in node.keywords
            )
            if not has_account:
                missing.append(f"{path.name}:{node.lineno}")
    assert missing == []


def test_shipping_instruction_rejects_missing_or_cross_account_shipment(temporary_data, monkeypatch):
    monkeypatch.setattr(shipping_instruction_module.packing_module, "PACKING_FILE", temporary_data / "packing_lists.json")
    monkeypatch.setattr(shipping_instruction_module.shipment_module, "SHIPMENT_FILE", temporary_data / "shipments.json")
    (temporary_data / "packing_lists.json").write_text(json.dumps([
        {"account_id": "account-a", "packing_no": "PK-001", "invoice_no": "INV-001"},
        {"account_id": "account-b", "packing_no": "PK-001", "invoice_no": "INV-001"},
    ]), encoding="utf-8")
    (temporary_data / "shipments.json").write_text(json.dumps([{
        "account_id": "account-a", "shipment_no": "SHP-001",
    }]), encoding="utf-8")

    shipping_instruction_module.validate_si_links("PK-001", "INV-001", "account-a", "SHP-001")
    with pytest.raises(DataValidationError):
        shipping_instruction_module.validate_si_links("PK-001", "INV-001", "account-a", "SHP-MISSING")
    with pytest.raises(DataValidationError):
        shipping_instruction_module.validate_si_links("PK-001", "INV-001", "account-b", "SHP-001")


def test_legacy_invoice_save_validates_document_references(temporary_data, monkeypatch):
    invoice_file = temporary_data / "invoices.json"
    monkeypatch.setattr(main_module, "DATA_FILE", invoice_file)
    monkeypatch.setattr(
        main_module.proforma_module, "load_proformas",
        lambda account_id: [{"pi_no": "PI-001"}] if account_id == "account-a" else [],
    )
    monkeypatch.setattr(
        main_module.shipment_module, "load_shipments",
        lambda account_id: [{"shipment_no": "SHP-001"}] if account_id == "account-a" else [],
    )
    payload = {
        "seller": "Seller", "buyer": "Buyer", "items": [{"name": "Cargo"}],
        "pi_no": "PI-001", "shipment_no": "SHP-001",
    }

    saved = main_module.save_invoice(payload, "account-a")
    assert saved["pi_no"] == "PI-001"
    assert saved["shipment_no"] == "SHP-001"
    with pytest.raises(DataValidationError):
        main_module.save_invoice({**payload, "pi_no": "PI-MISSING"}, "account-a")
    with pytest.raises(DataValidationError):
        main_module.save_invoice({**payload, "shipment_no": "SHP-MISSING"}, "account-a")
    with pytest.raises(DataValidationError):
        main_module.save_invoice(payload, "account-b")
