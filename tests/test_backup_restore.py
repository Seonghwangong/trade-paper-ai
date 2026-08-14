import json

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app import backup_restore


def _request(admin=True):
    return Request({
        "type": "http", "method": "POST", "path": "/admin/backups", "headers": [],
        "trade_paper_user": {"account_id": "ADMIN", "email": "admin@example.com", "is_admin": admin},
    })


def _write(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")


def test_manual_backup_confirm_restore_audit_and_account_isolation(tmp_path, monkeypatch):
    root = tmp_path / "admin_backups"
    monkeypatch.setattr(backup_restore, "BACKUP_ROOT", root)
    buyers = tmp_path / "buyers.json"
    products = tmp_path / "products.json"
    _write(buyers, [{"account_id": "A", "name": "Buyer Original"}, {"account_id": "B", "name": "Buyer B"}])
    _write(products, [{"account_id": "A", "name": "Product Original"}, {"account_id": "B", "name": "Product B"}])

    response = backup_restore.backup_create(_request(), "A")
    assert response.status_code == 303
    listed = backup_restore.list_backups("A")
    manual = next(item for item in listed if item["type"] == "Manual")
    bundle = json.loads((root / f'{manual["id"]}.json').read_text(encoding="utf-8"))
    assert bundle["account_id"] == "A" and "users.json" not in bundle["files"]
    assert bundle["files"]["buyers.json"] == [{"account_id": "A", "name": "Buyer Original"}]

    _write(buyers, [{"account_id": "A", "name": "Buyer Changed"}, {"account_id": "B", "name": "Buyer B Current"}])
    _write(products, [{"account_id": "A", "name": "Product Changed"}, {"account_id": "B", "name": "Product B Current"}])
    confirm = backup_restore.restore_confirm(_request(), "A", manual["id"]).body.decode()
    assert "Confirm Restore" in confirm and "required" in confirm and "only account A" in confirm
    with pytest.raises(HTTPException) as missing_confirmation:
        backup_restore.restore_execute(_request(), "A", manual["id"], "")
    assert missing_confirmation.value.status_code == 400

    restored = backup_restore.restore_execute(_request(), "A", manual["id"], "yes")
    assert "Backup restored successfully" in restored.body.decode()
    assert json.loads(buyers.read_text()) == [{"account_id": "B", "name": "Buyer B Current"}, {"account_id": "A", "name": "Buyer Original"}]
    assert json.loads(products.read_text()) == [{"account_id": "B", "name": "Product B Current"}, {"account_id": "A", "name": "Product Original"}]
    assert json.loads((tmp_path / "buyers.backup.json").read_text()) == [{"account_id": "A", "name": "Buyer Changed"}, {"account_id": "B", "name": "Buyer B Current"}]
    audit = json.loads((tmp_path / "audit_log.json").read_text())
    assert [(row["action"], row["document_type"]) for row in audit] == [("Create", "Backup"), ("Restore", "Backup")]
    assert all(row["account_id"] == "A" for row in audit)


def test_automatic_backup_restores_only_selected_account_and_requires_admin(tmp_path, monkeypatch):
    monkeypatch.setattr(backup_restore, "BACKUP_ROOT", tmp_path / "admin_backups")
    current = [{"account_id": "A", "name": "Current A"}, {"account_id": "B", "name": "Current B"}]
    previous = [{"account_id": "A", "name": "Previous A"}, {"account_id": "B", "name": "Previous B"}]
    _write(tmp_path / "buyers.json", current)
    _write(tmp_path / "buyers.backup.json", previous)
    automatic = next(item for item in backup_restore.list_backups("A") if item["type"] == "Automatic")
    backup_restore.restore_backup(automatic["id"], "A")
    assert json.loads((tmp_path / "buyers.json").read_text()) == [current[1], previous[0]]
    assert json.loads((tmp_path / "buyers.backup.json").read_text()) == current
    with pytest.raises(HTTPException) as denied:
        backup_restore.backup_list(_request(False), "A")
    assert denied.value.status_code == 403
