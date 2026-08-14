import asyncio
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re

import pytest
from fastapi import HTTPException
from playwright.sync_api import sync_playwright
from starlette.requests import Request

from app import admin_dashboard, analytics, main
from tests.test_auth_browser import auth_server


def _request(account_id="A", admin=False):
    return Request({"type": "http", "method": "GET", "path": "/admin/dashboard", "headers": [], "trade_paper_user": {"account_id": account_id, "is_admin": admin}})


def test_event_schema_rejects_unknown_events_and_never_accepts_personal_data(tmp_path):
    path = tmp_path / "analytics.json"
    entry = analytics.record_event("Invoice Created", "account-A", path=path)
    assert tuple(entry) == analytics.ALLOWED_FIELDS
    assert entry["account_id"] == "account-A"
    serialized = path.read_text(encoding="utf-8")
    for forbidden in ("password", "token", "email_body", "document_content", "attachment"):
        assert forbidden not in serialized.casefold()
    with pytest.raises(ValueError):
        analytics.record_event("Password Submitted", "account-A", path=path)


def test_account_isolation_rates_and_thirty_day_trend(tmp_path):
    path = tmp_path / "analytics.json"
    now = datetime(2026, 8, 14, 12, tzinfo=timezone.utc)
    for event in ("Signup", "Export Wizard Started", "Export Wizard Completed", "Invoice Created", "Email Sent"):
        analytics.record_event(event, "A", now=now, path=path)
    analytics.record_event("Invoice Created", "B", now=now - timedelta(days=1), path=path)
    analytics.record_event("Login", "A", now=now - timedelta(days=31), path=path)
    metrics = analytics.analytics_metrics("A", now=now, path=path)
    assert metrics["signups"] == 1
    assert metrics["wizard_completion_rate"] == 100.0
    assert metrics["email_send_rate"] == 100.0
    assert metrics["document_creation_rate"] == 100.0
    assert len(metrics["trend"]) == 30
    assert metrics["trend"][-1]["total"] == 5
    assert sum(day["total"] for day in metrics["trend"]) == 5
    assert all(row["account_id"] == "A" for row in analytics.events("A", path))


def test_middleware_records_successful_route_events_without_request_body(tmp_path, monkeypatch):
    path = tmp_path / "analytics.json"
    monkeypatch.setattr(analytics, "ANALYTICS_FILE", path)

    async def successful_app(scope, receive, send):
        scope["trade_paper_user"] = {"account_id": "A"}
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    middleware = main.ProductAnalyticsMiddleware(successful_app)

    async def run():
        sent = []
        scope = {"type": "http", "method": "POST", "path": "/export-wizard", "headers": []}
        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}
        async def send(message):
            sent.append(message)
        await middleware(scope, receive, send)

    asyncio.run(run())
    assert [row["event"] for row in reversed(analytics.events("A", path))] == ["Export Wizard Completed", "Invoice Created", "Onboarding Completed"]


def test_admin_dashboard_shows_global_analytics_only_to_admin(tmp_path, monkeypatch):
    path = tmp_path / "analytics.json"
    monkeypatch.setattr(analytics, "ANALYTICS_FILE", path)
    analytics.record_event("Signup", path=path)
    analytics.record_event("Export Wizard Started", "A", path=path)
    analytics.record_event("Export Wizard Completed", "A", path=path)
    body = admin_dashboard.admin_dashboard(_request(admin=True)).body.decode()
    assert "Product Analytics" in body
    assert "Wizard Completion" in body
    assert "Last 30 Days" in body
    assert "Privacy-minimized product-flow events only" in body
    with pytest.raises(HTTPException) as denied:
        admin_dashboard.admin_dashboard(_request(admin=False))
    assert denied.value.status_code == 403


@pytest.mark.parametrize("browser_name", ["chromium", "webkit"])
def test_analytics_admin_dashboard_browser(auth_server, browser_name):
    base_url, data_dir = auth_server
    with sync_playwright() as playwright:
        browser_type = getattr(playwright, browser_name)
        if not Path(browser_type.executable_path).exists():
            pytest.fail(f"{browser_name} browser binary is not installed")
        browser = browser_type.launch(headless=True)
        page = browser.new_page()
        email = f"browser-{browser_name}@example.com"
        try:
            page.goto(f"{base_url}/register")
            page.get_by_label("Company Name").fill("Analytics Company")
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
            page.goto(f"{base_url}/admin/dashboard")
            assert page.get_by_role("heading", name="Product Analytics", exact=True).is_visible()
            assert page.get_by_text("Wizard Completion", exact=True).is_visible()
            assert page.get_by_role("heading", name="Last 30 Days", exact=True).is_visible()
            records = json.loads((data_dir / "analytics.json").read_text(encoding="utf-8"))
            assert any(row["event"] == "Signup" for row in records)
            assert all(set(row) == set(analytics.ALLOWED_FIELDS) for row in records)
        finally:
            browser.close()
