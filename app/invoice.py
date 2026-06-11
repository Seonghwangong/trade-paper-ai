from io import BytesIO
from datetime import datetime
from fastapi import APIRouter, Body, Response
from fastapi.responses import HTMLResponse
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.lib import colors
import json
from pathlib import Path

COMPANY_FILE = Path("data/company.json")
INVOICE_FILE = Path("data/invoices.json")

router = APIRouter()


@router.post("/invoice")
def create_invoice(payload: dict = Body(...)):

    invoices = []

    if INVOICE_FILE.exists():
        with open(INVOICE_FILE, "r", encoding="utf-8") as f:
            invoices = json.load(f)

    invoice_no = f"INV-{len(invoices)+1:03d}"

    payload["invoice_no"] = invoice_no

    invoices.append(payload)

    with open(INVOICE_FILE, "w", encoding="utf-8") as f:
        json.dump(invoices, f, indent=4)

    return payload    

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

    total = sum(item["quantity"] * item["unit_price"] for item in items)

    buffer = BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4

    pdf.setTitle(f"Invoice {invoice_no}")

    pdf.setFillColor(colors.HexColor("#1F2937"))
    pdf.rect(0, height - 90, width, 90, fill=1, stroke=0)

    pdf.setFillColor(colors.white)
    pdf.setFont("Helvetica-Bold", 24)

    logo_path = Path("app/static/logo.png")
    if logo_path.exists():
        pdf.drawImage(
            str(logo_path),
            470,
            720,
            width=60,
            height=60,
            mask="auto"
        )

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
        line_total = item["quantity"] * item["unit_price"]

        pdf.drawString(60, y + 11, item["name"])
        pdf.drawRightString(320, y + 11, str(item["quantity"]))
        pdf.drawRightString(430, y + 11, f"USD {item['unit_price']}")
        pdf.drawRightString(540, y + 11, f"USD {line_total}")

        y -= 30

    y -= 60
    pdf.setFillColor(colors.HexColor("#111827"))
    pdf.roundRect(335, y - 10, 210, 45, 8, fill=1, stroke=0)

    pdf.setFillColor(colors.white)
    pdf.setFont("Helvetica-Bold", 13)
    pdf.drawRightString(525, y + 7, f"TOTAL: USD {total}")

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
@router.get("/invoice-list")
def invoice_list():
    invoices = []

    if INVOICE_FILE.exists():
        with open(INVOICE_FILE, "r", encoding="utf-8") as f:
            invoices = json.load(f)

    html = """
    <h1 style="font-family: Arial;">Invoice List</h1>

<table border="1" style="border-collapse: collapse; width: 90%; font-family: Arial;">
        <tr>
            <th>Invoice No</th>
            <th>Seller</th>
            <th>Buyer</th>
            <th>Items</th>
            <th>PDF</th>
        </tr>
    """

    for inv in invoices:
        if not inv.get("invoice_no"): continue
        items = inv.get("items", [])
        item_names = ", ".join([item.get("name", "") for item in items])

        html += f"""
        <tr>
            <td>{inv.get("invoice_no", "")}</td>
            <td>{inv.get("seller", "")}</td>
            <td>{inv.get("buyer", "")}</td>
            <td>{item_names}</td>
            <td><a href="/invoice-pdf/{inv.get("invoice_no", "")}">PDF</a></td>
        </tr>
        """
    html += f"""
    <p><b>Total Invoices:</b> {len(invoices)}</p>
    """
    html += """
    </table>
    """

    return HTMLResponse(html)