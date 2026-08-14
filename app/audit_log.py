from __future__ import annotations

from datetime import datetime, timezone
import html

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from app import auth
from app.storage import data_path, load_json_strict, locked_json_mutation


router = APIRouter()
AUDIT_FILE = data_path("audit_log.json")
ALLOWED_FIELDS = ("time", "account_id", "user", "action", "document_type", "document_no")


def record_audit(account_id, user, action, document_type="", document_no="", now=None, path=None):
    account_id = str(account_id or "").strip()
    if not account_id:
        return
    entry = {
        "time": (now or datetime.now(timezone.utc)).isoformat(),
        "account_id": account_id,
        "user": str(user or "").strip(),
        "action": str(action or "").strip(),
        "document_type": str(document_type or "").strip(),
        "document_no": str(document_no or "").strip(),
    }
    entry = {field: entry[field] for field in ALLOWED_FIELDS}
    locked_json_mutation(path or AUDIT_FILE, [], lambda rows: rows.append(entry), list)


def record_request_audit(request, action, document_type="", document_no="", path=None):
    user = request.scope.get("trade_paper_user") or {}
    record_audit(user.get("account_id"), user.get("email"), action, document_type, document_no, path=path)


def query_audit(account_id=None, date="", user="", document=""):
    rows = [item for item in load_json_strict(AUDIT_FILE, [], list) if isinstance(item, dict)]
    if account_id is not None:
        rows = [item for item in rows if item.get("account_id") == account_id]
    filters = (str(date or "").strip().casefold(), str(user or "").strip().casefold(), str(document or "").strip().casefold())
    if filters[0]:
        rows = [item for item in rows if str(item.get("time", "")).casefold().startswith(filters[0])]
    if filters[1]:
        rows = [item for item in rows if filters[1] in str(item.get("user", "")).casefold()]
    if filters[2]:
        rows = [item for item in rows if filters[2] in f'{item.get("document_type", "")} {item.get("document_no", "")}'.casefold()]
    return sorted(rows, key=lambda item: str(item.get("time", "")), reverse=True)


def _page(rows, title, action, date, user, document):
    def esc(value, attr=False): return html.escape(str(value or ""), quote=attr)
    rendered = "".join(f'<tr><td>{esc(item.get("time"))}</td><td>{esc(item.get("user"))}</td><td>{esc(item.get("action"))}</td><td>{esc(item.get("document_type"))}</td><td>{esc(item.get("document_no"))}</td></tr>' for item in rows)
    if not rendered:
        rendered = '<tr><td colspan="5">No audit activity found.</td></tr>'
    return HTMLResponse(f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{esc(title)}</title><style>*{{box-sizing:border-box}}body{{margin:0;padding:34px;background:#F3F4F6;color:#111827;font-family:Arial}}main{{width:min(1200px,100%);margin:auto}}form{{display:grid;grid-template-columns:1fr 1fr 1fr auto;gap:10px;margin:20px 0}}input,button{{min-height:44px;padding:10px;border:1px solid #CBD5E1;border-radius:9px}}button,a{{background:#111827;color:#fff;font-weight:bold}}a{{display:inline-block;padding:11px 15px;border-radius:9px;text-decoration:none}}table{{width:100%;border-collapse:collapse;background:#fff}}th,td{{padding:12px;border-bottom:1px solid #E5E7EB;text-align:left}}th{{background:#111827;color:#fff}}@media(max-width:700px){{form{{grid-template-columns:1fr}}}}</style></head><body><main><a href="/">Dashboard</a><h1>{esc(title)}</h1><form method="get" action="{esc(action, True)}"><input type="date" name="date" value="{esc(date, True)}" aria-label="Date"><input name="user" value="{esc(user, True)}" placeholder="Search user" aria-label="User"><input name="document" value="{esc(document, True)}" placeholder="Search document" aria-label="Document"><button>Search</button></form><table><thead><tr><th>Time</th><th>User</th><th>Action</th><th>Document Type</th><th>Document No</th></tr></thead><tbody>{rendered}</tbody></table></main></body></html>''')


@router.get("/audit-log")
def account_audit_log(request: Request, date: str = "", user: str = "", document: str = ""):
    identity = request.scope.get("trade_paper_user") or {}
    account_id = str(identity.get("account_id", "") or "")
    if not account_id:
        raise HTTPException(status_code=401, detail="Login required")
    return _page(query_audit(account_id, date, user, document), "Account Audit Log", "/audit-log", date, user, document)


@router.get("/admin/audit-log")
def admin_audit_log(request: Request, date: str = "", user: str = "", document: str = ""):
    auth.require_admin(request)
    return _page(query_audit(None, date, user, document)[:200], "Admin Audit Log", "/admin/audit-log", date, user, document)
