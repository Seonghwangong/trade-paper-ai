import json
import re

import pytest
from pathlib import Path
from playwright.sync_api import sync_playwright
from fastapi import HTTPException
from starlette.requests import Request

from app import buyer, export_wizard, invoice, packing, product, shipment, shipping_instruction
from app.routers import company
from tests.test_auth_browser import auth_server


def request(account_id="A"):
    return Request({"type": "http", "method": "POST", "path": "/export-wizard", "headers": [], "trade_paper_user": {"account_id": account_id, "email": f"{account_id}@example.test"}})


def configure(tmp_path, monkeypatch):
    paths = {
        "users": tmp_path / "users.json", "buyers": tmp_path / "buyers.json",
        "products": tmp_path / "products.json", "companies": tmp_path / "account_companies.json",
        "invoices": tmp_path / "invoices.json", "packing": tmp_path / "packing_lists.json",
        "si": tmp_path / "shipping_instructions.json", "shipments": tmp_path / "shipments.json",
    }
    for path in paths.values():
        path.write_text("[]", encoding="utf-8")
    paths["users"].write_text(json.dumps([{"account_id": "A"}, {"account_id": "B"}]), encoding="utf-8")
    paths["buyers"].write_text(json.dumps([{"account_id": "A", "name": "Alpha Buyer", "default_currency": "EUR", "default_trade_term": "CIF", "default_payment_term": "T/T 30 days", "preferred_carrier": "Ocean A", "preferred_loading_port": "Busan", "preferred_destination_port": "Hamburg", "default_remarks": "Handle with care"}, {"account_id": "B", "name": "Other Buyer", "default_currency": "SECRET"}]), encoding="utf-8")
    paths["products"].write_text(json.dumps([{"account_id": "A", "name": "Laptop", "hs_code": "847130", "origin": "KR", "unit": "EA", "unit_price": "100"}, {"account_id": "B", "name": "Hidden Product"}]), encoding="utf-8")
    paths["companies"].write_text(json.dumps([{"account_id": "A", "name": "Seller A"}, {"account_id": "B", "name": "Seller B"}]), encoding="utf-8")
    for module in (buyer, product, invoice, packing, shipping_instruction, shipment):
        monkeypatch.setattr(module, "USERS_FILE", paths["users"], raising=False)
    monkeypatch.setattr(buyer, "BUYER_FILE", paths["buyers"])
    monkeypatch.setattr(product, "PRODUCT_FILE", paths["products"])
    monkeypatch.setattr(company, "ACCOUNT_COMPANIES_FILE", paths["companies"])
    monkeypatch.setattr(export_wizard, "ACCOUNT_COMPANIES_FILE", paths["companies"])
    monkeypatch.setattr(invoice, "INVOICE_FILE", paths["invoices"])
    monkeypatch.setattr(packing, "INVOICE_FILE", paths["invoices"])
    monkeypatch.setattr(packing, "PACKING_FILE", paths["packing"])
    monkeypatch.setattr(shipping_instruction, "INVOICE_FILE", paths["invoices"])
    monkeypatch.setattr(shipping_instruction, "PACKING_FILE", paths["packing"])
    monkeypatch.setattr(shipping_instruction, "SI_FILE", paths["si"])
    monkeypatch.setattr(shipping_instruction, "SHIPMENT_FILE", paths["shipments"])
    monkeypatch.setattr(shipment, "SHIPMENT_FILE", paths["shipments"])
    return paths


def test_export_wizard_creates_owned_chain_and_edit_links(tmp_path, monkeypatch):
    paths = configure(tmp_path, monkeypatch)
    response = export_wizard.generate_export_documents(request(), "Alpha Buyer", "Laptop", "FOB", "2026-08-20")
    assert response.status_code == 200
    assert all(value in response.body.decode() for value in ("/edit-invoice/INV-001", "/edit-packing/PK-001", "/edit-si/SI-001", "/edit-shipment/SHP-001"))
    for key in ("invoices", "packing", "si", "shipments"):
        record = json.loads(paths[key].read_text(encoding="utf-8"))[0]
        assert record["account_id"] == "A"
    shipment_record = json.loads(paths["shipments"].read_text(encoding="utf-8"))[0]
    assert (shipment_record["invoice_no"], shipment_record["packing_no"], shipment_record["si_no"]) == ("INV-001", "PK-001", "SI-001")
    assert shipment_record["items"][0]["hs_code"] == "847130"


def test_export_wizard_rejects_other_account_master_data(tmp_path, monkeypatch):
    configure(tmp_path, monkeypatch)
    with pytest.raises(HTTPException) as denied:
        export_wizard.generate_export_documents(request(), "Other Buyer", "Laptop", "FOB", "2026-08-20")
    assert denied.value.status_code == 404


def test_export_wizard_shows_only_owned_buyer_defaults_and_keeps_overrides(tmp_path, monkeypatch):
    paths = configure(tmp_path, monkeypatch)
    response = export_wizard.export_wizard(request())
    html = response.body.decode()
    for value in ("EUR", "CIF", "T/T 30 days", "Ocean A", "Busan", "Hamburg", "Handle with care"):
        assert value in html
    assert "SECRET" not in html

    export_wizard.generate_export_documents(
        request(), "Alpha Buyer", "Laptop", "FOB", "2026-08-20",
        currency="JPY", payment_term="L/C at sight", carrier="User Carrier",
        loading_port="Incheon", destination_port="Tokyo", remarks="User remark",
    )
    invoice_record = json.loads(paths["invoices"].read_text(encoding="utf-8"))[0]
    instruction = json.loads(paths["si"].read_text(encoding="utf-8"))[0]
    shipment_record = json.loads(paths["shipments"].read_text(encoding="utf-8"))[0]
    assert invoice_record["currency"] == "JPY"
    assert invoice_record["payment_term"] == "L/C at sight"
    assert instruction["carrier"] == "User Carrier"
    assert instruction["port_of_loading"] == "Incheon"
    assert instruction["port_of_discharge"] == "Tokyo"
    assert instruction["special_instructions"] == "User remark"
    assert "User remark" in shipment_record["remarks"]


@pytest.mark.parametrize("browser_name", ["chromium", "webkit"])
def test_export_wizard_browser_flow(auth_server, browser_name):
    base_url, _ = auth_server
    with sync_playwright() as playwright:
        browser_type = getattr(playwright, browser_name)
        if not Path(browser_type.executable_path).exists():
            pytest.fail(f"{browser_name} browser binary is not installed")
        browser = browser_type.launch(headless=True)
        page = browser.new_page()
        email = f"wizard-{browser_name}@example.test"
        try:
            page.goto(f"{base_url}/register")
            page.get_by_label("Company Name").fill("Wizard Seller")
            page.get_by_label("Email").fill(email)
            page.get_by_label("Password", exact=True).fill("Test1234")
            page.get_by_label("Confirm Password").fill("Test1234")
            page.get_by_role("button", name="Register").click()
            page.goto(f"{base_url}/login?next=%2Fexport-wizard")
            page.get_by_label("Email").fill(email)
            page.get_by_label("Password").fill("Test1234")
            page.get_by_role("button", name="Login").click()
            page.wait_for_url(re.compile(rf"{base_url}/company\?setup=1&next=%2Fonboarding"))
            page.locator("#address").fill("Seoul")
            page.get_by_role("button", name="Save Company").click()
            page.wait_for_url(re.compile(rf"{base_url}/onboarding\?next=%2Fexport-wizard"))
            page.get_by_role("button", name="Skip for now").click()
            page.goto(f"{base_url}/pricing")
            page.get_by_role("button", name="Choose Professional").click()
            page.wait_for_url(f"{base_url}/subscription")
            page.goto(f"{base_url}/team")
            invited_email = f"invited-{browser_name}@example.test"
            page.get_by_label("Email").fill(invited_email)
            page.get_by_label("Temporary Password").fill("Temporary123")
            page.locator('form[action="/team/invite"] select[name="role"]').select_option("Staff")
            page.get_by_role("button", name="Invite User").click()
            page.wait_for_url(f"{base_url}/team")
            page.get_by_role("cell", name=invited_email, exact=True).wait_for(state="visible")
            page.get_by_label(f"Role for {invited_email}").select_option("Viewer")
            with page.expect_response(lambda response: response.request.method == "POST" and "/team/role" in response.url) as role_response:
                page.get_by_label(f"Role for {invited_email}").locator("xpath=ancestor::form").get_by_role("button", name="Update Role").click()
            assert role_response.value.status == 303
            page.goto(f"{base_url}/audit-log?document={invited_email}")
            assert page.get_by_role("cell", name="Invite", exact=True).is_visible()
            assert page.get_by_role("cell", name="Role Change", exact=True).is_visible()
            page.goto(f"{base_url}/buyer-form")
            page.locator('input[name="name"]').fill("Wizard Buyer")
            page.get_by_label("Default Currency").fill("EUR")
            page.get_by_label("Default Trade Term").fill("CIF")
            page.get_by_label("Default Payment Term").fill("T/T 30 days")
            page.get_by_label("Preferred Carrier").fill("Wizard Carrier")
            page.get_by_label("Preferred Loading Port").fill("Busan")
            page.get_by_label("Preferred Destination Port").fill("Hamburg")
            page.get_by_label("Default Remarks").fill("Buyer recommendation")
            page.get_by_role("button", name="Save Buyer").click()
            page.goto(f"{base_url}/product-form")
            page.locator('input[name="name"]').fill("Wizard Product")
            page.locator('input[name="hs_code"]').fill("847130")
            page.locator('input[name="unit_price"]').fill("100")
            page.locator('input[name="origin"]').fill("KR")
            page.get_by_role("button", name="Save Product").click()
            page.goto(f"{base_url}/export-wizard")
            page.get_by_label("Buyer").select_option(label="Wizard Buyer")
            assert page.get_by_label("Currency", exact=True).input_value() == "EUR"
            assert page.get_by_label("Trade Term").input_value() == "CIF"
            assert page.get_by_label("Payment Term", exact=True).input_value() == "T/T 30 days"
            assert page.get_by_label("Preferred Carrier", exact=True).input_value() == "Wizard Carrier"
            assert page.get_by_label("Loading Port", exact=True).input_value() == "Busan"
            assert page.get_by_label("Destination Port", exact=True).input_value() == "Hamburg"
            assert page.get_by_label("Remarks", exact=True).input_value() == "Buyer recommendation"
            page.get_by_label("Product").select_option(label="Wizard Product")
            page.get_by_label("Trade Term").select_option("FOB")
            page.get_by_label("Currency", exact=True).fill("JPY")
            page.get_by_label("Shipment Date").fill("2026-08-20")
            page.get_by_role("button", name="Generate Export Documents").click()
            page.get_by_role("heading", name="Export Documents Created").wait_for()
            for label in ("Edit Invoice", "Edit Packing List", "Edit Shipping Instruction", "Edit Shipment"):
                assert page.get_by_role("link", name=label).is_visible()
            page.goto(f"{base_url}/")
            assert page.get_by_text("100% Complete", exact=True).is_visible()
            assert page.get_by_role("link", name="View setup guide again").is_visible()
            page.get_by_role("button", name="Logout").click()
            page.get_by_label("Email").fill(invited_email)
            page.get_by_label("Password").fill("Temporary123")
            page.get_by_role("button", name="Login").click()
            page.wait_for_url(f"{base_url}/")
            assert page.request.post(f"{base_url}/save-buyer", form={"name": "Denied"}).status == 403
            assert page.goto(f"{base_url}/team").status == 403
        finally:
            browser.close()
