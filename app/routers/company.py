from fastapi import APIRouter, Body, Request
from app.account_company import load_account_company, safe_local_path, save_account_company
from app.storage import data_path
from app.validation import require_text

router = APIRouter()

ACCOUNT_COMPANIES_FILE = data_path("account_companies.json")


def _account_id(request):
    user = request.scope.get("trade_paper_user") or {}
    return str(user.get("account_id", "") or "").strip()


@router.get("/company-data")
def get_company_data(request: Request):
    return load_account_company(_account_id(request), ACCOUNT_COMPANIES_FILE)


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

    saved = save_account_company(account_id, data, ACCOUNT_COMPANIES_FILE)
    from app.audit_log import record_request_audit
    record_request_audit(request, "Update" if existed else "Create", "Company", name, path=ACCOUNT_COMPANIES_FILE.with_name("audit_log.json"))
    saved["redirect_to"] = safe_local_path(payload.get("next", "/"))
    return saved
