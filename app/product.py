from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from app.account_product import ensure_legacy_product_ownership, public_product
from app.auth import USERS_FILE
from app.storage import data_path, locked_json_mutation
from app.validation import require_text
from app.referential_integrity import find_soft_warnings, render_delete_page
from app.ui import html_escape

router = APIRouter()

PRODUCT_FILE = data_path("products.json")


def _account_id(request):
    user = request.scope.get("trade_paper_user") or {}
    return str(user.get("account_id", "") or "").strip()


def load_product_records():
    return ensure_legacy_product_ownership(PRODUCT_FILE, USERS_FILE)


def owned_product_entries(account_id):
    owner = str(account_id or "").strip()
    return [
        (index, record)
        for index, record in enumerate(load_product_records())
        if isinstance(record, dict) and str(record.get("account_id", "") or "").strip() == owner
    ]


def load_products(account_id):
    return [public_product(record) for _, record in owned_product_entries(account_id)]


def _owned_product(index, account_id):
    records = load_product_records()
    if (
        index < 0
        or index >= len(records)
        or not isinstance(records[index], dict)
        or str(records[index].get("account_id", "") or "").strip() != str(account_id or "").strip()
    ):
        raise HTTPException(status_code=404, detail="Product not found")
    return records[index]

@router.get("/product-data")
def product_data(request: Request):
    return load_products(_account_id(request))


@router.get("/products")
def product_list(request: Request, search: str = ""):
    entries = owned_product_entries(_account_id(request))

    if search:
        entries = [
            (index, product) for index, product in entries
            if search.lower() in str(product.get("name", "")).lower()
            or search.lower() in str(product.get("hs_code", "")).lower()
            or search.lower() in str(product.get("origin", "")).lower()
        ]
    next_action = (
        '<a href="/invoice"><button style="padding:13px 22px;background:#1D4ED8;color:white;border:none;border-radius:10px;font-size:16px;">Next: Create Invoice →</button></a>'
        if entries else ""
    )

    html = f"""
<h1 style="font-family:Arial;text-align:center;font-size:48px;margin-bottom:10px;">
Product List
</h1>

<p style="font-family:Arial;text-align:center;font-size:16px;color:#6B7280;margin-top:0;margin-bottom:35px;">
Manage all registered products
</p>

<div style="width:94%;margin:auto;font-family:Arial;">

<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:25px;gap:20px;">

<div style="display:flex;gap:12px;">
<a href="/product-form">
<button style="padding:13px 22px;background:#111827;color:white;border:none;border-radius:10px;font-size:16px;">
+ Add Product
</button>
</a>

<a href="/">
<button style="padding:13px 22px;background:#111827;color:white;border:none;border-radius:10px;font-size:16px;">
← Dashboard
</button>
</a>
</div>

{next_action}

<form action="/products" method="get" style="display:flex;gap:10px;align-items:center;margin:0;">
<input type="text" name="search" value="{html_escape(search, attribute=True)}" placeholder="Search product"
style="padding:13px;width:320px;border:1px solid #D1D5DB;border-radius:10px;font-size:15px;">

<button type="submit" style="padding:13px 22px;background:#111827;color:white;border:none;border-radius:10px;font-size:15px;">
Search
</button>

<a href="/products" style="color:#6B7280;font-weight:bold;">Reset</a>
</form>

</div>

<p style="font-size:18px;font-weight:bold;margin:25px 0;">
Total Products : {len(entries)}
</p>

<div style="background:white;border:1px solid #E5E7EB;border-radius:16px;overflow:hidden;">

<table style="width:100%;border-collapse:collapse;table-layout:fixed;">
<tr style="background:#F9FAFB;">
<th style="padding:14px;width:8%;">No</th>
<th style="width:24%;">Name</th>
<th style="width:18%;">HS Code</th>
<th style="width:16%;">Unit Price</th>
<th style="width:18%;">Origin</th>
<th style="width:8%;">Edit</th>
<th style="width:8%;">Delete</th>
</tr>
"""

    if not entries:
        html += """
<tr>
<td colspan="7" style="padding:35px;text-align:center;color:#6B7280;">
No products have been registered yet.
</td>
</tr>
"""
    else:
        for row_number, (index, product) in enumerate(entries, start=1):
            html += f"""
<tr style="border-top:1px solid #E5E7EB;">
<td style="padding:14px;text-align:center;">{row_number}</td>
<td style="padding:14px;word-break:break-word;">{html_escape(product.get("name", ""))}</td>
<td style="padding:14px;text-align:center;word-break:break-word;">{html_escape(product.get("hs_code", ""))}</td>
<td style="padding:14px;text-align:center;word-break:break-word;">{html_escape(product.get("unit_price", ""))}</td>
<td style="padding:14px;text-align:center;word-break:break-word;">{html_escape(product.get("origin", ""))}</td>
<td style="text-align:center;">
<a href="/edit-product/{index}" style="color:#111827;text-decoration:none;font-weight:bold;">Edit</a>
</td>
<td style="text-align:center;">
<a href="/delete-product/{index}" style="color:#DC2626;text-decoration:none;font-weight:bold;">Delete</a>
</td>
</tr>
"""

    html += """
</table>
</div>
</div>
"""

    return HTMLResponse(html)
    
        
@router.get("/edit-product/{index}")
def edit_product(index: int, request: Request):
    product = _owned_product(index, _account_id(request))

    html = f"""
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Edit Product</title>
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
<a href="/products"><button type="button" class="small">← Product List</button></a>
</div>

<h1 style="font-family:Arial;text-align:center;font-size:48px;margin-bottom:10px;">
Edit Product
</h1>

<p class="sub">
Update registered product information
</p>

<div class="card">
<h2 style="margin-top:0;">Product Information</h2>

<form action="/update-product/{index}" method="post">

<input name="name" value="{html_escape(product.get('name', ''), attribute=True)}" placeholder="Product Name">
<input name="hs_code" value="{html_escape(product.get('hs_code', ''), attribute=True)}" placeholder="HS Code">
<input name="unit_price" value="{html_escape(product.get('unit_price', ''), attribute=True)}" placeholder="Unit Price">
<input name="origin" value="{html_escape(product.get('origin', ''), attribute=True)}" placeholder="Country of Origin">

<button type="submit" class="full">Update Product</button>

</form>
</div>

</div>
</body>
</html>
"""

    return HTMLResponse(html)

@router.post("/update-product/{index}")
def update_product(
    index: int,
    request: Request,
    name: str = Form(""),
    hs_code: str = Form(""),
    unit_price: str = Form(""),
    origin: str = Form(""),
):
    name = require_text("Product name", name)
    account_id = _account_id(request)
    def replace_product(products):
        if (
            not 0 <= index < len(products)
            or not isinstance(products[index], dict)
            or str(products[index].get("account_id", "") or "").strip() != account_id
        ):
            raise HTTPException(status_code=404, detail="Product not found")
        products[index] = {
            "account_id": account_id,
            "name": name,
            "hs_code": hs_code,
            "unit_price": unit_price,
            "origin": origin
        }
    locked_json_mutation(PRODUCT_FILE, [], replace_product, list)

    return RedirectResponse(url="/products", status_code=303)

@router.get("/product-form")
def product_form(demo: int = 0):
    demo_values = {
        "name": "Notebook Computer",
        "hs_code": "847130",
        "unit_price": "850",
        "origin": "Korea",
    } if demo == 1 else {"name": "", "hs_code": "", "unit_price": "", "origin": ""}
    demo_notice = (
        '<div class="demo-preview"><b>Demo Preview</b><br>'
        'Temporary values — nothing is saved until you press Save.</div>'
        if demo == 1 else ""
    )

    html = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Product Master</title>
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
.demo-preview{padding:16px;margin-bottom:20px;border:1px solid #BFDBFE;border-radius:12px;background:#EFF6FF;color:#1E3A8A;line-height:1.5;}
</style>
</head>
<body>
<div class="container">

<div class="nav">
<a href="/"><button type="button" class="small">← Dashboard</button></a>
<a href="/products"><button type="button" class="small">← Product List</button></a>
</div>

<h1 style="font-family:Arial;text-align:center;font-size:48px;margin-bottom:10px;">
Product Master
</h1>

<p class="sub">
Manage product information for trade documents
</p>
__DEMO_NOTICE__

<div class="card">
<h2 style="margin-top:0;">Add Product</h2>

<form action="/save-product" method="post">

<input name="name" value="__DEMO_NAME__" placeholder="Product Name">
<input name="hs_code" value="__DEMO_HS_CODE__" placeholder="HS Code">
<input name="unit_price" value="__DEMO_UNIT_PRICE__" placeholder="Unit Price">
<input name="origin" value="__DEMO_ORIGIN__" placeholder="Country of Origin">

<button type="submit" class="full">Save Product</button>

</form>
</div>

</div>
</body>
</html>
"""

    html = (
        html.replace("__DEMO_NOTICE__", demo_notice)
        .replace("__DEMO_NAME__", demo_values["name"])
        .replace("__DEMO_HS_CODE__", demo_values["hs_code"])
        .replace("__DEMO_UNIT_PRICE__", demo_values["unit_price"])
        .replace("__DEMO_ORIGIN__", demo_values["origin"])
    )
    return HTMLResponse(html)

@router.post("/save-product")
def save_product(
    request: Request,
    name: str = Form(""),
    hs_code: str = Form(""),
    unit_price: str = Form(""),
    origin: str = Form(""),
):
    name = require_text("Product name", name)
    product = {
        "account_id": _account_id(request),
        "name": name,
        "hs_code": hs_code,
        "unit_price": unit_price,
        "origin": origin
    }

    locked_json_mutation(PRODUCT_FILE, [], lambda products: products.append(product), list)

    return RedirectResponse(url="/products", status_code=303)


@router.get("/delete-product/{index}")
def delete_product(index: int, request: Request):
    account_id = _account_id(request)
    product = _owned_product(index, account_id)
    name = str(product.get("name", "") or "").strip()
    return render_delete_page(
        "Product",
        name,
        f"/delete-product/{index}",
        "/products",
        warnings=find_soft_warnings("Product", name, account_id=account_id),
        expected_name=name,
    )

@router.post("/delete-product/{index}")
def confirm_delete_product(index: int, request: Request, expected_name: str = Form("")):
    account_id = _account_id(request)
    expected = str(expected_name or "").strip()

    def remove(products):
        if (
            not 0 <= index < len(products)
            or not isinstance(products[index], dict)
            or str(products[index].get("account_id", "") or "").strip() != account_id
            or str(products[index].get("name", "") or "").strip().casefold() != expected.casefold()
        ):
            raise HTTPException(status_code=404, detail="Product not found")
        products.pop(index)

    locked_json_mutation(PRODUCT_FILE, [], remove, list)
    return RedirectResponse("/products", status_code=303)
