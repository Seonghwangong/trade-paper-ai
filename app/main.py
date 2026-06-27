from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
import json
from pathlib import Path
from app.routers.company import router as company_router
from app.product import router as product_router
from app.buyer import router as buyer_router
app = FastAPI()
BASE_DIR = Path(__file__).resolve().parent
DATA_FILE = BASE_DIR.parent / "data" / "invoices.json"
PACKING_FILE = BASE_DIR.parent / "data" / "packing_lists.json"
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
@app.get("/company")
def company_page():
    with open(BASE_DIR / "static" / "company.html", "r") as f:
        return HTMLResponse(f.read())

@app.get("/invoice")
def invoice_page():
    with open(BASE_DIR / "static" / "invoice.html", "r", encoding="utf-8") as f:
        return HTMLResponse(f.read())

@app.get("/invoice-page")
def invoice_page():
    with open(BASE_DIR / "static" / "invoice.html", "r") as f:
        return HTMLResponse(f.read())


@app.get("/packing-page")
def packing_page():
    with open(BASE_DIR / "static" / "packing.html", "r") as f:
        return HTMLResponse(f.read())
@app.get("/status")
def status():
    return {
        "service": "trade-paper-backend",
        "version": "0.1.0",
        "status": "ok",
    }
from app.invoice import router as invoice_router
from app.packing import router as packing_router
from app.quotation import router as quotation_router
app.include_router(invoice_router)
app.include_router(packing_router)
app.include_router(quotation_router)
app.include_router(company_router)
app.include_router(product_router)
app.include_router(buyer_router)
def load_packing_lists():
    if not PACKING_FILE.exists():
        return []

    with open(PACKING_FILE, "r", encoding="utf-8") as f:
        return json.load(f)
def load_invoices():
    if not DATA_FILE.exists():
        return []

    with open(DATA_FILE, "r") as f:
        return json.load(f)


def save_invoice(invoice_data):
    invoices = load_invoices()
    invoice_data["invoice_no"] = f"INV-{len(invoices) + 1:03d}"
    invoices.append(invoice_data)

    with open(DATA_FILE, "w") as f:
        json.dump(invoices, f, indent=2)
@app.post("/save-invoice")
def create_invoice(invoice: dict):
    save_invoice(invoice)
    return {"message": "Invoice saved successfully"}
@app.get("/invoices")
def get_invoices():
    return load_invoices()
from fastapi.responses import HTMLResponse

@app.get("/invoice-list", response_class=HTMLResponse)
def invoice_list(search: str = ""):
    invoices = load_invoices()
    invoices = sorted(
    invoices,
    key=lambda inv: inv.get("invoice_no", ""),
    reverse=True
)
    if search:
        invoices = [
            inv for inv in invoices
            if search.lower() in inv["buyer"].lower()
            or search.lower() in inv["seller"].lower()
        ]
    html = """
    <html>
    <body style="font-family:Arial; background:#f4f7fb; padding:40px;">
    <h2>Invoice Management</h2>
    <form action="/invoice-list" method="get" style="margin-bottom:20px;">
    <input
        type="text"
        name="search"
        placeholder="Search buyer or seller"
        style="padding:10px; width:250px;"
    >
    <button type="submit">Search</button>
</form>
    """

    for index, inv in enumerate(invoices):
        html += f"""
        <div style="background:white; padding:20px; margin:20px; border-radius:16px;">
           <h2>{inv['seller']} → {inv['buyer']}</h2>
           <p>🧾 Invoice No: {inv.get('invoice_no', 'N/A')}</p>
<a href="/invoice/pdf/{index}">📄 PDF</a>
            <p>📦 Item: {inv['items'][0]['name']}</p>
            <p>🔢 Quantity: {inv['items'][0]['quantity']}</p>
            <p>💵 Unit Price: USD {inv['items'][0]['unit_price']}</p>
            <a href="/delete-invoice/{index}">Delete</a>
        </div>
        """

    html += """
    </body>
    </html>
    """

    return html




    return html
@app.get("/delete-invoice/{index}")
def delete_invoice(index: int):

    invoices = load_invoices()

    if index < len(invoices):
        invoices.pop(index)

        with open(DATA_FILE, "w") as f:
            json.dump(invoices, f, indent=2)

    return {"message": "Invoice deleted"}
@app.get("/invoice/pdf/{index}")
def invoice_pdf(index: int):
    invoices = load_invoices()

    if index >= len(invoices):
        return {"error": "Invoice not found"}

    invoice = invoices[index]

    from app.invoice import create_invoice_pdf

    return create_invoice_pdf(invoice)

@app.get("/")
def home():
    print("===== HOME FUNCTION =====")
    invoices = load_invoices()
    packing_lists = load_packing_lists()

    quotations = []
    quotation_file = Path("data/quotations.json")
    if quotation_file.exists():
        with open(quotation_file, "r", encoding="utf-8") as f:
            quotations = json.load(f)

    html = f"""
<h1 style="font-family:Arial;text-align:center;font-size:48px;">Trade Paper AI Dashboard</h1>

<div style="display:flex;gap:20px;justify-content:center;font-family:Arial;margin:40px;">
    <div style="background:#111827;color:white;padding:35px;width:240px;border-radius:16px;">
        <h2>Total Invoices</h2>
        <h1>{len(invoices)}</h1>
        <a style="color:white;" href="/invoice-list">View Invoice List</a>
    </div>

    <div style="background:#111827;color:white;padding:35px;width:240px;border-radius:16px;">
        <h2>Total Packings</h2>
        <h1>{len(packing_lists)}</h1>
        <a style="color:white;" href="/packing-list">View Packing List</a>
    </div>


    <div style="background:#111827;color:white;padding:35px;width:240px;border-radius:16px;">
        <h2>Total Quotations</h2>
        <h1>{len(quotations)}</h1>
        <a style="color:white;" href="/quotation-list">View Quotation List</a>
    </div>
</div>

<div style="font-family:Arial;width:80%;margin:auto;">
    <p><a href="/company"><button style="width:100%;padding:25px;margin:10px;background:#111827;color:white;border-radius:12px;font-size:24px;">Company</button></a></p>
    <p><a href="/invoice"><button style="width:100%;padding:25px;margin:10px;background:#111827;color:white;border-radius:12px;font-size:24px;">Invoice</button></a></p>
    <p><a href="/quotation-form"><button style="width:100%;padding:25px;margin:10px;background:#111827;color:white;border-radius:12px;font-size:24px;">Quotation</button></a></p>
    <p><a href="/packing-form"><button style="width:100%;padding:25px;margin:10px;background:#111827;color:white;border-radius:12px;font-size:24px;">Packing</button></a></p>
    <p><a href="/invoice-list"><button style="width:100%;padding:25px;margin:10px;background:#111827;color:white;border-radius:12px;font-size:24px;">Invoice List</button></a></p>
    <p><a href="/quotation-list"><button style="width:100%;padding:25px;margin:10px;background:#111827;color:white;border-radius:12px;font-size:24px;">Quotation List</button></a></p>
    <p><a href="/packing-list"><button style="width:100%;padding:25px;margin:10px;background:#111827;color:white;border-radius:12px;font-size:24px;">Packing List</button></a></p>
    <p><a href="/product"><button style="width:100%;padding:25px;margin:10px;background:#111827;color:white;border-radius:12px;font-size:24px;">Product</button></a></p>
    <p><a href="/buyer"><button style="width:100%;padding:25px;margin:10px;background:#111827;color:white;border-radius:12px;font-size:24px;">Buyer</button></a></p>
</div>
"""

    return HTMLResponse(html)