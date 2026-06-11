from fastapi import APIRouter, Form
from fastapi.responses import HTMLResponse

import json
from pathlib import Path

DATA_FILE = Path("data/company.json")

router = APIRouter()

company_data = {
    "name": "",
    "address": "",
    "email": ""
}
def load_company():
    if not DATA_FILE.exists():
        return company_data

    with open(DATA_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)

    return {
        "name": data.get("name", ""),
        "address": data.get("address", ""),
        "email": data.get("email", "")
    }


def save_company_to_file(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

@router.get("/company")
def company_form():

    global company_data
    company_data = load_company()

    html = f"""
    <h1>Company Information</h1>

    <form action="/save-company" method="post">
        <p>Company Name: <input type="text" name="name" value="{company_data['name']}"></p>
        <p>Address: <input type="text" name="address" value="{company_data['address']}"></p>
        <p>Email: <input type="text" name="email" value="{company_data['email']}"></p>

        <button type="submit">Save</button>
    </form>
    """

    return HTMLResponse(html)


@router.post("/save-company")
def save_company(
    name: str = Form(...),
    address: str = Form(...),
    email: str = Form(...)
):
    company_data["name"] = name
    company_data["address"] = address
    company_data["email"] = email
    save_company_to_file(company_data)

    html = f"""
    <h2>저장 완료</h2>

    <p>회사명: {name}</p>
    <p>주소: {address}</p>
    <p>이메일: {email}</p>

    <a href="/company">돌아가기</a>
    """

    return HTMLResponse(html)