import json

from app.referential_integrity import (
    DEPENDENCY_REGISTRY,
    SOURCE_META,
    confirmed_identifier_delete,
    find_dependencies,
    identifier_delete_confirmation,
)


def test_all_hard_dependency_edges(temporary_data):
    tested = 0
    for target_module, edges in DEPENDENCY_REGISTRY.items():
        for source_module, reference_field in edges:
            for filename, *_ in SOURCE_META.values():
                (temporary_data / filename).write_text("[]", encoding="utf-8")
            filename, identifier_field, title_field, *_ = SOURCE_META[source_module]
            record = {
                identifier_field: "REC-001",
                title_field: "Dependent",
                reference_field: "TARGET-001",
            }
            (temporary_data / filename).write_text(json.dumps([None, {}, record]), encoding="utf-8")
            dependencies = find_dependencies(target_module, "TARGET-001")
            assert [(item["module"], item["identifier"]) for item in dependencies] == [(source_module, "REC-001")]
            tested += 1
    assert tested == 43


def test_protected_and_confirmed_deletion(temporary_data):
    invoice_file = temporary_data / "invoices.json"
    packing_file = temporary_data / "packing_lists.json"
    invoice_file.write_text(json.dumps([{"invoice_no": "INV-001"}, {"invoice_no": "INV-002"}]), encoding="utf-8")
    packing_file.write_text(json.dumps([{"packing_no": "PK-001", "invoice_no": "INV-001"}]), encoding="utf-8")
    before = invoice_file.read_bytes()
    dependencies = find_dependencies("Commercial Invoice", "INV-001")
    assert [(item["module"], item["identifier"]) for item in dependencies] == [("Packing List", "PK-001")]
    get_response = identifier_delete_confirmation("Commercial Invoice", "Commercial Invoice", "INV-001", invoice_file, "invoice_no", "/delete-invoice/INV-001", "/invoice-list")
    assert get_response.status_code == 200
    assert invoice_file.read_bytes() == before
    blocked = confirmed_identifier_delete("Commercial Invoice", "Commercial Invoice", "INV-001", invoice_file, "invoice_no", "/delete-invoice/INV-001", "/invoice-list", "/invoice-list")
    assert blocked.status_code == 409
    assert invoice_file.read_bytes() == before

    packing_file.write_text("[]", encoding="utf-8")
    deleted = confirmed_identifier_delete("Commercial Invoice", "Commercial Invoice", "INV-001", invoice_file, "invoice_no", "/delete-invoice/INV-001", "/invoice-list", "/invoice-list")
    assert deleted.status_code == 303
    assert json.loads(invoice_file.read_text()) == [{"invoice_no": "INV-002"}]
