from fastapi import APIRouter, Body
from pathlib import Path
import json

router = APIRouter()

COMPANY_FILE = Path("data/company.json")


def load_company():
    if COMPANY_FILE.exists():
        with open(COMPANY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {
        "name": "",
        "address": "",
        "email": "",
        "phone": ""
    }


def save_company_data(data):
    with open(COMPANY_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)


@router.get("/company-data")
def get_company_data():
    return load_company()


@router.post("/save-company")
def save_company(payload: dict = Body(...)):
    data = {
        "name": payload.get("name", ""),
        "address": payload.get("address", ""),
        "email": payload.get("email", ""),
        "phone": payload.get("phone", "")
    }

    save_company_data(data)

    return data