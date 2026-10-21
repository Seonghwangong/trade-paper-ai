import asyncio
from base64 import b64decode
from io import BytesIO
import json

import pytest
from pypdf import PdfReader

from app import analytics, auth, guest_sample, main


VALID = {"quantity": 120, "unit_price": 9.50, "cartons": 6, "net_weight": 24, "gross_weight": 30}


def request(path, payload=None):
    messages = []
    body = json.dumps(payload).encode() if payload is not None else b""

    async def run():
        async def receive():
            return {"type": "http.request", "body": body, "more_body": False}

        async def send(message):
            messages.append(message)

        await main.app({"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
                        "method": "POST" if payload is not None else "GET", "scheme": "http",
                        "path": path, "raw_path": path.encode(), "query_string": b"",
                        "root_path": "", "server": ("test", 80), "client": ("test", 1),
                        "headers": [(b"content-type", b"application/json")]}, receive, send)
    asyncio.run(run())
    start = next(message for message in messages if message["type"] == "http.response.start")
    return start["status"], dict(start["headers"]), b"".join(message.get("body", b"") for message in messages)


@pytest.fixture(autouse=True)
def isolate_analytics(monkeypatch, tmp_path):
    monkeypatch.setattr(analytics, "VISITOR_ANALYTICS_FILE", tmp_path / "visits.json")
    monkeypatch.setattr(auth, "current_user", lambda request: None)


def test_anonymous_page_is_accessible_and_private_routes_stay_protected():
    status, _, body = request("/try-sample")
    assert status == 200
    assert b'data-trade-paper-ux' not in body
    assert b'localStorage' not in body
    status, headers, _ = request("/invoice-list")
    assert status == 303
    assert headers[b"location"].startswith(b"/login")


def test_both_languages_have_no_unreplaced_tokens():
    for lang, heading in [("en", "Try one shipment"), ("ko", "가입 없이")]:
        body = guest_sample.practice_page(lang).body.decode()
        assert heading in body and f'lang="{lang}"' in body
        assert "__" not in body
        assert 'name="email"' not in body and 'name="password"' not in body


def test_generated_pair_matches_input_and_only_activity_is_stored(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Practice must not read private company data")
    monkeypatch.setattr("app.invoice.load_json_strict", forbidden)
    monkeypatch.setattr("app.packing.load_company", forbidden)
    status, headers, body = request("/try-sample/documents", VALID)
    assert status == 200 and headers[b"cache-control"] == b"no-store"
    pair = json.loads(body)
    assert set(pair) == {"invoice", "packing"}
    texts = {}
    for name, encoded in pair.items():
        pdf = b64decode(encoded)
        assert pdf.startswith(b"%PDF")
        pages = PdfReader(BytesIO(pdf)).pages
        assert len(pages) == 1
        text = pages[0].extract_text()
        texts[name] = text
        for value in ["FICTIONAL PRACTICE", "NOT FOR COMMERCIAL USE", "Sample Export Co.",
                      "Sample Import Co.", "PRACTICE-INV-001", "120"]:
            assert value in text
    assert "1140" in texts["invoice"].replace(",", "")
    for value in ["6", "24", "30"]:
        assert value in texts["packing"]
    assert "All weights in kg" in texts["packing"]
    records = json.loads(analytics.VISITOR_ANALYTICS_FILE.read_text())
    assert [row["page"] for row in records] == ["Practice Pair Generated"]
    assert all(set(row) == {"time", "page", "source"} for row in records)


@pytest.mark.parametrize("changes", [
    {"quantity": 0}, {"quantity": 1.5}, {"quantity": True}, {"quantity": 10001},
    {"unit_price": -1}, {"unit_price": "NaN"}, {"unit_price": "Infinity"},
    {"unit_price": 0.001}, {"unit_price": 1000.01}, {"cartons": 121},
    {"cartons": 0}, {"net_weight": 31}, {"gross_weight": 0},
    {"gross_weight": "NaN"}, {"seller": "Actual company"}, {"account_id": "other-account"},
])
def test_bad_input_does_not_render_or_count_success(changes, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Invalid input must never reach rendering")
    monkeypatch.setattr(guest_sample, "build_practice_pair", forbidden)
    assert request("/try-sample/documents", dict(VALID, **changes))[0] == 422
    assert not analytics.VISITOR_ANALYTICS_FILE.exists()


def test_signed_in_viewer_without_setup_can_practice_without_billing(monkeypatch):
    monkeypatch.setattr(auth, "current_user", lambda request: {"account_id": "viewer", "role": "Viewer"})
    monkeypatch.setattr(auth, "company_setup_complete", lambda *args: False)
    assert request("/try-sample")[0] == 200
    assert request("/try-sample/documents", VALID)[0] == 200


def test_pdf_still_returns_if_activity_logging_fails(monkeypatch):
    monkeypatch.setattr(analytics, "record_visit", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("unavailable")))
    assert request("/try-sample/documents", VALID)[0] == 200
