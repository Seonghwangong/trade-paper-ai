from fastapi import APIRouter, Body, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from app.storage import atomic_write_json, data_path, load_json_strict, locked_json_mutation
from app.validation import require_text
from app.referential_integrity import confirmed_indexed_delete, indexed_delete_confirmation

router = APIRouter()

PRODUCT_FILE = data_path("products.json")


def load_products():
    return load_json_strict(PRODUCT_FILE, [], list)

@router.get("/product-data")
def product_data():
    return load_products()


def save_products(products):
    atomic_write_json(PRODUCT_FILE, products, list)


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

<form action="/products" method="get" style="display:flex;gap:10px;align-items:center;margin:0;">
<input type="text" name="search" value="{search}" placeholder="Search product"
style="padding:13px;width:320px;border:1px solid #D1D5DB;border-radius:10px;font-size:15px;">

<button type="submit" style="padding:13px 22px;background:#111827;color:white;border:none;border-radius:10px;font-size:15px;">
Search
</button>

<a href="/products" style="color:#6B7280;font-weight:bold;">Reset</a>
</form>

</div>

<p style="font-size:18px;font-weight:bold;margin:25px 0;">
Total Products : {len(products)}
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

    if not products:
        html += """
<tr>
<td colspan="7" style="padding:35px;text-align:center;color:#6B7280;">
No products have been registered yet.
</td>
</tr>
"""
    else:
        for index, product in enumerate(products):
            html += f"""
<tr style="border-top:1px solid #E5E7EB;">
<td style="padding:14px;text-align:center;">{index + 1}</td>
<td style="padding:14px;word-break:break-word;">{product.get("name", "")}</td>
<td style="padding:14px;text-align:center;word-break:break-word;">{product.get("hs_code", "")}</td>
<td style="padding:14px;text-align:center;word-break:break-word;">{product.get("unit_price", "")}</td>
<td style="padding:14px;text-align:center;word-break:break-word;">{product.get("origin", "")}</td>
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
def edit_product(index: int):

    products = load_products()

    if index >= len(products):
        return HTMLResponse("Product not found")

    product = products[index]

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

<input name="name" value="{product.get('name','')}" placeholder="Product Name">
<input name="hs_code" value="{product.get('hs_code','')}" placeholder="HS Code">
<input name="unit_price" value="{product.get('unit_price','')}" placeholder="Unit Price">
<input name="origin" value="{product.get('origin','')}" placeholder="Country of Origin">

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
    name: str = Form(""),
    hs_code: str = Form(""),
    unit_price: str = Form(""),
    origin: str = Form(""),
):
    name = require_text("Product name", name)
    def replace_product(products):
        if not 0 <= index < len(products):
            raise HTTPException(status_code=404, detail="Product not found")
        products[index] = {
            "name": name,
            "hs_code": hs_code,
            "unit_price": unit_price,
            "origin": origin
        }
    locked_json_mutation(PRODUCT_FILE, [], replace_product, list)

    return RedirectResponse(url="/products", status_code=303)

@router.get("/product-form")
def product_form():

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

<div class="card">
<h2 style="margin-top:0;">Add Product</h2>

<form action="/save-product" method="post">

<input name="name" placeholder="Product Name">
<input name="hs_code" placeholder="HS Code">
<input name="unit_price" placeholder="Unit Price">
<input name="origin" placeholder="Country of Origin">

<button type="submit" class="full">Save Product</button>

</form>
</div>

</div>
</body>
</html>
"""

    return HTMLResponse(html)

@router.post("/save-product")
def save_product(
    name: str = Form(""),
    hs_code: str = Form(""),
    unit_price: str = Form(""),
    origin: str = Form(""),
):
    name = require_text("Product name", name)
    product = {
        "name": name,
        "hs_code": hs_code,
        "unit_price": unit_price,
        "origin": origin
    }

    locked_json_mutation(PRODUCT_FILE, [], lambda products: products.append(product), list)

    return RedirectResponse(url="/products", status_code=303)


@router.get("/delete-product/{index}")
def delete_product(index: int):
    return indexed_delete_confirmation("Product", "Product", index, PRODUCT_FILE, "name", f"/delete-product/{index}", "/products")

@router.post("/delete-product/{index}")
def confirm_delete_product(index: int, expected_name: str = Form("")):
    return confirmed_indexed_delete("Product", "Product", index, expected_name, PRODUCT_FILE, "name", f"/delete-product/{index}", "/products", "/products")
