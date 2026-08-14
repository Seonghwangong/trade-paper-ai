from collections import Counter
import asyncio
import inspect

import app.main as main
import pytest
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.requests import Request
from tests.helpers import normalize_html, route_snapshot_digest


EXPECTED_ROUTE_DIGEST = "fc002609e8781f57a065538cbeb5c908a8106cf99a6fe7cf9a9e4b1e8a241cc3"


def test_application_import_and_route_order():
    assert main.app is not None
    assert len(main.app.routes) == 258
    assert route_snapshot_digest(main.app) == EXPECTED_ROUTE_DIGEST


def test_email_readiness_route_reuses_admin_dashboard_authorization():
    route = next(
        route for route in main.app.routes
        if route.path == "/admin/email-readiness" and "GET" in (route.methods or set())
    )
    assert "auth.require_admin(request)" in inspect.getsource(route.endpoint)


def test_no_route_duplicates():
    routes = [
        (method, route.path)
        for route in main.app.routes
        for method in sorted(getattr(route, "methods", None) or [])
        if method not in {"HEAD", "OPTIONS"}
    ]
    duplicates = {key: count for key, count in Counter(routes).items() if count > 1}
    assert duplicates == {}


def test_dashboard_and_search_render(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "load_account_company", lambda account_id, path: {})
    buyers_file = tmp_path / "buyers.json"
    products_file = tmp_path / "products.json"
    invoices_file = tmp_path / "invoices.json"
    packing_file = tmp_path / "packing_lists.json"
    shipping_file = tmp_path / "shipping_instructions.json"
    booking_file = tmp_path / "booking_confirmations.json"
    shipment_file = tmp_path / "shipments.json"
    users_file = tmp_path / "users.json"
    beta_file = tmp_path / "beta_applications.json"
    feedback_file = tmp_path / "feedback.json"
    buyers_file.write_text("[]\n", encoding="utf-8")
    products_file.write_text("[]\n", encoding="utf-8")
    invoices_file.write_text("[]\n", encoding="utf-8")
    packing_file.write_text("[]\n", encoding="utf-8")
    shipping_file.write_text("[]\n", encoding="utf-8")
    booking_file.write_text("[]\n", encoding="utf-8")
    shipment_file.write_text("[]\n", encoding="utf-8")
    users_file.write_text("[]\n", encoding="utf-8")
    beta_file.write_text('[{"company_name":"Recent Co","contact_name":"Kim","email":"kim@example.com","status":"New"}]\n', encoding="utf-8")
    feedback_file.write_text('[{"category":"Bug","rating":"5","feedback":"Recent feedback"}]\n', encoding="utf-8")
    monkeypatch.setattr(main.buyer_module, "BUYER_FILE", buyers_file)
    monkeypatch.setattr(main.buyer_module, "USERS_FILE", users_file)
    monkeypatch.setattr(main.product_module, "PRODUCT_FILE", products_file)
    monkeypatch.setattr(main.product_module, "USERS_FILE", users_file)
    monkeypatch.setattr(main.invoice_module, "INVOICE_FILE", invoices_file)
    monkeypatch.setattr(main.invoice_module, "USERS_FILE", users_file)
    monkeypatch.setattr(main.packing_module, "PACKING_FILE", packing_file)
    monkeypatch.setattr(main.packing_module, "USERS_FILE", users_file)
    monkeypatch.setattr(main.shipping_instruction_module, "SI_FILE", shipping_file)
    monkeypatch.setattr(main.shipping_instruction_module, "USERS_FILE", users_file)
    monkeypatch.setattr(main.booking_module, "BOOKING_FILE", booking_file)
    monkeypatch.setattr(main.booking_module, "USERS_FILE", users_file)
    monkeypatch.setattr(main.shipment_module, "SHIPMENT_FILE", shipment_file)
    monkeypatch.setattr(main.shipment_module, "USERS_FILE", users_file)
    monkeypatch.setattr(main.founding_beta_module, "BETA_APPLICATION_FILE", beta_file)
    monkeypatch.setattr(main.feedback_module, "FEEDBACK_FILE", feedback_file)
    monkeypatch.setattr(main.container_module, "load_containers", lambda account_id: [])
    request = Request({
        "type": "http",
        "method": "GET",
        "scheme": "http",
        "path": "/",
        "raw_path": b"/",
        "query_string": b"",
        "headers": [],
        "client": ("127.0.0.1", 1),
        "server": ("testserver", 80),
        "trade_paper_user": {"account_id": "test-account"},
    })
    dashboard = normalize_html(main.home(request))
    search = normalize_html(main.global_search(request, ""))
    assert "Trade Paper AI" in dashboard
    assert dashboard.count("Shipment Summary") == 1
    assert "Founding Beta" in dashboard
    assert "최근 신청 5건" in dashboard
    assert "Recent Co" in dashboard
    assert "최근 Feedback 5건" in dashboard
    assert "Recent feedback" in dashboard
    assert "Global Search" in search


def test_operations_dashboard_summary_counts_and_recent_order():
    applications = [
        {"company_name": "Old", "status": ""},
        {"company_name": "Middle", "status": "Contacted"},
        {"company_name": "Latest", "status": "Beta Customer"},
        {"company_name": "Closed", "status": "Closed"},
    ]
    feedback = [
        {"feedback": "Old", "category": "Feature Request"},
        {"feedback": "Middle", "category": "Bug"},
        {"feedback": "Latest", "category": "UI/UX"},
    ]

    summary = main.operations_dashboard_summary(applications, feedback, limit=2)

    assert summary["beta_counts"] == {
        "New": 1,
        "Contacted": 1,
        "Demo Scheduled": 0,
        "Beta Customer": 1,
    }
    assert summary["feedback_counts"] == {"Total": 3, "Bug": 1, "Feature": 1, "UI/UX": 1}
    assert [item["company_name"] for item in summary["recent_applications"]] == ["Closed", "Latest"]
    assert [item["feedback"] for item in summary["recent_feedback"]] == ["Latest", "Middle"]


def test_packing_success_exposes_shipping_instruction_next_action():
    html = main.packing_page().body.decode()
    assert "showPackingNextActions(result.packing_no)" in html
    assert "Create Shipping Instruction" in html
    assert '"/si-form?packing_no="+encodeURIComponent(packingNo)' in html
    assert 'if(shipmentNo){await window.tpSavedThenRedirect("/shipment/"+encodeURIComponent(shipmentNo));return;}' in html


def _cors_test_app(configuration):
    application = FastAPI()
    application.add_middleware(
        CORSMiddleware,
        allow_origins=configuration["allow_origins"],
        allow_origin_regex=configuration["allow_origin_regex"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @application.get("/probe")
    def probe():
        return {"ok": True}

    return application


def _cors_get(application, origin=None):
    messages = []
    request_headers = [] if origin is None else [(b"origin", origin.encode("latin-1"))]
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/probe",
        "raw_path": b"/probe",
        "query_string": b"",
        "headers": request_headers,
        "client": ("127.0.0.1", 1),
        "server": ("testserver", 80),
    }
    received = {"value": False}

    async def receive():
        if not received["value"]:
            received["value"] = True
            return {"type": "http.request", "body": b"", "more_body": False}
        return {"type": "http.disconnect"}

    async def send(message):
        messages.append(message)

    asyncio.run(application(scope, receive, send))
    start = next(message for message in messages if message["type"] == "http.response.start")
    return start["status"], {
        key.decode("latin-1").lower(): value.decode("latin-1")
        for key, value in start["headers"]
    }


def test_cors_development_localhost_and_same_origin_behavior():
    configuration = main.cors_configuration({"TRADE_PAPER_ENV": "development"})
    assert configuration["allow_origins"] == []
    assert configuration["allow_origin_regex"] == main._LOCAL_CORS_ORIGIN_REGEX
    application = _cors_test_app(configuration)

    for origin in ("http://localhost:5173", "http://127.0.0.1:8002"):
        status, headers = _cors_get(application, origin)
        assert status == 200
        assert headers["access-control-allow-origin"] == origin
        assert headers["access-control-allow-credentials"] == "true"

    status, headers = _cors_get(application)
    assert status == 200
    assert "access-control-allow-origin" not in headers


def test_cors_production_allowlist_and_fail_closed_default():
    configuration = main.cors_configuration({
        "TRADE_PAPER_ENV": "production",
        "TRADE_PAPER_CORS_ORIGINS": " https://app.example.com,https://APP.example.com/, https://api.example.com:443 ",
    })
    assert configuration == {
        "allow_origins": ["https://app.example.com", "https://api.example.com"],
        "allow_origin_regex": None,
    }
    assert "*" not in configuration["allow_origins"]
    application = _cors_test_app(configuration)

    _, allowed_headers = _cors_get(application, "https://app.example.com")
    assert allowed_headers["access-control-allow-origin"] == "https://app.example.com"
    assert allowed_headers["access-control-allow-credentials"] == "true"

    denied_status, denied_headers = _cors_get(application, "https://evil.example.com")
    assert denied_status == 200
    assert "access-control-allow-origin" not in denied_headers

    missing = main.cors_configuration({"TRADE_PAPER_ENV": "staging"})
    assert missing == {"allow_origins": [], "allow_origin_regex": None}
    _, missing_headers = _cors_get(_cors_test_app(missing), "https://app.example.com")
    assert "access-control-allow-origin" not in missing_headers


def test_production_configuration_requires_explicit_storage_and_one_worker(tmp_path):
    development = {"TRADE_PAPER_ENV": "development", "TRADE_PAPER_EMAIL_BACKEND": "disabled"}
    assert main.validate_production_configuration(development) is None

    production = {
        "TRADE_PAPER_ENV": "production",
        "TRADE_PAPER_EMAIL_BACKEND": "disabled",
        "TRADE_PAPER_DATA_DIR": str(tmp_path),
        "TRADE_PAPER_SESSION_SECRET": "s" * 32,
        "TRADE_PAPER_PUBLIC_BASE_URL": "https://trade.example.com",
        "TRADE_PAPER_CONTACT_EMAIL": "support@trade.example.com",
        "WEB_CONCURRENCY": "1",
    }
    assert main.validate_production_configuration(production) is None

    for invalid in (
        {**production, "TRADE_PAPER_DATA_DIR": ""},
        {**production, "WEB_CONCURRENCY": "2"},
        {**production, "WEB_CONCURRENCY": "many"},
    ):
        with pytest.raises(RuntimeError):
            main.validate_production_configuration(invalid)


def test_deployment_readiness_validates_required_production_settings(tmp_path):
    production = {
        "TRADE_PAPER_ENV": "production",
        "TRADE_PAPER_EMAIL_BACKEND": "disabled",
        "TRADE_PAPER_DATA_DIR": str(tmp_path),
        "TRADE_PAPER_SESSION_SECRET": "s" * 32,
        "TRADE_PAPER_PUBLIC_BASE_URL": "https://trade.example.com",
        "TRADE_PAPER_CONTACT_URL": "https://trade.example.com/contact",
        "WEB_CONCURRENCY": "1",
    }
    report = main.deployment_readiness(production)
    assert report["email_backend"] == "disabled"
    assert report["email_configuration"] == "Not Ready"
    assert report["warnings"] == [
        "SMTP/email delivery is disabled; password reset email delivery is unavailable."
    ]

    missing_dir = tmp_path / "missing"
    invalid_cases = (
        ({**production, "TRADE_PAPER_DATA_DIR": str(missing_dir)}, "must exist"),
        ({**production, "TRADE_PAPER_SESSION_SECRET": "short"}, "at least 32"),
        ({**production, "TRADE_PAPER_PUBLIC_BASE_URL": "http://trade.example.com"}, "valid HTTPS origin"),
        ({**production, "TRADE_PAPER_CONTACT_URL": "javascript:alert(1)", "TRADE_PAPER_CONTACT_EMAIL": ""}, "CONTACT_EMAIL"),
    )
    for environment, message in invalid_cases:
        with pytest.raises(RuntimeError, match=message):
            main.deployment_readiness(environment)


def test_deployment_readiness_rejects_non_writable_data_dir(tmp_path, monkeypatch):
    environment = {
        "TRADE_PAPER_ENV": "production",
        "TRADE_PAPER_EMAIL_BACKEND": "disabled",
        "TRADE_PAPER_DATA_DIR": str(tmp_path),
        "TRADE_PAPER_SESSION_SECRET": "s" * 32,
        "TRADE_PAPER_PUBLIC_BASE_URL": "https://trade.example.com",
        "TRADE_PAPER_CONTACT_EMAIL": "support@trade.example.com",
        "WEB_CONCURRENCY": "1",
    }
    monkeypatch.setattr(main.os, "access", lambda path, mode: False)
    with pytest.raises(RuntimeError, match="must be writable"):
        main.deployment_readiness(environment)


def test_health_endpoint_contains_only_public_release_metadata():
    assert main.health() == {
        "status": "ok",
        "version": "3.5.0",
        "release": "Founding Beta",
    }


def test_startup_log_reports_version_release_and_completion(monkeypatch, capsys):
    monkeypatch.setattr(main, "deployment_readiness", lambda **kwargs: {"warnings": []})
    monkeypatch.setattr(main, "audit_route_registrations", lambda application: {
        "exact_conflicts": {}, "structural_conflicts": {},
    })
    main.startup_stability_audit()
    assert capsys.readouterr().out == (
        "Trade Paper AI\nVersion 3.5.0\nFounding Beta\nStartup Complete\n"
    )


@pytest.mark.parametrize("origin", [
    "*",
    "ftp://app.example.com",
    "https://user:password@app.example.com",
    "https://app.example.com/path",
    "https://*.example.com",
    "https://bad host.example.com",
    "https://app.example.com?query=1",
])
def test_cors_invalid_origins_fail_clearly_without_echoing_value(origin):
    with pytest.raises(RuntimeError) as error:
        main.cors_configuration({
            "TRADE_PAPER_ENV": "production",
            "TRADE_PAPER_CORS_ORIGINS": origin,
        })
    assert "TRADE_PAPER_CORS_ORIGINS contains an invalid origin" in str(error.value)
    assert origin not in str(error.value)
