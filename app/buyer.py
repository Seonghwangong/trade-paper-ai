from fastapi import APIRouter, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from app.storage import atomic_write_json, data_path, load_json_strict, locked_json_mutation
from app.validation import require_text
from app.referential_integrity import confirmed_indexed_delete, indexed_delete_confirmation

router = APIRouter()

BUYER_FILE = data_path("buyers.json")


def load_buyers():
    return load_json_strict(BUYER_FILE, [], list)


def save_buyers(buyers):
    atomic_write_json(BUYER_FILE, buyers, list)


@router.get("/buyer-data")
def buyer_data():
    return load_buyers()


@router.get("/buyers")
def buyer_list():
    buyers = load_buyers()

    html = """
<h1 style="font-family:Arial;text-align:center;font-size:48px;margin-bottom:10px;">
Buyer Master
</h1>

<p style="font-family:Arial;text-align:center;font-size:16px;color:#6B7280;margin-top:0;margin-bottom:35px;">
Manage registered buyer information
</p>

<div style="font-family:Arial;width:94%;margin:auto;">

<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:25px;gap:20px;">
<div style="display:flex;gap:12px;">
<a href="/buyer-form">
<button style="padding:13px 22px;background:#111827;color:white;border:none;border-radius:10px;font-size:16px;">
+ Add Buyer
</button>
</a>

<a href="/">
<button style="padding:13px 22px;background:#111827;color:white;border:none;border-radius:10px;font-size:16px;">
← Dashboard
</button>
</a>
</div>
</div>

<p style="font-size:18px;font-weight:bold;margin:25px 0;">
Total Buyers : """ + str(len(buyers)) + """
</p>

<div style="background:white;border:1px solid #E5E7EB;border-radius:16px;overflow:hidden;">

<table style="width:100%;border-collapse:collapse;table-layout:fixed;">
<tr style="background:#F9FAFB;">
<th style="padding:14px;width:8%;">No</th>
<th style="width:18%;">Name</th>
<th style="width:30%;">Address</th>
<th style="width:22%;">Email</th>
<th style="width:12%;">Country</th>
<th style="width:5%;">Edit</th>
<th style="width:5%;">Delete</th>
</tr>
"""

    for index, buyer in enumerate(buyers):
        html += f"""
<tr style="border-top:1px solid #E5E7EB;">
<td style="padding:14px;text-align:center;">{index + 1}</td>
<td style="padding:14px;word-break:break-word;">{buyer.get("name", "")}</td>
<td style="padding:14px;word-break:break-word;">{buyer.get("address", "")}</td>
<td style="padding:14px;word-break:break-word;">{buyer.get("email", "")}</td>
<td style="padding:14px;text-align:center;word-break:break-word;">{buyer.get("country", "")}</td>
<td style="text-align:center;">
<a href="/edit-buyer/{index}" style="color:#111827;font-weight:bold;text-decoration:none;">Edit</a>
</td>
<td style="text-align:center;">
<a href="/delete-buyer/{index}" style="color:#DC2626;font-weight:bold;text-decoration:none;">Delete</a>
</td>
</tr>
"""

    html += """
</table>
</div>
</div>
"""

    return HTMLResponse(html)


@router.get("/buyer-form")
def buyer_form():
    html = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Add Buyer</title>
<style>
body{font-family:Arial,sans-serif;background:#f3f4f6;padding:40px;}
.container{max-width:900px;margin:auto;background:white;padding:35px;border-radius:16px;}
h1{text-align:center;font-size:48px;margin-bottom:10px;}
.sub{text-align:center;color:#6B7280;margin-bottom:35px;}
.nav{display:flex;gap:12px;margin-bottom:25px;}
.card{border:1px solid #E5E7EB;border-radius:16px;padding:25px;margin-bottom:25px;background:#fff;}
input{width:100%;padding:14px;margin-bottom:14px;border:1px solid #D1D5DB;border-radius:10px;font-size:16px;box-sizing:border-box;}
button{padding:16px;background:#111827;color:white;border:none;border-radius:12px;font-size:18px;cursor:pointer;}
.small{padding:13px 22px;font-size:16px;border-radius:10px;}
.full{width:100%;}
</style>
</head>
<body>
<div class="container">

<div class="nav">
<a href="/"><button type="button" class="small">← Dashboard</button></a>
<a href="/buyers"><button type="button" class="small">← Buyer List</button></a>
</div>

<h1>Add Buyer</h1>
<p class="sub">Register buyer master information</p>

<div class="card">
<h2>Buyer Information</h2>

<form action="/save-buyer" method="post">
<input type="text" name="name" placeholder="Buyer Name">
<input type="text" name="address" placeholder="Address">
<input type="text" name="email" placeholder="Email">
<input type="text" name="country" placeholder="Country">

<button type="submit" class="full">Save Buyer</button>
</form>
</div>

</div>
</body>
</html>
"""

    return HTMLResponse(html)


@router.post("/save-buyer")
def save_buyer(
    name: str = Form(""),
    address: str = Form(""),
    email: str = Form(""),
    country: str = Form(""),
):
    name = require_text("Buyer name", name)
    buyer = {
        "name": name,
        "address": address,
        "email": email,
        "country": country
    }

    locked_json_mutation(BUYER_FILE, [], lambda buyers: buyers.append(buyer), list)

    return RedirectResponse(url="/buyers", status_code=303)


@router.get("/edit-buyer/{index}")
def edit_buyer(index: int):
    buyers = load_buyers()

    if index >= len(buyers):
        return HTMLResponse("Buyer not found")

    buyer = buyers[index]

    html = f"""
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Edit Buyer</title>
<style>
body{{font-family:Arial,sans-serif;background:#f3f4f6;padding:40px;}}
.container{{max-width:900px;margin:auto;background:white;padding:35px;border-radius:16px;}}
h1{{text-align:center;font-size:48px;margin-bottom:10px;}}
.sub{{text-align:center;color:#6B7280;margin-bottom:35px;}}
.nav{{display:flex;gap:12px;margin-bottom:25px;}}
.card{{border:1px solid #E5E7EB;border-radius:16px;padding:25px;margin-bottom:25px;background:#fff;}}
input{{width:100%;padding:14px;margin-bottom:14px;border:1px solid #D1D5DB;border-radius:10px;font-size:16px;box-sizing:border-box;}}
button{{padding:16px;background:#111827;color:white;border:none;border-radius:12px;font-size:18px;cursor:pointer;}}
.small{{padding:13px 22px;font-size:16px;border-radius:10px;}}
.full{{width:100%;}}
</style>
</head>
<body>
<div class="container">

<div class="nav">
<a href="/"><button type="button" class="small">← Dashboard</button></a>
<a href="/buyers"><button type="button" class="small">← Buyer List</button></a>
</div>

<h1>Edit Buyer</h1>
<p class="sub">Update buyer master information</p>

<div class="card">
<h2>Buyer Information</h2>

<form action="/update-buyer/{index}" method="post">
<input type="text" name="name" value="{buyer.get('name', '')}" placeholder="Buyer Name">
<input type="text" name="address" value="{buyer.get('address', '')}" placeholder="Address">
<input type="text" name="email" value="{buyer.get('email', '')}" placeholder="Email">
<input type="text" name="country" value="{buyer.get('country', '')}" placeholder="Country">

<button type="submit" class="full">Update Buyer</button>
</form>
</div>

</div>
</body>
</html>
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
    name = require_text("Buyer name", name)
    def replace_buyer(buyers):
        if not 0 <= index < len(buyers):
            raise HTTPException(status_code=404, detail="Buyer not found")
        buyers[index] = {
            "name": name,
            "address": address,
            "email": email,
            "country": country
        }
    locked_json_mutation(BUYER_FILE, [], replace_buyer, list)

    return RedirectResponse(url="/buyers", status_code=303)


@router.get("/delete-buyer/{index}")
def delete_buyer(index: int):
    return indexed_delete_confirmation("Buyer", "Buyer", index, BUYER_FILE, "name", f"/delete-buyer/{index}", "/buyers")

@router.post("/delete-buyer/{index}")
def confirm_delete_buyer(index: int, expected_name: str = Form("")):
    return confirmed_indexed_delete("Buyer", "Buyer", index, expected_name, BUYER_FILE, "name", f"/delete-buyer/{index}", "/buyers", "/buyers")
