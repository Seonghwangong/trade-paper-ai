import json

import pytest

from app.storage import StorageCorruptionError, atomic_write_json, backup_path, load_json_strict, next_identifier
from app.validation import DataValidationError, require_existing_reference, require_text


def test_safe_storage_backup_and_recovery(tmp_path):
    path = tmp_path / "records.json"
    atomic_write_json(path, [{"invoice_no": "INV-001"}], list)
    atomic_write_json(path, [{"invoice_no": "INV-002"}], list)
    assert json.loads(backup_path(path).read_text()) == [{"invoice_no": "INV-001"}]
    path.write_text("{bad", encoding="utf-8")
    assert load_json_strict(path, [], list) == [{"invoice_no": "INV-001"}]


def test_corruption_without_backup_is_not_silenced(tmp_path):
    path = tmp_path / "records.json"
    path.write_text("{bad", encoding="utf-8")
    with pytest.raises(StorageCorruptionError):
        load_json_strict(path, [], list)


def test_identifier_and_validation_regressions():
    records = [{"invoice_no": ""}, {"invoice_no": "INV-BAD"}, {"invoice_no": "INV-009"}]
    assert next_identifier(records, "invoice_no", "INV") == "INV-010"
    with pytest.raises(DataValidationError):
        require_text("Invoice", " ")
    with pytest.raises(DataValidationError):
        require_existing_reference("Invoice", "INV-404", records, "invoice_no", required=True)
