from __future__ import annotations

from datetime import datetime, timezone
import html

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app import auth
from app.documents import get_document_definition
from app.storage import data_path, load_json_strict, locked_json_mutation


router = APIRouter()
ARCHIVE_KEYS = ("quotation", "proforma", "invoice", "packing", "shipping_instruction", "shipment", "booking", "container", "bill_of_lading", "certificate_of_origin", "inspection", "insurance", "weight", "customs")


def _storage_path(key):
    if key == "quotation": from app.quotation import QUOTATION_FILE as path
    elif key == "proforma": from app.proforma import PROFORMA_FILE as path
    elif key == "invoice": from app.invoice import INVOICE_FILE as path
    elif key == "packing": from app.packing import PACKING_FILE as path
    elif key == "shipping_instruction": from app.shipping_instruction import SI_FILE as path
    elif key == "shipment": from app.shipment import SHIPMENT_FILE as path
    elif key == "booking": from app.booking_confirmation import BOOKING_FILE as path
    elif key == "container": from app.container_management import CONTAINER_FILE as path
    elif key == "bill_of_lading": from app.bill_of_lading import BL_FILE as path
    elif key == "certificate_of_origin": from app.certificate_of_origin import CO_FILE as path
    elif key == "inspection": from app.inspection_certificate import INSPECTION_FILE as path
    elif key == "insurance": from app.insurance_certificate import INSURANCE_FILE as path
    elif key == "weight": from app.weight_certificate import WEIGHT_FILE as path
    elif key == "customs": from app.customs_declaration import CUSTOMS_FILE as path
    else: raise HTTPException(status_code=404, detail="Document type not found")
    return path


def archive_document(request, key, identifier, redirect_url):
    definition = get_document_definition(key)
    account_id = str((request.scope.get("trade_paper_user") or {}).get("account_id", "") or "")
    path = _storage_path(key)
    def mutate(rows):
        record = next((row for row in rows if isinstance(row, dict) and row.get("account_id") == account_id and str(row.get(definition.identifier_field, "")) == str(identifier) and not row.get("archived_at")), None)
        if record is None:
            raise HTTPException(status_code=404, detail=f"{definition.label} not found")
        record["archived_at"] = datetime.now(timezone.utc).isoformat()
        record["archived_by"] = str((request.scope.get("trade_paper_user") or {}).get("email", "") or "")
    locked_json_mutation(path, [], mutate, list)
    from app.audit_log import record_request_audit
    record_request_audit(request, "Archive", definition.label, identifier, path=path.with_name("audit_log.json"))
    return RedirectResponse(redirect_url, status_code=303)


def render_archive_page(document, identifier, action_url, cancel_url):
    return HTMLResponse(f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Archive {html.escape(document)}</title></head><body><main><section><p>Archive</p><h1>Archive this document?</h1><p>The document will be hidden from active work and can be restored later.</p><dl><dt>Document</dt><dd>{html.escape(document)}</dd><dt>Identifier</dt><dd>{html.escape(str(identifier))}</dd></dl><form action="{html.escape(action_url, quote=True)}" method="post"><button type="submit">Archive</button></form><a href="{html.escape(cancel_url, quote=True)}">Cancel</a></section></main></body></html>''')


def archived_records(account_id, search=""):
    query = str(search or "").strip().casefold()
    results = []
    for key in ARCHIVE_KEYS:
        definition = get_document_definition(key)
        for row in load_json_strict(_storage_path(key), [], list):
            if not isinstance(row, dict) or row.get("account_id") != account_id or not row.get("archived_at"):
                continue
            text = " ".join(str(row.get(field, "") or "") for field in (definition.identifier_field, definition.title_field, *definition.searchable_fields)).casefold()
            if query and query not in text:
                continue
            results.append({"key": key, "label": definition.label, "identifier": str(row.get(definition.identifier_field, "")), "archived_at": row.get("archived_at", "")})
    return sorted(results, key=lambda item: item["archived_at"], reverse=True)


@router.get("/archive")
def archive_list(request: Request, search: str = ""):
    account_id = str((request.scope.get("trade_paper_user") or {}).get("account_id", "") or "")
    is_admin = bool((request.scope.get("trade_paper_user") or {}).get("is_admin"))
    def row_html(item):
        admin_action = f'<form method="post" action="/admin/archive/permanent-delete"><input type="hidden" name="key" value="{item["key"]}"><input type="hidden" name="identifier" value="{html.escape(item["identifier"], quote=True)}"><button>Permanent Delete</button></form>' if is_admin else "Admin only"
        return f'<tr><td>{html.escape(item["archived_at"])}</td><td>{html.escape(item["label"])}</td><td>{html.escape(item["identifier"])}</td><td><form method="post" action="/archive/restore"><input type="hidden" name="key" value="{item["key"]}"><input type="hidden" name="identifier" value="{html.escape(item["identifier"], quote=True)}"><button>Restore</button></form></td><td>{admin_action}</td></tr>'
    rows = "".join(row_html(item) for item in archived_records(account_id, search)) or '<tr><td colspan="5">No archived documents.</td></tr>'
    return HTMLResponse(f'''<!doctype html><html><head><meta charset="utf-8"><title>Archive</title></head><body><main><a href="/">Dashboard</a><h1>Archive</h1><form method="get"><input name="search" value="{html.escape(search, quote=True)}" placeholder="Search archive"><button>Search</button></form><table><thead><tr><th>Archived</th><th>Document Type</th><th>Document No</th><th>Restore</th><th>Admin</th></tr></thead><tbody>{rows}</tbody></table></main></body></html>''')


def _mutate_archived(request, key, identifier, permanent=False):
    definition = get_document_definition(key)
    if key not in ARCHIVE_KEYS:
        raise HTTPException(status_code=404, detail="Archived document not found")
    account_id = str((request.scope.get("trade_paper_user") or {}).get("account_id", "") or "")
    path = _storage_path(key)
    def mutate(rows):
        index = next((i for i, row in enumerate(rows) if isinstance(row, dict) and row.get("account_id") == account_id and str(row.get(definition.identifier_field, "")) == identifier and row.get("archived_at")), None)
        if index is None:
            raise HTTPException(status_code=404, detail="Archived document not found")
        if permanent:
            rows.pop(index)
        else:
            rows[index].pop("archived_at", None); rows[index].pop("archived_by", None)
    locked_json_mutation(path, [], mutate, list)
    action = "Permanent Delete" if permanent else "Restore"
    from app.audit_log import record_request_audit
    record_request_audit(request, action, definition.label, identifier, path=path.with_name("audit_log.json"))
    return RedirectResponse("/archive", status_code=303)


@router.post("/archive/restore")
def restore_archived(request: Request, key: str = Form(""), identifier: str = Form("")):
    return _mutate_archived(request, key, identifier)


@router.post("/admin/archive/permanent-delete")
def permanent_delete_archived(request: Request, key: str = Form(""), identifier: str = Form("")):
    auth.require_admin(request)
    return _mutate_archived(request, key, identifier, True)
