from __future__ import annotations

from datetime import datetime, timezone
import html

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app import auth, subscription
from app.audit_log import record_request_audit
from app.storage import locked_json_mutation


router = APIRouter()
ROLES = ("Owner", "Admin", "Manager", "Staff", "Viewer")
TEAM_MANAGERS = frozenset({"Owner", "Admin"})


def role_for_identity(identity):
    role = str((identity or {}).get("role", "Owner") or "Owner").strip().title()
    return role if role in ROLES else "Staff"


def require_team_manager(request):
    identity = request.scope.get("trade_paper_user") or {}
    if role_for_identity(identity) not in TEAM_MANAGERS:
        raise HTTPException(status_code=403, detail="Owner or Admin role required")
    return identity


def require_professional(account_id):
    if subscription.subscription_for_account(account_id)["plan"] != "Professional":
        raise HTTPException(status_code=403, detail="Professional plan required")


def _members(account_id):
    return [row for row in auth.load_users() if isinstance(row, dict) and str(row.get("account_id", "")) == account_id]


@router.get("/team", response_class=HTMLResponse)
def team_page(request: Request):
    identity = require_team_manager(request)
    account_id = identity["account_id"]
    plan = subscription.subscription_for_account(account_id)["plan"]
    enabled = plan == "Professional"
    def member_row(row):
        email = str(row.get("email", "")); current_role = str(row.get("role", "Owner") or "Owner")
        management = f'''<form method="post" action="/team/role"><input type="hidden" name="email" value="{html.escape(email, quote=True)}"><select name="role" aria-label="Role for {html.escape(email, quote=True)}">{''.join(f'<option value="{role}"{" selected" if role == current_role else ""}>{role}</option>' for role in ROLES)}</select><button>Update Role</button></form>''' if enabled else "Professional required"
        return f'<tr><td>{html.escape(email)}</td><td>{html.escape(current_role)}</td><td>{management}</td><td><a href="/audit-log?document={html.escape(email, quote=True)}">Activity</a></td></tr>'
    rows = "".join(member_row(row) for row in _members(account_id))
    invite = f'''<section><h2>Invite User</h2><form method="post" action="/team/invite"><label>Email<input name="email" type="email" required></label><label>Temporary Password<input name="password" type="password" minlength="8" required></label><label>Role<select name="role">{''.join(f'<option value="{role}">{role}</option>' for role in ROLES if role != "Owner")}</select></label><button>Invite User</button></form></section>''' if enabled else '<section><h2>Invite User</h2><p>Invite User and role management require the Professional plan.</p><a href="/pricing">View Plans</a></section>'
    return HTMLResponse(f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Team</title><style>*{{box-sizing:border-box}}body{{margin:0;background:#f3f4f6;font-family:Arial;color:#111827}}main{{width:min(1050px,calc(100% - 32px));margin:36px auto}}section{{margin:20px 0;padding:24px;background:#fff;border-radius:16px}}form{{display:flex;gap:10px;align-items:end;flex-wrap:wrap}}label{{display:grid;gap:6px;font-weight:bold}}input,select,button{{min-height:44px;padding:10px;border:1px solid #cbd5e1;border-radius:9px}}button,a{{background:#111827;color:#fff;font-weight:bold;text-decoration:none}}a{{display:inline-block;padding:11px 14px;border-radius:9px}}table{{width:100%;border-collapse:collapse;background:#fff}}th,td{{padding:12px;border-bottom:1px solid #e5e7eb;text-align:left}}th{{background:#111827;color:#fff}}@media(max-width:700px){{table{{display:block;overflow:auto}}}}</style></head><body><main><a href="/">Dashboard</a><h1>Team & Permissions</h1><p>Current plan: <strong>{html.escape(plan)}</strong></p>{invite}<section><h2>Users</h2><table><thead><tr><th>User</th><th>Role</th><th>Permission Management</th><th>Audit</th></tr></thead><tbody>{rows}</tbody></table></section></main></body></html>''')


@router.post("/team/invite")
def invite_user(request: Request, email: str = Form(""), password: str = Form(""), role: str = Form("Staff")):
    identity = require_team_manager(request); account_id = identity["account_id"]
    require_professional(account_id)
    normalized = auth._normalized_email(email)
    if not auth._EMAIL_PATTERN.match(normalized) or len(password) < auth.REGISTRATION_PASSWORD_MIN_LENGTH or role not in ROLES or role == "Owner":
        raise HTTPException(status_code=422, detail="Invalid invitation")
    duplicate = {"value": False}
    def add(rows):
        if any(isinstance(row, dict) and auth._normalized_email(row.get("email")) == normalized for row in rows):
            duplicate["value"] = True; return
        rows.append({"account_id": account_id, "company": identity.get("company", ""), "email": normalized, "password": auth._password_hash(password), "session_version": 0, "role": role, "invited_at": datetime.now(timezone.utc).isoformat(), "invited_by": identity.get("email", "")})
    locked_json_mutation(auth.USERS_FILE, [], add, list)
    if duplicate["value"]: raise HTTPException(status_code=409, detail="User already exists")
    record_request_audit(request, "Invite", "User", normalized, path=auth.USERS_FILE.with_name("audit_log.json"))
    return RedirectResponse("/team", status_code=303)


@router.post("/team/role")
def update_role(request: Request, email: str = Form(""), role: str = Form("Staff")):
    identity = require_team_manager(request); account_id = identity["account_id"]
    require_professional(account_id)
    if role not in ROLES: raise HTTPException(status_code=422, detail="Invalid role")
    normalized = auth._normalized_email(email)
    def update(rows):
        member = next((row for row in rows if isinstance(row, dict) and row.get("account_id") == account_id and auth._normalized_email(row.get("email")) == normalized), None)
        if member is None: raise HTTPException(status_code=404, detail="User not found")
        owners = [row for row in rows if isinstance(row, dict) and row.get("account_id") == account_id and str(row.get("role", "Owner") or "Owner") == "Owner"]
        if str(member.get("role", "Owner") or "Owner") == "Owner" and role != "Owner" and len(owners) == 1:
            raise HTTPException(status_code=409, detail="The account must keep an Owner")
        member["role"] = role; member["session_version"] = int(member.get("session_version", 0) or 0) + 1
    locked_json_mutation(auth.USERS_FILE, [], update, list)
    record_request_audit(request, "Role Change", "User", f"{normalized} · {role}", path=auth.USERS_FILE.with_name("audit_log.json"))
    return RedirectResponse("/team", status_code=303)
