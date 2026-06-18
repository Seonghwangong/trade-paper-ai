from io import BytesIO
from datetime import datetime
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


@router.post("/packing-list")
def create_packing_list(payload: dict = Body(...)):
    packing_lists = load_packing_lists()

    packing_no = f"PK-{len(packing_lists) + 1:03d}"
    payload["packing_no"] = packing_no

    packing_lists.append(payload)
    save_packing_lists(packing_lists)

    return payload


@router.post("/packing-list/pdf")
def create_packing_list_pdf(payload: dict = Body(...)):
    company = load_company()

    packing_no = payload.get("packing_no", "PK-001")
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
            "carton": 1,
            "net_weight": "10KG",
            "gross_weight": "12KG"
        }
    ])

    buffer = BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4

    pdf.setTitle(f"Packing List {packing_no}")

    pdf.setFillColor(colors.HexColor("#1F2937"))
    pdf.rect(0, height - 90, width, 90, fill=1, stroke=0)

    pdf.setFillColor(colors.white)
    pdf.setFont("Helvetica-Bold", 24)

    logo_path = Path("app/static/logo.png")
    if logo_path.exists():
        pdf.drawImage(str(logo_path), 470, 720, width=60, height=60, mask="auto")

    pdf.drawString(50, height - 55, "PACKING LIST")

    pdf.setFont("Helvetica", 10)
    pdf.drawRightString(width - 50, height - 40, "Trade Paper AI")
    pdf.drawRightString(width - 50, height - 58, "Automated Trade Document")

    pdf.setFillColor(colors.black)
    pdf.setFont("Helvetica-Bold", 12)
    pdf.drawString(50, height - 125, f"Packing No: {packing_no}")
    pdf.drawString(50, height - 145, f"Invoice No: {invoice_no}")
    pdf.drawString(50, height - 165, f"Date: {today}")
    pdf.setFont("Helvetica-Bold", 11)
    pdf.drawString(50, height - 200, "SELLER")

    pdf.setFont("Helvetica", 10)
    pdf.drawString(50, height - 220, seller)
    pdf.drawString(50, height - 238, seller_address)
    pdf.drawString(50, height - 256, seller_email)

    pdf.setFont("Helvetica-Bold", 11)
    pdf.drawString(320, height - 200, "BUYER")

    pdf.setFont("Helvetica", 10)
    pdf.drawString(320, height - 220, buyer)
    pdf.drawString(320, height - 238, buyer_address)
    pdf.drawString(320, height - 256, buyer_email)

    pdf.setStrokeColor(colors.HexColor("#D1D5DB"))
    pdf.setFillColor(colors.HexColor("#F9FAFB"))
    pdf.roundRect(50, height - 255, 230, 80, 8, fill=1)
    pdf.roundRect(315, height - 255, 230, 80, 8, fill=1)

    pdf.setFillColor(colors.HexColor("#111827"))
    pdf.setFont("Helvetica-Bold", 11)
    pdf.drawString(65, height - 190, "SELLER")
    pdf.drawString(330, height - 190, "BUYER")

    pdf.setFont("Helvetica", 10)
    pdf.drawString(65, height - 215, seller)
    pdf.drawString(65, height - 230, seller_address)
    pdf.drawString(65, height - 240, seller_email)

    pdf.drawString(330, height - 215, buyer)
    pdf.drawString(330, height - 230, buyer_address)
    pdf.drawString(330, height - 240, buyer_email)

    y = height - 310
    pdf.setFillColor(colors.HexColor("#E5E7EB"))
    pdf.rect(50, y, 495, 28, fill=1, stroke=0)

    pdf.setFillColor(colors.black)
    pdf.setFont("Helvetica-Bold", 10)
    pdf.drawString(60, y + 10, "Item")
    pdf.drawRightString(260, y + 10, "Carton")
    pdf.drawRightString(390, y + 10, "Net Weight")
    pdf.drawRightString(520, y + 10, "Gross Weight")

    y -= 30
    pdf.setStrokeColor(colors.HexColor("#D1D5DB"))
    pdf.setFont("Helvetica", 10)

    for item in items:
        pdf.rect(50, y, 495, 30, fill=0)

        pdf.drawString(60, y + 11, item.get("name", ""))
        pdf.drawRightString(260, y + 11, str(item.get("carton", 1)))
        pdf.drawRightString(390, y + 11, str(item.get("net_weight", "10KG")))
        pdf.drawRightString(520, y + 11, str(item.get("gross_weight", "12KG")))

        y -= 30

    y -= 60
    pdf.setFillColor(colors.HexColor("#111827"))
    pdf.roundRect(335, y - 10, 210, 45, 8, fill=1, stroke=0)

    pdf.setFillColor(colors.white)
    pdf.setFont("Helvetica-Bold", 13)
    pdf.drawRightString(525, y + 7, "END OF PACKING LIST")

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
            "Content-Disposition": f'attachment; filename="{packing_no}.pdf"'
        }
    )


@router.get("/packing-list-pdf/{packing_no}")
def packing_list_pdf(packing_no: str):
    packing_lists = load_packing_lists()

    for packing in packing_lists:
        if packing.get("packing_no") == packing_no:
            return create_packing_list_pdf(packing)

    return {"error": "Packing list not found"}


@router.get("/packing-list")
def packing_list(search: str = ""):
    packing_lists = load_packing_lists()

    if search:

        packing_lists = [
            p for p in packing_lists
            if search.lower() in str(p.get("buyer", "")).lower()
            or search.lower() in str(p.get("seller", "")).lower()
        ]    

    html = """
<h1 style="font-family: Arial;">Packing List</h1>

<form action="/packing-list" method="get" style="margin-bottom:20px;">
<input
    type="text"
    name="search"
    placeholder="Search buyer or seller"
    style="padding:10px; width:250px;"
>
<button type="submit">Search</button>
</form>
"""

    html += f"""
    <p><b>Total Packing Lists:</b> {len(packing_lists)}</p>
    """

    html += """
    <table border="1" style="border-collapse: collapse; width: 90%; font-family: Arial;">
        <tr style="background-color:#f3f4f6;">
            <th style="padding:10px;">Packing No</th>
            <th style="padding:10px;">Invoice No</th>
            <th style="padding:10px;">Seller</th>
            <th style="padding:10px;">Buyer</th>
            <th style="padding:10px;">Items</th>
            <th style="padding:10px;">PDF</th>
            <th style="padding:10px;">Delete</th>
        </tr>
    """

    for packing in packing_lists:
        if not packing.get("packing_no"):
            continue

        items = packing.get("items", [])
        item_names = ", ".join([item.get("name", "") for item in items])

        html += f"""
        <tr>
            <td>{packing.get("packing_no", "")}</td>
            <td>{packing.get("invoice_no", "")}</td>
            <td>{packing.get("seller", "")}</td>
            <td>{packing.get("buyer", "")}</td>
            <td>{item_names}</td>
            <td><a href="/packing-list-pdf/{packing.get("packing_no", "")}">PDF</a></td>
            <td><a href="/packing-delete/{packing.get("packing_no", "")}">Delete</a></td>
        </tr>
        """

    html += """
    </table>
    """

    return HTMLResponse(html)
@router.post("/packing")
def save_packing(
    invoice_no: str = Form(""),
    seller: str = Form(""),
    buyer: str = Form(""),
    item_name: str = Form(""),
):
    packing_lists = load_packing_lists()

    next_no = len(packing_lists) + 1
    packing_no = f"PK-{next_no:03d}"

    packing = {
        "packing_no": packing_no,
        "invoice_no": invoice_no,
        "seller": seller,
        "buyer": buyer,
        "items": [
            {
                "name": item_name
            }
        ]
    }

    packing_lists.append(packing)
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

    invoice_options = """
<option value="">Select Invoice</option>
"""
    for invoice in invoices:
        invoice_no = invoice.get("invoice_no", "")
        seller = invoice.get("seller", "")
        buyer = invoice.get("buyer", "")

        if not invoice_no:
            continue

        invoice_options += f"""
        <option value="{invoice_no}" data-seller="{seller}" data-buyer="{buyer}">{invoice_no} - {buyer}</option>
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
        <p>Item Name</p>
        <input type="text" name="item_name">

        <br><br>
        <button type="submit">Save Packing</button>
</form>

<script>
document.getElementById("invoice_no").addEventListener("change", function() {{

    const selectedText =
        this.options[this.selectedIndex].text;

    const parts = selectedText.split(" - ");

    if (parts.length > 1) {{
        document.getElementById("buyer").value = parts[1];

        document.getElementById("seller").value =
    this.options[this.selectedIndex].dataset.seller;
    }}
}});
</script>

<br>
<a href="/">Back Home</a>
    <br>
    <a href="/packing-list">Back to Packing List</a>
    """

    return HTMLResponse(html)