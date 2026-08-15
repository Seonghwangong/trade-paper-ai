from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import html
import json
from pathlib import Path
import uuid

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app import auth
from app.documents import DOCUMENT_DEFINITIONS
from app.storage import atomic_write_json, data_path, load_json_strict


router = APIRouter()
BACKUP_ROOT = data_path("admin_backups")
ACCOUNT_FILES = tuple(dict.fromkeys([
    "account_companies.json", "buyers.json", "products.json", "customers.json",
    *(definition.storage_filename for definition in DOCUMENT_DEFINITIONS if definition.key != "company"),
    "email_history.json", "billing_history.json", "usage_events.json", "payment_orders.json",
]))


def _safe(value):
    return html.escape(str(value or ""), quote=True)


def _data_dir():
    return Path(BACKUP_ROOT).parent


def _account_rows(path, account_id):
    rows = load_json_strict(path, [], list)
    return [row for row in rows if isinstance(row, dict) and str(row.get("account_id", "") or "") == account_id]


def create_account_backup(account_id, actor):
    account_id = str(account_id or "").strip()
    if not account_id:
        raise HTTPException(status_code=400, detail="Account is required")
    files = {}
    for filename in ACCOUNT_FILES:
        path = _data_dir() / filename
        if path.exists():
            files[filename] = _account_rows(path, account_id)
    bundle = {
        "backup_version": 1, "backup_type": "manual", "account_id": account_id,
        "created_at": datetime.now(timezone.utc).isoformat(), "created_by": str(actor or ""),
        "files": files,
    }
    backup_id = uuid.uuid4().hex
    BACKUP_ROOT.mkdir(parents=True, exist_ok=True)
    atomic_write_json(BACKUP_ROOT / f"{backup_id}.json", bundle, dict)
    return backup_id, bundle


def list_backups(account_id):
    result = []
    if BACKUP_ROOT.exists():
        for path in BACKUP_ROOT.glob("*.json"):
            try:
                bundle = load_json_strict(path, {}, dict)
            except Exception:
                continue
            if bundle.get("account_id") == account_id and bundle.get("backup_type") == "manual":
                result.append({"id": path.stem, "type": "Manual", "created_at": bundle.get("created_at", ""), "files": len(bundle.get("files", {}))})
    for path in _data_dir().glob("*.backup.json"):
        source_name = path.name.replace(".backup.json", ".json")
        if source_name not in ACCOUNT_FILES:
            continue
        try:
            count = len(_account_rows(path, account_id))
        except Exception:
            continue
        if count:
            result.append({"id": f"auto:{source_name}", "type": "Automatic", "created_at": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(), "files": 1})
    return sorted(result, key=lambda item: item["created_at"], reverse=True)


def _merge_account_file(filename, snapshot_rows, account_id):
    if filename not in ACCOUNT_FILES or not isinstance(snapshot_rows, list):
        raise HTTPException(status_code=400, detail="Invalid backup content")
    if any(not isinstance(row, dict) or row.get("account_id") != account_id for row in snapshot_rows):
        raise HTTPException(status_code=400, detail="Backup ownership validation failed")
    path = _data_dir() / filename
    current = load_json_strict(path, [], list)
    merged = [row for row in current if not isinstance(row, dict) or row.get("account_id") != account_id]
    merged.extend(snapshot_rows)
    atomic_write_json(path, merged, list)


def restore_backup(backup_id, account_id):
    if backup_id.startswith("auto:"):
        filename = backup_id.removeprefix("auto:")
        if filename not in ACCOUNT_FILES:
            raise HTTPException(status_code=404, detail="Backup not found")
        backup = _data_dir() / filename.replace(".json", ".backup.json")
        if not backup.exists():
            raise HTTPException(status_code=404, detail="Backup not found")
        _merge_account_file(filename, _account_rows(backup, account_id), account_id)
        return
    if not backup_id or any(character not in "0123456789abcdef" for character in backup_id) or len(backup_id) != 32:
        raise HTTPException(status_code=404, detail="Backup not found")
    bundle_path = BACKUP_ROOT / f"{backup_id}.json"
    if not bundle_path.exists():
        raise HTTPException(status_code=404, detail="Backup not found")
    bundle = load_json_strict(bundle_path, {}, dict)
    if bundle.get("account_id") != account_id or bundle.get("backup_type") != "manual" or bundle.get("backup_version") != 1:
        raise HTTPException(status_code=404, detail="Backup not found")
    files = bundle.get("files", {})
    if not isinstance(files, dict):
        raise HTTPException(status_code=400, detail="Invalid backup content")
    for filename, rows in files.items():
        if filename not in ACCOUNT_FILES or not isinstance(rows, list) or any(not isinstance(row, dict) or row.get("account_id") != account_id for row in rows):
            raise HTTPException(status_code=400, detail="Backup ownership validation failed")
    for filename, rows in files.items():
        _merge_account_file(filename, rows, account_id)


def _page(account_id, backups, message=""):
    rows = "".join(
        f'<tr><td>{_safe(item["type"])}</td><td>{_safe(item["created_at"])}</td><td>{item["files"]}</td><td><a href="/admin/backups/restore-confirm?account_id={_safe(account_id)}&amp;backup_id={_safe(item["id"])}">Restore</a></td></tr>'
        for item in backups
    ) or '<tr><td colspan="4">No backups available for this account.</td></tr>'
    notice = f'<p role="status">{_safe(message)}</p>' if message else ""
    return HTMLResponse(f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Backup &amp; Restore</title><style>body{{margin:0;padding:36px;background:#F3F4F6;font-family:Arial;color:#111827}}main{{width:min(1050px,100%);margin:auto}}nav,form{{display:flex;gap:10px;align-items:center;flex-wrap:wrap}}a,button{{padding:10px 14px;border:0;border-radius:9px;background:#111827;color:#fff;text-decoration:none;font-weight:bold}}input{{padding:10px;border:1px solid #CBD5E1;border-radius:9px}}table{{width:100%;margin-top:22px;border-collapse:collapse;background:#fff}}th,td{{padding:12px;border-bottom:1px solid #E5E7EB;text-align:left}}th{{background:#111827;color:#fff}}</style></head><body><main><nav><a href="/admin/dashboard">Admin Dashboard</a><a href="/admin/audit-log">Audit Log</a></nav><h1>Backup &amp; Restore</h1><p>Account-scoped backups only. Authentication credentials are not included.</p>{notice}<form method="get" action="/admin/backups"><label>Account ID <input name="account_id" value="{_safe(account_id)}" required></label><button>View</button></form><form method="post" action="/admin/backups/create"><input type="hidden" name="account_id" value="{_safe(account_id)}"><button>Create Manual Backup</button></form><table><thead><tr><th>Type</th><th>Created</th><th>Files</th><th>Action</th></tr></thead><tbody>{rows}</tbody></table></main></body></html>''')


@router.get("/admin/backups")
def backup_list(request: Request, account_id: str = ""):
    admin = auth.require_admin(request)
    target = str(account_id or admin.get("account_id", "") or "").strip()
    return _page(target, list_backups(target))


@router.post("/admin/backups/create")
def backup_create(request: Request, account_id: str = Form("")):
    admin = auth.require_admin(request)
    backup_id, _ = create_account_backup(account_id, admin.get("email"))
    from app.audit_log import record_audit
    record_audit(account_id, admin.get("email"), "Create", "Backup", backup_id, path=_data_dir() / "audit_log.json")
    return RedirectResponse(f"/admin/backups?account_id={account_id}", status_code=303)


@router.get("/admin/backups/restore-confirm")
def restore_confirm(request: Request, account_id: str, backup_id: str):
    auth.require_admin(request)
    if not any(item["id"] == backup_id for item in list_backups(account_id)):
        raise HTTPException(status_code=404, detail="Backup not found")
    return HTMLResponse(f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Confirm Restore</title></head><body><main><h1>Confirm Restore</h1><p>This will replace only account {_safe(account_id)} records with the selected backup. A validated automatic backup of current files will be created first.</p><form method="post" action="/admin/backups/restore" data-native-submit="true"><input type="hidden" name="account_id" value="{_safe(account_id)}"><input type="hidden" name="backup_id" value="{_safe(backup_id)}"><label><input type="checkbox" name="confirmed" value="yes" required> I understand and want to restore this backup.</label><button type="submit">Restore Backup</button></form><a href="/admin/backups?account_id={_safe(account_id)}">Cancel</a></main></body></html>''')


@router.post("/admin/backups/restore")
def restore_execute(request: Request, account_id: str = Form(""), backup_id: str = Form(""), confirmed: str = Form("")):
    admin = auth.require_admin(request)
    if confirmed != "yes":
        raise HTTPException(status_code=400, detail="Restore confirmation is required")
    restore_backup(backup_id, account_id)
    from app.audit_log import record_audit
    record_audit(account_id, admin.get("email"), "Restore", "Backup", backup_id, path=_data_dir() / "audit_log.json")
    return _page(account_id, list_backups(account_id), "Backup restored successfully.")
