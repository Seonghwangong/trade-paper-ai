import json
from pathlib import Path
import re

import pytest
from playwright.sync_api import sync_playwright
from starlette.requests import Request

from app import billing, subscription
from tests.test_auth_browser import auth_server


def _request(account="A", method="GET", path="/subscription"):
    return Request({
        "type": "http", "method": method, "path": path, "headers": [],
        "trade_paper_user": {"account_id": account, "email": f"{account.lower()}@example.test"},
    })


def _files(tmp_path, monkeypatch):
    users = tmp_path / "users.json"
    history = tmp_path / "billing_history.json"
    usage = tmp_path / "usage_events.json"
    users.write_text(json.dumps([
        {"account_id": "A", "plan": "Free", "subscription_status": "Active"},
        {"account_id": "B", "plan": "Professional", "subscription_status": "Active"},
    ]), encoding="utf-8")
    history.write_text(json.dumps([
        {"account_id": "A", "created_at": "2026-08-14T01:00:00Z", "plan": "Free", "status": "Active", "amount": 0, "event": "Plan Change"},
        {"account_id": "B", "created_at": "2026-08-14T02:00:00Z", "plan": "Professional", "status": "Active", "amount": 99, "event": "Invoice", "invoice_no": "BILL-B"},
    ]), encoding="utf-8")
    usage.write_text(json.dumps([
        {"account_id": "A", "created_at": "2026-08-01T00:00:00Z", "path": "/invoice"},
        {"account_id": "B", "created_at": "2026-08-01T00:00:00Z", "path": "/invoice"},
    ]), encoding="utf-8")
    monkeypatch.setattr(subscription, "USERS_FILE", users)
    monkeypatch.setattr(subscription, "BILLING_HISTORY_FILE", history)
    monkeypatch.setattr(subscription, "USAGE_EVENTS_FILE", usage)
    return users, history


def test_my_subscription_upgrade_downgrade_cancel_usage_and_isolation(tmp_path, monkeypatch):
    users, history = _files(tmp_path, monkeypatch)
    page = subscription.subscription_page(_request()).body.decode()
    assert "My Subscription" in page and "Documents this month: 1 / 5" in page
    assert subscription.PAID_PLAN_NOTICE in page
    assert "Upgrade to Starter" not in page and "Upgrade to Professional" not in page
    assert "Cancel Subscription" in page and "Invoice History" in page
    assert "Payment integration is not active" in page
    assert "BILL-B" not in page and "$99.00" not in page

    before_users = users.read_text(encoding="utf-8")
    before_history = history.read_text(encoding="utf-8")
    assert subscription.change_plan(_request(method="POST", path="/subscription/plan"), "Professional").status_code == 403
    assert subscription.change_plan(_request(method="POST", path="/subscription/plan"), "Starter").status_code == 403
    assert users.read_text(encoding="utf-8") == before_users
    assert history.read_text(encoding="utf-8") == before_history
    existing_paid = subscription.subscription_page(_request("B")).body.decode()
    assert "Professional" in existing_paid and "Downgrade to Free" in existing_paid
    assert all(row["account_id"] == "A" for row in billing.account_billing_history("A", history))
    assert all(row.get("invoice_no") != "BILL-B" for row in billing.account_invoice_history("A", history))
    stored = json.loads(users.read_text(encoding="utf-8"))
    assert next(row for row in stored if row["account_id"] == "B")["plan"] == "Professional"


def test_stripe_adapter_is_interface_only():
    assert getattr(billing.StripeAdapter, "_is_protocol", False) is True
    assert callable(getattr(billing.StripeAdapter, "create_checkout_session"))
    assert callable(getattr(billing.StripeAdapter, "create_customer_portal_session"))


@pytest.mark.parametrize("browser_name", ["chromium", "webkit"])
def test_billing_portal_browser_flow(auth_server, browser_name):
    base_url, _ = auth_server
    with sync_playwright() as playwright:
        browser_type = getattr(playwright, browser_name)
        if not Path(browser_type.executable_path).exists():
            pytest.fail(f"{browser_name} browser binary is not installed")
        browser = browser_type.launch(headless=True)
        page = browser.new_page()
        email = f"browser-{browser_name}@example.com"
        try:
            page.goto(f"{base_url}/register")
            page.get_by_label("Company Name").fill("Billing Company")
            page.get_by_label("Email").fill(email)
            page.get_by_label("Password", exact=True).fill("Test1234")
            page.get_by_label("Confirm Password").fill("Test1234")
            page.get_by_role("button", name="Register").click()
            page.goto(f"{base_url}/login")
            page.get_by_label("Email").fill(email)
            page.get_by_label("Password").fill("Test1234")
            page.get_by_role("button", name="Login").click()
            page.wait_for_url(re.compile(rf"{base_url}/company"))
            page.locator("#address").fill("Seoul")
            page.get_by_role("button", name="Save Company").click()
            page.wait_for_url(re.compile(rf"{base_url}/onboarding"))
            page.get_by_role("button", name="Skip for now").click()
            page.goto(f"{base_url}/subscription")
            assert page.get_by_role("heading", name="My Subscription").is_visible()
            assert page.get_by_text(subscription.PAID_PLAN_NOTICE).is_visible()
            assert page.get_by_role("button", name="Upgrade to Starter").count() == 0
            status = page.evaluate("fetch('/subscription/plan',{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},body:'plan=Starter'}).then(response=>response.status)")
            assert status == 403
            page.reload()
            assert page.locator(".summary h2").text_content() == "Free"
            assert page.locator(".badge", has_text="Trial").is_visible()
            assert page.get_by_role("heading", name="Billing History").is_visible()
            assert page.get_by_role("heading", name="Invoice History").is_visible()
        finally:
            browser.close()
