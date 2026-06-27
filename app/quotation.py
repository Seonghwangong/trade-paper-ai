from typing import List
from pathlib import Path
from datetime import datetime
from io import BytesIO
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
import json

from fastapi import APIRouter, Form, Body
from fastapi.responses import HTMLResponse, RedirectResponse, Response

router = APIRouter()

QUOTATION_FILE = Path("data/quotations.json")
COMPANY_FILE = Path("data/company.json")

def load_company():
    if COMPANY_FILE.exists():
        with open(COMPANY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def load_quotations():
    if QUOTATION_FILE.exists():
        with open(QUOTATION_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


def save_quotations(quotations):
    with open(QUOTATION_FILE, "w", encoding="utf-8") as f:
        json.dump(quotations, f, ensure_ascii=False, indent=4)


@router.get("/quotation-list")
def quotation_list():
    quotations = load_quotations()
    quotations = list(reversed(quotations))

    html = """
<h1>Quotation List</h1>

<p><a href="/quotation-form">+ New Quotation</a></p>

<table border="1" style="border-collapse:collapse;width:100%;">
<tr>
    <th>Quotation No</th>
    <th>Buyer</th>
    <th>Total</th>
    <th>PDF</th>
    <th>Edit</th>
    <th>Delete</th>
</tr>
"""

    if not quotations:
        html += """
<tr>
    <td colspan="6" align="center">No Quotations</td>
</tr>
"""
    else:
        for quotation in quotations:
            items = quotation.get("items", [])
            total = 0

            for item in items:
                try:
                    total += float(item.get("amount", 0) or 0)
                except:
                    pass

            html += f"""
<tr>
    <td>{quotation.get("quotation_no", "")}</td>
    <td>{quotation.get("buyer_name", "")}</td>
    <td>{quotation.get("currency", "USD")} {total:g}</td>
    <td><a href="/quotation-pdf/{quotation.get('quotation_no','')}">PDF</a></td>
    <td><a href="/edit-quotation/{quotation.get('quotation_no','')}">Edit</a></td>
    <td><a href="/delete-quotation/{quotation.get('quotation_no','')}">Delete</a></td>
</tr>
"""

    html += """
</table>

<br>
<a href="/">Back Home</a>
"""

    return HTMLResponse(html)

@router.get("/quotation-form")
def quotation_form():

    html = """
<h1>Quotation Input</h1>
<form action="/quotation" method="post">

<p>Buyer</p>
<select id="buyer">
    <option value="">Select Buyer</option>
</select>

<br><br>
<input id="buyer_name" type="text" name="buyer_name" placeholder="Buyer Name">

<br><br>
<input id="buyer_address" type="text" name="buyer_address" placeholder="Address">

<br><br>
<input id="buyer_email" type="text" name="buyer_email" placeholder="Email">

<p>Seller</p>
<input id="seller" type="text">

<p>Valid Until</p>
<input type="date" name="valid_until">

<p>Currency</p>
<select>
    <option>USD</option>
    <option>EUR</option>
    <option>KRW</option>
</select>

<p>Items</p>

<div id="items_area">

<div class="item-row" style="border:1px solid #ddd;padding:10px;margin-bottom:10px;">

<p>Product</p>
<select onchange="selectProduct(this)">
    <option value="">Select Product</option>
</select>

<p>Item</p>
<input type="text" name="item_name">

<p>HS Code</p>
<input type="text" name="hs_code">

<p>Qty</p>
<input type="text" name="qty" oninput="calculateAmount(this)">

<p>Unit Price</p>
<input type="text" name="unit_price" oninput="calculateAmount(this)">

<p>Amount</p>
<input type="text" name="amount" readonly>

</div>

</div>

<button type="button" onclick="addItem()">+ Add Item</button>

<br>
<button type="submit">Save Quotation</button>
</form>
<br><br>
<a href="/quotation-list">Back to List</a>


<script>
let buyers = [];

async function loadBuyers() {
    const response = await fetch("/buyer-data");
    buyers = await response.json();

    const select = document.getElementById("buyer");

    buyers.forEach((buyer, index) => {
        select.innerHTML += `<option value="${index}">${buyer.name}</option>`;
    });
}

document.getElementById("buyer").addEventListener("change", function () {
    if (this.value === "") return;

    const buyer = buyers[this.value];

    document.getElementById("buyer_name").value = buyer.name || "";
    document.getElementById("buyer_address").value = buyer.address || "";
    document.getElementById("buyer_email").value = buyer.email || "";
});

let products = [];

async function loadProducts() {
    const response = await fetch("/product-data");
    products = await response.json();

    fillProductSelects();
}

function fillProductSelects() {
    document.querySelectorAll(".item-row select").forEach(select => {

        if (select.options.length > 1) return;

        products.forEach((product, index) => {
            select.innerHTML += `<option value="${index}">${product.name}</option>`;
        });

    });
}

function selectProduct(select) {

    const index = select.value;

    if (index === "") return;

    const row = select.closest(".item-row");

    const product = products[index];

    row.querySelector('input[name="item_name"]').value = product.name || "";
    row.querySelector('input[name="hs_code"]').value = product.hs_code || "";

    if (row.querySelector('input[name="unit_price"]')) {
        row.querySelector('input[name="unit_price"]').value = product.unit_price || "";
    }
}

loadBuyers();
function calculateAmount(input) {

    const row = input.closest(".item-row");

    const qty = parseFloat(row.querySelector('input[name="qty"]').value) || 0;

    const unitPrice = parseFloat(row.querySelector('input[name="unit_price"]').value) || 0;

    row.querySelector('input[name="amount"]').value = qty * unitPrice;
}

function addItem() {
    const area = document.getElementById("items_area");
    const firstRow = document.querySelector(".item-row");
    const newRow = firstRow.cloneNode(true);

    newRow.querySelectorAll("input").forEach(input => {
        input.value = "";
    });

    newRow.querySelector("select").selectedIndex = 0;

    area.appendChild(newRow);

    fillProductSelects();
}
loadProducts();
</script>
"""

    return HTMLResponse(html)

@router.get("/edit-quotation/{quotation_no}")
def edit_quotation(quotation_no: str):
    quotations = load_quotations()

    for quotation in quotations:
        if quotation.get("quotation_no") == quotation_no:
            items = quotation.get("items", [])

            html = f"""
<h1>Edit Quotation</h1>

<form action="/update-quotation/{quotation_no}" method="post">

<p>Buyer Name</p>
<input type="text" name="buyer_name" value="{quotation.get('buyer_name','')}">

<p>Buyer Address</p>
<input type="text" name="buyer_address" value="{quotation.get('buyer_address','')}">

<p>Buyer Email</p>
<input type="text" name="buyer_email" value="{quotation.get('buyer_email','')}">

<p>Seller</p>
<input type="text" name="seller" value="{quotation.get('seller','')}">

<p>Currency</p>
<input type="text" name="currency" value="{quotation.get('currency','USD')}">

<h3>Items</h3>

<div id="items_area">
"""

            for item in items:
                html += f"""
<div class="item-row" style="border:1px solid #ddd;padding:10px;margin-bottom:10px;">

<p>Item</p>
<input type="text" name="item_name" value="{item.get('name','')}">

<p>HS Code</p>
<input type="text" name="hs_code" value="{item.get('hs_code','')}">

<p>Qty</p>
<input type="text" name="qty" value="{item.get('qty','')}" oninput="calculateAmount(this)">

<p>Unit Price</p>
<input type="text" name="unit_price" value="{item.get('unit_price','')}" oninput="calculateAmount(this)">

<p>Amount</p>
<input type="text" name="amount" value="{item.get('amount','')}" readonly>

</div>
"""

            html += """
</div>

<br>
<button type="submit">Update Quotation</button>

</form>

<script>
function calculateAmount(input) {
    const row = input.closest(".item-row");

    const qty = parseFloat(row.querySelector('input[name="qty"]').value) || 0;
    const unitPrice = parseFloat(row.querySelector('input[name="unit_price"]').value) || 0;

    row.querySelector('input[name="amount"]').value = qty * unitPrice;
}
</script>

<br>
<a href="/quotation-list">Back to List</a>
"""

            return HTMLResponse(html)

    return HTMLResponse("Quotation Not Found")  

@router.post("/quotation")
def save_quotation(
    buyer_name: str = Form(""),
    buyer_address: str = Form(""),
    buyer_email: str = Form(""),
    seller: str = Form(""),
    valid_until: str = Form(""),
    currency: str = Form("USD"),
    item_name: List[str] = Form([]),
    hs_code: List[str] = Form([]),
    qty: List[str] = Form([]),
    unit_price: List[str] = Form([]),
    amount: List[str] = Form([]),
):
    quotations = load_quotations()

    existing_numbers = [
        int(q.get("quotation_no", "QT-000").split("-")[1])
        for q in quotations
        if q.get("quotation_no", "").startswith("QT-")
    ]

    next_no = max(existing_numbers, default=0) + 1
    quotation_no = f"QT-{next_no:03d}"

    items = []

    for i in range(len(item_name)):
        if not item_name[i].strip():
            continue

        items.append({
            "name": item_name[i],
            "hs_code": hs_code[i] if i < len(hs_code) else "",
            "qty": qty[i] if i < len(qty) else "",
            "unit_price": unit_price[i] if i < len(unit_price) else "",
            "amount": amount[i] if i < len(amount) else "",
        })

    quotation = {
        "quotation_no": quotation_no,
        "buyer_name": buyer_name,
        "buyer_address": buyer_address,
        "buyer_email": buyer_email,
        "seller": seller,
        "valid_until": valid_until,
        "currency": currency,
        "items": items,
    }

    quotations.append(quotation)
    save_quotations(quotations)

    return RedirectResponse(url="/quotation-list", status_code=303)

@router.post("/update-quotation/{quotation_no}")
def update_quotation(
    quotation_no: str,
    buyer_name: str = Form(""),
    buyer_address: str = Form(""),
    buyer_email: str = Form(""),
    seller: str = Form(""),
    currency: str = Form("USD"),
    item_name: List[str] = Form([]),
    hs_code: List[str] = Form([]),
    qty: List[str] = Form([]),
    unit_price: List[str] = Form([]),
    amount: List[str] = Form([]),
):
    quotations = load_quotations()

    items = []

    for i in range(len(item_name)):
        if not item_name[i].strip():
            continue

        items.append({
            "name": item_name[i],
            "hs_code": hs_code[i] if i < len(hs_code) else "",
            "qty": qty[i] if i < len(qty) else "",
            "unit_price": unit_price[i] if i < len(unit_price) else "",
            "amount": amount[i] if i < len(amount) else "",
        })

    for quotation in quotations:
        if quotation.get("quotation_no") == quotation_no:
            quotation["buyer_name"] = buyer_name
            quotation["buyer_address"] = buyer_address
            quotation["buyer_email"] = buyer_email
            quotation["seller"] = seller
            quotation["currency"] = currency
            quotation["items"] = items
            break

    save_quotations(quotations)

    return RedirectResponse(
        url="/quotation-list",
        status_code=303
    ) 

@router.get("/delete-quotation/{quotation_no}")
def delete_quotation(quotation_no: str):
    quotations = load_quotations()

    quotations = [
        q for q in quotations
        if q.get("quotation_no") != quotation_no
    ]

    save_quotations(quotations)

    return RedirectResponse(url="/quotation-list", status_code=303)

@router.post("/quotation/pdf")
def create_quotation_pdf(payload: dict = Body(...)):
    company = load_company()

    quotation_no = payload.get("quotation_no", "QT-001")
    valid_until = payload.get("valid_until", "")
    today = datetime.now().strftime("%Y-%m-%d")

    buyer_name = payload.get("buyer_name", "")
    buyer_address = payload.get("buyer_address", "")
    buyer_email = payload.get("buyer_email", "")
    seller = company.get("name") or payload.get("seller", "")
    seller_address = company.get("address") or payload.get("seller_address", "")
    seller_email = company.get("email") or payload.get("seller_email", "")

    items = payload.get("items", [])
    currency = payload.get("currency", "USD")
    total_amount = 0

    buffer = BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4

    pdf.setTitle(f"Quotation {quotation_no}")
    pdf.setFillColor(colors.HexColor("#111827"))
    pdf.rect(0, height - 90, width, 90, fill=1, stroke=0)

    pdf.setFillColor(colors.white)
    pdf.setFont("Helvetica-Bold", 24)
    pdf.drawString(45, height - 55, "QUOTATION")

    pdf.setFont("Helvetica", 9)
    pdf.drawRightString(width - 45, height - 38, "Trade Paper AI")
    pdf.drawRightString(width - 45, height - 55, "Automated Trade Document")

    pdf.setFillColor(colors.black)
    pdf.setFont("Helvetica-Bold", 11)
    pdf.drawString(45, height - 125, f"Quotation No: {quotation_no}")
    pdf.drawString(45, height - 143, f"Valid Until: {valid_until}")
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

    pdf.drawString(325, height - 220, buyer_name)
    pdf.drawString(325, height - 235, buyer_address)
    pdf.drawString(325, height - 250, buyer_email)

    y = height - 315
    pdf.setFillColor(colors.HexColor("#E5E7EB"))
    pdf.rect(45, y, 505, 28, fill=1, stroke=0)

    pdf.setFillColor(colors.black)
    pdf.setFont("Helvetica-Bold", 8)
    pdf.drawString(52, y + 10, "No")
    pdf.drawString(80, y + 10, "Item")
    pdf.drawString(205, y + 10, "HS Code")
    pdf.drawRightString(330, y + 10, "Qty")
    pdf.drawRightString(430, y + 10, "Unit Price")
    pdf.drawRightString(540, y + 10, "Amount")

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
            total_amount += float(item.get("amount", 0) or 0)
        except:
            pass

        pdf.rect(45, y, 505, 26, fill=0)
        pdf.drawString(52, y + 9, str(index))
        pdf.drawString(80, y + 9, str(item.get("name", ""))[:25])
        pdf.drawString(205, y + 9, str(item.get("hs_code", "")))
        pdf.drawRightString(330, y + 9, str(item.get("qty", "")))
        pdf.drawRightString(430, y + 9, str(item.get("unit_price", "")))
        pdf.drawRightString(540, y + 9, str(item.get("amount", "")))
        y -= 26

    y -= 25
    pdf.setFillColor(colors.HexColor("#111827"))
    pdf.roundRect(325, y - 5, 225, 65, 8, fill=1, stroke=0)

    pdf.setFillColor(colors.white)
    pdf.setFont("Helvetica-Bold", 10)
    pdf.drawString(340, y + 42, f"Total Amount: {currency} {total_amount:,.2f}")
    pdf.drawString(340, y + 24, f"Currency: {currency}")
    pdf.drawString(340, y + 6, f"Valid Until: {valid_until}")
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
            "Content-Disposition": f'attachment; filename="{quotation_no}.pdf"'
        },
    )

@router.get("/quotation-pdf/{quotation_no}")
def quotation_pdf(quotation_no: str):
    quotations = load_quotations()

    for quotation in quotations:
        if quotation.get("quotation_no") == quotation_no:
            return create_quotation_pdf(quotation)

    return {"error": "Quotation not found"}     