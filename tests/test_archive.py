import json
import pytest
from fastapi import HTTPException
from starlette.requests import Request
from app import archive, invoice
import app.storage as storage

def req(account="A", admin=False):
    return Request({"type":"http","method":"POST","path":"/","headers":[],"trade_paper_user":{"account_id":account,"email":f"{account}@x.test","is_admin":admin}})

def test_archive_restore_search_permanent_delete_and_isolation(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DATA_DIR", tmp_path)
    monkeypatch.setattr(invoice, "INVOICE_FILE", tmp_path / "invoices.json")
    monkeypatch.setattr(invoice, "USERS_FILE", tmp_path / "users.json")
    (tmp_path / "users.json").write_text('[{"account_id":"A"},{"account_id":"B"}]')
    (tmp_path / "invoices.json").write_text(json.dumps([{"account_id":"A","invoice_no":"INV-A","buyer":"Alpha"},{"account_id":"B","invoice_no":"INV-B","buyer":"Beta"}]))
    monkeypatch.setattr(archive, "data_path", lambda name: tmp_path / name)
    archive.archive_document(req(), "invoice", "INV-A", "/invoice-list")
    assert invoice.load_invoices("A") == [] and invoice.load_invoices("B")[0]["invoice_no"] == "INV-B"
    assert [x["identifier"] for x in archive.archived_records("A", "Alpha")] == ["INV-A"]
    assert archive.archived_records("B", "INV-A") == []
    archive.restore_archived(req(), "invoice", "INV-A")
    assert invoice.load_invoices("A")[0]["invoice_no"] == "INV-A"
    archive.archive_document(req(), "invoice", "INV-A", "/invoice-list")
    with pytest.raises(HTTPException) as denied: archive.permanent_delete_archived(req(admin=False), "invoice", "INV-A")
    assert denied.value.status_code == 403
    archive.permanent_delete_archived(req(admin=True), "invoice", "INV-A")
    raw=json.loads((tmp_path/"invoices.json").read_text()); assert [x["invoice_no"] for x in raw]==["INV-B"]
    audit=json.loads((tmp_path/"audit_log.json").read_text()); assert [x["action"] for x in audit]==["Archive","Restore","Archive","Permanent Delete"]
