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
    name = require_text("Company name", payload.get("name", ""))
    data = {
        "name": name,
        "address": payload.get("address", ""),
        "email": payload.get("email", ""),
        "phone": payload.get("phone", "")
    }

    saved = save_account_company(_account_id(request), data, ACCOUNT_COMPANIES_FILE)
    saved["redirect_to"] = safe_local_path(payload.get("next", "/"))
    return saved
