import asyncio
import json
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app import analytics, auth, guest_sample, main, paddle_live_runtime, traffic_quality
import app.storage as storage


def request(path="/admin/growth", admin=True, query=b"", headers=None):
    return Request({"type": "http", "method": "GET", "path": path,
                    "scheme": "https", "server": ("trade.example", 443),
                    "query_string": query, "headers": headers or [(b"host", b"trade.example")],
                    "trade_paper_user": {"account_id": "operator" if admin else "", "is_admin": admin}})


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DATA_DIR", tmp_path)
    monkeypatch.setattr(auth, "USERS_FILE", tmp_path / "users.json")
    monkeypatch.setattr(analytics, "ANALYTICS_FILE", tmp_path / "analytics.json")
    monkeypatch.setattr(analytics, "VISITOR_ANALYTICS_FILE", tmp_path / "visits.json")
    monkeypatch.setenv("TRADE_PAPER_ADMIN_EMAILS", "operator@example.com")
    users = [{"account_id": "operator", "email": "operator@example.com", "company": "Operator"},
             {"account_id": "test", "email": "qa@example.com", "company": "Known QA"},
             {"account_id": "customer", "email": "buyer@example.com", "company": "Buyer"},
             {"account_id": "customer", "email": "staff@example.com", "company": "Buyer"}]
    (tmp_path / "users.json").write_text(json.dumps(users))
    (tmp_path / "analytics_internal_accounts.json").write_text('["test"]')
    monkeypatch.setattr(paddle_live_runtime, "account_state", lambda account, now=None: (False, None))
    return tmp_path


def test_old_traffic_remains_unknown_and_new_internal_is_separated(isolated):
    old = [{"time": "2026-11-12T00:00:00+00:00", "page": "Practice Page", "source": "Google"}]
    analytics.VISITOR_ANALYTICS_FILE.write_text(json.dumps(old))
    analytics.record_visit("Practice Pair Generated", audience="Internal")
    analytics.record_visit("Practice Pair Generated")
    assert analytics.visitor_metrics(audience="Legacy unknown")["visits"] == 1
    assert analytics.visitor_metrics(audience="Internal")["practice"]["Practice Pair Generated"] == 1
    assert analytics.visitor_metrics(audience="Unclassified")["practice"]["Practice Pair Generated"] == 1
    assert json.loads(analytics.VISITOR_ANALYTICS_FILE.read_text())[0] == old[0]
    assert analytics.visitor_metrics()["practice"]["Practice Pair Generated"] == 2


def test_qa_link_survives_generation_without_login_cookie(isolated, monkeypatch):
    req = request("/try-sample", admin=False, query=b"traffic=internal&lang=en")
    page = guest_sample.practice_page("en", req).body.decode()
    assert 'data-traffic="internal"' in page
    monkeypatch.setattr(guest_sample, "build_practice_pair", lambda shipment: {"invoice": b"pdf", "packing": b"pdf"})
    shipment = guest_sample.PracticeShipment(quantity=100, unit_price="8", cartons=5, net_weight="20", gross_weight="25")
    guest_sample.practice_documents(shipment, req)
    assert analytics.visitor_metrics(audience="Internal")["practice"]["Practice Pair Generated"] == 1
    with pytest.raises(HTTPException) as error:
        main.analytics_visit(req, "Practice Pair Generated", "Google")
    assert error.value.status_code == 422
    assert analytics.request_audience(request(admin=False, headers=[(b"user-agent", b"Googlebot")])) == "Automation hint"
    assert analytics.request_audience(request(admin=False)) == "Unclassified"
    assert analytics.request_audience(request()) == "Internal"


def test_deduplicated_account_evidence_excludes_internal_and_surfaces_billing_failure(isolated, monkeypatch):
    analytics.record_event("Signup", "customer")
    analytics.record_event("Signup", "customer")
    (isolated / "invoices.json").write_text(json.dumps([
        {"account_id": "test", "invoice_no": "INV-001"},
        {"account_id": "customer", "invoice_no": "INV-002"}]))
    (isolated / "packing_lists.json").write_text(json.dumps([
        {"account_id": "customer", "invoice_no": "INV-001"},
        {"account_id": "test", "invoice_no": "INV-001"}]))
    def billing(account, now=None):
        if account == "customer":
            raise paddle_live_runtime.LiveBillingUnavailable()
        return False, None
    monkeypatch.setattr(paddle_live_runtime, "account_state", billing)
    rows = traffic_quality.account_evidence()
    assert len(rows) == 3
    customer = next(row for row in rows if row["account_id"] == "customer")
    assert customer["signup_event"] and customer["invoice"] and not customer["pair"]
    assert customer["category"] == "Other accounts" and customer["paid"] is None
    assert {row["account_id"] for row in rows if row["category"] == "Internal"} == {"operator", "test"}
    page = traffic_quality.growth_dashboard(request()).body.decode()
    assert "Billing unknown" in page and "Legacy unknown" in page
    with pytest.raises(HTTPException):
        traffic_quality.growth_dashboard(request(admin=False))


def test_mark_internal_requires_admin_origin_and_existing_account(isolated):
    baseline = (isolated / "users.json").read_bytes()
    for req in (request(admin=False), request(), request(headers=[(b"origin", b"https://evil.example")])):
        with pytest.raises(HTTPException):
            traffic_quality.mark_internal(req, "customer", "yes")
    req = request(headers=[(b"host", b"trade.example"), (b"origin", b"https://trade.example")])
    assert traffic_quality.mark_internal(req, "customer", "yes").status_code == 303
    assert "customer" in traffic_quality.internal_account_ids()
    traffic_quality.mark_internal(req, "customer", "no")
    assert "customer" not in traffic_quality.internal_account_ids()
    assert (isolated / "users.json").read_bytes() == baseline


def test_signup_links_saved_account_without_granting_session(isolated, monkeypatch):
    monkeypatch.setattr(auth, "load_users", lambda: [])
    req = request("/register", admin=False)
    req.scope.pop("trade_paper_user")
    response = auth.register(company="Exporter", email="new@example.com", password="Test12345", confirm_password="Test12345", next_path="", website_confirm="", request=req)
    assert response.status_code == 303 and "set-cookie" not in response.headers
    new = json.loads(auth.USERS_FILE.read_text())[-1]
    assert req.scope["trade_paper_analytics_account"] == new["account_id"]
    assert "trade_paper_user" not in req.scope
    async def app(scope, receive, send):
        scope["trade_paper_analytics_account"] = new["account_id"]
        await send({"type": "http.response.start", "status": 303, "headers": []})
        await send({"type": "http.response.body", "body": b""})
    async def run():
        async def receive(): return {"type": "http.request", "body": b""}
        async def send(message): pass
        await main.ProductAnalyticsMiddleware(app)({"type": "http", "method": "POST", "path": "/register", "headers": []}, receive, send)
    asyncio.run(run())
    assert analytics.events()[0]["account_id"] == new["account_id"]
