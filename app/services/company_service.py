import json
from pathlib import Path

DATA_DIR = Path("data")
COMPANY_FILE = DATA_DIR / "company.json"

DEFAULT_COMPANY = {
    "company_name": "",
    "representative": "",
    "business_number": "",
    "address": "",
    "email": "",
    "phone": "",
    "website": "",
    "bank_name": "",
    "bank_account": "",
    "swift_code": ""
}


def load_company():
    DATA_DIR.mkdir(exist_ok=True)

    if not COMPANY_FILE.exists():
        save_company(DEFAULT_COMPANY)
        return DEFAULT_COMPANY

    with open(COMPANY_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_company(company_data: dict):
    DATA_DIR.mkdir(exist_ok=True)

    with open(COMPANY_FILE, "w", encoding="utf-8") as f:
        json.dump(company_data, f, ensure_ascii=False, indent=2)

    return company_data