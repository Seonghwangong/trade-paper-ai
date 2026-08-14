import json

from reportlab import rl_config
from starlette.requests import Request

from app import buyer, invoice, product
from app.routers import company


def _request(account="A", path="/"):
    return Request({
        "type": "http", "method": "POST", "path": path, "headers": [],
        "trade_paper_user": {"account_id": account, "email": f"{account.lower()}@example.com"},
    })


def _invoice_payload():
    return {
        "seller": "Alpha Export", "buyer": "Samsung Trading", "currency": "USD",
        "items": [{"name": "Laptop", "quantity": 2, "unit_price": 100}],
    }


def test_master_changes_preserve_existing_snapshot_and_feed_only_new_documents(tmp_path, monkeypatch):
    files = {name: tmp_path / name for name in ("account_companies.json", "buyers.json", "products.json", "invoices.json", "users.json")}
    files["users.json"].write_text(json.dumps([{"account_id": "A"}, {"account_id": "B"}]), encoding="utf-8")
    for name in ("buyers.json", "products.json", "invoices.json"):
        files[name].write_text("[]\n", encoding="utf-8")
    files["account_companies.json"].write_text("[]\n", encoding="utf-8")
    monkeypatch.setattr(company, "ACCOUNT_COMPANIES_FILE", files["account_companies.json"])
    monkeypatch.setattr(invoice, "ACCOUNT_COMPANIES_FILE", files["account_companies.json"])
    monkeypatch.setattr(buyer, "BUYER_FILE", files["buyers.json"])
    monkeypatch.setattr(buyer, "USERS_FILE", files["users.json"])
    monkeypatch.setattr(product, "PRODUCT_FILE", files["products.json"])
    monkeypatch.setattr(product, "USERS_FILE", files["users.json"])
    monkeypatch.setattr(invoice, "INVOICE_FILE", files["invoices.json"])
    monkeypatch.setattr(invoice, "USERS_FILE", files["users.json"])

    company.save_company(_request(), {"name": "Alpha Export", "address": "Old Company Address", "email": "old@alpha.test", "phone": "111"})
    buyer.save_buyer(_request(), "Samsung Trading", "Old Buyer Address", "old@samsung.test", "KR")
    product.save_product(_request(), "Laptop", "847130", "100", "KR", "PCS")
    first = invoice.create_invoice(_request(path="/invoice"), _invoice_payload())
    assert first["seller_address"] == "Old Company Address" and first["buyer_address"] == "Old Buyer Address"
    assert first["items"][0] == {"name": "Laptop", "quantity": 2, "unit_price": 100, "hs_code": "847130", "origin": "KR", "unit": "PCS"}

    company.save_company(_request(), {"name": "Alpha Export", "address": "New Company Address", "email": "new@alpha.test", "phone": "222"})
    buyer.update_buyer(0, _request(), "Samsung Trading", "New Buyer Address", "new@samsung.test", "US")
    product.update_product(0, _request(), "Laptop", "847131", "120", "US", "SET")

    stored_first = invoice.load_invoices("A")[0]
    assert stored_first == first
    edit = invoice.edit_invoice(first["invoice_no"], _request()).body.decode()
    assert "Old Company Address" in edit and "Old Buyer Address" in edit and "847130" in edit and "KR" in edit
    assert "New Company Address" not in edit and "New Buyer Address" not in edit and "847131" not in edit
    monkeypatch.setattr(rl_config, "pageCompression", 0)
    pdf = invoice.invoice_pdf(first["invoice_no"], _request())
    assert b"Old Company Address" in pdf.body and b"Old Buyer Address" in pdf.body and b"847130" in pdf.body
    assert b"New Company Address" not in pdf.body and b"847131" not in pdf.body

    second = invoice.create_invoice(_request(path="/invoice"), _invoice_payload())
    assert second["seller_address"] == "New Company Address" and second["seller_email"] == "new@alpha.test"
    assert second["buyer_address"] == "New Buyer Address" and second["buyer_email"] == "new@samsung.test"
    assert second["items"][0]["hs_code"] == "847131" and second["items"][0]["origin"] == "US" and second["items"][0]["unit"] == "SET"

    audit = json.loads((tmp_path / "audit_log.json").read_text(encoding="utf-8"))
    updates = {(row["document_type"], row["action"]) for row in audit}
    assert {("Company", "Update"), ("Buyer", "Update"), ("Product", "Update")} <= updates
    assert all(row["account_id"] == "A" for row in audit)
    assert invoice.load_invoices("B") == []
