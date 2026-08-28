from __future__ import annotations

import os
import json
import re
from pathlib import Path
import socket
import subprocess
import sys
import time
import uuid
from urllib.parse import parse_qs, urlparse
from urllib.error import URLError
from urllib.request import urlopen

import pytest
from playwright.sync_api import sync_playwright


def _free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture(scope="module")
def auth_server(tmp_path_factory):
    data_dir = tmp_path_factory.mktemp("auth-browser-data")
    (data_dir / "users.json").write_text("[]\n", encoding="utf-8")
    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path.cwd())
    env["TRADE_PAPER_DATA_DIR"] = str(data_dir)
    env["TRADE_PAPER_ADMIN_EMAILS"] = "browser-chromium@example.com,browser-webkit@example.com"
    process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "tests.e2e_server:app", "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
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
                raise RuntimeError(f"Browser QA server exited early: {process.stderr.read()}")
            try:
                with urlopen(f"{base_url}/login", timeout=0.5) as response:
                    if response.status == 200:
                        break
            except (OSError, URLError):
                time.sleep(0.1)
        else:
            raise RuntimeError("Browser QA server did not become ready.")
        yield base_url, data_dir
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


@pytest.mark.parametrize("browser_name", ["chromium", "webkit"])
def test_authentication_browser_flow(auth_server, browser_name):
    base_url, data_dir = auth_server
    with sync_playwright() as playwright:
        browser_type = getattr(playwright, browser_name)
        executable = Path(browser_type.executable_path)
        if not executable.exists():
            if browser_name == "webkit":
                pytest.skip("WebKit browser binary is not installed.")
            pytest.fail("Chromium browser binary is not installed. Run: python -m playwright install chromium")

        browser = browser_type.launch(headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 720})
        email = f"browser-{browser_name}@example.com"
        try:
            page.goto(f"{base_url}/")
            parsed = urlparse(page.url)
            assert parsed.path == "/"
            health = page.evaluate("fetch('/health').then(response => response.json())")
            assert health == {"status": "ok", "version": "3.5.0", "release": "Founding Beta"}
            assert page.get_by_role("heading", name="Create Export Documents in Minutes, Not Hours.").is_visible()
            assert page.get_by_role("link", name="Start Free").first.get_attribute("href") == "/register"
            assert page.get_by_role("link", name="Watch 15-Second Demo").get_attribute("href") == "#demo"
            page.get_by_role("link", name="Watch 15-Second Demo").click()
            assert page.locator("#demo").is_visible()
            assert page.locator(".dashboard-screenshot").evaluate("image => image.complete && image.naturalWidth > 0")
            assert page.get_by_role("link", name="Start Free to try the workflow →").get_attribute("href") == "/register"
            assert page.get_by_role("link", name="15-Second Demo", exact=True).get_attribute("href") == "#demo"
            for protected_path, login_url in (
                ("/demo", f"{base_url}/login?next=%2Fdemo"),
                ("/pricing", f"{base_url}/login?next=%2Fpricing"),
            ):
                page.goto(f"{base_url}{protected_path}")
                page.wait_for_url(login_url)
                assert page.get_by_role("heading", name="Welcome back").is_visible()
            page.goto(f"{base_url}/")
            page.get_by_role("link", name="Send Feedback").click()
            page.wait_for_url(f"{base_url}/feedback")
            page.get_by_label("Name").fill(f"Feedback User {browser_name}")
            page.get_by_label("Email").fill(f"feedback-{browser_name}@example.com")
            page.get_by_role("radio", name="5 star rating").check()
            page.get_by_label("Category").select_option(label="UI/UX")
            page.locator("#feedback").fill(f"Browser feedback {browser_name}")
            page.get_by_role("button", name="Send Feedback").click()
            page.wait_for_url(f"{base_url}/feedback/thank-you")
            assert page.get_by_role("heading", name="Thank You", exact=True).is_visible()
            assert page.get_by_text("Your feedback has been received").is_visible()
            page.goto(f"{base_url}/")
            page.get_by_role("link", name="Apply for Founding Beta").first.click()
            page.wait_for_url(f"{base_url}/founding-beta")
            page.get_by_label("Company Name").fill(f"Beta Applicant {browser_name}")
            page.get_by_label("Contact Name").fill("First Customer")
            page.get_by_label("Email").fill(f"beta-{browser_name}@example.com")
            page.get_by_label("Country").fill("Korea")
            page.get_by_label("What do you export?").fill("Industrial components")
            page.get_by_label("Monthly export documents").select_option(label="11–50")
            page.get_by_role("button", name="Apply for Founding Beta").click()
            page.wait_for_url(f"{base_url}/founding-beta/thank-you")
            assert page.get_by_role("heading", name="Thank You", exact=True).is_visible()
            assert page.get_by_text("✓ First 10 companies").is_visible()
            assert page.get_by_text("We'll contact you within 2 business days.").is_visible()
            page.goto(f"{base_url}/buyers")
            assert urlparse(page.url).path == "/login"
            page.goto(f"{base_url}/")
            assert page.get_by_role("link", name="Start Free").first.get_attribute("href") == "/register"
            assert page.get_by_role("heading", name="Ready to simplify export documentation?").is_visible()

            for protected_path in ["/buyers", "/invoice", "/admin/founding-beta", "/admin/feedback"]:
                page.goto(f"{base_url}{protected_path}")
                parsed = urlparse(page.url)
                assert parsed.path == "/login"
                assert parse_qs(parsed.query).get("next") == [protected_path]

            page.goto(f"{base_url}/login")
            assert page.locator(".auth-card").is_visible()
            page.get_by_role("link", name="Forgot password?").click()
            page.wait_for_url(f"{base_url}/forgot-password")
            page.get_by_label("Email").fill(f"missing-{browser_name}@example.com")
            page.get_by_role("button", name="Send reset instructions").click()
            assert page.get_by_text("If an account exists for that email, password reset instructions will be sent.").is_visible()
            assert "reset-password?token=" not in page.content()
            page.goto(f"{base_url}/register")
            card = page.locator(".auth-card")
            assert card.is_visible()
            assert page.get_by_label("Company Name").is_visible()
            assert page.get_by_label("Email").is_visible()
            assert page.get_by_label("Password", exact=True).is_visible()
            assert page.get_by_label("Confirm Password").is_visible()
            assert page.get_by_role("button", name="Register").is_visible()
            box = card.bounding_box()
            assert box is not None
            assert abs((box["x"] + box["width"] / 2) - 640) < 4

            page.get_by_label("Company Name").fill("Browser Test Company")
            page.get_by_label("Email").fill(email)
            page.get_by_label("Password", exact=True).fill("Test1234")
            page.get_by_label("Confirm Password").fill("Test1234")
            page.get_by_role("button", name="Register").click()
            page.wait_for_url(f"{base_url}/login?registered=1")
            assert page.get_by_text("Registration successful. Please sign in.").is_visible()
            assert not page.locator(".message.error").is_visible()

            page.goto(f"{base_url}/register")
            page.get_by_label("Company Name").fill("Browser Test Company")
            page.get_by_label("Email").fill(email.upper())
            page.get_by_label("Password", exact=True).fill("Test1234")
            page.get_by_label("Confirm Password").fill("Test1234")
            page.get_by_role("button", name="Register").click()
            page.wait_for_url(f"{base_url}/register")
            assert page.get_by_text("An account with this email already exists.").is_visible()

            page.goto(f"{base_url}/register")
            page.get_by_label("Company Name").fill("Mismatch Company")
            page.get_by_label("Email").fill(f"mismatch-{browser_name}@example.com")
            page.get_by_label("Password", exact=True).fill("Test1234")
            page.get_by_label("Confirm Password").fill("Test4321")
            page.get_by_role("button", name="Register").click()
            page.wait_for_url(f"{base_url}/register")
            assert page.get_by_text("Password and Confirm Password must match.").is_visible()

            page.goto(f"{base_url}/login?next=%2Fbuyers")
            page.get_by_label("Email").fill(email)
            page.get_by_label("Password").fill("wrong")
            page.get_by_role("button", name="Login").click()
            page.wait_for_url(f"{base_url}/login")
            assert page.get_by_text("Invalid email or password.").is_visible()

            page.get_by_label("Password").fill("Test1234")
            page.get_by_role("button", name="Login").click()
            page.wait_for_url(re.compile(rf"{base_url}/company\?setup=1&next=%2Fonboarding"))
            assert page.get_by_role("heading", name="Company Setup").is_visible()
            assert page.locator("#name").input_value() == "Browser Test Company"
            page.locator("#address").fill(f"{browser_name} account address")
            page.locator("#email").fill(email)
            page.locator("#phone").fill("010-0000-0000")
            page.get_by_role("button", name="Save Company").click()
            page.wait_for_url(re.compile(rf"{base_url}/onboarding\?next=%2Fbuyers"))
            assert page.get_by_role("progressbar").get_attribute("aria-valuenow") == "25"
            page.get_by_role("button", name="Skip for now").click()
            page.wait_for_url(f"{base_url}/buyers")
            assert page.locator(".tp-auth-user", has_text="Browser Test Company").is_visible()
            assert page.locator(".tp-auth-user", has_text=email).is_visible()
            company = page.evaluate("fetch('/company-data').then(response => response.json())")
            assert company == {
                "name": "Browser Test Company",
                "address": f"{browser_name} account address",
                "email": email,
                "phone": "010-0000-0000",
            }
            page.goto(f"{base_url}/pricing")
            assert page.get_by_role("heading", name="Free", exact=True).is_visible()
            assert page.get_by_role("button", name="Choose Starter").count() == 0
            assert page.get_by_role("link", name="Purchase details").is_visible()
            assert page.get_by_text("Contact us", exact=True).is_visible()
            users = json.loads((data_dir / "users.json").read_text(encoding="utf-8"))
            next(user for user in users if user.get("email") == email).update({"plan": "Starter", "subscription_status": "Active"})
            (data_dir / "users.json").write_text(json.dumps(users), encoding="utf-8")
            page.goto(f"{base_url}/admin/dashboard")
            assert page.get_by_role("heading", name="Admin Dashboard 2.0", exact=True).is_visible()
            assert page.get_by_role("heading", name="Overview", exact=True).is_visible()
            assert page.get_by_role("link", name="Create Invoice", exact=True).is_visible()
            users = json.loads((data_dir / "users.json").read_text(encoding="utf-8"))
            account_id = next(user["account_id"] for user in users if user.get("email") == email)
            page.goto(f"{base_url}/admin/backups?account_id={account_id}")
            assert page.get_by_role("heading", name="Backup & Restore", exact=True).is_visible()
            page.get_by_role("button", name="Create Manual Backup").click()
            page.wait_for_url(f"{base_url}/admin/backups?account_id={account_id}")
            page.locator("tr", has_text="Manual").get_by_role("link", name="Restore").click()
            assert page.get_by_role("heading", name="Confirm Restore", exact=True).is_visible()
            page.get_by_label("I understand and want to restore this backup.").check()
            page.get_by_role("button", name="Restore Backup").click()
            assert page.get_by_role("status").get_by_text("Backup restored successfully.").is_visible()
            page.goto(f"{base_url}/admin/feedback?search=Browser%20feedback%20{browser_name}")
            assert page.get_by_role("heading", name="Feedback Admin", exact=True).is_visible()
            assert page.get_by_text(f"Browser feedback {browser_name}", exact=True).is_visible()
            assert page.get_by_text(f"feedback-{browser_name}@example.com", exact=True).is_visible()
            assert page.get_by_text("UI/UX", exact=True).is_visible()
            assert page.get_by_text("5", exact=True).is_visible()
            page.goto(f"{base_url}/admin/email-readiness")
            assert page.get_by_role("heading", name="Email Readiness", exact=True).is_visible()
            assert page.get_by_text("Email Backend", exact=True).is_visible()
            assert page.get_by_text("Disabled", exact=True).is_visible()
            assert "SMTP password" not in page.content()
            page.goto(f"{base_url}/admin/founding-beta?search=Beta%20Applicant")
            assert page.get_by_role("heading", name="Founding Beta Admin", exact=True).is_visible()
            assert page.get_by_text(f"Beta Applicant {browser_name}", exact=True).is_visible()
            email_link = page.get_by_role("link", name=f"beta-{browser_name}@example.com")
            assert email_link.get_attribute("href") == f"mailto:beta-{browser_name}@example.com?subject=Trade+Paper+AI+Founding+Beta"
            page.get_by_role("button", name=f"Copy email for Beta Applicant {browser_name}").click()
            assert page.get_by_role("status").get_by_text("Email copied.").is_visible()
            status_select = page.get_by_label(f"Status for Beta Applicant {browser_name}")
            assert status_select.input_value() == "New"
            status_select.select_option(label="Contacted")
            status_select.locator("xpath=ancestor::form").get_by_role("button", name="Update").click()
            page.wait_for_url(f"{base_url}/admin/founding-beta?updated=1")
            assert page.get_by_role("status").get_by_text("Status updated successfully.").is_visible()
            assert page.get_by_label(f"Status for Beta Applicant {browser_name}").input_value() == "Contacted"
            for path, heading, expected in (
                ("/about", "About Trade Paper AI", "Founding Beta"),
                ("/release-notes", "Version 3.5.0 Release Notes", "account isolation"),
                ("/version-history", "Version History", "password recovery"),
                ("/contact", "Contact Trade Paper AI", "Questions and product inquiries"),
                ("/privacy", "Privacy Policy", "Contact page"),
                ("/terms", "Terms of Service", "Contact page"),
            ):
                page.goto(f"{base_url}{path}")
                assert page.get_by_role("heading", name=heading, exact=True).is_visible()
                assert expected in page.content()
                assert "hello@tradepaper.ai" not in page.content()
                assert "www.tradepaper.ai" not in page.content()
            page.goto(f"{base_url}/demo")
            assert page.get_by_role("heading", name="Trade Paper AI Demo").is_visible()
            assert page.get_by_text("Step 1 · Company").is_visible()
            assert page.get_by_text("Step 6 · Shipment Hub").is_visible()
            page.goto(f"{base_url}/company?demo=1")
            assert page.get_by_text("Demo Preview", exact=True).is_visible()
            assert page.locator("#name").input_value() == "Busan Comfort Trading"
            page.goto(f"{base_url}/buyer-form?demo=1")
            assert page.locator('input[name="name"]').input_value() == "Sakura Retail Co."
            assert page.locator('input[name="address"]').input_value() == "Tokyo, Japan"
            page.goto(f"{base_url}/product-form?demo=1")
            assert page.locator('input[name="name"]').input_value() == "Notebook Computer"
            assert page.locator('input[name="hs_code"]').input_value() == "847130"
            assert page.evaluate("fetch('/company-data').then(response => response.json())") == company
            assert page.evaluate("fetch('/buyer-data').then(response => response.json())") == []
            assert page.evaluate("fetch('/product-data').then(response => response.json())") == []
            customer_a_name = f"CODEX-CUSTOMER-A-{browser_name}"
            page.goto(f"{base_url}/customer")
            page.locator('input[name="company"]').fill(customer_a_name)
            page.locator('input[name="country"]').fill("KR")
            page.locator('input[name="address"]').fill("Customer A address")
            page.locator('input[name="email"]').fill(f"customer-a-{browser_name}@example.com")
            page.locator('input[name="phone"]').fill("010-1111-1111")
            page.locator('input[name="pic"]').fill("Customer A PIC")
            page.get_by_role("button", name="Save Customer").click()
            page.get_by_text(customer_a_name).wait_for(state="visible")
            customer_a_edit_path = page.get_by_role("link", name="Edit").get_attribute("href")
            customer_a_delete_path = page.get_by_role("link", name="Delete").get_attribute("href")
            assert customer_a_edit_path and customer_a_delete_path
            page.goto(f"{base_url}{customer_a_edit_path}")
            page.locator('input[name="address"]').fill("Customer A updated address")
            page.get_by_role("button", name="Update Customer").click()
            page.wait_for_url(f"{base_url}/customer")
            customer_a_data = page.evaluate("fetch('/customer-data').then(response => response.json())")
            assert customer_a_data[0]["company"] == customer_a_name
            assert customer_a_data[0]["address"] == "Customer A updated address"
            assert "account_id" not in customer_a_data[0]
            page.goto(f"{base_url}/search?q={customer_a_name}")
            assert page.get_by_role("heading", name=customer_a_name, exact=True).is_visible()
            buyer_a_name = f"CODEX-BUYER-A-{browser_name}"
            page.goto(f"{base_url}/buyer-form")
            page.locator('input[name="name"]').fill(buyer_a_name)
            page.locator('input[name="address"]').fill("Buyer A address")
            page.locator('input[name="email"]').fill(f"buyer-a-{browser_name}@example.com")
            page.locator('input[name="country"]').fill("KR")
            page.get_by_role("button", name="Save Buyer").click()
            page.wait_for_url(f"{base_url}/buyers")
            assert page.get_by_text(buyer_a_name).is_visible()
            buyer_a_edit_path = page.locator(f'tr:has-text("{buyer_a_name}") a', has_text="Edit").get_attribute("href")
            assert buyer_a_edit_path
            page.goto(f"{base_url}{buyer_a_edit_path}")
            page.locator('input[name="address"]').fill("Buyer A updated address")
            page.locator('select[name="status"]').select_option(label="Prospect")
            page.get_by_role("button", name="Update Buyer").click()
            page.wait_for_url(f"{base_url}/buyers")
            buyer_workspace_path = page.locator(f'tr:has-text("{buyer_a_name}") a', has_text="View").get_attribute("href")
            assert buyer_workspace_path
            page.goto(f"{base_url}{buyer_workspace_path}")
            assert page.get_by_role("heading", name=buyer_a_name, exact=True).is_visible()
            assert page.get_by_text("Prospect", exact=True).count() >= 1
            assert page.get_by_text("Transactions", exact=True).is_visible()
            page.goto(f"{base_url}/buyers")
            buyer_a_data = page.evaluate("fetch('/buyer-data').then(response => response.json())")
            assert buyer_a_data == [{
                "name": buyer_a_name,
                "address": "Buyer A updated address",
                "email": f"buyer-a-{browser_name}@example.com",
                "country": "KR",
            }]
            assert "account_id" not in buyer_a_data[0]
            page.goto(f"{base_url}/search?q={buyer_a_name}")
            assert page.get_by_role("heading", name=buyer_a_name, exact=True).is_visible()
            product_a_name = f"CODEX-PRODUCT-A-{browser_name}"
            page.goto(f"{base_url}/product-form")
            page.locator('input[name="name"]').fill(product_a_name)
            page.locator('input[name="hs_code"]').fill("111111")
            page.locator('input[name="unit_price"]').fill("15")
            page.locator('input[name="origin"]').fill("KR")
            page.get_by_role("button", name="Save Product").click()
            page.wait_for_url(f"{base_url}/products")
            assert page.get_by_text(product_a_name).is_visible()
            product_a_edit_path = page.locator(f'tr:has-text("{product_a_name}") a', has_text="Edit").get_attribute("href")
            assert product_a_edit_path
            page.goto(f"{base_url}{product_a_edit_path}")
            page.locator('input[name="unit_price"]').fill("25")
            page.get_by_role("button", name="Update Product").click()
            page.wait_for_url(f"{base_url}/products")
            product_a_data = page.evaluate("fetch('/product-data').then(response => response.json())")
            assert product_a_data == [{
                "name": product_a_name,
                "hs_code": "111111",
                "unit_price": "25",
                "origin": "KR",
                "unit": "",
            }]
            assert "account_id" not in product_a_data[0]
            page.goto(f"{base_url}/products?search=111111")
            assert page.get_by_text(product_a_name).is_visible()
            page.goto(f"{base_url}/search?q={product_a_name}")
            assert page.get_by_role("heading", name=product_a_name, exact=True).is_visible()
            assert page.locator("#tp-global-search-input").is_visible()
            page.locator("#tp-global-search-input").fill(product_a_name[:12])
            page.locator("#tp-global-search-suggestions option").first.wait_for(state="attached")
            assert product_a_name in page.locator("#tp-global-search-suggestions option").first.get_attribute("value")
            page.locator("#tp-global-search-input").fill(product_a_name)
            page.locator(".tp-global-search").evaluate("form => form.submit()")
            page.wait_for_url(f"{base_url}/search?q={product_a_name}")
            page.locator("#recent-searches").get_by_text(product_a_name, exact=True).wait_for(state="visible")
            page.goto(f"{base_url}/invoice?demo=1")
            page.get_by_text("Demo Preview", exact=True).wait_for(state="visible")
            assert page.locator("#seller").input_value() == "Busan Comfort Trading"
            assert page.locator("#buyer").input_value() == "Sakura Retail Co."
            assert page.locator("#item1").input_value() == "Notebook Computer"
            assert page.locator("#hs1").input_value() == "847130"
            assert page.locator("#qty1").input_value() == "1"
            assert page.locator("#price1").input_value() == "850"
            assert page.evaluate("fetch('/invoice-data').then(response => response.json())") == []
            page.goto(f"{base_url}/invoice")
            assert not page.get_by_text("Demo Preview", exact=True).is_visible()
            assert page.locator("#item1").input_value() == ""
            page.locator("#buyerCompanySelect").select_option(label=buyer_a_name)
            page.locator("#product1").select_option(label=product_a_name)
            assert page.locator("#price1").input_value() == "25"
            assert page.locator("#qty1").input_value() == ""
            assert page.locator("#total").text_content() == "Total: USD 0"
            page.locator("#qty1").fill("4")
            assert page.locator("#total").text_content() == "Total: USD 100"
            page.locator("#price1").fill("30")
            assert page.locator("#total").text_content() == "Total: USD 120"
            page.get_by_role("button", name="Save Invoice").click()
            page.locator("#invoice-next-actions").wait_for(state="visible")
            invoice_a_data = page.evaluate("fetch('/invoice-data').then(response => response.json())")
            assert len(invoice_a_data) == 1
            invoice_a_no = invoice_a_data[0]["invoice_no"]
            assert invoice_a_data[0]["buyer"] == buyer_a_name
            assert invoice_a_data[0]["items"][0]["name"] == product_a_name
            assert invoice_a_data[0]["items"][0]["quantity"] == 4
            assert invoice_a_data[0]["items"][0]["unit_price"] == 30
            assert "account_id" not in invoice_a_data[0]
            page.goto(f"{base_url}/invoice-list")
            invoice_a_edit_path = page.locator(f'tr:has-text("{invoice_a_no}") a', has_text="Edit").get_attribute("href")
            invoice_a_pdf_url = page.locator(f'tr:has-text("{invoice_a_no}") .tp-export-action.download').get_attribute("href")
            assert invoice_a_edit_path and invoice_a_pdf_url
            page.goto(f"{base_url}{invoice_a_edit_path}")
            page.locator("#product1").wait_for(state="visible")
            assert page.locator("#price1").input_value() == "30"
            assert page.locator("#total").text_content() == "Total: USD 120"
            page.locator('input[name="buyer"]').fill(f"{buyer_a_name} Updated")
            page.get_by_role("button", name="Update Invoice").click()
            page.wait_for_url(f"{base_url}/invoice-list")
            invoice_a_data = page.evaluate("fetch('/invoice-data').then(response => response.json())")
            assert invoice_a_data[0]["buyer"] == f"{buyer_a_name} Updated"
            page.goto(f"{base_url}/audit-log?document={invoice_a_no}")
            assert page.get_by_role("heading", name="Account Audit Log").is_visible()
            assert page.get_by_role("cell", name="Create", exact=True).is_visible()
            assert page.get_by_role("cell", name="Update", exact=True).is_visible()
            assert page.get_by_role("cell", name="Commercial Invoice", exact=True).count() == 2
            assert page.get_by_role("cell", name=invoice_a_no, exact=True).count() == 2
            page.goto(f"{base_url}{product_a_edit_path}")
            page.locator('input[name="hs_code"]').fill("222222")
            page.locator('input[name="origin"]').fill("JP")
            page.get_by_role("button", name="Update Product").click()
            page.wait_for_url(f"{base_url}/products")
            page.get_by_role("link", name="History").click()
            page.wait_for_url(f"{base_url}/audit-log?document=Product")
            assert page.get_by_role("cell", name="Update", exact=True).count() >= 2
            page.goto(f"{base_url}{invoice_a_edit_path}")
            assert page.locator("#hs1").input_value() == "111111"
            assert page.locator("#origin1").input_value() == "KR"
            page.goto(f"{base_url}/invoice")
            page.locator("#product1").select_option(label=product_a_name)
            assert page.locator("#hs1").input_value() == "222222"
            assert page.locator("#origin1").input_value() == "JP"
            page.goto(f"{base_url}{product_a_edit_path}")
            page.locator('input[name="hs_code"]').fill("111111")
            page.locator('input[name="origin"]').fill("KR")
            page.get_by_role("button", name="Update Product").click()
            page.wait_for_url(f"{base_url}/products")
            page.goto(f"{base_url}/invoice-list")
            invoice_a_pdf = page.request.get(invoice_a_pdf_url)
            assert invoice_a_pdf.ok
            assert invoice_a_pdf.headers["content-type"].startswith("application/pdf")
            assert invoice_a_pdf.body().startswith(b"%PDF")
            page.goto(f"{base_url}/packing-page")
            page.locator("#invoice_no").select_option(invoice_a_no)
            page.locator(".item-card .carton").first.fill("2")
            page.locator(".item-card .net_weight").first.fill("10")
            page.locator(".item-card .gross_weight").first.fill("12")
            page.get_by_role("button", name="Save Packing List").click()
            page.locator("#packing-next-actions").wait_for(state="visible")
            page.get_by_role("link", name="Back to Packing List").click()
            page.wait_for_url(f"{base_url}/packing-list")
            packing_a_edit_path = page.locator('a', has_text="Edit").first.get_attribute("href")
            packing_a_pdf_url = page.locator(".tp-export-action.download").first.get_attribute("href")
            assert packing_a_edit_path and packing_a_pdf_url
            packing_a_no = packing_a_edit_path.rsplit("/", 1)[-1]
            page.goto(f"{base_url}{packing_a_edit_path}")
            page.locator('input[name="quantity"]').fill("5")
            page.get_by_role("button", name="Update Packing").click()
            page.wait_for_url(f"{base_url}/packing-list")
            assert page.get_by_text(packing_a_no).is_visible()
            packing_a_pdf = page.request.get(packing_a_pdf_url)
            assert packing_a_pdf.ok
            assert packing_a_pdf.headers["content-type"].startswith("application/pdf")
            assert packing_a_pdf.body().startswith(b"%PDF")
            page.goto(f"{base_url}/si-form?packing_no={packing_a_no}")
            page.locator('input[name="carrier"]').fill("CODEX Carrier A")
            page.locator('input[name="vessel"]').fill("CODEX Vessel A")
            page.get_by_role("button", name="Save Shipping Instruction").click()
            page.wait_for_url(f"{base_url}/si-list")
            si_a_edit_path = page.locator('a', has_text="Edit").first.get_attribute("href")
            si_a_pdf_url = page.locator('a', has_text="PDF").first.get_attribute("href")
            assert si_a_edit_path and si_a_pdf_url
            si_a_no = si_a_edit_path.rsplit("/", 1)[-1]
            page.goto(f"{base_url}{si_a_edit_path}")
            page.locator('input[name="carrier"]').fill("CODEX Carrier A Updated")
            page.get_by_role("button", name="Update Shipping Instruction").click()
            page.wait_for_url(f"{base_url}/si-list")
            assert page.get_by_text(si_a_no).is_visible()
            si_a_pdf = page.request.get(si_a_pdf_url)
            assert si_a_pdf.ok and si_a_pdf.body().startswith(b"%PDF")
            page.goto(f"{base_url}/shipment-form")
            page.locator('#shipment-si').select_option(si_a_no)
            page.wait_for_url(f"{base_url}/shipment-form?si_no={si_a_no}")
            page.locator('input[name="shipment_name"]').fill(f"CODEX Shipment A {browser_name}")
            page.locator('input[name="buyer"]').fill(buyer_a_name)
            page.get_by_role("button", name="Save Shipment").click()
            continue_booking = page.get_by_role("link", name="Continue to Booking →")
            continue_booking.wait_for(state="visible")
            assert continue_booking.is_visible(), page.locator("body").inner_text()
            shipment_a_no = page.get_by_role("link", name="View Shipment").get_attribute("href").rsplit("/", 1)[-1]
            assert f"shipment_no={shipment_a_no}" in continue_booking.get_attribute("href")
            page.goto(f"{base_url}/shipment-list")
            shipment_a_detail_path = f"/shipment/{shipment_a_no}"
            shipment_a_edit_path = f"/edit-shipment/{shipment_a_no}"
            shipment_a_pdf_path = f"/shipment-pdf/{shipment_a_no}"
            shipment_a_data_path = f"/shipment-data/{shipment_a_no}"
            shipment_a_delete_path = page.locator("tbody tr").first.locator('a', has_text="Delete").get_attribute("href")
            assert shipment_a_delete_path
            page.goto(f"{base_url}{shipment_a_detail_path}")
            page.get_by_role("button", name="Duplicate Shipment").click()
            page.wait_for_url(re.compile(rf"{base_url}/edit-shipment/SHP-\d+"))
            duplicate_no = page.locator('input[name="shipment_no"]').input_value()
            assert duplicate_no != shipment_a_no
            assert page.locator('input[name="shipment_date"]').input_value() == time.strftime("%Y-%m-%d")
            assert page.locator('input[name="buyer"]').input_value() == buyer_a_name
            assert page.locator('input[name="shipper"]').input_value() == "Browser Test Company"
            assert page.get_by_text(product_a_name, exact=True).is_visible()
            assert page.locator('input[name="invoice_no"]').input_value() == invoice_a_no
            assert page.locator('input[name="packing_no"]').input_value() == packing_a_no
            assert page.locator('input[name="si_no"]').input_value() == si_a_no
            duplicate_delete = page.request.post(f"{base_url}/delete-shipment/{duplicate_no}")
            assert duplicate_delete.ok
            page.goto(f"{base_url}/booking-form?shipment_no={shipment_a_no}&si_no={si_a_no}&packing_no={packing_a_no}")
            page.locator('input[name="booking_reference"]').fill(f"CODEX-BOOK-A-{browser_name}")
            page.locator('input[name="carrier"]').fill("CODEX Booking Carrier A")
            page.get_by_role("button", name="Save Booking").click()
            page.get_by_role("heading", name="Booking Saved").wait_for(state="visible")
            continue_bl = page.get_by_role("link", name="Continue to Bill of Lading →")
            assert continue_bl.is_visible()
            assert f"shipment_no={shipment_a_no}" in continue_bl.get_attribute("href")
            assert f"packing_no={packing_a_no}" in continue_bl.get_attribute("href")
            continue_bl.click()
            page.wait_for_url(re.compile(rf"{base_url}/bl-form\?booking_record_no=BK-\d+.*"))
            assert page.locator('select[name="booking_record_no"]').input_value()
            assert page.locator('input[name="shipment_no"]').input_value() == shipment_a_no
            assert page.locator('input[name="packing_no"]').input_value() == packing_a_no
            assert page.locator('input[name="shipper"]').get_attribute("readonly") is not None
            assert page.locator('input[name="item_name"]').first.get_attribute("readonly") is not None
            page.locator('input[name="carrier"]').fill("CODEX Ocean Carrier A")
            page.locator('input[name="place_of_receipt"]').fill("Busan Warehouse")
            page.locator('input[name="freight_term"]').fill("Prepaid")
            page.get_by_role("button", name="Save Bill of Lading").click()
            page.get_by_role("heading", name="Bill of Lading Saved").wait_for(state="visible")
            assert page.get_by_role("link", name="Continue to Certificate of Origin →").is_visible()
            bl_a_no = __import__("json").loads((data_dir / "bills_of_lading.json").read_text(encoding="utf-8"))[-1]["bl_no"]
            page.goto(f"{base_url}/booking-list")
            booking_a_edit_path = page.locator('a', has_text="Edit").first.get_attribute("href")
            booking_a_pdf_url = page.locator('a', has_text="PDF").first.get_attribute("href")
            assert booking_a_edit_path and booking_a_pdf_url
            booking_a_no = booking_a_edit_path.rsplit("/", 1)[-1]
            page.goto(f"{base_url}{booking_a_edit_path}")
            page.locator('input[name="carrier"]').fill("CODEX Booking Carrier A Updated")
            page.get_by_role("button", name="Update Booking").click()
            page.wait_for_url(f"{base_url}{shipment_a_detail_path}")
            booking_a_pdf = page.request.get(booking_a_pdf_url)
            assert booking_a_pdf.ok and booking_a_pdf.body().startswith(b"%PDF")
            page.goto(f"{base_url}/container-form?shipment_no={shipment_a_no}&packing_no={packing_a_no}")
            page.locator('input[name="container_no"]').fill(f"CODEX-CONT-A-{browser_name}")
            page.get_by_role("button", name="Save Container Record").click()
            page.wait_for_url(f"{base_url}{shipment_a_detail_path}")
            page.goto(f"{base_url}/container-list")
            container_a_edit_path = page.locator('a', has_text="Edit").first.get_attribute("href")
            container_a_pdf_path = page.locator('a', has_text="PDF").first.get_attribute("href")
            assert container_a_edit_path and container_a_pdf_path
            container_a_no = container_a_edit_path.rsplit("/", 1)[-1]
            shipments = __import__("json").loads((data_dir / "shipments.json").read_text(encoding="utf-8"))
            for shipment in shipments:
                if shipment.get("shipment_no") == shipment_a_no:
                    shipment["invoice_no"] = ""
                    shipment["packing_no"] = ""
                    shipment["si_no"] = ""
            (data_dir / "shipments.json").write_text(__import__("json").dumps(shipments, indent=2), encoding="utf-8")

            for protected_path in ["/", "/company", "/buyers", "/products", "/invoice", "/packing-page"]:
                response = page.goto(f"{base_url}{protected_path}")
                assert response is not None and response.ok
                assert urlparse(page.url).path == protected_path
                assert page.locator(".tp-auth-user").is_visible()

            page.get_by_role("button", name="Logout").click()
            page.wait_for_url(f"{base_url}/login")
            assert not any(cookie["name"] == "trade_paper_session" for cookie in page.context.cookies())
            page.goto(f"{base_url}/")
            assert urlparse(page.url).path == "/"
            assert page.get_by_role("link", name="Start Free").first.is_visible()

            for unsafe_next in ["https%3A%2F%2Fexample.com", "%2F%2Fexample.com"]:
                page.goto(f"{base_url}/login?next={unsafe_next}")
                page.get_by_label("Email").fill(email)
                page.get_by_label("Password").fill("Test1234")
                page.get_by_role("button", name="Login").click()
                page.wait_for_url(f"{base_url}/")
                assert page.locator("header.hero h1", has_text="Trade Paper AI").is_visible()
                page.get_by_role("button", name="Logout").click()
                page.wait_for_url(f"{base_url}/login")

            page.get_by_label("Email").fill(email)
            page.get_by_label("Password").fill("Test1234")
            page.get_by_role("button", name="Login").click()
            page.wait_for_url(f"{base_url}/")
            assert page.locator("header.hero h1", has_text="Trade Paper AI").is_visible()

            users = __import__("json").loads((data_dir / "users.json").read_text(encoding="utf-8"))
            assert len([user for user in users if user.get("email") == email]) == 1
            user = next(user for user in users if user.get("email") == email)
            assert uuid.UUID(user["account_id"]).version == 4

            page.get_by_role("button", name="Logout").click()
            page.goto(f"{base_url}/register")
            second_email = f"browser-isolation-{browser_name}@example.com"
            page.get_by_label("Company Name").fill("Isolated Company B")
            page.get_by_label("Email").fill(second_email)
            page.get_by_label("Password", exact=True).fill("Test5678")
            page.get_by_label("Confirm Password").fill("Test5678")
            page.get_by_role("button", name="Register").click()
            page.wait_for_url(f"{base_url}/login?registered=1")
            page.get_by_label("Email").fill(second_email)
            page.get_by_label("Password").fill("Test5678")
            page.get_by_role("button", name="Login").click()
            page.wait_for_url(re.compile(rf"{base_url}/company\?setup=1&next=%2Fonboarding"))
            page.locator("#address").fill("Account B address")
            page.get_by_role("button", name="Save Company").click()
            page.wait_for_url(re.compile(rf"{base_url}/onboarding\?next=%2F"))
            page.get_by_role("button", name="Skip for now").click()
            page.wait_for_url(f"{base_url}/")
            company_b = page.evaluate("fetch('/company-data').then(response => response.json())")
            assert company_b["name"] == "Isolated Company B"
            assert company_b["address"] == "Account B address"
            assert "account_id" not in company_b
            page.goto(f"{base_url}/pricing")
            assert page.get_by_role("button", name="Choose Starter").count() == 0
            assert page.get_by_role("link", name="Purchase details").is_visible()
            users = json.loads((data_dir / "users.json").read_text(encoding="utf-8"))
            next(user for user in users if user.get("email") == second_email).update({"plan": "Starter", "subscription_status": "Active"})
            (data_dir / "users.json").write_text(json.dumps(users), encoding="utf-8")
            assert page.evaluate("fetch('/buyer-data').then(response => response.json())") == []
            assert page.evaluate("fetch('/product-data').then(response => response.json())") == []
            assert page.evaluate("fetch('/invoice-data').then(response => response.json())") == []
            assert page.evaluate("fetch('/customer-data').then(response => response.json())") == []
            denied_admin = page.goto(f"{base_url}/admin/dashboard")
            assert denied_admin is not None and denied_admin.status == 403
            denied_email_readiness = page.goto(f"{base_url}/admin/email-readiness")
            assert denied_email_readiness is not None and denied_email_readiness.status == 403
            page.goto(f"{base_url}/customer")
            assert page.get_by_text(customer_a_name).count() == 0
            denied = page.goto(f"{base_url}{customer_a_edit_path}")
            assert denied is not None and denied.status == 404
            denied = page.goto(f"{base_url}{customer_a_delete_path}")
            assert denied is not None and denied.status == 404
            page.goto(f"{base_url}/search?q={customer_a_name}")
            assert page.get_by_text("No matching documents found.").is_visible()
            page.goto(f"{base_url}/")
            assert page.locator('.dashboard-stat-card[href="/products"] strong').text_content() == "0"
            assert page.locator('.dashboard-stat-card[href="/invoice-list"] strong').text_content() == "0"
            assert page.locator('.dashboard-stat-card[href="/packing-list"] strong').text_content() == "0"
            assert page.locator("article.document-card").filter(has_text="Shipping Instruction").locator(".document-count").text_content() == "0"
            assert page.locator("article.document-card").filter(has_text="Booking Confirmation").locator(".document-count").text_content() == "0"
            page.goto(f"{base_url}/search?q={buyer_a_name}")
            assert page.get_by_text("No matching documents found.").is_visible()
            denied = page.goto(f"{base_url}{buyer_a_edit_path}")
            assert denied is not None and denied.status == 404
            page.goto(f"{base_url}/search?q={product_a_name}")
            assert page.get_by_text("No matching documents found.").is_visible()
            denied = page.goto(f"{base_url}{product_a_edit_path}")
            assert denied is not None and denied.status == 404
            page.goto(f"{base_url}/search?q={invoice_a_no}")
            assert page.get_by_text("No matching documents found.").is_visible()
            denied = page.goto(f"{base_url}{invoice_a_edit_path}")
            assert denied is not None and denied.status == 404
            denied_pdf = page.request.get(invoice_a_pdf_url)
            assert denied_pdf.status == 404
            page.goto(f"{base_url}/search?q={packing_a_no}")
            assert page.get_by_text("No matching documents found.").is_visible()
            denied = page.goto(f"{base_url}{packing_a_edit_path}")
            assert denied is not None and denied.status == 404
            denied_pdf = page.request.get(packing_a_pdf_url)
            assert denied_pdf.status == 404
            page.goto(f"{base_url}/search?q={si_a_no}")
            assert page.get_by_text("No matching documents found.").is_visible()
            denied = page.goto(f"{base_url}{si_a_edit_path}")
            assert denied is not None and denied.status == 404
            denied_pdf = page.request.get(si_a_pdf_url)
            assert denied_pdf.status == 404
            page.goto(f"{base_url}/search?q={booking_a_no}")
            assert page.get_by_text("No matching documents found.").is_visible()
            denied = page.goto(f"{base_url}{booking_a_edit_path}")
            assert denied is not None and denied.status == 404
            denied_pdf = page.request.get(booking_a_pdf_url)
            assert denied_pdf.status == 404

            buyer_b_name = f"CODEX-BUYER-B-{browser_name}"
            page.goto(f"{base_url}/buyer-form")
            page.locator('input[name="name"]').fill(buyer_b_name)
            page.locator('input[name="address"]').fill("Buyer B address")
            page.locator('input[name="email"]').fill(f"buyer-b-{browser_name}@example.com")
            page.locator('input[name="country"]').fill("US")
            page.get_by_role("button", name="Save Buyer").click()
            page.wait_for_url(f"{base_url}/buyers")
            buyer_b_edit_path = page.locator(f'tr:has-text("{buyer_b_name}") a', has_text="Edit").get_attribute("href")
            assert buyer_b_edit_path
            page.goto(f"{base_url}/search?q={buyer_b_name}")
            buyer_b_result = page.locator("article.result-card", has_text=buyer_b_name)
            buyer_b_detail_path = buyer_b_result.get_by_role("link", name="Open", exact=True).get_attribute("href")
            assert buyer_b_detail_path == buyer_b_edit_path.replace("/edit-buyer/", "/buyer/")
            assert buyer_b_detail_path != buyer_a_edit_path.replace("/edit-buyer/", "/buyer/")
            buyer_b_result.get_by_role("link", name="Open", exact=True).click()
            page.wait_for_url(f"{base_url}{buyer_b_detail_path}")
            assert page.get_by_role("heading", name=buyer_b_name, exact=True).is_visible()
            assert page.get_by_text(buyer_a_name, exact=True).count() == 0
            product_b_name = f"CODEX-PRODUCT-B-{browser_name}"
            page.goto(f"{base_url}/product-form")
            page.locator('input[name="name"]').fill(product_b_name)
            page.locator('input[name="hs_code"]').fill("222222")
            page.locator('input[name="unit_price"]').fill("35")
            page.locator('input[name="origin"]').fill("US")
            page.get_by_role("button", name="Save Product").click()
            page.wait_for_url(f"{base_url}/products")
            product_b_edit_path = page.locator(f'tr:has-text("{product_b_name}") a', has_text="Edit").get_attribute("href")
            assert product_b_edit_path
            page.goto(f"{base_url}/invoice")
            page.locator("#buyerCompanySelect").select_option(label=buyer_b_name)
            page.locator("#product1").select_option(label=product_b_name)
            page.locator("#qty1").fill("2")
            page.locator("#price1").fill("35")
            page.get_by_role("button", name="Save Invoice").click()
            page.locator("#invoice-next-actions").wait_for(state="visible")
            invoice_b_data = page.evaluate("fetch('/invoice-data').then(response => response.json())")
            assert len(invoice_b_data) == 1
            invoice_b_no = invoice_b_data[0]["invoice_no"]
            page.goto(f"{base_url}/invoice-list")
            invoice_b_edit_path = page.locator(f'tr:has-text("{invoice_b_no}") a', has_text="Edit").get_attribute("href")
            assert invoice_b_edit_path
            page.goto(f"{base_url}/packing-page")
            page.locator("#invoice_no").select_option(invoice_b_no)
            page.locator(".item-card .carton").first.fill("1")
            page.locator(".item-card .net_weight").first.fill("5")
            page.locator(".item-card .gross_weight").first.fill("6")
            page.get_by_role("button", name="Save Packing List").click()
            page.locator("#packing-next-actions").wait_for(state="visible")
            page.get_by_role("link", name="Back to Packing List").click()
            page.wait_for_url(f"{base_url}/packing-list")
            packing_b_edit_path = page.locator('a', has_text="Edit").first.get_attribute("href")
            assert packing_b_edit_path
            packing_b_no = packing_b_edit_path.rsplit("/", 1)[-1]
            page.goto(f"{base_url}/si-form?packing_no={packing_b_no}")
            page.locator('input[name="carrier"]').fill("CODEX Carrier B")
            page.locator('input[name="vessel"]').fill("CODEX Vessel B")
            page.get_by_role("button", name="Save Shipping Instruction").click()
            page.wait_for_url(f"{base_url}/si-list")
            si_b_edit_path = page.locator('a', has_text="Edit").first.get_attribute("href")
            assert si_b_edit_path
            si_b_no = si_b_edit_path.rsplit("/", 1)[-1]
            page.goto(f"{base_url}/shipment-form")
            page.locator('#shipment-si').select_option(si_b_no)
            page.wait_for_url(f"{base_url}/shipment-form?si_no={si_b_no}")
            page.locator('input[name="shipment_name"]').fill(f"CODEX Shipment B {browser_name}")
            page.locator('input[name="buyer"]').fill(buyer_b_name)
            page.get_by_role("button", name="Save Shipment").click()
            shipment_b_no = page.get_by_role("link", name="View Shipment").get_attribute("href").rsplit("/", 1)[-1]
            page.goto(f"{base_url}/shipment-list")
            shipment_b_detail_path = f"/shipment/{shipment_b_no}"
            shipment_b_edit_path = f"/edit-shipment/{shipment_b_no}"
            shipment_b_pdf_path = f"/shipment-pdf/{shipment_b_no}"
            shipment_b_data_path = f"/shipment-data/{shipment_b_no}"
            assert page.get_by_text(shipment_a_no).count() == 0
            for path in [shipment_a_detail_path, shipment_a_edit_path, shipment_a_delete_path, shipment_a_pdf_path, shipment_a_data_path]:
                denied = page.goto(f"{base_url}{path}")
                assert denied is not None and denied.status == 404
            page.goto(f"{base_url}/shipment-list")
            page.goto(f"{base_url}/booking-form?shipment_no={shipment_b_no}&si_no={si_b_no}&packing_no={packing_b_no}")
            page.locator('input[name="booking_reference"]').fill(f"CODEX-BOOK-B-{browser_name}")
            page.locator('input[name="carrier"]').fill("CODEX Booking Carrier B")
            page.get_by_role("button", name="Save Booking").click()
            page.get_by_role("heading", name="Booking Saved").wait_for(state="visible")
            page.goto(f"{base_url}/booking-list")
            booking_b_edit_path = page.locator('a', has_text="Edit").first.get_attribute("href")
            assert booking_b_edit_path
            page.goto(f"{base_url}/container-form?shipment_no={shipment_b_no}&packing_no={packing_b_no}")
            page.locator('input[name="container_no"]').fill(f"CODEX-CONT-B-{browser_name}")
            page.get_by_role("button", name="Save Container Record").click()
            page.wait_for_url(f"{base_url}/shipment/{shipment_b_no}")
            page.goto(f"{base_url}/container-list")
            container_b_edit_path = page.locator('a', has_text="Edit").first.get_attribute("href")
            assert container_b_edit_path
            container_b_no = container_b_edit_path.rsplit("/", 1)[-1]
            assert page.get_by_text(container_a_no).count() == 0
            for path in [f"/container/{container_a_no}", container_a_edit_path, f"/delete-container/{container_a_no}", f"/container-data/{container_a_no}", container_a_pdf_path]:
                denied = page.goto(path if path.startswith("http") else f"{base_url}{path}")
                assert denied is not None and denied.status == 404

            page.get_by_role("button", name="Logout").click()
            page.wait_for_url(f"{base_url}/login")
            page.get_by_label("Email").fill(email)
            page.get_by_label("Password").fill("Test1234")
            page.get_by_role("button", name="Login").click()
            page.wait_for_url(f"{base_url}/")
            company_a = page.evaluate("fetch('/company-data').then(response => response.json())")
            assert company_a["name"] == "Browser Test Company"
            assert company_a["address"] == f"{browser_name} account address"
            assert company_a != company_b
            buyer_a_data = page.evaluate("fetch('/buyer-data').then(response => response.json())")
            assert [record["name"] for record in buyer_a_data] == [buyer_a_name]
            product_a_data = page.evaluate("fetch('/product-data').then(response => response.json())")
            assert [record["name"] for record in product_a_data] == [product_a_name]
            invoice_a_data = page.evaluate("fetch('/invoice-data').then(response => response.json())")
            assert [record["invoice_no"] for record in invoice_a_data] == [invoice_a_no]
            assert page.locator('.dashboard-stat-card[href="/products"] strong').text_content() == "1"
            assert page.locator('.dashboard-stat-card[href="/invoice-list"] strong').text_content() == "1"
            assert page.locator('.dashboard-stat-card[href="/packing-list"] strong').text_content() == "1"
            assert page.locator("article.document-card").filter(has_text="Shipping Instruction").locator(".document-count").text_content() == "1"
            assert page.locator("article.document-card").filter(has_text="Booking Confirmation").locator(".document-count").text_content() == "1"
            assert page.locator("article.document-card").filter(has_text="Container Management").locator(".document-count").text_content() == "1"
            assert "Total Shipments" in page.content() and shipment_a_no in page.content()
            shipment_data = page.evaluate(f"fetch('{shipment_a_data_path}').then(response => response.json())")
            assert shipment_data["shipment_no"] == shipment_a_no and "account_id" not in shipment_data
            for path in [shipment_b_detail_path, shipment_b_edit_path, f"/delete-shipment/{shipment_b_no}", shipment_b_pdf_path, shipment_b_data_path]:
                denied = page.goto(f"{base_url}{path}")
                assert denied is not None and denied.status == 404
            for path in [f"/container/{container_b_no}", container_b_edit_path, f"/delete-container/{container_b_no}", f"/container-data/{container_b_no}", f"/container-pdf/{container_b_no}"]:
                denied = page.goto(f"{base_url}{path}")
                assert denied is not None and denied.status == 404
            denied = page.goto(f"{base_url}{buyer_b_edit_path}")
            assert denied is not None and denied.status == 404
            denied = page.goto(f"{base_url}{buyer_b_edit_path.replace('/edit-buyer/', '/delete-buyer/')}")
            assert denied is not None and denied.status == 404
            denied = page.goto(f"{base_url}{product_b_edit_path}")
            assert denied is not None and denied.status == 404
            denied = page.goto(f"{base_url}{product_b_edit_path.replace('/edit-product/', '/delete-product/')}")
            assert denied is not None and denied.status == 404
            denied = page.goto(f"{base_url}{invoice_b_edit_path}")
            assert denied is not None and denied.status == 404
            denied = page.goto(f"{base_url}{invoice_b_edit_path.replace('/edit-invoice/', '/delete-invoice/')}")
            assert denied is not None and denied.status == 404
            denied = page.goto(f"{base_url}{packing_b_edit_path}")
            assert denied is not None and denied.status == 404
            denied = page.goto(f"{base_url}{packing_b_edit_path.replace('/edit-packing/', '/packing-delete/')}")
            assert denied is not None and denied.status == 404
            denied = page.goto(f"{base_url}{si_b_edit_path}")
            assert denied is not None and denied.status == 404
            denied = page.goto(f"{base_url}{si_b_edit_path.replace('/edit-si/', '/delete-si/')}")
            assert denied is not None and denied.status == 404
            denied = page.goto(f"{base_url}{booking_b_edit_path}")
            assert denied is not None and denied.status == 404
            denied = page.goto(f"{base_url}{booking_b_edit_path.replace('/edit-booking/', '/delete-booking/')}")
            assert denied is not None and denied.status == 404
            page.goto(f"{base_url}{booking_a_edit_path.replace('/edit-booking/', '/delete-booking/')}")
            page.get_by_role("button", name="Archive", exact=True).click()
            page.locator(".tp-confirm-dialog").get_by_role("button", name="Archive", exact=True).click()
            page.wait_for_url(f"{base_url}/booking-list")
            assert page.get_by_text(booking_a_no).count() == 0
            page.goto(f"{base_url}/delete-container/{container_a_no}")
            page.get_by_role("button", name="Archive", exact=True).click()
            page.locator(".tp-confirm-dialog").get_by_role("button", name="Archive", exact=True).click()
            page.wait_for_url(f"{base_url}/container-list")
            assert page.get_by_text(container_a_no).count() == 0
            page.goto(f"{base_url}{shipment_a_delete_path}")
            page.get_by_role("button", name="Archive", exact=True).click()
            page.locator(".tp-confirm-dialog").get_by_role("button", name="Archive", exact=True).click()
            page.wait_for_url(f"{base_url}/shipment-list")
            page.goto(f"{base_url}{si_a_edit_path.replace('/edit-si/', '/delete-si/')}")
            page.get_by_role("button", name="Archive", exact=True).click()
            page.locator(".tp-confirm-dialog").get_by_role("button", name="Archive", exact=True).click()
            page.wait_for_url(f"{base_url}/si-list")
            assert page.get_by_text(si_a_no).count() == 0
            page.goto(f"{base_url}/delete-bl/{bl_a_no}")
            page.get_by_role("button", name="Archive", exact=True).click()
            page.locator(".tp-confirm-dialog").get_by_role("button", name="Archive", exact=True).click()
            page.wait_for_url(f"{base_url}/bl-list")
            page.goto(f"{base_url}{packing_a_edit_path.replace('/edit-packing/', '/packing-delete/')}")
            page.get_by_role("button", name="Archive", exact=True).click()
            page.locator(".tp-confirm-dialog").get_by_role("button", name="Archive", exact=True).click()
            page.wait_for_url(f"{base_url}/packing-list")
            assert page.get_by_text(packing_a_no).count() == 0
            page.goto(f"{base_url}{buyer_a_edit_path.replace('/edit-buyer/', '/delete-buyer/')}")
            page.get_by_role("button", name="Delete", exact=True).click()
            page.locator(".tp-confirm-dialog").get_by_role("button", name="Delete", exact=True).click()
            page.wait_for_url(f"{base_url}/buyers")
            assert page.evaluate("fetch('/buyer-data').then(response => response.json())") == []
            page.goto(f"{base_url}{product_a_edit_path.replace('/edit-product/', '/delete-product/')}")
            page.get_by_role("button", name="Delete", exact=True).click()
            page.locator(".tp-confirm-dialog").get_by_role("button", name="Delete", exact=True).click()
            page.wait_for_url(f"{base_url}/products")
            assert page.evaluate("fetch('/product-data').then(response => response.json())") == []
            page.goto(f"{base_url}{invoice_a_edit_path.replace('/edit-invoice/', '/delete-invoice/')}")
            page.get_by_role("button", name="Archive", exact=True).click()
            page.locator(".tp-confirm-dialog").get_by_role("button", name="Archive", exact=True).click()
            page.wait_for_url(f"{base_url}/invoice-list")
            assert page.evaluate("fetch('/invoice-data').then(response => response.json())") == []
        finally:
            browser.close()


@pytest.mark.parametrize("browser_name", ["chromium", "webkit"])
def test_buyer_and_product_stored_xss_browser_rendering(auth_server, browser_name):
    base_url, _ = auth_server
    payloads = [
        "<script>alert(1)</script>",
        "<img src=x onerror=alert(1)>",
        '"><svg/onload=alert(1)>',
    ]
    with sync_playwright() as playwright:
        browser_type = getattr(playwright, browser_name)
        if not Path(browser_type.executable_path).exists():
            pytest.fail(f"{browser_name} browser binary is not installed")
        browser = browser_type.launch(headless=True)
        page = browser.new_page()
        dialogs = []
        page.on("dialog", lambda dialog: (dialogs.append(dialog.message), dialog.dismiss()))
        email = f"xss-{browser_name}-{uuid.uuid4().hex}@example.com"
        try:
            page.goto(f"{base_url}/register")
            page.get_by_label("Company Name").fill("XSS Browser Company")
            page.get_by_label("Email").fill(email)
            page.get_by_label("Password", exact=True).fill("Test1234")
            page.get_by_label("Confirm Password").fill("Test1234")
            page.get_by_role("button", name="Register").click()
            page.goto(f"{base_url}/login?next=%2Fbuyers")
            page.get_by_label("Email").fill(email)
            page.get_by_label("Password").fill("Test1234")
            page.get_by_role("button", name="Login").click()
            page.wait_for_url(re.compile(rf"{base_url}/company\?setup=1&next=%2Fonboarding"))
            page.locator("#name").fill("XSS Browser Company")
            page.locator("#address").fill("Seoul")
            page.locator("#email").fill(email)
            page.locator("#phone").fill("010-0000-0000")
            page.get_by_role("button", name="Save Company").click()
            page.wait_for_url(re.compile(rf"{base_url}/onboarding\?next=%2Fbuyers"))
            page.get_by_role("button", name="Skip for now").click()
            page.wait_for_url(f"{base_url}/buyers")

            page.goto(f"{base_url}/buyer-form")
            for field, value in zip(("name", "address", "email", "country"), payloads + ["한국어 日本語 中文"]):
                page.locator(f'input[name="{field}"]').fill(value)
            page.get_by_role("button", name="Save Buyer").click()
            page.wait_for_url(f"{base_url}/buyers")
            assert page.get_by_text(payloads[0], exact=True).is_visible()
            assert page.locator("tbody script, tbody img, tbody svg").count() == 0
            buyer_source = page.request.get(f"{base_url}/buyers").text()
            assert "<script>alert(1)</script>" not in buyer_source
            assert "&lt;script&gt;alert(1)&lt;/script&gt;" in buyer_source
            buyer_edit = page.locator('a[href^="/edit-buyer/"]').first.get_attribute("href")
            page.goto(f"{base_url}{buyer_edit}")
            assert page.locator('input[name="name"]').input_value() == payloads[0]
            assert page.locator('input[name="address"]').input_value() == payloads[1]
            assert page.locator('input[name="email"]').input_value() == payloads[2]

            page.goto(f"{base_url}/product-form")
            for field, value in zip(("name", "hs_code", "unit_price", "origin"), payloads + ["한국어 日本語 中文"]):
                page.locator(f'input[name="{field}"]').fill(value)
            page.get_by_role("button", name="Save Product").click()
            page.wait_for_url(f"{base_url}/products")
            assert page.get_by_text(payloads[0], exact=True).is_visible()
            assert page.locator("tbody script, tbody img, tbody svg").count() == 0
            product_source = page.request.get(f"{base_url}/products?search=%3Cscript%3E").text()
            assert "<script>alert(1)</script>" not in product_source
            assert "&lt;script&gt;alert(1)&lt;/script&gt;" in product_source
            product_edit = page.locator('a[href^="/edit-product/"]').first.get_attribute("href")
            page.goto(f"{base_url}{product_edit}")
            assert page.locator('input[name="name"]').input_value() == payloads[0]
            assert page.locator('input[name="hs_code"]').input_value() == payloads[1]
            assert page.locator('input[name="unit_price"]').input_value() == payloads[2]

            page.goto(f"{base_url}/search?q=alert")
            assert page.get_by_role("heading", name=payloads[0], exact=True).count() == 2
            assert page.locator(".result-card script, .result-card img, .result-card svg").count() == 0
            search_source = page.request.get(f"{base_url}/search?q=alert").text()
            assert "<script>alert(1)</script>" not in search_source
            assert "&lt;script&gt;alert(1)&lt;/script&gt;" in search_source
            assert dialogs == []
        finally:
            browser.close()
