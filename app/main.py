from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
import json
from pathlib import Path
from app.routers.company import router as company_router
app = FastAPI()
BASE_DIR = Path(__file__).resolve().parent
DATA_FILE = BASE_DIR.parent / "data" / "invoices.json"
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
@app.get("/", response_class=HTMLResponse)
def root():
    with open(BASE_DIR / "static" / "index.html", "r") as f:
        return f.read()
@app.get("/company")
def company_page():
    with open(BASE_DIR / "static" / "company.html", "r") as f:
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
app.include_router(invoice_router)
app.include_router(packing_router)
app.include_router(company_router)
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
    if search:
        invoices = [
            inv for inv in invoices
            if search.lower() in inv["buyer"].lower()
            or search.lower() in inv["seller"].lower()
        ]
    html = """
    <html>
    <body style="font-family:Arial; background:#f4f7fb; padding:40px;">
    <h1>Trade Paper AI</h1>
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