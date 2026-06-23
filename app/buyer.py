from fastapi import APIRouter, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from pathlib import Path
import json

router = APIRouter()

BUYER_FILE = Path("data/buyers.json")


def load_buyers():
    if BUYER_FILE.exists():
        with open(BUYER_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


def save_buyers(buyers):
    with open(BUYER_FILE, "w", encoding="utf-8") as f:
        json.dump(buyers, f, ensure_ascii=False, indent=4)


@router.get("/buyer-data")
def buyer_data():
    return load_buyers()


@router.get("/buyers")
def buyer_list():
    buyers = load_buyers()

    html = """
    <h1>Buyer Master</h1>

    <p><a href="/buyer-form">Add Buyer</a></p>
    <p><a href="/">Back Home</a></p>

    <table border="1" cellpadding="10">
        <tr>
            <th>No</th>
            <th>Name</th>
            <th>Address</th>
            <th>Email</th>
            <th>Country</th>
            <th>Edit</th>
            <th>Delete</th>
        </tr>
    """

    for index, buyer in enumerate(buyers):
        html += f"""
        <tr>
            <td>{index + 1}</td>
            <td>{buyer.get("name", "")}</td>
            <td>{buyer.get("address", "")}</td>
            <td>{buyer.get("email", "")}</td>
            <td>{buyer.get("country", "")}</td>
            <td><a href="/edit-buyer/{index}">Edit</a></td>
            <td><a href="/delete-buyer/{index}">Delete</a></td>
        </tr>
        """

    html += """
    </table>
    """

    return HTMLResponse(html)


@router.get("/buyer-form")
def buyer_form():
    html = """
    <h1>Add Buyer</h1>

    <form action="/save-buyer" method="post">
        <p>Buyer Name</p>
        <input type="text" name="name">

        <p>Address</p>
        <input type="text" name="address">

        <p>Email</p>
        <input type="text" name="email">

        <p>Country</p>
        <input type="text" name="country">

        <br><br>
        <button type="submit">Save Buyer</button>
    </form>

    <br>
    <a href="/buyers">Back to Buyer List</a>
    """

    return HTMLResponse(html)


@router.post("/save-buyer")
def save_buyer(
    name: str = Form(""),
    address: str = Form(""),
    email: str = Form(""),
    country: str = Form(""),
):
    buyers = load_buyers()

    buyer = {
        "name": name,
        "address": address,
        "email": email,
        "country": country
    }

    buyers.append(buyer)
    save_buyers(buyers)

    return RedirectResponse(url="/buyers", status_code=303)


@router.get("/edit-buyer/{index}")
def edit_buyer(index: int):
    buyers = load_buyers()

    if index >= len(buyers):
        return HTMLResponse("Buyer not found")

    buyer = buyers[index]

    html = f"""
    <h1>Edit Buyer</h1>

    <form action="/update-buyer/{index}" method="post">
        <p>Buyer Name</p>
        <input type="text" name="name" value="{buyer.get('name', '')}">

        <p>Address</p>
        <input type="text" name="address" value="{buyer.get('address', '')}">

        <p>Email</p>
        <input type="text" name="email" value="{buyer.get('email', '')}">

        <p>Country</p>
        <input type="text" name="country" value="{buyer.get('country', '')}">

        <br><br>
        <button type="submit">Update Buyer</button>
    </form>

    <br>
    <a href="/buyers">Back to Buyer List</a>
    """

    return HTMLResponse(html)


@router.post("/update-buyer/{index}")
def update_buyer(
    index: int,
    name: str = Form(""),
    address: str = Form(""),
    email: str = Form(""),
    country: str = Form(""),
):
    buyers = load_buyers()

    if 0 <= index < len(buyers):
        buyers[index] = {
            "name": name,
            "address": address,
            "email": email,
            "country": country
        }

    save_buyers(buyers)

    return RedirectResponse(url="/buyers", status_code=303)


@router.get("/delete-buyer/{index}")
def delete_buyer(index: int):
    buyers = load_buyers()

    if 0 <= index < len(buyers):
        buyers.pop(index)

    save_buyers(buyers)

    return RedirectResponse(url="/buyers", status_code=303)