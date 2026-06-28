from fastapi import APIRouter, Body, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from pathlib import Path
import json

router = APIRouter()

PRODUCT_FILE = Path("data/products.json")


def load_products():
    if PRODUCT_FILE.exists():
        with open(PRODUCT_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []

@router.get("/product-data")
def product_data():
    return load_products()


def save_products(products):
    with open(PRODUCT_FILE, "w", encoding="utf-8") as f:
        json.dump(products, f, ensure_ascii=False, indent=4)


@router.get("/products")
def product_list(search: str = ""):
    products = load_products()

    if search:
        products = [
            p for p in products
            if search.lower() in p.get("name", "").lower()
            or search.lower() in p.get("hs_code", "").lower()
            or search.lower() in p.get("origin", "").lower()
        ]

    html = f"""
<h1 style="font-family:Arial;text-align:center;font-size:48px;margin-bottom:10px;">
Product List
</h1>

<p style="font-family:Arial;text-align:center;font-size:16px;color:#6B7280;letter-spacing:0.5px;margin-top:0;margin-bottom:35px;">
Manage all registered products
</p>

<div style="width:90%;margin:auto;font-family:Arial;">

<p>
<a href="/product-form">
<button style="padding:12px 25px;background:#111827;color:white;border:none;border-radius:10px;font-size:16px;">
+ Add Product
</button>
</a>

<a href="/" style="margin-left:10px;">
<button style="padding:12px 25px;background:#111827;color:white;border:none;border-radius:10px;font-size:16px;">
← Dashboard
</button>
</a>
</p>

<form action="/products" method="get" style="margin:25px 0;">
<input type="text" name="search" value="{search}" placeholder="Search product name, HS code, or origin"
style="padding:14px;width:280px;border:1px solid #D1D5DB;border-radius:10px;font-size:16px;">

<button type="submit" style="padding:14px 30px;background:#111827;color:white;border:none;border-radius:10px;font-size:16px;">
Search
</button>

<a href="/products" style="margin-left:15px;color:#6B7280;">Reset</a>
</form>

<p style="font-size:18px;font-weight:bold;margin:25px 0;">
Total Products : {len(products)}
</p>

<table style="width:100%;border-collapse:collapse;background:white;border:1px solid #E5E7EB;">
<tr style="background:#F9FAFB;">
<th style="padding:15px;">No</th>
<th>Name</th>
<th>HS Code</th>
<th>Unit Price</th>
<th>Origin</th>
<th>Edit</th>
<th>Delete</th>
</tr>
"""

    if not products:
        html += """
<tr>
<td colspan="7" style="padding:30px;text-align:center;color:#6B7280;">
No products have been registered yet.
</td>
</tr>
"""
    else:
        for index, product in enumerate(products):
            html += f"""
<tr style="border-bottom:1px solid #E5E7EB;">
<td style="padding:15px;text-align:center;">{index + 1}</td>
<td>{product.get("name", "")}</td>
<td>{product.get("hs_code", "")}</td>
<td>{product.get("unit_price", "")}</td>
<td>{product.get("origin", "")}</td>
<td align="center">
<a href="/edit-product/{index}" style="color:#111827;text-decoration:none;font-weight:bold;">Edit</a>
</td>
<td align="center">
<a href="/delete-product/{index}" style="color:#DC2626;text-decoration:none;font-weight:bold;">Delete</a>
</td>
</tr>
"""

    html += """
</table>
</div>
"""

    return HTMLResponse(html)
    
        
@router.get("/edit-product/{index}")
def edit_product(index: int):

    products = load_products()

    if index >= len(products):
        return HTMLResponse("Product not found")

    product = products[index]

    html = f"""
<!DOCTYPE html>
<html>

<head>

<title>Edit Product</title>

<style>

body{{
    font-family:Arial,sans-serif;
    background:#f3f4f6;
    padding:40px;
}}

.container{{
    max-width:800px;
    margin:auto;
    background:white;
    padding:40px;
    border-radius:14px;
}}

input{{
    width:100%;
    padding:15px;
    margin-bottom:18px;
    border:1px solid #ddd;
    border-radius:10px;
    font-size:16px;
    box-sizing:border-box;
}}

button{{
    width:100%;
    padding:16px;
    background:#081B4B;
    color:white;
    border:none;
    border-radius:10px;
    font-size:18px;
}}

</style>

</head>

<body>

<div class="container">

<h1>Edit Product</h1>

<form action="/update-product/{index}" method="post">

<input
name="name"
value="{product.get('name','')}">

<input
name="hs_code"
value="{product.get('hs_code','')}">

<input
name="unit_price"
value="{product.get('unit_price','')}">

<input
name="origin"
value="{product.get('origin','')}">

<button type="submit">
Update Product
</button>

</form>

</div>

</body>

</html>
"""

    return HTMLResponse(html)

@router.post("/update-product/{index}")
def update_product(
    index: int,
    name: str = Form(""),
    hs_code: str = Form(""),
    unit_price: str = Form(""),
    origin: str = Form(""),
):
    products = load_products()

    if 0 <= index < len(products):
        products[index] = {
            "name": name,
            "hs_code": hs_code,
            "unit_price": unit_price,
            "origin": origin
        }

    save_products(products)

    return RedirectResponse(url="/products", status_code=303)

@router.get("/product-form")
def product_form():

    html = """
<h1 style="font-family:Arial;text-align:center;font-size:48px;margin-bottom:10px;">
Product Master
</h1>

<p style="font-family:Arial;text-align:center;font-size:16px;color:#6B7280;letter-spacing:0.5px;margin-top:0;margin-bottom:35px;">
Manage product information for trade documents
</p>

<div style="font-family:Arial;width:80%;margin:auto;">

<div style="background:#ffffff;border:1px solid #E5E7EB;border-radius:16px;padding:30px;margin-bottom:30px;">
<h2 style="margin-top:0;">Add Product</h2>

<form action="/save-product" method="post">

    <p>Product Name</p>
    <input name="name" style="width:100%;padding:14px;border:1px solid #D1D5DB;border-radius:10px;">

    <p>HS Code</p>
    <input name="hs_code" style="width:100%;padding:14px;border:1px solid #D1D5DB;border-radius:10px;">

    <p>Unit Price</p>
    <input name="unit_price" style="width:100%;padding:14px;border:1px solid #D1D5DB;border-radius:10px;">

    <p>Country of Origin</p>
    <input name="origin" style="width:100%;padding:14px;border:1px solid #D1D5DB;border-radius:10px;">

    <br><br>
    <button type="submit" style="width:100%;padding:16px;background:#111827;color:white;border:none;border-radius:12px;font-size:18px;">
        Save Product
    </button>

</form>
</div>

<a href="/products">
    <button style="width:220px;padding:15px;background:#111827;color:white;border:none;border-radius:10px;font-size:18px;">
        ← Product List
    </button>
</a>

<a href="/">
    <button style="width:220px;padding:15px;background:#111827;color:white;border:none;border-radius:10px;font-size:18px;margin-left:10px;">
        ← Dashboard
    </button>
</a>

</div>
"""

    return HTMLResponse(html)

@router.post("/save-product")
def save_product(
    name: str = Form(""),
    hs_code: str = Form(""),
    unit_price: str = Form(""),
    origin: str = Form(""),
):
    products = load_products()

    product = {
        "name": name,
        "hs_code": hs_code,
        "unit_price": unit_price,
        "origin": origin
    }

    products.append(product)
    save_products(products)

    return RedirectResponse(url="/products", status_code=303)


@router.get("/delete-product/{index}")
def delete_product(index: int):
    products = load_products()

    if 0 <= index < len(products):
        products.pop(index)

    save_products(products)

    return RedirectResponse(url="/products", status_code=303)