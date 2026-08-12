import hashlib
import json
import uuid

import pytest
from starlette.requests import Request

import app.auth as auth


def _request(client_host="192.0.2.10", path="/forgot-password"):
    return Request({
        "type": "http", "method": "POST", "scheme": "https", "path": path,
        "raw_path": path.encode(), "query_string": b"", "headers": [],
        "client": (client_host, 1), "server": ("testserver", 443),
    })


def _request_with_cookie(token):
    return Request({
        "type": "http", "method": "GET", "scheme": "https", "path": "/",
        "raw_path": b"/", "query_string": b"",
        "headers": [(b"cookie", f"{auth.SESSION_COOKIE}={token}".encode())],
        "client": ("192.0.2.20", 1), "server": ("testserver", 443),
    })


@pytest.fixture(autouse=True)
def reset_limiters():
    auth._reset_password_reset_rate_limiter()
    auth._reset_login_rate_limiter()
    yield
    auth._reset_password_reset_rate_limiter()
    auth._reset_login_rate_limiter()


@pytest.fixture
def reset_setup(tmp_path, monkeypatch):
    users_file = tmp_path / "users.json"
    users = [
        {
            "account_id": "account-a", "company": "Company A",
            "email": "owner-a@example.com", "password": auth._password_hash("legacy-a"),
        },
        {
            "account_id": "account-b", "company": "Company B",
            "email": "owner-b@example.com", "password": "legacy-b",
        },
    ]
    users_file.write_text(json.dumps(users), encoding="utf-8")
    monkeypatch.setattr(auth, "USERS_FILE", users_file)
    now = [1_700_000_000]
    monkeypatch.setattr(auth, "_PASSWORD_RESET_CLOCK", lambda: now[0])
    delivered = []
    monkeypatch.setattr(auth, "_deliver_password_reset", lambda email, token: delivered.append((email, token)))
    return users_file, now, delivered


def test_reset_request_is_generic_and_stores_only_current_token_hash(reset_setup):
    users_file, now, delivered = reset_setup
    existing = auth.request_password_reset("OWNER-A@example.com", _request())
    unknown = auth.request_password_reset("missing@example.com", _request("192.0.2.11"))

    assert existing.status_code == unknown.status_code == 200
    assert existing.body == unknown.body
    assert auth.PASSWORD_RESET_REQUEST_MESSAGE in existing.body.decode()
    assert len(delivered) == 1
    email, token = delivered[0]
    assert email == "owner-a@example.com"
    assert len(token) >= 43
    assert token.encode() not in existing.body

    records = json.loads(users_file.read_text())
    account_a = next(record for record in records if record["account_id"] == "account-a")
    account_b = next(record for record in records if record["account_id"] == "account-b")
    assert account_a["password_reset_token_hash"] == hashlib.sha256(token.encode()).hexdigest()
    assert account_a["password_reset_expires_at"] == now[0] + 30 * 60
    assert token not in users_file.read_text()
    assert "password_reset_token_hash" not in account_b

    now[0] += 61
    auth.request_password_reset("owner-a@example.com", _request())
    second_token = delivered[-1][1]
    assert second_token != token
    assert auth._valid_password_reset_record(token) is None
    assert auth._valid_password_reset_record(second_token)["account_id"] == "account-a"


def test_reset_is_single_use_enforces_policy_and_revokes_existing_sessions(reset_setup):
    users_file, _, delivered = reset_setup
    legacy_session = auth._session_token("owner-a@example.com", 0)
    assert auth.current_user(_request_with_cookie(legacy_session))["account_id"] == "account-a"

    auth.request_password_reset("owner-a@example.com", _request())
    token = delivered[0][1]
    short = auth.reset_password(token, "1234567", "1234567")
    assert short.status_code == 400
    assert "at least 8 characters" in short.body.decode()

    success = auth.reset_password(token, "NewPass8", "NewPass8")
    assert success.status_code == 303
    assert success.headers["location"] == "/login?reset=1"
    records = json.loads(users_file.read_text())
    account_a = next(record for record in records if record["account_id"] == "account-a")
    account_b = next(record for record in records if record["account_id"] == "account-b")
    assert account_a["session_version"] == 1
    assert "password_reset_token_hash" not in account_a
    assert account_b["password"] == "legacy-b"
    assert "session_version" not in account_b
    assert auth.current_user(_request_with_cookie(legacy_session)) is None
    assert auth.reset_password(token, "Another8", "Another8").status_code == 400
    assert auth.login("owner-a@example.com", "legacy-a", "", _request()).status_code == 401
    assert auth.login("owner-a@example.com", "NewPass8", "", _request()).status_code == 303
    assert auth.login("owner-b@example.com", "legacy-b", "", _request()).status_code == 303


def test_invalid_expired_mismatch_interval_and_rate_limit(reset_setup, monkeypatch):
    _, now, delivered = reset_setup
    assert auth.reset_password_page("not-a-token").status_code == 400
    assert auth.reset_password("not-a-token", "NewPass8", "NewPass8").status_code == 400

    auth.request_password_reset("owner-a@example.com", _request())
    token = delivered[0][1]
    mismatch = auth.reset_password(token, "NewPass8", "Different8")
    assert mismatch.status_code == 400
    auth.request_password_reset("owner-a@example.com", _request())
    assert len(delivered) == 1

    now[0] += auth.PASSWORD_RESET_TTL_SECONDS + 1
    assert auth.reset_password_page(token).status_code == 400
    assert auth.reset_password(token, "NewPass8", "NewPass8").status_code == 400

    monkeypatch.setattr(auth, "PASSWORD_RESET_RATE_LIMIT", 2)
    key = auth._password_reset_rate_key(_request("198.51.100.10"), "rate@example.com")
    assert auth._password_reset_rate_allowed(key) is True
    assert auth._password_reset_rate_allowed(key) is True
    assert auth._password_reset_rate_allowed(key) is False


def test_legacy_session_and_user_without_version_remain_compatible(reset_setup, monkeypatch):
    _, _, _ = reset_setup
    monkeypatch.setattr(auth, "_SESSION_SECRET", b"password-reset-test-secret")
    email = "owner-a@example.com"
    issued_at = 1_700_000_000
    monkeypatch.setattr(auth.time, "time", lambda: issued_at)
    signed = f"{email}|{issued_at}"
    signature = __import__("hmac").new(
        auth._SESSION_SECRET, signed.encode(), hashlib.sha256,
    ).hexdigest()
    legacy_token = __import__("base64").urlsafe_b64encode(
        f"{signed}|{signature}".encode(),
    ).decode().rstrip("=")
    assert auth._session_email(legacy_token) == email
    assert auth.current_user(_request_with_cookie(legacy_token))["account_id"] == "account-a"
