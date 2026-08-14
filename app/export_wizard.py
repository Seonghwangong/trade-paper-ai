from __future__ import annotations

from datetime import date
import html
import json
from urllib.parse import quote

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse

from app import buyer as buyer_module, invoice, packing, product as product_module
from app import shipment, shipping_instruction as si
from app.account_company import load_account_company
from app.audit_log import record_request_audit
from app.routers.company import ACCOUNT_COMPANIES_FILE
from app.storage import locked_json_mutation, next_identifier


router = APIRouter()
TRADE_TERMS = ("EXW", "FCA", "FOB", "CFR", "CIF", "DAP", "DDP")


def _account_id(request):
    return str((request.scope.get("trade_paper_user") or {}).get("account_id", "") or "").strip()


def _owned_choice(records, field, value, label):
    target = str(value or "").strip().casefold()
    record = next((row for row in records if str(row.get(field, "") or "").strip().casefold() == target), None)
    if record is None:
        raise HTTPException(status_code=404, detail=f"{label} not found")
    return record


@router.get("/export-wizard", response_class=HTMLResponse)
def export_wizard(request: Request):
    account_id = _account_id(request)
    buyers = buyer_module.load_buyers(account_id)
    products = product_module.load_products(account_id)
    buyer_options = "".join(f'<option value="{html.escape(row["name"], quote=True)}">{html.escape(row["name"])}</option>' for row in buyers)
    product_options = "".join(f'<option value="{html.escape(row["name"], quote=True)}">{html.escape(row["name"])}</option>' for row in products)
    terms = "".join(f'<option value="{term}">{term}</option>' for term in TRADE_TERMS)
    buyer_defaults = json.dumps({row["name"]: {key: row.get(key, "") for key in ("default_currency", "default_trade_term", "default_payment_term", "preferred_carrier", "preferred_loading_port", "preferred_destination_port", "default_remarks")} for row in buyers}, ensure_ascii=False).replace("<", "\\u003c")
    empty = "" if buyers and products else '<p role="alert">Create at least one Buyer and Product before using the wizard.</p>'
    disabled = "" if buyers and products else " disabled"
    return HTMLResponse(f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Export Wizard</title><style>*{{box-sizing:border-box}}body{{margin:0;background:#f3f4f6;color:#111827;font-family:Arial}}main{{width:min(820px,calc(100% - 32px));margin:40px auto;background:#fff;padding:30px;border-radius:16px}}.grid{{display:grid;grid-template-columns:1fr 1fr;gap:16px}}label{{display:block;font-weight:700;margin-bottom:7px}}select,input,textarea,button{{width:100%;min-height:46px;padding:11px;border:1px solid #cbd5e1;border-radius:9px;font:inherit}}textarea{{min-height:90px}}button{{margin-top:22px;background:#111827;color:#fff;font-weight:800;cursor:pointer}}a{{color:#1d4ed8}}.hint{{color:#64748b;font-size:14px}}@media(max-width:650px){{.grid{{grid-template-columns:1fr}}}}</style></head><body><main><a href="/">Dashboard</a><h1>Export Wizard</h1><p>Select the core trade details to create an editable document chain.</p>{empty}<form method="post" action="/export-wizard"><div class="grid"><div><label for="buyer">Buyer</label><select id="buyer" name="buyer" required><option value="">Select Buyer</option>{buyer_options}</select></div><div><label for="product">Product</label><select id="product" name="product" required><option value="">Select Product</option>{product_options}</select></div><div><label for="trade_term">Trade Term</label><select id="trade_term" name="trade_term" required><option value="">Select Trade Term</option>{terms}</select></div><div><label for="shipment_date">Shipment Date</label><input id="shipment_date" name="shipment_date" type="date" value="{date.today().isoformat()}" required></div><div><label for="currency">Currency</label><input id="currency" name="currency" value="USD"></div><div><label for="payment_term">Payment Term</label><input id="payment_term" name="payment_term"></div><div><label for="carrier">Preferred Carrier</label><input id="carrier" name="carrier"></div><div><label for="loading_port">Loading Port</label><input id="loading_port" name="loading_port"></div><div><label for="destination_port">Destination Port</label><input id="destination_port" name="destination_port"></div><div><label for="remarks">Remarks</label><textarea id="remarks" name="remarks"></textarea></div></div><p class="hint">Buyer defaults are suggestions. You can change every value before generating documents.</p><button type="submit"{disabled}>Generate Export Documents</button></form><script>const buyerDefaults={buyer_defaults};document.getElementById("buyer").addEventListener("change",event=>{{const values=buyerDefaults[event.target.value]||{{}};const fields={{trade_term:"default_trade_term",currency:"default_currency",payment_term:"default_payment_term",carrier:"preferred_carrier",loading_port:"preferred_loading_port",destination_port:"preferred_destination_port",remarks:"default_remarks"}};for(const [id,key] of Object.entries(fields)){{document.getElementById(id).value=values[key]|| (id==="currency" ? "USD" : "");}}}});</script></main></body></html>''')


@router.post("/export-wizard", response_class=HTMLResponse)
def generate_export_documents(request: Request, buyer: str = Form(...), product: str = Form(...), trade_term: str = Form(...), shipment_date: str = Form(...), currency: str = Form(""), payment_term: str = Form(""), carrier: str = Form(""), loading_port: str = Form(""), destination_port: str = Form(""), remarks: str = Form("")):
    account_id = _account_id(request)
    buyer_record = _owned_choice(buyer_module.load_buyers(account_id), "name", buyer, "Buyer")
    product_record = _owned_choice(product_module.load_products(account_id), "name", product, "Product")
    trade_term = str(trade_term or "").strip().upper()
    if trade_term not in TRADE_TERMS:
        raise HTTPException(status_code=422, detail="Invalid Trade Term")
    try:
        date.fromisoformat(shipment_date)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Invalid Shipment Date") from exc
    company = load_account_company(account_id, ACCOUNT_COMPANIES_FILE)
    if not company.get("name"):
        raise HTTPException(status_code=422, detail="Company setup is required")

    item = {"name": product_record["name"], "hs_code": product_record.get("hs_code", ""), "origin": product_record.get("origin", ""), "unit": product_record.get("unit", ""), "quantity": 1, "unit_price": product_record.get("unit_price", "") or 0}
    optional = lambda value: value if isinstance(value, str) else ""
    currency, payment_term, carrier, loading_port, destination_port, remarks = map(optional, (currency, payment_term, carrier, loading_port, destination_port, remarks))
    invoice_record = invoice.create_invoice(request, {"invoice_date": shipment_date, "seller": company["name"], "buyer": buyer_record["name"], "trade_term": trade_term, "currency": currency or "USD", "payment_term": payment_term, "remarks": remarks, "items": [item]})
    packing_record = packing.create_packing_list(request, {"invoice_no": invoice_record["invoice_no"], "seller": company["name"], "buyer": buyer_record["name"], "items": [{**item, "carton": 1, "net_weight": "", "gross_weight": ""}]})

    instruction = si.payload_from_packing(packing_record["packing_no"], account_id)
    instruction.update({"si_date": shipment_date, "freight_terms": trade_term, "carrier": carrier, "port_of_loading": loading_port, "port_of_discharge": destination_port, "special_instructions": remarks, "account_id": account_id})
    def add_instruction(records):
        instruction["si_no"] = next_identifier(records, "si_no", "SI")
        records.append(instruction)
    locked_json_mutation(si.SI_FILE, [], add_instruction, list)
    record_request_audit(request, "Create", "Shipping Instruction", instruction["si_no"], path=si.SI_FILE.with_name("audit_log.json"))

    shipment_record = shipment.blank_shipment()
    shipment_remarks = " · ".join(value for value in (f"Trade Term: {trade_term}", payment_term and f"Payment: {payment_term}", remarks) if value)
    shipment_record.update({"shipment_date": shipment_date, "shipment_name": f'{buyer_record["name"]} · {product_record["name"]}', "customer": buyer_record["name"], "buyer": buyer_record["name"], "invoice_no": invoice_record["invoice_no"], "packing_no": packing_record["packing_no"], "si_no": instruction["si_no"], "remarks": shipment_remarks})
    shipment_record = shipment.resolve_shipment_snapshot(shipment_record, account_id, instruction=instruction, preserve_empty=False)
    def add_shipment(records):
        shipment_record["shipment_no"] = next_identifier(records, "shipment_no", "SHP")
        shipment_record["account_id"] = account_id
        records.append(shipment_record)
    locked_json_mutation(shipment.SHIPMENT_FILE, [], add_shipment, list)
    record_request_audit(request, "Create", "Shipment", shipment_record["shipment_no"], path=shipment.SHIPMENT_FILE.with_name("audit_log.json"))
    from app.onboarding import mark
    mark(account_id, "completed_at", shipment.SHIPMENT_FILE.with_name("onboarding.json"))

    links = (("Invoice", f'/edit-invoice/{quote(invoice_record["invoice_no"], safe="")}'), ("Packing List", f'/edit-packing/{quote(packing_record["packing_no"], safe="")}'), ("Shipping Instruction", f'/edit-si/{quote(instruction["si_no"], safe="")}'), ("Shipment", f'/edit-shipment/{quote(shipment_record["shipment_no"], safe="")}'))
    actions = "".join(f'<a href="{url}">Edit {label}</a>' for label, url in links)
    return HTMLResponse(f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Export Documents Created</title><style>body{{font-family:Arial;background:#f3f4f6}}main{{width:min(720px,90%);margin:50px auto;padding:32px;background:#fff;border-radius:16px}}.actions{{display:grid;gap:10px}}a{{padding:13px;background:#111827;color:#fff;border-radius:9px;text-decoration:none;font-weight:bold}}</style></head><body><main><h1>Export Documents Created</h1><p>Invoice, Packing List, Shipping Instruction, and Shipment are ready for review.</p><div class="actions">{actions}</div></main></body></html>''')
