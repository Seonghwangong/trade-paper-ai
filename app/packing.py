from io import BytesIO
from datetime import datetime
from typing import List
from fastapi import APIRouter, Body, Response, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.lib import colors
import json
from pathlib import Path

COMPANY_FILE = Path("data/company.json")
PACKING_FILE = Path("data/packing_lists.json")
INVOICE_FILE = Path("data/invoices.json")

router = APIRouter()


def load_company():
    if COMPANY_FILE.exists():
        with open(COMPANY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def load_packing_lists():
    if PACKING_FILE.exists():
        with open(PACKING_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


def save_packing_lists(packing_lists):
    with open(PACKING_FILE, "w", encoding="utf-8") as f:
        json.dump(packing_lists, f, ensure_ascii=False, indent=4)


@router.get("/packing-list")
def packing_list(search: str = ""):
    packing_lists = load_packing_lists()
    packing_lists = list(reversed(packing_lists))

    if search:
        packing_lists = [
            p for p in packing_lists
            if search.lower() in str(p.get("buyer", "")).lower()
            or search.lower() in str(p.get("seller", "")).lower()
            or search.lower() in str(p.get("items", "")).lower()
        ]

    html = f"""
<h1 style="font-family: Arial;">Packing List</h1>

<form action="/packing-list" method="get" style="margin-bottom:20px;">
<input type="text" name="search" value="{search}" placeholder="Search buyer, seller or item" style="padding:10px; width:250px;">
<button type="submit">Search</button>
</form>

<p><b>Total Packing Lists:</b> {len(packing_lists)}</p>

<p><a href="/packing-form">+ New Packing List</a></p>

<table border="1" style="border-collapse: collapse; width: 100%; font-family: Arial;">
<tr style="background-color:#f3f4f6;">
    <th style="padding:8px;">Packing No</th>
    <th style="padding:8px;">Invoice No</th>
    <th style="padding:8px;">Seller</th>
    <th style="padding:8px;">Buyer</th>
    <th style="padding:8px;">Item</th>
    <th style="padding:8px;">HS Code</th>
    <th style="padding:8px;">Carton</th>
    <th style="padding:8px;">Net Weight</th>
    <th style="padding:8px;">Gross Weight</th>
    <th style="padding:8px;">PDF</th>
    <th style="padding:8px;">Edit</th>
    <th style="padding:8px;">Delete</th>
</tr>
"""

    for packing in packing_lists:
        if not packing.get("packing_no"):
            continue

        items = packing.get("items", [])
        first_item = items[0] if items else {}
        item_names = "<br>".join(item.get("name", "") for item in items)
        hs_codes = "<br>".join(item.get("hs_code", "") for item in items)
        cartons = "<br>".join(str(item.get("carton", "")) for item in items)
        net_weights = "<br>".join(str(item.get("net_weight", "")) for item in items)
        gross_weights = "<br>".join(str(item.get("gross_weight", "")) for item in items)
        html += f"""
<tr>
    <td style="padding:8px;">{packing.get("packing_no", "")}</td>
    <td style="padding:8px;">{packing.get("invoice_no", "")}</td>
    <td style="padding:8px;">{packing.get("seller", "")}</td>
    <td style="padding:8px;">{packing.get("buyer", "")}</td>
    <td style="padding:8px;">{item_names}</td>
    <td style="padding:8px;">{hs_codes}</td>
    <td style="padding:8px;">{cartons}</td>
    <td style="padding:8px;">{net_weights}</td>
    <td style="padding:8px;">{gross_weights}</td>
    <td style="padding:8px;"><a href="/packing-list-pdf/{packing.get("packing_no", "")}">PDF</a></td>
    <td style="padding:8px;"><a href="/edit-packing/{packing.get("packing_no", "")}">Edit</a></td>
    <td style="padding:8px;"><a href="/packing-delete/{packing.get("packing_no", "")}">Delete</a></td>
</tr>
"""

    html += "</table>"
    return HTMLResponse(html)


@router.post("/packing")
def save_packing(
    invoice_no: str = Form(""),
    seller: str = Form(""),
    buyer: str = Form(""),
    item_name: List[str] = Form([]),
    hs_code: List[str] = Form([]),
    carton: List[str] = Form([]),
    net_weight: List[str] = Form([]),
    gross_weight: List[str] = Form([]),
):
    packing_lists = load_packing_lists()

    existing_numbers = [
        int(p.get("packing_no", "PK-000").split("-")[1])
        for p in packing_lists
        if p.get("packing_no", "").startswith("PK-")
    ]

    next_no = max(existing_numbers, default=0) + 1
    packing_no = f"PK-{next_no:03d}"

    items = []

    for i in range(len(item_name)):
        if not item_name[i].strip():
            continue

        items.append({
            "name": item_name[i],
            "hs_code": hs_code[i] if i < len(hs_code) else "",
            "carton": carton[i] if i < len(carton) else "",
            "net_weight": net_weight[i] if i < len(net_weight) else "",
            "gross_weight": gross_weight[i] if i < len(gross_weight) else "",
        })

    packing = {
        "packing_no": packing_no,
        "invoice_no": invoice_no,
        "seller": seller,
        "buyer": buyer,
        "items": items,
    }

    packing_lists.append(packing)
    save_packing_lists(packing_lists)

    return RedirectResponse(url="/packing-list", status_code=303)

@router.get("/edit-packing/{packing_no}")
def edit_packing(packing_no: str):
    packing_lists = load_packing_lists()

    for packing in packing_lists:
        if packing.get("packing_no") == packing_no:
            items = packing.get("items", [])

            html = f"""
<h1>Edit Packing List</h1>

<form action="/update-packing/{packing_no}" method="post">

<p>Invoice No</p>
<input type="text" name="invoice_no" value="{packing.get('invoice_no','')}">

<p>Seller</p>
<input type="text" name="seller" value="{packing.get('seller','')}">

<p>Buyer</p>
<input type="text" name="buyer" value="{packing.get('buyer','')}">

<h3>Items</h3>

<div id="items_area">
"""

            for item in items:
                html += f"""
<div class="item-row" style="border:1px solid #ddd;padding:10px;margin-bottom:10px;">

<p>Product</p>
<select onchange="selectProduct(this)">
    <option value="">Select Product</option>
</select>

<p>Item Name</p>
<input type="text" name="item_name" value="{item.get('name','')}">

<p>HS Code</p>
<input type="text" name="hs_code" value="{item.get('hs_code','')}">

<p>Gross Weight</p>
<input type="text" name="gross_weight" value="{item.get('gross_weight','')}">

<button type="button" onclick="removeItem(this)">Remove Item</button>

</div>
"""

            html += """
</div>

<button type="button" onclick="addItem()">+ Add Item</button>

<br><br>
<button type="submit">Update Packing</button>

</form>

<script>
let products = [];

async function loadProducts() {
    const response = await fetch("/product-data");
    products = await response.json();

    document.querySelectorAll(".item-row select").forEach(fillSelect);
}

function fillSelect(select) {
    select.innerHTML = '<option value="">Select Product</option>';

    products.forEach((product, index) => {
        select.innerHTML += `<option value="${index}">${product.name}</option>`;
    });
}

function selectProduct(select) {
    const index = select.value;
    if (index === "") return;

    const row = select.closest(".item-row");
    const product = products[index];

    row.querySelector('input[name="item_name"]').value = product.name || "";
    row.querySelector('input[name="hs_code"]').value = product.hs_code || "";
}

function addItem() {
    const area = document.getElementById("items_area");
    const firstRow = document.querySelector(".item-row");
    const newRow = firstRow.cloneNode(true);

    newRow.querySelectorAll("input").forEach(input => input.value = "");
    newRow.querySelector("select").selectedIndex = 0;

    area.appendChild(newRow);

    fillSelect(newRow.querySelector("select"));
}

function removeItem(button) {
    const rows = document.querySelectorAll(".item-row");

    if (rows.length <= 1) {
        alert("At least one item is required.");
        return;
    }

    button.closest(".item-row").remove();
}

loadProducts();
</script>

<br>
<a href="/packing-list">Back to Packing List</a>
"""

            return HTMLResponse(html)

    return {"error": "Packing List not found"}



@router.post("/update-packing/{packing_no}")
def update_packing(
    packing_no: str,
    invoice_no: str = Form(""),
    seller: str = Form(""),
    buyer: str = Form(""),
    item_name: List[str] = Form([]),
    hs_code: List[str] = Form([]),
    carton: List[str] = Form([]),
    net_weight: List[str] = Form([]),
    gross_weight: List[str] = Form([]),
):
    packing_lists = load_packing_lists()

    items = []

    for i in range(len(item_name)):
        if not item_name[i].strip():
            continue

        items.append({
            "name": item_name[i],
            "hs_code": hs_code[i] if i < len(hs_code) else "",
            "carton": carton[i] if i < len(carton) else "",
            "net_weight": net_weight[i] if i < len(net_weight) else "",
            "gross_weight": gross_weight[i] if i < len(gross_weight) else "",
        })

    for packing in packing_lists:
        if packing.get("packing_no") == packing_no:
            packing["invoice_no"] = invoice_no
            packing["seller"] = seller
            packing["buyer"] = buyer
            packing["items"] = items
            break

    save_packing_lists(packing_lists)

    return RedirectResponse(url="/packing-list", status_code=303)

@router.get("/packing-delete/{packing_no}")
def delete_packing(packing_no: str):
    packing_lists = load_packing_lists()

    packing_lists = [
        p for p in packing_lists
        if p.get("packing_no") != packing_no
    ]

    save_packing_lists(packing_lists)

    return RedirectResponse(url="/packing-list", status_code=303)


@router.get("/packing-form")
def packing_form():
    invoices = []

    if INVOICE_FILE.exists():
        with open(INVOICE_FILE, "r", encoding="utf-8") as f:
            invoices = json.load(f)

    invoice_options = '<option value="">Select Invoice</option>'

    for invoice in invoices:
        invoice_no = invoice.get("invoice_no", "")
        seller = invoice.get("seller", "")
        buyer = invoice.get("buyer", "")

        if not invoice_no:
            continue

        invoice_options += f"""
<option value="{invoice_no}" data-seller="{seller}" data-buyer="{buyer}">
    {invoice_no} - {buyer}
</option>
"""

    html = f"""
<h1>Packing Input</h1>

<form action="/packing" method="post">
    <p>Invoice No</p>
    <select id="invoice_no" name="invoice_no">
        {invoice_options}
    </select>

    <p>Seller</p>
    <input id="seller" type="text" name="seller">

    <p>Buyer</p>
    <input id="buyer" type="text" name="buyer">

    <h3>Items</h3>

    <div id="items_area">
        <div class="item-row" style="border:1px solid #ddd; padding:10px; margin-bottom:10px;">
            <p>Product</p>
            <select onchange="selectProduct(this)">
                <option value="">Select Product</option>
            </select>

            <p>Item Name</p>
            <input type="text" name="item_name">

            <p>HS Code</p>
            <input type="text" name="hs_code">

            <p>Carton</p>
            <input type="text" name="carton">

            <p>Net Weight</p>
            <input type="text" name="net_weight">

            <p>Gross Weight</p>
            <input type="text" name="gross_weight">
        </div>
    </div>

    <button type="button" onclick="addItem()">+ Add Item</button>

    <br><br>
    <button type="submit">Save Packing</button>
</form>

<script>
document.getElementById("invoice_no").addEventListener("change", function() {{
    const selected = this.options[this.selectedIndex];

    document.getElementById("seller").value = selected.dataset.seller || "";
    document.getElementById("buyer").value = selected.dataset.buyer || "";
}});

let products = [];

async function loadProducts() {{
    const response = await fetch("/product-data");
    products = await response.json();

    fillProductSelects();
}}

function fillProductSelects() {{
    document.querySelectorAll(".item-row select").forEach(select => {{
        if (select.options.length > 1) return;

        products.forEach((product, index) => {{
            select.innerHTML += '<option value="' + index + '">' + product.name + '</option>';
        }});
    }});
}}

function selectProduct(select) {{
    const index = select.value;
    if (index === "") return;

    const row = select.closest(".item-row");
    const product = products[index];

    row.querySelector('input[name="item_name"]').value = product.name || "";
    row.querySelector('input[name="hs_code"]').value = product.hs_code || "";
}}

function addItem() {{
    const area = document.getElementById("items_area");
    const firstRow = document.querySelector(".item-row");
    const newRow = firstRow.cloneNode(true);

    newRow.querySelectorAll("input").forEach(input => input.value = "");
    newRow.querySelector("select").selectedIndex = 0;

    area.appendChild(newRow);
    fillProductSelects();
}}

loadProducts();
</script>

<br>
<a href="/">Back Home</a>
<br>
<a href="/packing-list">Back to Packing List</a>
"""

    return HTMLResponse(html)

@router.post("/packing-list/pdf")
def create_packing_list_pdf(payload: dict = Body(...)):
    company = load_company()

    packing_no = payload.get("packing_no", "PK-001")
    invoice_no = payload.get("invoice_no", "")
    today = datetime.now().strftime("%Y-%m-%d")

    buyer = payload.get("buyer", "")
    seller = company.get("name") or payload.get("seller", "")
    seller_address = company.get("address") or payload.get("seller_address", "")
    seller_email = company.get("email") or payload.get("seller_email", "")

    items = payload.get("items", [])

    buffer = BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4

    pdf.setTitle(f"Packing List {packing_no}")

    pdf.setFillColor(colors.HexColor("#111827"))
    pdf.rect(0, height - 90, width, 90, fill=1, stroke=0)

    pdf.setFillColor(colors.white)
    pdf.setFont("Helvetica-Bold", 24)
    pdf.drawString(45, height - 55, "PACKING LIST")

    pdf.setFont("Helvetica", 9)
    pdf.drawRightString(width - 45, height - 38, "Trade Paper AI")
    pdf.drawRightString(width - 45, height - 55, "Automated Trade Document")

    pdf.setFillColor(colors.black)
    pdf.setFont("Helvetica-Bold", 11)
    pdf.drawString(45, height - 125, f"Packing No: {packing_no}")
    pdf.drawString(45, height - 143, f"Invoice No: {invoice_no}")
    pdf.drawString(45, height - 161, f"Date: {today}")

    pdf.setStrokeColor(colors.HexColor("#D1D5DB"))
    pdf.setFillColor(colors.HexColor("#F9FAFB"))
    pdf.roundRect(45, height - 260, 240, 80, 8, fill=1)
    pdf.roundRect(310, height - 260, 240, 80, 8, fill=1)

    pdf.setFillColor(colors.HexColor("#111827"))
    pdf.setFont("Helvetica-Bold", 11)
    pdf.drawString(60, height - 197, "SELLER")
    pdf.drawString(325, height - 197, "BUYER")

    pdf.setFont("Helvetica", 9)
    pdf.drawString(60, height - 220, seller)
    pdf.drawString(60, height - 235, seller_address)
    pdf.drawString(60, height - 250, seller_email)

    pdf.drawString(325, height - 220, buyer)

    y = height - 315
    pdf.setFillColor(colors.HexColor("#E5E7EB"))
    pdf.rect(45, y, 505, 28, fill=1, stroke=0)

    pdf.setFillColor(colors.black)
    pdf.setFont("Helvetica-Bold", 8)
    pdf.drawString(52, y + 10, "No")
    pdf.drawString(80, y + 10, "Item")
    pdf.drawString(205, y + 10, "HS Code")
    pdf.drawRightString(330, y + 10, "Carton")
    pdf.drawRightString(430, y + 10, "Net Weight")
    pdf.drawRightString(540, y + 10, "Gross Weight")

    y -= 28
    pdf.setFont("Helvetica", 8)
    pdf.setStrokeColor(colors.HexColor("#D1D5DB"))

    total_carton = 0
    total_net_weight = 0.0
    total_gross_weight = 0.0

    for index, item in enumerate(items, start=1):
        carton = item.get("carton", "")
        net_weight = item.get("net_weight", "")
        gross_weight = item.get("gross_weight", "")

        try:
            total_carton += int(float(carton or 0))
        except:
            pass

        try:
            total_net_weight += float(net_weight or 0)
        except:
            pass

        try:
            total_gross_weight += float(gross_weight or 0)
        except:
            pass

        pdf.rect(45, y, 505, 26, fill=0)

        pdf.drawString(52, y + 9, str(index))
        pdf.drawString(80, y + 9, str(item.get("name", ""))[:25])
        pdf.drawString(205, y + 9, str(item.get("hs_code", "")))
        pdf.drawRightString(330, y + 9, str(carton))
        pdf.drawRightString(430, y + 9, str(net_weight))
        pdf.drawRightString(540, y + 9, str(gross_weight))

        y -= 26

    y -= 25
    pdf.setFillColor(colors.HexColor("#111827"))
    pdf.roundRect(325, y - 5, 225, 65, 8, fill=1, stroke=0)

    pdf.setFillColor(colors.white)
    pdf.setFont("Helvetica-Bold", 10)
    pdf.drawString(340, y + 42, f"Total Cartons: {total_carton}")
    pdf.drawString(340, y + 24, f"Total Net Weight: {total_net_weight:g}")
    pdf.drawString(340, y + 6, f"Total Gross Weight: {total_gross_weight:g}")

    pdf.setFillColor(colors.black)
    pdf.setFont("Helvetica", 10)
    pdf.drawString(45, 115, "Authorized Signature:")
    pdf.line(170, 115, 330, 115)

    pdf.setFillColor(colors.HexColor("#6B7280"))
    pdf.setFont("Helvetica", 8)
    pdf.drawString(45, 60, "This document was generated by Trade Paper AI.")
    pdf.drawString(45, 45, "For trade documentation automation.")

    pdf.showPage()
    pdf.save()
    buffer.seek(0)

    return Response(
        content=buffer.getvalue(),
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{packing_no}.pdf"'
        },
    )

@router.get("/packing-list-pdf/{packing_no}")
def packing_list_pdf(packing_no: str):
    packing_lists = load_packing_lists()

    for packing in packing_lists:
        if packing.get("packing_no") == packing_no:
            return create_packing_list_pdf(packing)

    return {"error": "Packing list not found"}