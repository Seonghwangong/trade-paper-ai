from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
import time

import pytest
from playwright.sync_api import sync_playwright
from starlette.requests import Request

from app import admin_dashboard, analytics
from tests.test_auth_browser import auth_server


def _admin_request():
    return Request({"type": "http", "method": "GET", "path": "/admin/dashboard", "headers": [], "trade_paper_user": {"account_id": "admin", "is_admin": True}})


def test_visit_count_page_funnel_and_daily_trend_without_personal_data(tmp_path, monkeypatch):
    visitor_file = tmp_path / "visitor_analytics.json"
    event_file = tmp_path / "analytics.json"
    monkeypatch.setattr(analytics, "ANALYTICS_FILE", event_file)
    now = datetime(2026, 8, 14, 12, tzinfo=timezone.utc)
    for page in ("Landing", "Pricing", "FAQ", "Signup"):
        analytics.record_visit(page, "Product Hunt", now=now, path=visitor_file)
    analytics.record_visit("Landing", "Direct", now=now - timedelta(days=1), path=visitor_file)
    analytics.record_event("Onboarding Started", "account-a", now=now, path=event_file)
    metrics = analytics.visitor_metrics(now=now, path=visitor_file)
    assert metrics["visits"] == 5
    assert metrics["pages"] == {"Landing": 2, "Pricing": 1, "FAQ": 1, "Signup": 1}
    assert metrics["landing_to_signup_rate"] == 50.0
    assert metrics["signup_to_onboarding_rate"] == 100.0
    assert metrics["landing_to_onboarding_rate"] == 50.0
    assert len(metrics["trend"]) == 30
    assert metrics["trend"][-1]["total"] == 4
    records = json.loads(visitor_file.read_text(encoding="utf-8"))
    assert all(set(record) == set(analytics.VISITOR_FIELDS) for record in records)
    serialized = visitor_file.read_text(encoding="utf-8").casefold()
    for forbidden in ("ip", "email", "cookie", "referer", "account_id"):
        assert forbidden not in serialized


@pytest.mark.parametrize("referer,query,expected", [
    ("", "", "Direct"),
    ("https://www.google.com/search?q=export", "", "Google"),
    ("https://www.producthunt.com/posts/trade-paper-ai", "", "Product Hunt"),
    ("https://www.reddit.com/r/export/", "", "Reddit"),
    ("https://example.com/article", "", "Other"),
    ("", "utm_source=producthunt&utm_campaign=launch", "Product Hunt"),
])
def test_acquisition_source_is_reduced_to_allow_list(referer, query, expected):
    assert analytics.classify_source(referer, query) == expected


def test_visitor_dashboard_is_global_admin_only(tmp_path, monkeypatch):
    visitor_file = tmp_path / "visitor_analytics.json"
    event_file = tmp_path / "analytics.json"
    monkeypatch.setattr(analytics, "VISITOR_ANALYTICS_FILE", visitor_file)
    monkeypatch.setattr(analytics, "ANALYTICS_FILE", event_file)
    analytics.record_visit("Landing", "Google", path=visitor_file)
    analytics.record_visit("Signup", "Google", path=visitor_file)
    body = admin_dashboard.admin_dashboard(_admin_request()).body.decode()
    for value in ("Visitor Analytics", "Visited Pages", "Acquisition Sources", "Landing → Signup", "Visitor Trend · Last 30 Days"):
        assert value in body
    assert "No IP, email, referrer URL, or tracking cookie is stored." in body


@pytest.mark.parametrize("browser_name", ["chromium", "webkit"])
def test_visitor_analytics_browser_flow(auth_server, browser_name):
    base_url, data_dir = auth_server
    with sync_playwright() as playwright:
        browser_type = getattr(playwright, browser_name)
        if not Path(browser_type.executable_path).exists():
            pytest.fail(f"{browser_name} browser binary is not installed")
        browser = browser_type.launch(headless=True)
        page = browser.new_page()
        email = f"browser-{browser_name}@example.com"
        try:
            page.goto(f"{base_url}/?utm_source=producthunt")
            with page.expect_response(lambda response: "/analytics/visit" in response.url and "page=Pricing" in response.url):
                page.locator("#pricing").scroll_into_view_if_needed()
            with page.expect_response(lambda response: "/analytics/visit" in response.url and "page=FAQ" in response.url):
                page.locator("#faq").scroll_into_view_if_needed()
            page.goto(f"{base_url}/register")
            page.get_by_label("Company Name").fill("Visitor Analytics")
            page.get_by_label("Email").fill(email)
            page.get_by_label("Password", exact=True).fill("Test1234")
            page.get_by_label("Confirm Password").fill("Test1234")
            page.get_by_role("button", name="Register").click()
            page.goto(f"{base_url}/login")
            page.get_by_label("Email").fill(email)
            page.get_by_label("Password").fill("Test1234")
            page.get_by_role("button", name="Login").click()
            page.wait_for_url(re.compile(rf"{base_url}/company\?setup=1&next=%2Fonboarding"))
            page.locator("#address").fill("Seoul")
            page.get_by_role("button", name="Save Company").click()
            page.wait_for_url(re.compile(rf"{base_url}/onboarding"))
            deadline = time.monotonic() + 5
            records = []
            while time.monotonic() < deadline:
                path = data_dir / "visitor_analytics.json"
                if path.exists():
                    records = json.loads(path.read_text(encoding="utf-8"))
                    if {record["page"] for record in records} >= {"Landing", "Pricing", "FAQ", "Signup"}:
                        break
                time.sleep(0.05)
            assert {record["page"] for record in records} >= {"Landing", "Pricing", "FAQ", "Signup"}
            assert any(record["source"] == "Product Hunt" for record in records)
            assert all(set(record) == set(analytics.VISITOR_FIELDS) for record in records)
            page.goto(f"{base_url}/admin/dashboard")
            assert page.get_by_role("heading", name="Visitor Analytics", exact=True).is_visible()
            assert page.get_by_text("Acquisition Sources", exact=True).is_visible()
            assert page.get_by_text("Product Hunt", exact=True).is_visible()
        finally:
            browser.close()
