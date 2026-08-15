import json
from pathlib import Path
import re

import pytest
from fastapi import HTTPException
from playwright.sync_api import sync_playwright
from starlette.requests import Request

from app import auth, backup_restore, subscription, toss_payments
from tests.test_auth_browser import auth_server


def _request(account="A", admin=False):
    return Request({
        "type": "http", "method": "GET", "path": "/subscription/checkout", "headers": [],
        "trade_paper_user": {"account_id": account, "email": f"{account.lower()}@example.test", "is_admin": admin, "role": "Owner"},
    })


def test_toss_readiness_is_secret_free_and_keys_are_optional():
    missing = toss_payments.toss_readiness({})
    assert missing == {
        "provider": "Toss Payments", "configuration": "Not Ready", "activation": "Not Active",
        "issues": ["Client key is not configured.", "Secret key is not configured."],
    }
    configured = toss_payments.toss_readiness({
        "TRADE_PAPER_TOSS_CLIENT_KEY": "test_ck_example",
        "TRADE_PAPER_TOSS_SECRET_KEY": "test_sk_example",
    })
    assert configured == {"provider": "Toss Payments", "configuration": "Configured", "activation": "Not Active", "issues": []}
    assert "example" not in json.dumps(configured)
    mismatch = toss_payments.toss_readiness({
        "TRADE_PAPER_TOSS_CLIENT_KEY": "test_ck_example",
        "TRADE_PAPER_TOSS_SECRET_KEY": "live_sk_example",
    })
    assert mismatch["configuration"] == "Not Ready"
    assert mismatch["issues"] == ["Client and secret key modes do not match."]


def test_pending_orders_use_server_catalog_and_are_account_isolated(tmp_path):
    path = tmp_path / "payment_orders.json"
    first = toss_payments.create_pending_order("A", path=path, order_id="TPA-order-A")
    second = toss_payments.create_pending_order("B", path=path, order_id="TPA-order-B")
    assert first["amount"] == subscription.PLANS["Starter"]["price"] == 29_000
    assert first["currency"] == "KRW" and first["billing_cycle"] == "Monthly"
    assert first["status"] == "Pending" and first["provider"] == "Toss Payments"
    assert toss_payments.account_orders("A", path=path) == [first]
    assert toss_payments.account_orders("B", path=path) == [second]
    assert toss_payments.validate_redirect_order("A", "TPA-order-A", "29000", path=path) == first
    for amount in ("1", "29000.5", "not-a-number"):
        with pytest.raises(HTTPException) as error:
            toss_payments.validate_redirect_order("A", "TPA-order-A", amount, path=path)
        assert error.value.status_code == 400
    with pytest.raises(HTTPException) as error:
        toss_payments.validate_redirect_order("A", "TPA-order-B", "29000", path=path)
    assert error.value.status_code == 404
    assert path.with_name("payment_orders.backup.json").exists()


def test_product_checkout_and_admin_readiness_do_not_activate_or_create_orders(tmp_path, monkeypatch):
    orders = tmp_path / "payment_orders.json"
    monkeypatch.setattr(toss_payments, "PAYMENT_ORDERS_FILE", orders)
    product = toss_payments.starter_product_page().body.decode()
    assert "Starter" in product and "₩29,000 / month" in product
    assert "Monthly subscription" in product and "Unlimited documents" in product and "Direct onboarding" in product
    assert "does not create a paid subscription or collect payment" in product
    for path in ("/terms", "/privacy", "/refund-policy", "/contact"):
        assert f'href="{path}"' in product
    checkout = toss_payments.checkout_preparation(_request()).body.decode()
    assert "No payment order has been created" in checkout
    assert "Not Active" in checkout and not orders.exists()
    with pytest.raises(HTTPException) as error:
        toss_payments.checkout_preparation(_request(), "Professional")
    assert error.value.status_code == 400
    readiness = toss_payments.payment_readiness_page(_request(admin=True)).body.decode()
    assert "Payment Readiness" in readiness and "Secret key is not configured" in readiness
    assert "TRADE_PAPER_TOSS" not in readiness
    assert "payment_orders.json" in backup_restore.ACCOUNT_FILES
    assert "/starter" in auth.PUBLIC_PATHS


@pytest.mark.parametrize("browser_name", ["chromium", "webkit"])
@pytest.mark.parametrize("viewport", [{"width": 1280, "height": 900}, {"width": 390, "height": 844}])
def test_starter_purchase_preparation_browser(auth_server, browser_name, viewport):
    base_url, _ = auth_server
    with sync_playwright() as playwright:
        browser_type = getattr(playwright, browser_name)
        if not Path(browser_type.executable_path).exists():
            pytest.fail(f"{browser_name} browser binary is not installed")
        browser = browser_type.launch(headless=True)
        page = browser.new_page(viewport=viewport)
        email = f"browser-{browser_name}@example.com" if viewport["width"] > 500 else f"toss-{browser_name}-mobile@example.test"
        try:
            page.goto(f"{base_url}/starter")
            assert page.get_by_role("heading", name="Starter", exact=True).is_visible()
            assert page.get_by_text("₩29,000 / month", exact=True).is_visible()
            assert page.get_by_text("Online payment processing is not active yet", exact=False).is_visible()
            assert page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")
            for path, heading in (
                ("/terms", "Terms of Service"),
                ("/privacy", "Privacy Policy"),
                ("/refund-policy", "Cancellation and Refund Policy"),
                ("/contact", "Contact Trade Paper AI"),
            ):
                assert page.locator(f'a[href="{path}"]').first.is_visible()
                page.goto(f"{base_url}{path}")
                assert page.get_by_role("heading", name=heading, exact=True).is_visible()
                assert page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")
                page.goto(f"{base_url}/starter")
            page.goto(f"{base_url}/register")
            page.get_by_label("Company Name").fill("Toss Review Company")
            page.get_by_label("Email").fill(email)
            page.get_by_label("Password", exact=True).fill("Test1234")
            page.get_by_label("Confirm Password").fill("Test1234")
            page.get_by_role("button", name="Register").click()
            page.goto(f"{base_url}/login")
            page.get_by_label("Email").fill(email)
            page.get_by_label("Password").fill("Test1234")
            page.get_by_role("button", name="Login").click()
            page.wait_for_url(re.compile(rf"{base_url}/company"))
            page.locator("#address").fill("Changwon")
            page.get_by_role("button", name="Save Company").click()
            page.wait_for_url(re.compile(rf"{base_url}/onboarding"))
            page.get_by_role("button", name="Skip for now").click()
            page.goto(f"{base_url}/subscription/checkout?plan=Starter")
            assert page.get_by_role("heading", name="Trade Paper AI Starter Monthly").is_visible()
            assert page.get_by_text("No payment order has been created", exact=False).is_visible()
            assert page.get_by_text("Not Active", exact=True).is_visible()
            if viewport["width"] > 500:
                page.goto(f"{base_url}/admin/payment-readiness")
                assert page.get_by_role("heading", name="Payment Readiness").is_visible()
                assert page.get_by_text("Not Active", exact=True).is_visible()
                assert "test_sk_" not in page.content() and "live_sk_" not in page.content()
        finally:
            browser.close()
