import asyncio
import json
from datetime import datetime, timezone

from starlette.requests import Request

from app import auth, subscription


def _request(account="A", path="/subscription", method="GET"):
    return Request({
        "type": "http", "method": method, "scheme": "http", "path": path,
        "raw_path": path.encode(), "query_string": b"", "headers": [],
        "server": ("test", 80), "client": ("127.0.0.1", 1),
        "trade_paper_user": {"account_id": account, "email": f"{account.lower()}@example.com"},
    })


def _files(tmp_path, monkeypatch):
    users = tmp_path / "users.json"
    billing = tmp_path / "billing_history.json"
    usage = tmp_path / "usage_events.json"
    users.write_text(json.dumps([
        {"account_id": "A", "company": "Alpha", "email": "a@example.com"},
        {"account_id": "B", "company": "Beta", "email": "b@example.com", "plan": "Professional", "subscription_status": "Active"},
    ]), encoding="utf-8")
    billing.write_text("[]\n", encoding="utf-8")
    usage.write_text("[]\n", encoding="utf-8")
    monkeypatch.setattr(subscription, "USERS_FILE", users)
    monkeypatch.setattr(subscription, "BILLING_HISTORY_FILE", billing)
    monkeypatch.setattr(subscription, "USAGE_EVENTS_FILE", usage)
    return users, billing, usage


def test_default_free_trial_plan_change_and_account_billing_isolation(tmp_path, monkeypatch):
    users, billing, _ = _files(tmp_path, monkeypatch)
    assert subscription.subscription_for_account("A") == {"plan": "Free", "status": "Trial"}
    response = subscription.change_plan(_request(), "Starter")
    assert response.status_code == 303
    stored = json.loads(users.read_text(encoding="utf-8"))
    assert stored[0]["plan"] == "Starter" and stored[0]["subscription_status"] == "Trial"
    history = json.loads(billing.read_text(encoding="utf-8"))
    assert history == [{
        "account_id": "A", "created_at": history[0]["created_at"], "plan": "Starter",
        "status": "Trial", "amount": 0, "event": "Plan Change",
    }]
    audit = json.loads((tmp_path / "audit_log.json").read_text(encoding="utf-8"))
    assert audit[0]["action"] == "Change" and audit[0]["document_type"] == "Subscription"
    assert audit[0]["document_no"] == "Starter" and audit[0]["account_id"] == "A"
    own_html = subscription.subscription_page(_request("A")).body.decode()
    other_html = subscription.subscription_page(_request("B")).body.decode()
    assert "Starter" in own_html and "Plan Change" in own_html
    assert history[0]["created_at"] in own_html
    assert history[0]["created_at"] not in other_html and "Plan Change" not in other_html


def test_status_trial_active_expired_cancelled_and_admin_metrics(tmp_path, monkeypatch):
    users, _, _ = _files(tmp_path, monkeypatch)
    for status in subscription.SUBSCRIPTION_STATUSES:
        response = subscription.update_subscription_status("A", _request("B", "/admin/subscriptions/A/status", "POST"), status)
        assert response.status_code == 303
        assert subscription.subscription_for_account("A")["status"] == status
    subscription.update_subscription_status("A", _request("B"), "Active")
    subscription.change_plan(_request("A"), "Starter")
    subscription.update_subscription_status("A", _request("B"), "Active")
    html = subscription.subscription_admin(_request("B", "/admin/subscriptions")).body.decode()
    assert "Subscribers" in html and ">2<" in html
    assert "Paid Users" in html and "$0.00" in html


def test_free_limit_and_unlimited_paid_usage(tmp_path, monkeypatch):
    users, _, usage = _files(tmp_path, monkeypatch)
    now = datetime(2026, 8, 12, tzinfo=timezone.utc)
    for index in range(5):
        subscription.record_document_usage("A", f"/invoice-{index}", now)
    summary = subscription.usage_summary("A", now)
    assert summary == {"plan": "Free", "status": "Trial", "used": 5, "limit": 5, "allowed": False}
    rows = json.loads(users.read_text(encoding="utf-8"))
    rows[0].update({"plan": "Starter", "subscription_status": "Active"})
    users.write_text(json.dumps(rows), encoding="utf-8")
    assert subscription.usage_summary("A", now)["allowed"] is True
    assert subscription.usage_summary("B", now)["used"] == 0
    assert len(json.loads(usage.read_text(encoding="utf-8"))) == 5


def test_auth_middleware_blocks_limit_and_records_only_success(monkeypatch):
    monkeypatch.setattr(auth, "current_user", lambda request: {"account_id": "A", "email": "a@example.com"})
    monkeypatch.setattr(auth, "company_setup_complete", lambda account, path: True)
    monkeypatch.setattr(subscription, "usage_summary", lambda account: {"plan": "Free", "status": "Trial", "used": 5, "limit": 5, "allowed": False})
    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 201, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})
    middleware = auth.AuthenticationMiddleware(app)
    scope = {"type": "http", "method": "POST", "scheme": "http", "path": "/invoice", "raw_path": b"/invoice", "query_string": b"", "headers": [], "server": ("test", 80), "client": ("127.0.0.1", 1)}
    messages = []
    async def receive(): return {"type": "http.request", "body": b"", "more_body": False}
    async def send(message): messages.append(message)
    asyncio.run(middleware(scope, receive, send))
    assert messages[0]["status"] == 402

    recorded = []
    monkeypatch.setattr(subscription, "usage_summary", lambda account: {"plan": "Free", "status": "Trial", "used": 4, "limit": 5, "allowed": True})
    monkeypatch.setattr(subscription, "record_document_usage", lambda account, path: recorded.append((account, path)))
    messages.clear()
    asyncio.run(middleware(scope, receive, send))
    assert messages[0]["status"] == 201 and recorded == [("A", "/invoice")]


def test_pricing_and_dashboard_plan_markup(tmp_path, monkeypatch):
    _files(tmp_path, monkeypatch)
    pricing = subscription.pricing(_request()).body.decode()
    assert all(plan in pricing for plan in ("Free", "Starter", "Professional"))
    assert subscription.plan_price_label("Starter") == "₩29,000 / month"
    assert "₩29,000 / month" in pricing
    assert subscription.PLANS["Free"]["monthly_document_limit"] == 5
    assert subscription.plan_price_label("Professional") == "Contact"
    assert "Payment integration will be added" in pricing
