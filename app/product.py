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
def product_list():
    products = load_products()

    html = """
    <h1>Product Master</h1>

    <p><a href="/product-form">Add Product</a></p>
    <p><a href="/">Back Home</a></p>

    <table border="1" cellpadding="10">
        <tr>
            <th>No</th>
            <th>Name</th>
            <th>HS Code</th>
            <th>Unit Price</th>
            <th>Origin</th>
<th>Edit</th>
<th>Delete</th>
        </tr>
    """

    for index, product in enumerate(products):
        html += f"""
        <tr>
            <td>{index + 1}</td>
            <td>{product.get("name", "")}</td>
            <td>{product.get("hs_code", "")}</td>
            <td>{product.get("unit_price", "")}</td>
            <td>{product.get("origin", "")}</td>

<td>
<a href="/edit-product/{index}">
Edit
</a>
</td>

<td>
<a href="/delete-product/{index}">
Delete
</a>
</td>
        </tr>
        """

    html += """
    </table>
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
<!DOCTYPE html>
<html>

<head>

<title>Product Master</title>

<style>

body{
    font-family:Arial,sans-serif;
    background:#f3f4f6;
    padding:40px;
}

.container{
    max-width:800px;
    margin:auto;
    background:white;
    padding:40px;
    border-radius:14px;
}

h1{
    margin-bottom:30px;
}

input{
    width:100%;
    padding:15px;
    margin-bottom:18px;
    border:1px solid #ddd;
    border-radius:10px;
    font-size:16px;
    box-sizing:border-box;
}

button{
    width:100%;
    padding:16px;
    background:#081B4B;
    color:white;
    border:none;
    border-radius:10px;
    font-size:18px;
    cursor:pointer;
}

button:hover{
    opacity:.9;
}

.back{
    margin-bottom:30px;
}

</style>

</head>

<body>

<div class="container">

<div class="back">
<a href="/products">← Back Product List</a>
</div>

<h1>Product Master</h1>

<form action="/save-product" method="post">

<input
name="name"
placeholder="Product Name">

<input
name="hs_code"
placeholder="HS Code">

<input
name="unit_price"
placeholder="Unit Price">

<input
name="origin"
placeholder="Country of Origin">

<button type="submit">
Save Product
</button>

</form>

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