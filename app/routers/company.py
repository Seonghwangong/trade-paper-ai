from fastapi import APIRouter, Body, Request
from app.account_company import load_account_company, safe_local_path, save_account_company, company_setup_complete
from app.storage import data_path
from app.validation import require_text

router = APIRouter()

ACCOUNT_COMPANIES_FILE = data_path("account_companies.json")


def _account_id(request):
    user = request.scope.get("trade_paper_user") or {}
    return str(user.get("account_id", "") or "").strip()


@router.get("/company-data")
def get_company_data(request: Request):
    account_id = _account_id(request)
    data = load_account_company(account_id, ACCOUNT_COMPANIES_FILE)
    # A suggestion until the first save, never a replacement for saved contact details.
    if not data["email"] and not company_setup_complete(account_id, ACCOUNT_COMPANIES_FILE):
        data["email"] = str((request.scope.get("trade_paper_user") or {}).get("email", "") or "")
    return data


@router.post("/save-company")
def save_company(request: Request, payload: dict = Body(...)):
    account_id = _account_id(request)
    existed = bool(load_account_company(account_id, ACCOUNT_COMPANIES_FILE).get("name"))
    name = require_text("Company name", payload.get("name", ""))
    data = {
        "name": name,
        "address": payload.get("address", ""),
        "email": payload.get("email", ""),
        "phone": payload.get("phone", "")
    }

    initial_setup = not company_setup_complete(account_id, ACCOUNT_COMPANIES_FILE)
    saved = save_account_company(account_id, data, ACCOUNT_COMPANIES_FILE)
    from app.audit_log import record_request_audit
    record_request_audit(request, "Update" if existed else "Create", "Company", name, path=ACCOUNT_COMPANIES_FILE.with_name("audit_log.json"))
    target = safe_local_path(payload.get("next", "/"))
    # Keep explicit file-import/demo destinations; only the standard first-use flow advances.
    from urllib.parse import urlsplit, parse_qs
    if target.split("?", 1)[0] == "/onboarding":
        target = safe_local_path(parse_qs(urlsplit(target).query).get("next", ["/"])[0])
    setup_flow = initial_setup or payload.get("flow") == "setup" or safe_local_path(payload.get("next", "")).split("?", 1)[0] == "/onboarding"
    saved["continue_setup"] = setup_flow
    saved["redirect_to"] = "/buyer-form?flow=setup" if setup_flow and target == "/" else target
    return saved
