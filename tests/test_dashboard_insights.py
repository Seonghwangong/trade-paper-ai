from datetime import datetime, timezone
import json
from pathlib import Path
import re

import pytest
from playwright.sync_api import sync_playwright

from app import admin_dashboard, dashboard_insights, storage
from tests.test_auth_browser import auth_server


def _write(path, records):
    path.write_text(json.dumps(records), encoding="utf-8")


def test_account_statistics_top_buyers_products_recent_activity_and_trend(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DATA_DIR", tmp_path)
    now = datetime(2026, 8, 14, 12, tzinfo=timezone.utc)
    _write(tmp_path / "invoices.json", [
        {"account_id": "A", "invoice_no": "INV-002", "invoice_date": "2026-08-14", "buyer": "Alpha Buyer", "items": [{"name": "Laptop"}, {"name": "Charger"}]},
        {"account_id": "A", "invoice_no": "INV-001", "invoice_date": "2026-08-13", "buyer": "Alpha Buyer", "items": [{"name": "Laptop"}]},
        {"account_id": "B", "invoice_no": "INV-999", "invoice_date": "2026-08-14", "buyer": "Private Buyer", "items": [{"name": "Private Product"}]},
    ])
    _write(tmp_path / "packing_lists.json", [{"account_id": "A", "packing_no": "PK-001", "packing_date": "2026-08-12"}])
    _write(tmp_path / "shipments.json", [
        {"account_id": "A", "shipment_no": "SHP-001", "shipment_date": "2026-08-14"},
        {"account_id": "B", "shipment_no": "SHP-999", "shipment_date": "2026-08-14"},
    ])
    _write(tmp_path / "email_history.json", [
        {"account_id": "A", "sent_at": "2026-08-14T11:00:00+00:00", "document_no": "INV-002", "shipment_no": "SHP-001"},
        {"account_id": "B", "sent_at": "2026-08-14T12:00:00+00:00", "document_no": "INV-999"},
    ])

    result = dashboard_insights.dashboard_insights("A", now=now)
    assert result["month"] == {"Documents": 4, "Shipments": 1, "Emails": 1}
    assert result["top_buyers"] == [("Alpha Buyer", 2)]
    assert result["top_products"] == [("Laptop", 2), ("Charger", 1)]
    assert result["recent"][0]["type"] == "Email"
    assert {row["type"] for row in result["recent"][:3]} == {"Email", "Invoice", "Shipment"}
    assert len(result["trend"]) == 30
    assert result["trend"][-1] == {"date": "2026-08-14", "Documents": 2, "Shipments": 1, "Emails": 1}
    serialized = json.dumps(result)
    assert "Private Buyer" not in serialized and "Private Product" not in serialized and "INV-999" not in serialized


def test_dashboard_rendering_is_escaped_and_separate_from_admin_scope():
    html = dashboard_insights.render_dashboard_insights({
        "month": {"Documents": 1, "Shipments": 0, "Emails": 0},
        "top_buyers": [("<script>alert(1)</script>", 1)],
        "top_products": [], "recent": [], "trend": [],
    })
    assert 'data-dashboard-scope="account"' in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "<script>alert(1)</script>" not in html
    assert admin_dashboard.DASHBOARD_SCOPE == "service"


@pytest.mark.parametrize("browser_name", ["chromium", "webkit"])
def test_personal_dashboard_insights_browser_and_admin_separation(auth_server, browser_name):
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
            page.get_by_label("Company Name").fill("Insights Company")
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
            page.wait_for_url(f"{base_url}/")
            section = page.locator('[data-dashboard-scope="account"]')
            assert section.get_by_role("heading", name="Your Trade Activity").is_visible()
            for label in ("Documents", "Shipments", "Emails", "Top Buyers", "Top Products", "Recent Activity", "30-Day Trend"):
                assert section.get_by_text(label, exact=True).first.is_visible()
            page.goto(f"{base_url}/admin/dashboard")
            assert page.get_by_role("heading", name="Admin Dashboard 2.0").is_visible()
            assert page.get_by_role("heading", name="Your Trade Activity").count() == 0
        finally:
            browser.close()
