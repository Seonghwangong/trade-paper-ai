from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import re
from urllib.error import URLError
from urllib.request import urlopen

import pytest
from playwright.sync_api import sync_playwright


def _free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture(scope="module")
def linkage_server(tmp_path_factory):
    """Run the real ASGI app against storage that exists only for this test."""
    data_dir = tmp_path_factory.mktemp("company-invoice-packing-browser-data")
    (data_dir / "users.json").write_text("[]\n", encoding="utf-8")
    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path.cwd())
    env["TRADE_PAPER_DATA_DIR"] = str(data_dir)
    env["TRADE_PAPER_ENV"] = "test"
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "tests.e2e_server:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        cwd=Path.cwd(),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError(f"Linkage QA server exited early: {process.stderr.read()}")
            try:
                with urlopen(f"{base_url}/login", timeout=0.5) as response:
                    if response.status == 200:
                        break
            except (OSError, URLError):
                time.sleep(0.1)
        else:
            raise RuntimeError("Linkage QA server did not become ready.")
        yield base_url, data_dir
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def _register_login_and_setup(page, base_url, browser_name, suffix):
    company_name = f"CODEX-LINK-{suffix}-COMPANY-{browser_name}"
    email = f"codex-link-{suffix.lower()}-{browser_name}@example.com"
    page.goto(f"{base_url}/register")
    page.get_by_label("Company Name").fill(company_name)
    page.get_by_label("Email").fill(email)
    page.get_by_label("Password", exact=True).fill("Test-1234")
    page.get_by_label("Confirm Password").fill("Test-1234")
    page.get_by_role("button", name="Register").click()
    page.wait_for_url(f"{base_url}/login?registered=1")
    page.get_by_label("Email").fill(email)
    page.get_by_label("Password").fill("Test-1234")
    page.get_by_role("button", name="Login").click()
    page.wait_for_url(re.compile(rf"{base_url}/company\?setup=1&next=%2Fonboarding"))
    return company_name, email


@pytest.mark.parametrize("browser_name", ["chromium", "webkit"])
def test_company_invoice_packing_linkage_and_observed_snapshot_behavior(linkage_server, browser_name):
    base_url, data_dir = linkage_server
    with sync_playwright() as playwright:
        browser_type = getattr(playwright, browser_name)
        if not Path(browser_type.executable_path).exists():
            if browser_name == "webkit":
                pytest.skip("WebKit browser binary is not installed.")
            pytest.fail("Chromium browser binary is not installed. Run: python -m playwright install chromium")

        browser = browser_type.launch(headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        company_name = buyer_name = product_name = invoice_no = packing_no = bl_no = shipment_no = ""
        try:
            # Steps 1-2: a brand-new account and Company Master over real HTTP.
            company_name, email = _register_login_and_setup(page, base_url, browser_name, "A")
            company_address = "101 CODEX Export Road, Seoul"
            company_phone = "+82-2-555-0101"
            page.locator("#address").fill(company_address)
            page.locator("#email").fill(email)
            page.locator("#phone").fill(company_phone)
            page.get_by_role("button", name="Save Company").click()
            page.wait_for_url(re.compile(rf"{base_url}/onboarding\?next=%2F"))
            page.get_by_role("button", name="Skip for now").click()
            page.wait_for_url(f"{base_url}/")
            assert page.get_by_text("Current Plan", exact=True).is_visible()
            assert page.get_by_role("heading", name="Free", exact=True).is_visible()
            page.get_by_role("link", name="Upgrade", exact=True).click()
            assert page.get_by_role("button", name="Choose Starter").count() == 0
            assert page.get_by_text("Online payment coming soon").count() == 2
            users = json.loads((data_dir / "users.json").read_text(encoding="utf-8"))
            next(user for user in users if user.get("email") == email).update({"plan": "Starter", "subscription_status": "Active"})
            (data_dir / "users.json").write_text(json.dumps(users), encoding="utf-8")
            page.goto(f"{base_url}/")
            company = page.evaluate("fetch('/company-data').then(response => response.json())")
            assert company == {
                "name": company_name,
                "address": company_address,
                "email": email,
                "phone": company_phone,
            }

            # Steps 3-4: Buyer Master and its public API snapshot.
            buyer_name = f"CODEX-LINK-A-BUYER-{browser_name}"
            buyer_address = "202 CODEX Buyer Avenue, Busan"
            buyer_email = f"buyer-link-{browser_name}@example.com"
            page.goto(f"{base_url}/buyer-form")
            page.locator('input[name="name"]').fill(buyer_name)
            page.locator('input[name="address"]').fill(buyer_address)
            page.locator('input[name="email"]').fill(buyer_email)
            page.locator('input[name="country"]').fill("KR")
            page.get_by_role("button", name="Save Buyer").click()
            page.wait_for_url(f"{base_url}/buyers")
            assert page.locator('input[list="buyer-search-options"]').is_visible()
            assert page.locator(f'#buyer-search-options option[value="{buyer_name}"]').count() == 1
            buyers = page.evaluate("fetch('/buyer-data').then(response => response.json())")
            assert buyers == [{
                "name": buyer_name,
                "address": buyer_address,
                "email": buyer_email,
                "country": "KR",
            }]

            # Steps 5-6: Product Master preserves HS Code and Unit Price.
            product_name = f"CODEX-LINK-A-PRODUCT-{browser_name}"
            page.goto(f"{base_url}/product-form")
            page.locator('input[name="name"]').fill(product_name)
            page.locator('input[name="hs_code"]').fill("847130")
            page.locator('input[name="unit_price"]').fill("125")
            page.locator('input[name="origin"]').fill("KR")
            page.locator('input[name="unit"]').fill("PCS")
            page.get_by_role("button", name="Save Product").click()
            page.wait_for_url(f"{base_url}/products")
            products = page.evaluate("fetch('/product-data').then(response => response.json())")
            assert products == [{
                "name": product_name,
                "hs_code": "847130",
                "unit_price": "125",
                "origin": "KR",
                "unit": "PCS",
            }]

            # Steps 7-9: selections fill the Invoice UI and the saved payload is observed exactly.
            page.goto(f"{base_url}/invoice")
            page.locator("#sellerSelect").select_option(label=company_name)
            assert page.locator("#seller").input_value() == company_name
            assert page.locator("#seller_address").input_value() == company_address
            assert page.locator("#seller_email").input_value() == email
            assert page.locator("#seller_phone").input_value() == company_phone
            page.locator("#buyerCompanySelect").select_option(label=buyer_name)
            assert page.locator("#buyer").input_value() == buyer_name
            assert page.locator("#buyer_address").input_value() == buyer_address
            assert page.locator("#buyer_email").input_value() == buyer_email
            page.locator("#product1").select_option(label=product_name)
            assert page.locator("#item1").input_value() == product_name
            assert page.locator("#hs1").input_value() == "847130"
            assert page.locator("#price1").input_value() == "125"
            assert page.locator("#origin1").input_value() == "KR"
            assert page.locator("#unit1").input_value() == "PCS"
            page.locator("#qty1").fill("4")
            assert page.locator("#total").text_content() == "Total: USD 500"
            page.get_by_role("button", name="Save Invoice").click()
            page.locator("#invoice-next-actions").wait_for(state="visible")
            invoices = page.evaluate("fetch('/invoice-data').then(response => response.json())")
            assert len(invoices) == 1
            invoice = invoices[0]
            invoice_no = invoice["invoice_no"]
            assert invoice["seller"] == company_name
            assert invoice["buyer"] == buyer_name
            assert invoice["buyer_address"] == buyer_address
            assert invoice["buyer_email"] == buyer_email
            assert invoice["items"] == [{
                "name": product_name,
                "hs_code": "847130",
                "quantity": 4,
                "unit_price": 125,
                "origin": "KR",
                "unit": "PCS",
            }]
            assert invoice["seller_address"] == company_address
            assert invoice["seller_email"] == email
            assert invoice["seller_phone"] == company_phone

            # Step 10: the Invoice edit form preserves every field that was actually saved.
            page.goto(f"{base_url}/edit-invoice/{invoice_no}")
            assert page.locator('input[name="seller"]').input_value() == company_name
            assert page.locator('input[name="buyer"]').input_value() == buyer_name
            assert page.locator('input[name="buyer_address"]').input_value() == buyer_address
            assert page.locator('input[name="buyer_email"]').input_value() == buyer_email
            assert page.locator('input[name="item_name"]').input_value() == product_name
            assert page.locator('input[name="hs_code"]').input_value() == "847130"
            assert page.locator('input[name="quantity"]').input_value() == "4"
            assert page.locator('input[name="unit_price"]').input_value() == "125"
            assert page.locator('input[name="origin"]').input_value() == "KR"
            assert page.locator('input[name="unit"]').input_value() == "PCS"
            assert page.locator('input[name="seller_address"]').input_value() == company_address
            assert page.locator('input[name="seller_email"]').input_value() == email
            assert page.locator('input[name="seller_phone"]').input_value() == company_phone
            page.get_by_role("button", name="Update Invoice").click()
            page.wait_for_url(f"{base_url}/invoice-list")
            updated_invoice = page.evaluate(
                "number => fetch('/invoice-data').then(response => response.json()).then(records => records.find(record => record.invoice_no === number))",
                invoice_no,
            )
            assert updated_invoice["seller"] == company_name
            assert updated_invoice["buyer"] == buyer_name
            assert updated_invoice["buyer_address"] == buyer_address
            assert updated_invoice["buyer_email"] == buyer_email
            assert updated_invoice["items"] == invoice["items"]
            assert updated_invoice["seller_address"] == company_address
            assert updated_invoice["seller_email"] == email
            assert updated_invoice["seller_phone"] == company_phone

            # Steps 11-12: Invoice -> Packing copies only supported document fields.
            page.goto(f"{base_url}/packing-page?invoice_no={invoice_no}")
            page.locator("#invoice_no").wait_for(state="visible")
            page.wait_for_function(
                "value => document.querySelector('#invoice_no').value === value",
                arg=invoice_no,
            )
            assert page.locator("#seller").input_value() == company_name
            assert page.locator("#seller_address").input_value() == company_address
            assert page.locator("#seller_email").input_value() == email
            assert page.locator("#seller_phone").input_value() == company_phone
            assert page.locator("#buyer").input_value() == buyer_name
            assert page.locator("#buyer_address").input_value() == buyer_address
            assert page.locator("#buyer_email").input_value() == buyer_email
            item_card = page.locator(".item-card").first
            assert item_card.locator(".item").input_value() == product_name
            assert item_card.locator(".quantity").input_value() == "4"
            assert item_card.locator(".hs_code").input_value() == "847130"
            assert item_card.locator(".origin").input_value() == "KR"
            assert item_card.locator(".unit").input_value() == "PCS"
            assert item_card.locator(".carton").input_value() == ""
            assert item_card.locator(".net_weight").input_value() == ""
            assert item_card.locator(".gross_weight").input_value() == ""
            assert item_card.locator(".unit_price, .amount").count() == 0
            item_card.locator(".carton").fill("2")
            item_card.locator(".net_weight").fill("40")
            item_card.locator(".gross_weight").fill("44")
            page.get_by_role("button", name="Save Packing List").click()
            page.locator("#packing-next-actions").wait_for(state="visible")
            create_si = page.get_by_role("link", name="Create Shipping Instruction")
            create_si_href = create_si.get_attribute("href")
            assert create_si_href and create_si_href.startswith("/si-form?packing_no=PK-")
            packing_no = create_si_href.split("=", 1)[1]
            create_si.click()
            page.wait_for_url(f"{base_url}/si-form?packing_no={packing_no}")
            assert page.locator('select[name="packing_no"]').input_value() == packing_no
            assert page.locator('input[name="invoice_no"]').input_value() == invoice_no
            assert page.locator('input[name="shipper"]').input_value() == company_name
            assert page.locator('input[name="consignee"]').input_value() == buyer_name
            assert page.locator('input[name="item_name"]').input_value() == product_name
            assert page.locator('input[name="quantity"]').input_value() == "4"
            assert page.locator('input[name="hs_code"]').input_value() == "847130"
            page.goto(f"{base_url}/si-form")
            page.locator('select[name="packing_no"]').select_option(packing_no)
            assert page.locator('input[name="invoice_no"]').input_value() == invoice_no
            assert page.locator('input[name="shipper"]').input_value() == company_name
            assert page.locator('input[name="consignee"]').input_value() == buyer_name
            assert page.locator('input[name="item_name"]').input_value() == product_name
            assert page.locator('input[name="quantity"]').input_value() == "4"
            assert page.locator('input[name="hs_code"]').input_value() == "847130"
            page.goto(f"{base_url}/packing-list")
            packing_edit_path = page.locator(f'tr:has-text("{invoice_no}") a', has_text="Edit").get_attribute("href")
            assert packing_edit_path
            assert packing_edit_path.rsplit("/", 1)[-1] == packing_no

            # Steps 13-14: refresh, edit, save again, refresh again; no item value is lost.
            page.reload()
            assert page.get_by_text(packing_no).is_visible()
            page.goto(f"{base_url}{packing_edit_path}")
            expected_edit_values = {
                "invoice_no": invoice_no,
                "seller": company_name,
                "seller_address": company_address,
                "seller_email": email,
                "seller_phone": company_phone,
                "buyer": buyer_name,
                "buyer_address": buyer_address,
                "buyer_email": buyer_email,
                "item_name": product_name,
                "quantity": "4",
                "hs_code": "847130",
                "origin": "KR",
                "unit": "PCS",
                "carton": "2",
                "net_weight": "40",
                "gross_weight": "44",
            }
            for field, expected in expected_edit_values.items():
                assert page.locator(f'input[name="{field}"]').input_value() == expected
            page.get_by_role("button", name="Update Packing").click()
            page.wait_for_url(f"{base_url}/packing-list")
            page.reload()
            page.goto(f"{base_url}{packing_edit_path}")
            for field, expected in expected_edit_values.items():
                assert page.locator(f'input[name="{field}"]').input_value() == expected

            stored_packings = json.loads((data_dir / "packing_lists.json").read_text(encoding="utf-8"))
            stored_packing = next(record for record in stored_packings if record.get("packing_no") == packing_no)
            assert stored_packing["seller_address"] == company_address
            assert stored_packing["seller_email"] == email
            assert stored_packing["seller_phone"] == company_phone
            assert stored_packing["buyer_address"] == buyer_address
            assert stored_packing["buyer_email"] == buyer_email
            assert [{key: value for key, value in item.items() if key != "item_id"}
                    for item in stored_packing["items"]] == [{
                "name": product_name,
                "quantity": 4,
                "hs_code": "847130",
                "origin": "KR",
                "unit": "PCS",
                "carton": "2",
                "net_weight": "40",
                "gross_weight": "44",
            }]
            assert stored_packing["items"][0]["item_id"].startswith("ITEM-")

            # Packing -> B/L copies and persists the complete party snapshot.
            page.goto(f"{base_url}/bl-form?packing_no={packing_no}")
            expected_bl_party = {
                "shipper": company_name,
                "shipper_address": company_address,
                "shipper_email": email,
                "shipper_phone": company_phone,
                "consignee": buyer_name,
                "consignee_address": buyer_address,
                "consignee_email": buyer_email,
            }
            for field, expected in expected_bl_party.items():
                assert page.locator(f'input[name="{field}"]').input_value() == expected
            page.locator('input[name="vessel"]').fill("CODEX Vessel")
            page.locator('input[name="voyage_no"]').fill("V-001")
            page.locator('input[name="port_of_loading"]').fill("Busan")
            page.locator('input[name="port_of_discharge"]').fill("Los Angeles")
            page.get_by_role("button", name="Save Bill of Lading").click()
            page.get_by_role("heading", name="Bill of Lading Saved").wait_for(state="visible")
            assert page.get_by_role("link", name="Continue to Certificate of Origin →").is_visible()
            page.goto(f"{base_url}/bl-list")
            bl_edit_path = page.locator(f'tr:has-text("{packing_no}") a', has_text="Edit").get_attribute("href")
            assert bl_edit_path
            bl_no = bl_edit_path.rsplit("/", 1)[-1]
            page.goto(f"{base_url}{bl_edit_path}")
            for field, expected in expected_bl_party.items():
                assert page.locator(f'input[name="{field}"]').input_value() == expected
            page.get_by_role("button", name="Update Bill of Lading").click()
            page.wait_for_url(f"{base_url}/bl-list")
            saved_pdf = page.request.get(f"{base_url}/bl-pdf/{bl_no}")
            assert saved_pdf.ok and saved_pdf.body().startswith(b"%PDF")
            stored_bills = json.loads((data_dir / "bills_of_lading.json").read_text(encoding="utf-8"))
            stored_bill = next(record for record in stored_bills if record.get("bl_no") == bl_no)
            for field, expected in expected_bl_party.items():
                assert stored_bill[field] == expected
            assert stored_bill["items"][0]["item_id"] == stored_packing["items"][0]["item_id"]

            page.goto(f"{base_url}/si-form?packing_no={packing_no}")
            page.get_by_role("button", name="Save Shipping Instruction").click()
            page.wait_for_url(f"{base_url}/si-list")
            si_edit_path = page.locator('a', has_text="Edit").first.get_attribute("href")
            assert si_edit_path
            si_no = si_edit_path.rsplit("/", 1)[-1]

            # Shipping Instruction -> Shipment copies and preserves references, party, and cargo snapshots.
            page.goto(f"{base_url}/shipment-form?si_no={si_no}")
            for field, expected in expected_bl_party.items():
                assert page.locator(f'input[name="{field}"]').input_value() == expected
            assert page.locator('#shipment-si').input_value() == si_no
            assert page.locator('input[name="packing_no"]').input_value() == packing_no
            assert page.locator('input[name="invoice_no"]').input_value() == invoice_no
            assert product_name in page.locator("text=Cargo Snapshot").locator("xpath=following::table[1]").inner_text()
            shipment_name = f"CODEX-LINK-A-SHIPMENT-{browser_name}"
            page.locator('input[name="shipment_name"]').fill(shipment_name)
            page.get_by_role("button", name="Save Shipment").click()
            page.get_by_role("link", name="Continue to Booking →").wait_for(state="visible")
            page.goto(f"{base_url}/shipment-list")
            shipment_edit_path = page.locator(f'tr:has-text("{shipment_name}") a', has_text="Edit").get_attribute("href")
            assert shipment_edit_path
            shipment_no = shipment_edit_path.rsplit("/", 1)[-1]
            page.goto(f"{base_url}{shipment_edit_path}")
            for field, expected in expected_bl_party.items():
                assert page.locator(f'input[name="{field}"]').input_value() == expected
            assert product_name in page.locator("text=Cargo Snapshot").locator("xpath=following::table[1]").inner_text()
            page.get_by_role("button", name="Update Shipment").click()
            page.wait_for_url(f"{base_url}/shipment-list")
            shipment_pdf = page.request.get(f"{base_url}/shipment-pdf/{shipment_no}")
            assert shipment_pdf.ok and shipment_pdf.body().startswith(b"%PDF")
            stored_shipments = json.loads((data_dir / "shipments.json").read_text(encoding="utf-8"))
            stored_shipment = next(record for record in stored_shipments if record.get("shipment_no") == shipment_no)
            for field, expected in expected_bl_party.items():
                assert stored_shipment[field] == expected
            assert stored_shipment["items"] == stored_bill["items"]

            # Shipment -> C/O copies and persists the complete party and cargo snapshot.
            page.goto(f"{base_url}/co-form?bl_no={bl_no}&shipment_no={shipment_no}")
            co_party = {
                "exporter_name": company_name,
                "exporter_address": company_address,
                "exporter_email": email,
                "exporter_phone": company_phone,
                "consignee_name": buyer_name,
                "consignee_address": buyer_address,
                "consignee_email": buyer_email,
            }
            for field, expected in co_party.items():
                assert page.locator(f'input[name="{field}"]').input_value() == expected
            for field, expected in {"item_name": product_name, "hs_code": "847130", "quantity": "4",
                                    "unit": "PCS", "carton": "2", "net_weight": "40", "gross_weight": "44"}.items():
                assert page.locator(f'input[name="{field}"]').input_value() == expected
            assert page.locator('select[name="bl_no"]').input_value() == bl_no
            for field in ("shipment_no", "invoice_no", "packing_no"):
                assert page.locator(f'input[name="{field}"]').get_attribute("readonly") is not None
            page.locator('input[name="exporter_name"]').fill("User Edited Exporter")
            co_party["exporter_name"] = "User Edited Exporter"
            page.locator('input[name="issuing_authority"]').fill("Busan Chamber")
            page.locator('input[name="certificate_type"]').fill("Preferential")
            page.locator('input[name="unit"]').fill("SET")
            page.locator('input[name="destination_country"]').fill("US")
            page.get_by_role("button", name="Save Certificate of Origin").click()
            page.get_by_role("heading", name="Certificate of Origin Saved").wait_for(state="visible")
            view_certificate = page.get_by_role("link", name="View Certificate")
            assert view_certificate.is_visible()
            assert page.get_by_role("link", name="Back to Dashboard").is_visible()
            co_no = view_certificate.get_attribute("href").rsplit("/", 1)[-1]
            page.goto(f"{base_url}/co-list")
            co_edit_path = page.locator(f'tr:has-text("{bl_no}") a', has_text="Edit").get_attribute("href")
            assert co_edit_path
            assert co_edit_path.rsplit("/", 1)[-1] == co_no
            page.goto(f"{base_url}{co_edit_path}")
            for field, expected in co_party.items():
                assert page.locator(f'input[name="{field}"]').input_value() == expected
            page.get_by_role("button", name="Update Certificate of Origin").click()
            page.wait_for_url(f"{base_url}/shipment/{shipment_no}")
            co_api = page.evaluate(f"fetch('/co-data/{co_no}').then(response => response.json())")
            assert "account_id" not in co_api and co_api["shipment_no"] == shipment_no
            assert co_api["items"][0]["gross_weight"] == "44"
            assert co_api["items"][0]["unit"] == "SET"
            assert co_api["issuing_authority"] == "Busan Chamber"
            assert co_api["certificate_type"] == "Preferential"
            co_pdf = page.request.get(f"{base_url}/co-pdf/{co_no}")
            assert co_pdf.ok and co_pdf.body().startswith(b"%PDF") and b"account_id" not in co_pdf.body()

            # Shipment Document Package resolves all six documents and downloads owned PDFs only.
            page.goto(f"{base_url}/shipment/{shipment_no}")
            package_link = page.get_by_role("link", name="Document Package")
            assert package_link.is_visible()
            package_link.click()
            page.wait_for_url(f"{base_url}/shipment/{shipment_no}/package")
            assert page.get_by_role("heading", name="Document Package").is_visible()
            for label in ("Commercial Invoice", "Packing List", "Shipping Instruction",
                          "Booking Confirmation", "Bill of Lading", "Certificate of Origin"):
                assert page.get_by_role("heading", name=label).is_visible()
            assert page.get_by_text("5 / 6 documents complete").is_visible()
            assert page.get_by_text("Booking Confirmation is missing").is_visible()
            assert page.get_by_role("link", name="View", exact=True).count() == 5
            assert page.get_by_role("link", name="Edit", exact=True).count() == 5
            package_zip = page.request.get(f"{base_url}/shipment/{shipment_no}/package.zip")
            assert package_zip.ok and package_zip.headers["content-type"].startswith("application/zip")
            page.locator(f'a[href="/send-email/document-package/{shipment_no}"]').click()
            page.wait_for_url(f"{base_url}/send-email/document-package/{shipment_no}")
            assert page.get_by_label("Recipient").input_value() == buyer_email
            assert page.get_by_label("Subject").input_value() == f"Document Package {shipment_no}"
            page.get_by_label("Message").fill("Please review the attached trade documents.")
            with page.expect_response(lambda response: response.request.method == "POST") as sent_response:
                page.get_by_role("button", name="Send Email").click()
            assert sent_response.value.status == 200, sent_response.value.text()
            assert "Failed" in sent_response.value.text()

            # Shipment Tracking is explicitly user-managed and remains visible on Detail.
            page.goto(f"{base_url}/shipment/{shipment_no}")
            assert page.get_by_text("Draft", exact=True).first.is_visible()
            page.get_by_role("link", name="Edit Tracking").click()
            page.wait_for_url(f"{base_url}/shipment/{shipment_no}/tracking")
            page.get_by_label("Shipment Status").select_option(label="In Transit")
            page.get_by_label("Container No").fill(f"CONT-{browser_name}")
            page.get_by_label("Seal No").fill(f"SEAL-{browser_name}")
            page.get_by_label("Container Type").fill("40HC")
            page.get_by_label("ETD").fill("2026-08-20")
            page.get_by_label("ETA").fill("2026-09-02")
            page.get_by_label("Actual Departure").fill("2026-08-21")
            page.get_by_label("Tracking Memo").fill("Vessel departed on schedule.")
            page.get_by_role("button", name="Save Tracking").click()
            page.wait_for_url(f"{base_url}/shipment/{shipment_no}")
            assert page.get_by_text("In Transit", exact=True).first.is_visible()
            assert page.get_by_text(f"CONT-{browser_name}", exact=True).is_visible()
            assert page.get_by_text("Vessel departed on schedule.").is_visible()
            page.get_by_role("link", name="Edit Tracking").click()
            assert page.get_by_label("Shipment Status").input_value() == "In Transit"
            assert page.get_by_label("Container No").input_value() == f"CONT-{browser_name}"

            # Account B cannot list, search, edit, or fetch Account A records.
            page.get_by_role("button", name="Logout").click()
            page.wait_for_url(f"{base_url}/login")
            _register_login_and_setup(page, base_url, browser_name, "B")
            page.get_by_role("button", name="Save Company").click()
            page.wait_for_url(re.compile(rf"{base_url}/onboarding\?next=%2F"))
            page.get_by_role("button", name="Skip for now").click()
            page.wait_for_url(f"{base_url}/")
            assert page.evaluate("fetch('/buyer-data').then(response => response.json())") == []
            assert page.evaluate("fetch('/product-data').then(response => response.json())") == []
            assert page.evaluate("fetch('/invoice-data').then(response => response.json())") == []
            page.goto(f"{base_url}/invoice-list?search={invoice_no}")
            assert page.get_by_text(invoice_no).count() == 0
            page.goto(f"{base_url}/packing-list?search={packing_no}")
            assert page.get_by_text(packing_no).count() == 0
            page.goto(f"{base_url}/bl-list?search={bl_no}")
            assert page.get_by_text(bl_no).count() == 0
            page.goto(f"{base_url}/shipment-list?search={shipment_no}")
            assert page.get_by_text(shipment_no).count() == 0
            page.goto(f"{base_url}/co-list?search={co_no}")
            assert page.get_by_text(co_no).count() == 0
            denied_invoice = page.goto(f"{base_url}/edit-invoice/{invoice_no}")
            assert denied_invoice is not None and denied_invoice.status == 404
            denied_packing = page.goto(f"{base_url}/edit-packing/{packing_no}")
            assert denied_packing is not None and denied_packing.status == 404
            denied_bl = page.goto(f"{base_url}/edit-bl/{bl_no}")
            assert denied_bl is not None and denied_bl.status == 404
            denied_shipment = page.goto(f"{base_url}/edit-shipment/{shipment_no}")
            assert denied_shipment is not None and denied_shipment.status == 404
            denied_shipment_pdf = page.goto(f"{base_url}/shipment-pdf/{shipment_no}")
            assert denied_shipment_pdf is not None and denied_shipment_pdf.status == 404
            denied_package = page.goto(f"{base_url}/shipment/{shipment_no}/package")
            assert denied_package is not None and denied_package.status == 404
            denied_tracking = page.goto(f"{base_url}/shipment/{shipment_no}/tracking")
            assert denied_tracking is not None and denied_tracking.status == 404
            denied_co = page.goto(f"{base_url}/edit-co/{co_no}")
            assert denied_co is not None and denied_co.status == 404
            denied_co_api = page.goto(f"{base_url}/co-data/{co_no}")
            assert denied_co_api is not None and denied_co_api.status == 404
            denied_co_pdf = page.goto(f"{base_url}/co-pdf/{co_no}")
            assert denied_co_pdf is not None and denied_co_pdf.status == 404
        finally:
            browser.close()
