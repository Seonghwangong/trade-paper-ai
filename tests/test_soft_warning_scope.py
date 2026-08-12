import json

import app.referential_integrity as referential_integrity


def _write(path, records):
    path.write_text(json.dumps(records, indent=2) + "\n", encoding="utf-8")


def test_buyer_and_product_soft_warnings_are_account_scoped(tmp_path, monkeypatch):
    paths = {}
    for filename, *_ in referential_integrity.SOURCE_META.values():
        paths.setdefault(filename, tmp_path / filename)
    for path in paths.values():
        _write(path, [])

    _write(paths["invoices.json"], [
        {
            "account_id": "account-a", "invoice_no": "INV-A", "buyer": "Shared Buyer",
            "items": [{"name": "Shared Product"}],
        },
        {
            "account_id": "account-b", "invoice_no": "INV-B", "buyer": "Shared Buyer",
            "items": [{"name": "Shared Product"}],
        },
    ])
    _write(paths["packing_lists.json"], [
        {"account_id": "account-a", "packing_no": "PK-A", "invoice_no": "INV-A"},
        {"account_id": "account-b", "packing_no": "PK-B", "invoice_no": "INV-A"},
    ])
    monkeypatch.setattr(referential_integrity, "data_path", lambda filename: paths[filename])

    buyer_a = referential_integrity.find_soft_warnings("Buyer", "Shared Buyer", account_id="account-a")
    buyer_b = referential_integrity.find_soft_warnings("Buyer", "Shared Buyer", account_id="account-b")
    product_a = referential_integrity.find_soft_warnings("Product", "Shared Product", account_id="account-a")
    product_b = referential_integrity.find_soft_warnings("Product", "Shared Product", account_id="account-b")

    assert [(item["module"], item["identifier"]) for item in buyer_a] == [("Commercial Invoice", "INV-A")]
    assert [(item["module"], item["identifier"]) for item in buyer_b] == [("Commercial Invoice", "INV-B")]
    assert [(item["module"], item["identifier"]) for item in product_a] == [("Commercial Invoice", "INV-A")]
    assert [(item["module"], item["identifier"]) for item in product_b] == [("Commercial Invoice", "INV-B")]

    dependencies = referential_integrity.find_dependencies(
        "Commercial Invoice", "INV-A", account_id="account-a"
    )
    assert [(item["module"], item["identifier"]) for item in dependencies] == [("Packing List", "PK-A")]


def test_hard_dependencies_use_explicit_account_when_identifiers_collide(tmp_path, monkeypatch):
    paths = {}
    for filename, *_ in referential_integrity.SOURCE_META.values():
        paths.setdefault(filename, tmp_path / filename)
    for path in paths.values():
        _write(path, [])

    _write(paths["invoices.json"], [
        {"account_id": "account-b", "invoice_no": "INV-SAME", "buyer": "Buyer B"},
        {"account_id": "account-a", "invoice_no": "INV-SAME", "buyer": "Buyer A"},
    ])
    _write(paths["packing_lists.json"], [
        {
            "account_id": "account-b", "packing_no": "PK-B", "invoice_no": "INV-SAME",
            "buyer": "Private Buyer B",
        },
        {
            "account_id": "account-a", "packing_no": "PK-A", "invoice_no": "INV-SAME",
            "buyer": "Buyer A",
        },
    ])
    monkeypatch.setattr(referential_integrity, "data_path", lambda filename: paths[filename])

    dependencies = referential_integrity.find_dependencies(
        "Commercial Invoice", "INV-SAME", account_id="account-a"
    )

    assert dependencies == [{
        "module": "Packing List",
        "identifier": "PK-A",
        "title": "Buyer A",
        "view_url": "",
        "edit_url": "/edit-packing/PK-A",
    }]
    assert "Private Buyer B" not in str(dependencies)
    assert referential_integrity.find_dependencies(
        "Commercial Invoice", "INV-SAME", account_id=""
    ) == []
