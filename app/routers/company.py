from fastapi import APIRouter, Body
from app.storage import atomic_write_json, data_path, load_json_strict
from app.validation import require_text

router = APIRouter()

COMPANY_FILE = data_path("company.json")


def load_company():
    return load_json_strict(COMPANY_FILE, {
        "name": "",
        "address": "",
        "email": "",
        "phone": ""
    }, dict)


def save_company_data(data):
    atomic_write_json(COMPANY_FILE, data, dict)


@router.get("/company-data")
def get_company_data():
    return load_company()


@router.post("/save-company")
def save_company(payload: dict = Body(...)):
    name = require_text("Company name", payload.get("name", ""))
    data = {
        "name": name,
        "address": payload.get("address", ""),
        "email": payload.get("email", ""),
        "phone": payload.get("phone", "")
    }

    save_company_data(data)

    return data
