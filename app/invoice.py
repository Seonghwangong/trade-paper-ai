from io import BytesIO
from datetime import datetime
from fastapi import APIRouter, Body, Response, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.lib import colors
import json
import os
from pathlib import Path

COMPANY_FILE = Path("data/company.json")
INVOICE_FILE = Path("data/invoices.json")
PACKING_FILE = Path("data/packing_lists.json")

router = APIRouter()
def load_packing_lists():
    if PACKING_FILE.exists():
        with open(PACKING_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


@router.post("/invoice")
def create_invoice(payload: dict = Body(...)):

    invoices = []

    if INVOICE_FILE.exists():
        with open(INVOICE_FILE, "r", encoding="utf-8") as f:
            invoices = json.load(f)

    if invoices:
        last_no = int(invoices[-1]["invoice_no"].split("-")[1])
        invoice_no = f"INV-{last_no + 1:03d}"
    else:
        invoice_no = "INV-001"

    payload["invoice_no"] = invoice_no

    invoices.append(payload)

    with open(INVOICE_FILE, "w", encoding="utf-8") as f:
        json.dump(invoices, f, indent=4)

    return payload  

@router.get("/invoice-data")
def invoice_data():
    if not INVOICE_FILE.exists():
        return []

    with open(INVOICE_FILE, "r", encoding="utf-8") as f:
        return json.load(f)      

@router.get("/invoice-pdf/{invoice_no}")
def invoice_pdf(invoice_no: str):

    invoices = []

    if INVOICE_FILE.exists():
        with open(INVOICE_FILE, "r", encoding="utf-8") as f:
            invoices = json.load(f)

    for inv in invoices:
        if inv.get("invoice_no") == invoice_no:
            return create_invoice_pdf(inv)
    return {"error": "Invoice not found"}

@router.post("/invoice/pdf")
def create_invoice_pdf(payload: dict = Body(...)):
    company = {}

    if COMPANY_FILE.exists():
        with open(COMPANY_FILE, "r", encoding="utf-8") as f:
            company = json.load(f)

    invoice_no = payload.get("invoice_no", "INV-001")
    today = datetime.now().strftime("%Y-%m-%d")

    buyer = payload.get("buyer", "Unknown Buyer")
    buyer_address = payload.get("buyer_address", "Dubai, UAE")
    buyer_email = payload.get("buyer_email", "sales@abctrading.com")

    seller = company.get("name") or payload.get("seller", "Unknown Seller")
    seller_address = company.get("address") or payload.get("seller_address", "Seoul, Korea")
    seller_email = company.get("email") or payload.get("seller_email", "contact@tradepaper.ai")

    items = payload.get("items", [
        {
            "name": "Sample Item",
            "quantity": 1,
            "unit_price": 0
        }
    ])

    total = sum(
    int(item.get("quantity", 1)) * float(item.get("unit_price", 0))
    for item in items
)

    buffer = BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4

    pdf.setTitle(f"Invoice {invoice_no}")

    pdf.setFillColor(colors.HexColor("#111827"))
    pdf.rect(0, height - 90, width, 90, fill=1, stroke=0)

    pdf.setFillColor(colors.white)
    pdf.setFont("Helvetica-Bold", 24)

    logo_path = Path("app/static/logo.png")
    # if logo_path.exists():
    #     pdf.drawImage(
    #         str(logo_path),
    #         470,
    #         720,
    #         width=60,
    #         height=60,
    #         mask="auto"
    #     )

    pdf.drawString(50, height - 55, "COMMERCIAL INVOICE")

    pdf.setFont("Helvetica", 10)
    pdf.drawRightString(width - 50, height - 40, "Trade Paper AI")
    pdf.drawRightString(width - 50, height - 58, "Automated Trade Document")

    pdf.setFillColor(colors.black)
    pdf.setFont("Helvetica-Bold", 12)
    pdf.drawString(50, height - 125, f"Invoice No: {invoice_no}")
    pdf.drawString(50, height - 145, f"Date: {today}")

    pdf.setStrokeColor(colors.HexColor("#D1D5DB"))
    pdf.setFillColor(colors.HexColor("#F9FAFB"))
    pdf.roundRect(50, height - 235, 230, 80, 8, fill=1)
    pdf.roundRect(315, height - 235, 230, 80, 8, fill=1)

    pdf.setFillColor(colors.HexColor("#111827"))
    pdf.setFont("Helvetica-Bold", 11)
    pdf.drawString(65, height - 170, "SELLER")
    pdf.drawString(330, height - 170, "BUYER")

    pdf.setFont("Helvetica", 10)
    pdf.drawString(65, height - 195, seller)
    pdf.drawString(65, height - 210, seller_address)
    pdf.drawString(65, height - 220, seller_email)

    pdf.drawString(330, height - 195, buyer)
    pdf.drawString(330, height - 210, buyer_address)
    pdf.drawString(330, height - 220, buyer_email)

    y = height - 290
    pdf.setFillColor(colors.HexColor("#E5E7EB"))
    pdf.rect(50, y, 495, 28, fill=1, stroke=0)

    pdf.setFillColor(colors.black)
    pdf.setFont("Helvetica-Bold", 10)
    pdf.drawString(60, y + 10, "Item")
    pdf.drawRightString(320, y + 10, "Qty")
    pdf.drawRightString(430, y + 10, "Unit Price")
    pdf.drawRightString(540, y + 10, "Total")

    y -= 30
    pdf.setStrokeColor(colors.HexColor("#D1D5DB"))
    pdf.setFont("Helvetica", 10)

    for item in items:
        pdf.rect(50, y, 495, 30, fill=0)
        quantity = int(item.get("quantity", 1))
        unit_price = float(item.get("unit_price", 0))
        line_total = quantity * unit_price
        pdf.drawString(60, y + 11, item["name"])
        pdf.drawRightString(320, y + 11, str(quantity))
        pdf.drawRightString(430, y + 11, f"USD {unit_price:,.2f}")
        pdf.drawRightString(540, y + 11, f"USD {line_total:,.2f}")
        y -= 30

    y -= 60
    pdf.setFillColor(colors.HexColor("#111827"))
    pdf.roundRect(335, y - 10, 210, 45, 8, fill=1, stroke=0)

    pdf.setFillColor(colors.white)
    pdf.setFont("Helvetica-Bold", 13)
    pdf.drawRightString(525, y + 7, f"TOTAL: USD {total:,.2f}")

    pdf.setFillColor(colors.HexColor("#6B7280"))
    pdf.setFont("Helvetica", 9)
    pdf.drawString(50, 60, "This document was generated by Trade Paper AI.")
    pdf.drawString(50, 45, "For trade documentation automation.")

    pdf.showPage()
    pdf.save()
    buffer.seek(0)

    return Response(
        content=buffer.getvalue(),
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{invoice_no}.pdf"'
        }
    )
@router.get("/edit-invoice/{invoice_no}")
def edit_invoice(invoice_no: str):  
    if not INVOICE_FILE.exists():
        return {"error": "No invoices"}
    with open(INVOICE_FILE, "r", encoding="utf-8") as f:
        invoices = json.load(f)
    for inv in invoices:
        if inv.get("invoice_no") == invoice_no:
            items = inv.get("items", [])
            item_name = items[0].get("name", "") if items else ""

            html = f"""
            <h1>Edit Invoice</h1>

            <form action="/update-invoice/{invoice_no}" method="post">
                <p>Seller</p>
                <input type="text" name="seller" value="{inv.get('seller', '')}">

                <p>Buyer</p>
                <input type="text" name="buyer" value="{inv.get('buyer', '')}">

                <p>Item Name</p>
                <input type="text" name="item_name" value="{item_name}">

                <br><br>
                <button type="submit">Update Invoice</button>
            git status</form>

            <br>
            <a href="/invoice-list">Back to Invoice List</a>
            """

            return HTMLResponse(html)

    return {"error": "Invoice not found"} 
@router.post("/update-invoice/{invoice_no}")
def update_invoice(
    invoice_no: str,
    seller: str = Form(""),
    buyer: str = Form(""),
    item_name: str = Form(""),
):
    if not INVOICE_FILE.exists():
        return {"error": "No invoices"}

    with open(INVOICE_FILE, "r", encoding="utf-8") as f:
        invoices = json.load(f)

    for inv in invoices:
        if inv.get("invoice_no") == invoice_no:
            inv["seller"] = seller
            inv["buyer"] = buyer
            inv["items"] = [{"name": item_name}]

    with open(INVOICE_FILE, "w", encoding="utf-8") as f:
        json.dump(invoices, f, indent=4)

    return RedirectResponse(url="/invoice-list", status_code=303)     
@router.get("/delete-invoice/{invoice_no}")

def delete_invoice(invoice_no: str):

    if not INVOICE_FILE.exists():
        return {"error": "No invoices"}

    with open(INVOICE_FILE, "r", encoding="utf-8") as f:
        invoices = json.load(f)

    invoices = [
        inv for inv in invoices
        if inv.get("invoice_no") != invoice_no
    ]

    with open(INVOICE_FILE, "w", encoding="utf-8") as f:
        json.dump(invoices, f, indent=4)

    return RedirectResponse(
        url="/invoice-list",
        status_code=302
    )    
@router.get("/invoice-list")
def invoice_list(search: str = ""):

    if not os.path.exists(INVOICE_FILE):
        return HTMLResponse("<h1>No Invoices</h1>")

    with open(INVOICE_FILE, "r", encoding="utf-8") as f:
        invoices = json.load(f)

    valid_invoices = [
        inv for inv in invoices
        if inv.get("invoice_no")
    ]

    valid_invoices = sorted(
        valid_invoices,
        key=lambda inv: inv.get("invoice_no", ""),
        reverse=True
    )

    if search:
        search_lower = search.lower()

        valid_invoices = [
            inv for inv in valid_invoices
            if search_lower in inv.get("invoice_no", "").lower()
            or search_lower in inv.get("buyer", "").lower()
            or search_lower in inv.get("seller", "").lower()
            or search_lower in str(inv.get("items", "")).lower()
        ]

    html = f"""
<h1 style="font-family:Arial;text-align:center;font-size:48px;margin-bottom:10px;">
Invoice List
</h1>

<p style="font-family:Arial;text-align:center;font-size:16px;color:#6B7280;margin-top:0;margin-bottom:35px;">
Manage all invoice documents
</p>

<div style="font-family:Arial;width:94%;margin:auto;">

<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:25px;gap:20px;">

<div style="display:flex;gap:12px;">
<a href="/invoice">
<button style="padding:13px 22px;background:#111827;color:white;border:none;border-radius:10px;font-size:16px;">
+ New Invoice
</button>
</a>

<a href="/">
<button style="padding:13px 22px;background:#111827;color:white;border:none;border-radius:10px;font-size:16px;">
← Dashboard
</button>
</a>
</div>

<form action="/invoice-list" method="get" style="display:flex;gap:10px;align-items:center;margin:0;">
<input
type="text"
name="search"
value="{search}"
placeholder="Search invoice, buyer, seller or item"
style="padding:13px;width:360px;border:1px solid #D1D5DB;border-radius:10px;font-size:15px;">

<button type="submit" style="padding:13px 22px;background:#111827;color:white;border:none;border-radius:10px;font-size:15px;">
Search
</button>

<a href="/invoice-list" style="color:#6B7280;font-weight:bold;">Reset</a>
</form>

</div>

<p style="font-size:18px;font-weight:bold;margin:25px 0;">
Total Invoices : {len(valid_invoices)}
</p>

<div style="background:white;border:1px solid #E5E7EB;border-radius:16px;overflow:hidden;">

<table style="width:100%;border-collapse:collapse;table-layout:fixed;">
<tr style="background:#F9FAFB;">
<th style="padding:14px;width:12%;">Invoice<br>No</th>
<th style="width:18%;">Seller</th>
<th style="width:16%;">Buyer</th>
<th style="width:22%;">Product</th>
<th style="width:12%;">Total</th>
<th style="width:7%;">PDF</th>
<th style="width:8%;">Packing</th>
<th style="width:6%;">Edit</th>
<th style="width:7%;">Delete</th>
</tr>
"""

    for inv in valid_invoices:
        packing_exists = any(p.get("invoice_no") == inv.get("invoice_no") for p in load_packing_lists())
        items = inv.get("items", [])
        item_names = "<br>".join([item.get("name", "") for item in items])

        total = sum(
            item.get("quantity", 0) * item.get("unit_price", 0)
            for item in items
        )

        html += f"""
<tr style="border-top:1px solid #E5E7EB;">
<td style="padding:14px;text-align:center;">{inv.get("invoice_no","")}</td>
<td style="padding:10px;word-break:break-word;">{inv.get("seller","")}</td>
<td style="padding:10px;word-break:break-word;">{inv.get("buyer","")}</td>
<td style="padding:10px;word-break:break-word;">{item_names}</td>
<td style="text-align:center;">USD {total:g}</td>

<td style="text-align:center;">
<a href="/invoice-pdf/{inv.get('invoice_no','')}" style="color:#2563EB;font-weight:bold;text-decoration:none;">
PDF
</a>
</td>

<td style="text-align:center;">
<a href="{'/edit-packing/' + next((p.get('packing_no') for p in load_packing_lists() if p.get('invoice_no') == inv.get('invoice_no')), '')}"
style="color:{'#2563EB' if packing_exists else '#059669'};font-weight:bold;text-decoration:none;">
{"Created" if packing_exists else "Packing"}
</a>
</td>

<td style="text-align:center;">
<a href="/edit-invoice/{inv.get('invoice_no','')}" style="color:#111827;font-weight:bold;text-decoration:none;">
Edit
</a>
</td>

<td style="text-align:center;">
<a href="/delete-invoice/{inv.get('invoice_no','')}" style="color:#DC2626;font-weight:bold;text-decoration:none;">
Delete
</a>
</td>
</tr>
"""

    html += """
</table>
</div>
</div>
"""

    return HTMLResponse(html)