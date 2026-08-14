import json
import os
import subprocess
import sys
import uuid

import app.auth as auth
import pytest
from starlette.requests import Request


def _request_with_cookie(token):
    return Request({
        "type": "http",
        "method": "GET",
        "scheme": "http",
        "path": "/",
        "raw_path": b"/",
        "query_string": b"",
        "headers": [(b"cookie", f"{auth.SESSION_COOKIE}={token}".encode())],
        "client": ("127.0.0.1", 1),
        "server": ("testserver", 80),
    })


def _request(scheme="http", headers=None, client_host="127.0.0.1"):
    return Request({
        "type": "http",
        "method": "POST",
        "scheme": scheme,
        "path": "/login",
        "raw_path": b"/login",
        "query_string": b"",
        "headers": headers or [],
        "client": (client_host, 1),
        "server": ("testserver", 443 if scheme == "https" else 80),
    })


@pytest.fixture(autouse=True)
def reset_login_rate_limiter():
    auth._reset_login_rate_limiter()
    yield
    auth._reset_login_rate_limiter()


def test_registration_and_login_use_temporary_storage(tmp_path, monkeypatch):
    users_file = tmp_path / "users.json"
    users_file.write_text("[]\n", encoding="utf-8")
    monkeypatch.setattr(auth, "USERS_FILE", users_file)

    registered = auth.register("Test Company", "Owner@Test.com", "secret12", "secret12")
    assert registered.status_code == 303
    assert registered.headers["location"] == "/login?registered=1"

    users = json.loads(users_file.read_text(encoding="utf-8"))
    assert users[0]["company"] == "Test Company"
    assert users[0]["email"] == "owner@test.com"
    assert uuid.UUID(users[0]["account_id"]).version == 4
    assert users[0]["password"].startswith("pbkdf2_sha256$")
    assert users[0]["password"] != "secret12"
    companies = json.loads((tmp_path / "account_companies.json").read_text(encoding="utf-8"))
    assert companies == [{
        "account_id": users[0]["account_id"],
        "name": "Test Company",
        "address": "",
        "email": "",
        "phone": "",
        "setup_complete": False,
    }]

    duplicate = auth.register("Other Company", "OWNER@test.com", "secret12", "secret12")
    assert duplicate.status_code == 409
    assert len(json.loads(users_file.read_text(encoding="utf-8"))) == 1

    mismatch = auth.register("Other Company", "other@test.com", "secret12", "different")
    assert mismatch.status_code == 400

    invalid = auth.login("owner@test.com", "wrong")
    assert invalid.status_code == 401
    assert "Invalid email or password." in invalid.body.decode()

    logged_in = auth.login("OWNER@test.com", "secret12", "")
    assert logged_in.status_code == 303
    assert logged_in.headers["location"] == "/onboarding?next=%2F"
    assert "trade_paper_session=" in logged_in.headers["set-cookie"]
    assert "HttpOnly" in logged_in.headers["set-cookie"]
    assert "SameSite=lax" in logged_in.headers["set-cookie"]
    assert "Secure" not in logged_in.headers["set-cookie"]
    assert auth.login("owner@test.com", "secret12", "/buyers").headers["location"] == "/buyers"


def test_registration_password_policy_and_existing_login_compatibility(tmp_path, monkeypatch):
    users_file = tmp_path / "users.json"
    users_file.write_text(json.dumps([{
        "account_id": str(uuid.uuid4()),
        "company": "Existing Company",
        "email": "existing@example.com",
        "password": auth._password_hash("old"),
    }]), encoding="utf-8")
    monkeypatch.setattr(auth, "USERS_FILE", users_file)

    too_short = auth.register("Short Password", "short@example.com", "abcdef7", "abcdef7")
    assert too_short.status_code == 400
    assert "Password must be at least 8 characters." in too_short.body.decode()
    assert "Letters and numbers are recommended." in too_short.body.decode()

    eight_characters = auth.register("Eight Characters", "eight@example.com", "abcdefgh", "abcdefgh")
    assert eight_characters.status_code == 303

    letters_and_numbers = auth.register("Recommended Password", "recommended@example.com", "Trade123", "Trade123")
    assert letters_and_numbers.status_code == 303

    existing_login = auth.login("existing@example.com", "old", "")
    assert existing_login.status_code == 303

    register_page = auth.register_page().body.decode()
    assert 'minlength="8"' in register_page
    assert "Use at least 8 characters. Letters and numbers are recommended." in register_page


def test_session_identity_safe_redirects_and_plaintext_compatibility(tmp_path, monkeypatch):
    users_file = tmp_path / "users.json"
    users_file.write_text(
        json.dumps([{"company": "Legacy Company", "email": "legacy@example.com", "password": "legacy-pass"}]),
        encoding="utf-8",
    )
    monkeypatch.setattr(auth, "USERS_FILE", users_file)
    monkeypatch.setattr(auth, "_SESSION_SECRET", b"test-session-secret")

    assert auth.safe_next_path("/buyers") == "/buyers"
    assert auth.safe_next_path("/invoice-list?search=INV") == "/invoice-list?search=INV"
    assert auth.safe_next_path("https://example.com") == "/"
    assert auth.safe_next_path("//example.com") == "/"
    assert auth.safe_next_path("/\\example.com") == "/"

    migrated = auth.load_users()
    account_id = migrated[0]["account_id"]
    assert uuid.UUID(account_id).version == 4
    assert auth.load_users()[0]["account_id"] == account_id
    backup = json.loads((tmp_path / "users.backup.json").read_text(encoding="utf-8"))
    assert "account_id" not in backup[0]
    assert backup[0]["password"] == "legacy-pass"
    companies = json.loads((tmp_path / "account_companies.json").read_text(encoding="utf-8"))
    assert companies[0]["account_id"] == account_id
    assert companies[0]["name"] == "Legacy Company"
    assert companies[0]["setup_complete"] is False

    logged_in = auth.login("LEGACY@example.com", "legacy-pass", "/buyers")
    assert logged_in.headers["location"] == "/onboarding?next=%2Fbuyers"
    upgraded = json.loads(users_file.read_text(encoding="utf-8"))[0]
    assert upgraded["password"].startswith("pbkdf2_sha256$")
    assert upgraded["password"] != "legacy-pass"
    second_login = auth.login("legacy@example.com", "legacy-pass", "/buyers")
    assert second_login.status_code == 303 and second_login.headers["location"] == "/buyers"
    cookie = logged_in.headers["set-cookie"].split(";", 1)[0].split("=", 1)[1]
    user = auth.current_user(_request_with_cookie(cookie))
    assert user == {
        "account_id": account_id,
        "company": "Legacy Company",
        "email": "legacy@example.com",
        "role": "Owner",
    }
    assert "password" not in user
    assert auth.current_user(_request_with_cookie(cookie + "tampered")) is None

    external = auth.login("legacy@example.com", "legacy-pass", "https://example.com")
    protocol_relative = auth.login("legacy@example.com", "legacy-pass", "//example.com")
    assert external.headers["location"] == "/"
    assert protocol_relative.headers["location"] == "/"

    logged_out = auth.logout()
    assert logged_out.status_code == 303
    assert logged_out.headers["location"] == "/login"
    assert "Max-Age=0" in logged_out.headers["set-cookie"]


def test_plaintext_password_upgrade_is_atomic_and_preserves_identity(tmp_path, monkeypatch):
    users_file = tmp_path / "users.json"
    account_id = str(uuid.uuid4())
    original = [{
        "account_id": account_id,
        "company": "Legacy Upgrade Company",
        "email": "upgrade@example.com",
        "password": "legacy-password",
        "custom_field": "preserved",
    }]
    users_file.write_text(json.dumps(original, indent=2) + "\n", encoding="utf-8")
    monkeypatch.setattr(auth, "USERS_FILE", users_file)

    response = auth.login("UPGRADE@example.com", "legacy-password", "/buyers")

    assert response.status_code == 303
    assert response.headers["location"] == "/onboarding?next=%2Fbuyers"
    upgraded = json.loads(users_file.read_text(encoding="utf-8"))[0]
    assert upgraded["password"].startswith("pbkdf2_sha256$")
    assert upgraded["password"] != "legacy-password"
    assert {key: upgraded[key] for key in ("account_id", "company", "email", "custom_field")} == {
        "account_id": account_id,
        "company": "Legacy Upgrade Company",
        "email": "upgrade@example.com",
        "custom_field": "preserved",
    }
    assert json.loads((tmp_path / "users.backup.json").read_text(encoding="utf-8")) == original
    assert not list(tmp_path.glob(".users.json.*.tmp"))
    assert "legacy-password" not in response.body.decode("utf-8", errors="ignore")
    assert upgraded["password"] not in str(response.headers)
    assert auth.login("upgrade@example.com", "legacy-password", "/buyers").status_code == 303


def test_failed_plaintext_and_existing_pbkdf2_logins_do_not_write(tmp_path, monkeypatch):
    users_file = tmp_path / "users.json"
    account_id = str(uuid.uuid4())
    plaintext = [{
        "account_id": account_id,
        "company": "No Write Company",
        "email": "plain@example.com",
        "password": "correct-password",
    }]
    users_file.write_text(json.dumps(plaintext, indent=2) + "\n", encoding="utf-8")
    monkeypatch.setattr(auth, "USERS_FILE", users_file)
    before_invalid = users_file.read_bytes()

    invalid = auth.login("plain@example.com", "wrong-password", "")

    assert invalid.status_code == 401
    assert users_file.read_bytes() == before_invalid
    assert not (tmp_path / "users.backup.json").exists()
    assert "correct-password" not in invalid.body.decode("utf-8", errors="ignore")

    hashed = auth._password_hash("correct-password")
    hashed_records = [{**plaintext[0], "password": hashed}]
    users_file.write_text(json.dumps(hashed_records, indent=2) + "\n", encoding="utf-8")
    before_hashed = users_file.read_bytes()

    valid = auth.login("plain@example.com", "correct-password", "")

    assert valid.status_code == 303
    assert users_file.read_bytes() == before_hashed
    assert not (tmp_path / "users.backup.json").exists()
    assert hashed not in str(valid.headers)


def test_login_rate_limit_threshold_cooldown_and_no_extension(tmp_path, monkeypatch):
    users_file = tmp_path / "users.json"
    users_file.write_text("[]\n", encoding="utf-8")
    monkeypatch.setattr(auth, "USERS_FILE", users_file)
    now = [1_000.0]
    monkeypatch.setattr(auth, "_LOGIN_RATE_CLOCK", lambda: now[0])
    request = _request(client_host="192.0.2.10")

    for _ in range(4):
        response = auth.login("Missing@Example.com", "not-a-password", "", request)
        assert response.status_code == 401
        assert "Retry-After" not in response.headers

    threshold = auth.login("missing@example.com", "not-a-password", "", request)
    assert threshold.status_code == 401

    blocked = auth.login("MISSING@example.com", "not-a-password", "", request)
    assert blocked.status_code == 429
    assert blocked.headers["Retry-After"] == "60"
    assert "Invalid email or password." in blocked.body.decode()

    now[0] += 10
    still_blocked = auth.login("missing@example.com", "not-a-password", "", request)
    assert still_blocked.status_code == 429
    assert still_blocked.headers["Retry-After"] == "50"

    now[0] += 51
    cooldown_expired = auth.login("missing@example.com", "not-a-password", "", request)
    assert cooldown_expired.status_code == 401


def test_success_clears_failures_and_rate_keys_are_independent(tmp_path, monkeypatch):
    users_file = tmp_path / "users.json"
    users_file.write_text(json.dumps([{
        "account_id": str(uuid.uuid4()),
        "company": "Rate Limit Company",
        "email": "owner@example.com",
        "password": auth._password_hash("correct-password"),
    }]), encoding="utf-8")
    monkeypatch.setattr(auth, "USERS_FILE", users_file)
    monkeypatch.setattr(auth, "_LOGIN_RATE_CLOCK", lambda: 2_000.0)
    ip_a = _request(client_host="192.0.2.20")
    ip_b = _request(client_host="192.0.2.21")

    for _ in range(4):
        assert auth.login("OWNER@example.com", "wrong", "", ip_a).status_code == 401

    assert auth.login("owner@example.com", "correct-password", "/buyers", ip_a).status_code == 303
    for _ in range(5):
        assert auth.login("owner@example.com", "wrong", "", ip_a).status_code == 401
    assert auth.login("owner@example.com", "wrong", "", ip_a).status_code == 429

    assert auth.login("other@example.com", "wrong", "", ip_a).status_code == 401
    assert auth.login("owner@example.com", "wrong", "", ip_b).status_code == 401


def test_unknown_and_existing_users_have_same_visible_failure(tmp_path, monkeypatch):
    password_hash = auth._password_hash("correct-password")
    users_file = tmp_path / "users.json"
    users_file.write_text(json.dumps([{
        "account_id": str(uuid.uuid4()),
        "company": "Visible Error Company",
        "email": "known@example.com",
        "password": password_hash,
    }]), encoding="utf-8")
    monkeypatch.setattr(auth, "USERS_FILE", users_file)

    known = auth.login("known@example.com", "wrong-secret-value", "", _request(client_host="192.0.2.30"))
    unknown = auth.login("unknown@example.com", "wrong-secret-value", "", _request(client_host="192.0.2.31"))

    for response in (known, unknown):
        body = response.body.decode()
        assert response.status_code == 401
        assert "Invalid email or password." in body
        assert "wrong-secret-value" not in body
        assert password_hash not in body
        assert "Visible Error Company" not in body


def test_login_rate_limiter_is_bounded_and_cleans_expired_entries(monkeypatch):
    now = [3_000.0]
    monkeypatch.setattr(auth, "_LOGIN_RATE_CLOCK", lambda: now[0])
    monkeypatch.setattr(auth, "LOGIN_RATE_MAX_KEYS", 3)

    for index in range(5):
        auth._record_login_failure((f"192.0.2.{index}", f"user-{index}@example.com"))

    assert len(auth._LOGIN_RATE_ENTRIES) == 3
    assert ("192.0.2.0", "user-0@example.com") not in auth._LOGIN_RATE_ENTRIES
    assert ("192.0.2.4", "user-4@example.com") in auth._LOGIN_RATE_ENTRIES

    now[0] += auth.LOGIN_RATE_WINDOW_SECONDS + 1
    assert auth._login_rate_retry_after(("198.51.100.1", "new@example.com")) == 0
    assert auth._LOGIN_RATE_ENTRIES == {}


def test_login_rate_key_uses_request_client_and_normalizes_ip_and_email():
    expanded = _request(client_host="2001:0DB8:0000:0000:0000:0000:0000:0001")
    compressed = _request(client_host="2001:db8::1")

    assert auth._login_rate_key(expanded, "Owner@Example.COM") == (
        "2001:db8::1",
        "owner@example.com",
    )
    assert auth._login_rate_key(expanded, "Owner@Example.COM") == auth._login_rate_key(
        compressed,
        "owner@example.com",
    )


def test_session_cookie_secure_in_https_and_production(tmp_path, monkeypatch):
    users_file = tmp_path / "users.json"
    users_file.write_text(
        json.dumps([{"account_id": str(uuid.uuid4()), "company": "Secure Company", "email": "secure@example.com", "password": "secret"}]),
        encoding="utf-8",
    )
    monkeypatch.setattr(auth, "USERS_FILE", users_file)
    monkeypatch.delenv("TRADE_PAPER_ENV", raising=False)
    monkeypatch.delenv("TRADE_PAPER_SESSION_COOKIE_SECURE", raising=False)

    development = auth.login("secure@example.com", "secret", "", _request("http"))
    assert "Secure" not in development.headers["set-cookie"]

    https = auth.login("secure@example.com", "secret", "", _request("https"))
    assert "Secure" in https.headers["set-cookie"]

    forwarded_https = auth.login(
        "secure@example.com",
        "secret",
        "",
        _request("http", [(b"x-forwarded-proto", b"https")]),
    )
    assert "Secure" in forwarded_https.headers["set-cookie"]

    monkeypatch.setenv("TRADE_PAPER_ENV", "production")
    production = auth.login("secure@example.com", "secret", "", _request("http"))
    assert "Secure" in production.headers["set-cookie"]
    logged_out = auth.logout(_request("http"))
    assert "Secure" in logged_out.headers["set-cookie"]

    monkeypatch.setenv("TRADE_PAPER_SESSION_COOKIE_SECURE", "false")
    production_cannot_disable_secure = auth.login("secure@example.com", "secret", "", _request("http"))
    assert "Secure" in production_cannot_disable_secure.headers["set-cookie"]


def test_session_secret_environment_policy(caplog):
    development_a = auth._session_secret_from_environment({"TRADE_PAPER_ENV": "development"})
    development_b = auth._session_secret_from_environment({"TRADE_PAPER_ENV": "development"})
    test_secret = auth._session_secret_from_environment({"TRADE_PAPER_ENV": "test"})
    assert len(development_a) == 32
    assert len(development_b) == 32
    assert len(test_secret) == 32
    assert development_a != development_b

    for environment in ("production", "prod", "staging", "stage"):
        with pytest.raises(RuntimeError) as error:
            auth._session_secret_from_environment({"TRADE_PAPER_ENV": environment})
        assert "TRADE_PAPER_SESSION_SECRET" in str(error.value)
        assert "secret-value-must-not-appear" not in str(error.value)

    with pytest.raises(RuntimeError):
        auth._session_secret_from_environment({
            "TRADE_PAPER_ENV": "production",
            "TRADE_PAPER_SESSION_SECRET": "   ",
        })

    configured = "stable-session-secret-shared-by-workers"
    production_a = auth._session_secret_from_environment({
        "TRADE_PAPER_ENV": "production",
        "TRADE_PAPER_SESSION_SECRET": configured,
    })
    production_b = auth._session_secret_from_environment({
        "TRADE_PAPER_ENV": "production",
        "TRADE_PAPER_SESSION_SECRET": configured,
    })
    assert production_a == production_b == configured.encode("utf-8")

    short_secret = "compatible-short-secret"
    with caplog.at_level("WARNING", logger="trade-paper-ai.auth"):
        assert auth._session_secret_from_environment({
            "TRADE_PAPER_ENV": "production",
            "TRADE_PAPER_SESSION_SECRET": short_secret,
        }) == short_secret.encode("utf-8")
    assert "at least 32 characters" in caplog.text
    assert short_secret not in caplog.text


def test_same_session_secret_survives_restart_and_workers(monkeypatch):
    shared_secret = b"shared-production-secret-for-all-workers"
    monkeypatch.setattr(auth, "_SESSION_SECRET", shared_secret)
    token = auth._session_token("owner@example.com")

    monkeypatch.setattr(auth, "_SESSION_SECRET", bytes(shared_secret))
    assert auth._session_email(token) == "owner@example.com"

    monkeypatch.setattr(auth, "_SESSION_SECRET", b"different-production-secret-value")
    assert auth._session_email(token) == ""


def test_production_application_import_requires_session_secret():
    environment = os.environ.copy()
    environment["TRADE_PAPER_ENV"] = "production"
    environment.pop("TRADE_PAPER_SESSION_SECRET", None)
    missing = subprocess.run(
        [sys.executable, "-c", "import app.main"],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert missing.returncode != 0
    assert "TRADE_PAPER_SESSION_SECRET is required" in (missing.stdout + missing.stderr)

    environment["TRADE_PAPER_SESSION_SECRET"] = "stable-production-secret-shared-by-workers"
    configured = subprocess.run(
        [sys.executable, "-c", "import app.main"],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert configured.returncode == 0, configured.stderr
    assert environment["TRADE_PAPER_SESSION_SECRET"] not in (configured.stdout + configured.stderr)
