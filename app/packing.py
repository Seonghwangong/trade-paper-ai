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


def next_packing_no(packing_lists):
    existing_numbers = [
        int(p.get("packing_no", "PK-000").split("-")[1])
        for p in packing_lists
        if p.get("packing_no", "").startswith("PK-")
    ]

    next_no = max(existing_numbers, default=0) + 1
    return f"PK-{next_no:03d}"


@router.post("/packing-list")
def create_packing_list(payload: dict = Body(...)):
    packing_lists = load_packing_lists()
    packing_no = next_packing_no(packing_lists)

    payload["packing_no"] = packing_no

    packing_lists.append(payload)
    save_packing_lists(packing_lists)

    return payload


@router.get("/packing-list")
def packing_list(search: str = ""):
    packing_lists = load_packing_lists()
    packing_lists = list(reversed(packing_lists))

    if search:
        search_lower = search.lower()
        packing_lists = [
            p for p in packing_lists
            if search_lower in str(p.get("packing_no", "")).lower()
            or search_lower in str(p.get("invoice_no", "")).lower()
            or search_lower in str(p.get("seller", "")).lower()
            or search_lower in str(p.get("buyer", "")).lower()
            or search_lower in str(p.get("items", "")).lower()
        ]

    html = f"""
<h1 style="font-family:Arial;text-align:center;font-size:48px;margin-bottom:10px;">
Packing List
</h1>

<p style="font-family:Arial;text-align:center;font-size:16px;color:#6B7280;margin-top:0;margin-bottom:35px;">
Manage all packing documents
</p>

<div style="font-family:Arial;width:94%;margin:auto;">

<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:25px;gap:20px;">

<div style="display:flex;gap:12px;">
<a href="/packing-page">
<button style="padding:13px 22px;background:#111827;color:white;border:none;border-radius:10px;font-size:16px;">
+ New Packing List
</button>
</a>

<a href="/">
<button style="padding:13px 22px;background:#111827;color:white;border:none;border-radius:10px;font-size:16px;">
← Dashboard
</button>
</a>
</div>

<form action="/packing-list" method="get" style="display:flex;gap:10px;align-items:center;margin:0;">
<input
type="text"
name="search"
value="{search}"
placeholder="Search packing, invoice, buyer, seller or item"
style="padding:13px;width:360px;border:1px solid #D1D5DB;border-radius:10px;font-size:15px;">

<button type="submit" style="padding:13px 22px;background:#111827;color:white;border:none;border-radius:10px;font-size:15px;">
Search
</button>

<a href="/packing-list" style="color:#6B7280;font-weight:bold;">Reset</a>
</form>

</div>

<p style="font-size:18px;font-weight:bold;margin:25px 0;">
Total Packing Lists : {len(packing_lists)}
</p>

<div style="background:white;border:1px solid #E5E7EB;border-radius:16px;overflow:hidden;">

<table style="width:100%;border-collapse:collapse;table-layout:fixed;">
<tr style="background:#F9FAFB;">
<th style="padding:14px;width:9%;">Packing<br>No</th>
<th style="width:9%;">Invoice<br>No</th>
<th style="width:10%;">Seller</th>
<th style="width:10%;">Buyer</th>
<th style="width:16%;">Item</th>
<th style="width:10%;">HS<br>Code</th>
<th style="width:7%;">Carton</th>
<th style="width:7%;">Net</th>
<th style="width:7%;">Gross</th>
<th style="width:5%;">PDF</th>
<th style="width:5%;">Edit</th>
<th style="width:5%;">Delete</th>
</tr>
"""

    for packing in packing_lists:
        if not packing.get("packing_no"):
            continue

        items = packing.get("items", [])

        item_names = "<br>".join(item.get("name", "") for item in items)
        hs_codes = "<br>".join(item.get("hs_code", "") for item in items)
        cartons = "<br>".join(str(item.get("carton", "")) for item in items)
        net_weights = "<br>".join(str(item.get("net_weight", "")) for item in items)
        gross_weights = "<br>".join(str(item.get("gross_weight", "")) for item in items)

        html += f"""
<tr style="border-top:1px solid #E5E7EB;">
<td style="padding:14px;text-align:center;">{packing.get("packing_no","")}</td>
<td style="text-align:center;">{packing.get("invoice_no","")}</td>
<td style="padding:10px;word-break:break-word;">{packing.get("seller","")}</td>
<td style="padding:10px;word-break:break-word;">{packing.get("buyer","")}</td>
<td style="padding:10px;word-break:break-word;">{item_names}</td>
<td style="padding:10px;text-align:center;word-break:break-word;">{hs_codes}</td>
<td style="text-align:center;">{cartons}</td>
<td style="text-align:center;">{net_weights}</td>
<td style="text-align:center;">{gross_weights}</td>

<td style="text-align:center;">
<a href="/packing-list-pdf/{packing.get('packing_no','')}" style="color:#2563EB;font-weight:bold;text-decoration:none;">
PDF
</a>
</td>

<td style="text-align:center;">
<a href="/edit-packing/{packing.get('packing_no','')}" style="color:#111827;font-weight:bold;text-decoration:none;">
Edit
</a>
</td>

<td style="text-align:center;">
<a href="/packing-delete/{packing.get('packing_no','')}" style="color:#DC2626;font-weight:bold;text-decoration:none;">
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
    packing_no = next_packing_no(packing_lists)

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

            if not items:
                items = [{}]

            html = f"""
<h1 style="font-family:Arial;text-align:center;font-size:48px;margin-bottom:10px;">
Edit Packing List
</h1>

<p style="font-family:Arial;text-align:center;font-size:16px;color:#6B7280;margin-bottom:35px;">
Update packing list information
</p>

<div style="font-family:Arial;width:80%;margin:auto;">

<div style="background:white;border:1px solid #E5E7EB;border-radius:16px;padding:30px;margin-bottom:30px;">
<h2 style="margin-top:0;">Packing Information</h2>

<form action="/update-packing/{packing_no}" method="post">

<p>Invoice No</p>
<input type="text" name="invoice_no" value="{packing.get('invoice_no','')}" style="width:100%;padding:14px;border:1px solid #D1D5DB;border-radius:10px;">

<p>Seller</p>
<input type="text" name="seller" value="{packing.get('seller','')}" style="width:100%;padding:14px;border:1px solid #D1D5DB;border-radius:10px;">

<p>Buyer</p>
<input type="text" name="buyer" value="{packing.get('buyer','')}" style="width:100%;padding:14px;border:1px solid #D1D5DB;border-radius:10px;">

<h2>Items</h2>
"""

            for item in items:
                html += f"""
<div style="border:1px solid #E5E7EB;border-radius:14px;padding:20px;margin-bottom:20px;background:#F9FAFB;">

<p>Item Name</p>
<input type="text" name="item_name" value="{item.get('name','')}" style="width:100%;padding:14px;border:1px solid #D1D5DB;border-radius:10px;">

<p>HS Code</p>
<input type="text" name="hs_code" value="{item.get('hs_code','')}" style="width:100%;padding:14px;border:1px solid #D1D5DB;border-radius:10px;">

<p>Carton</p>
<input type="text" name="carton" value="{item.get('carton','')}" style="width:100%;padding:14px;border:1px solid #D1D5DB;border-radius:10px;">

<p>Net Weight</p>
<input type="text" name="net_weight" value="{item.get('net_weight','')}" style="width:100%;padding:14px;border:1px solid #D1D5DB;border-radius:10px;">

<p>Gross Weight</p>
<input type="text" name="gross_weight" value="{item.get('gross_weight','')}" style="width:100%;padding:14px;border:1px solid #D1D5DB;border-radius:10px;">

</div>
"""

            html += """
<br>
<button type="submit" style="width:100%;padding:16px;background:#111827;color:white;border:none;border-radius:12px;font-size:18px;">
Update Packing
</button>

</form>
</div>

<a href="/packing-list">
<button style="width:240px;padding:15px;background:#111827;color:white;border:none;border-radius:10px;font-size:18px;">
← Packing List
</button>
</a>

<a href="/">
<button style="width:240px;padding:15px;background:#111827;color:white;border:none;border-radius:10px;font-size:18px;margin-left:10px;">
← Dashboard
</button>
</a>

</div>
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

    return HTMLResponse("""
<script>
alert("Packing Updated");
window.location.href = "/packing-list";
</script>
""")


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
    return HTMLResponse("""
<h1 style="font-family:Arial;text-align:center;font-size:48px;">Packing Form</h1>
<p style="font-family:Arial;text-align:center;color:#6B7280;">Use /packing-page for the new Packing UI.</p>

<div style="font-family:Arial;width:80%;margin:auto;text-align:center;">
<a href="/packing-page">
<button style="padding:15px 25px;background:#111827;color:white;border:none;border-radius:10px;font-size:18px;">
Go to New Packing Page
</button>
</a>
</div>
""")


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