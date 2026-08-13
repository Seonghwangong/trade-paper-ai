from __future__ import annotations

import base64
from collections import OrderedDict
import hashlib
import hmac
import ipaddress
import logging
import math
import os
import re
import secrets
import threading
import time
import uuid

from urllib.parse import quote, urlsplit

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.release import APP_NAME
from app.landing import landing_page
from app import email_delivery
from app.account_company import company_setup_complete, ensure_account_companies
from app.storage import data_path, load_json_strict, locked_json_mutation


router = APIRouter()
USERS_FILE = data_path("users.json")
PASSWORD_ITERATIONS = 260_000
REGISTRATION_PASSWORD_MIN_LENGTH = 8
SESSION_MAX_AGE = 8 * 60 * 60
SESSION_COOKIE = "trade_paper_session"
_EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
_PRODUCTION_ENVIRONMENTS = frozenset({"production", "prod", "staging", "stage"})
_SESSION_SECRET_RECOMMENDED_LENGTH = 32
LOGIN_RATE_WINDOW_SECONDS = 10 * 60
LOGIN_RATE_FAILURE_LIMIT = 5
LOGIN_RATE_COOLDOWN_SECONDS = 60
LOGIN_RATE_MAX_KEYS = 10_000
PASSWORD_RESET_TTL_SECONDS = 30 * 60
PASSWORD_RESET_REQUEST_INTERVAL_SECONDS = 60
PASSWORD_RESET_RATE_WINDOW_SECONDS = 60 * 60
PASSWORD_RESET_RATE_LIMIT = 5
PASSWORD_RESET_RATE_MAX_KEYS = 10_000
PUBLIC_PATHS = frozenset({
    "/login", "/register", "/logout", "/forgot-password", "/reset-password",
    "/privacy", "/terms", "/status", "/health", "/healthz",
    "/founding-beta", "/founding-beta/thank-you",
    "/feedback", "/feedback/thank-you",
})
COMPANY_SETUP_PATHS = frozenset({
    "/company", "/company-data", "/save-company", "/logout",
    "/forgot-password", "/reset-password",
})

logger = logging.getLogger("trade-paper-ai.auth")
_LOGIN_RATE_LOCK = threading.Lock()
_LOGIN_RATE_ENTRIES = OrderedDict()
_LOGIN_RATE_CLOCK = time.monotonic
_PASSWORD_RESET_RATE_LOCK = threading.Lock()
_PASSWORD_RESET_RATE_ENTRIES = OrderedDict()
_PASSWORD_RESET_CLOCK = time.time


def _session_secret_from_environment(environment=None):
    """Load a stable production secret, retaining an ephemeral local/test fallback."""
    source = os.environ if environment is None else environment
    deployment = str(source.get("TRADE_PAPER_ENV", "") or "").strip().casefold()
    configured = str(source.get("TRADE_PAPER_SESSION_SECRET", "") or "")
    has_configured_secret = bool(configured.strip())
    if deployment in _PRODUCTION_ENVIRONMENTS and not has_configured_secret:
        raise RuntimeError(
            "TRADE_PAPER_SESSION_SECRET is required for production and staging startup."
        )
    if has_configured_secret:
        if len(configured) < _SESSION_SECRET_RECOMMENDED_LENGTH:
            logger.warning(
                "TRADE_PAPER_SESSION_SECRET should contain at least %d characters; "
                "the configured value remains supported for compatibility.",
                _SESSION_SECRET_RECOMMENDED_LENGTH,
            )
        return configured.encode("utf-8")
    return secrets.token_bytes(32)


_SESSION_SECRET = _session_secret_from_environment()


def _account_companies_file():
    return USERS_FILE.with_name("account_companies.json")


def _legacy_company_file():
    return USERS_FILE.with_name("company.json")


def load_users():
    users = load_json_strict(USERS_FILE, [], list)
    if any(isinstance(record, dict) and not str(record.get("account_id", "") or "").strip() for record in users):
        def assign_legacy_account_ids(records):
            for record in records:
                if isinstance(record, dict) and not str(record.get("account_id", "") or "").strip():
                    record["account_id"] = str(uuid.uuid4())
            return records

        users = locked_json_mutation(USERS_FILE, [], assign_legacy_account_ids, list)
    ensure_account_companies(users, _account_companies_file(), _legacy_company_file())
    return users


def _normalized_email(value):
    return str(value or "").strip().casefold()


def _integer(value, default=0):
    try:
        return int(value or default)
    except (TypeError, ValueError):
        return default


def _normalized_client_ip(request):
    client = getattr(request, "client", None) if request is not None else None
    value = str(getattr(client, "host", "") or "").strip()
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        return value.casefold()


def _login_rate_key(request, email):
    return (_normalized_client_ip(request), _normalized_email(email))


def _prune_login_rate_entries(now):
    for key, entry in list(_LOGIN_RATE_ENTRIES.items()):
        entry["failures"] = [
            timestamp for timestamp in entry["failures"]
            if now - timestamp < LOGIN_RATE_WINDOW_SECONDS
        ]
        if entry["blocked_until"] <= now:
            entry["blocked_until"] = 0.0
        if not entry["failures"] and not entry["blocked_until"]:
            del _LOGIN_RATE_ENTRIES[key]


def _login_rate_retry_after(key):
    now = _LOGIN_RATE_CLOCK()
    with _LOGIN_RATE_LOCK:
        _prune_login_rate_entries(now)
        entry = _LOGIN_RATE_ENTRIES.get(key)
        if entry is None or entry["blocked_until"] <= now:
            return 0
        _LOGIN_RATE_ENTRIES.move_to_end(key)
        return max(1, math.ceil(entry["blocked_until"] - now))


def _record_login_failure(key):
    now = _LOGIN_RATE_CLOCK()
    with _LOGIN_RATE_LOCK:
        _prune_login_rate_entries(now)
        entry = _LOGIN_RATE_ENTRIES.get(key)
        if entry is None:
            while len(_LOGIN_RATE_ENTRIES) >= LOGIN_RATE_MAX_KEYS:
                _LOGIN_RATE_ENTRIES.popitem(last=False)
            entry = {"failures": [], "blocked_until": 0.0}
            _LOGIN_RATE_ENTRIES[key] = entry
        entry["failures"].append(now)
        if len(entry["failures"]) >= LOGIN_RATE_FAILURE_LIMIT:
            entry["blocked_until"] = now + LOGIN_RATE_COOLDOWN_SECONDS
        _LOGIN_RATE_ENTRIES.move_to_end(key)


def _clear_login_failures(key):
    with _LOGIN_RATE_LOCK:
        _LOGIN_RATE_ENTRIES.pop(key, None)


def _reset_login_rate_limiter():
    """Clear process-local limiter state for isolated application tests."""
    with _LOGIN_RATE_LOCK:
        _LOGIN_RATE_ENTRIES.clear()


def _password_reset_rate_key(request, email):
    return (_normalized_client_ip(request), _normalized_email(email))


def _password_reset_rate_allowed(key):
    now = _PASSWORD_RESET_CLOCK()
    with _PASSWORD_RESET_RATE_LOCK:
        for existing_key, timestamps in list(_PASSWORD_RESET_RATE_ENTRIES.items()):
            active = [
                timestamp for timestamp in timestamps
                if now - timestamp < PASSWORD_RESET_RATE_WINDOW_SECONDS
            ]
            if active:
                _PASSWORD_RESET_RATE_ENTRIES[existing_key] = active
            else:
                del _PASSWORD_RESET_RATE_ENTRIES[existing_key]
        timestamps = _PASSWORD_RESET_RATE_ENTRIES.get(key, [])
        if len(timestamps) >= PASSWORD_RESET_RATE_LIMIT:
            return False
        while len(_PASSWORD_RESET_RATE_ENTRIES) >= PASSWORD_RESET_RATE_MAX_KEYS and key not in _PASSWORD_RESET_RATE_ENTRIES:
            _PASSWORD_RESET_RATE_ENTRIES.popitem(last=False)
        timestamps.append(now)
        _PASSWORD_RESET_RATE_ENTRIES[key] = timestamps
        _PASSWORD_RESET_RATE_ENTRIES.move_to_end(key)
        return True


def _reset_password_reset_rate_limiter():
    with _PASSWORD_RESET_RATE_LOCK:
        _PASSWORD_RESET_RATE_ENTRIES.clear()


def _password_hash(password):
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PASSWORD_ITERATIONS)
    return "pbkdf2_sha256${}${}${}".format(
        PASSWORD_ITERATIONS,
        base64.urlsafe_b64encode(salt).decode("ascii").rstrip("="),
        base64.urlsafe_b64encode(digest).decode("ascii").rstrip("="),
    )


def _decode_base64(value):
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _password_is_pbkdf2(stored):
    return str(stored or "").startswith("pbkdf2_sha256$")


def _password_matches(password, stored):
    stored = str(stored or "")
    if not _password_is_pbkdf2(stored):
        return hmac.compare_digest(stored, password)
    try:
        _, iterations, encoded_salt, encoded_digest = stored.split("$", 3)
        actual = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            _decode_base64(encoded_salt),
            int(iterations),
        )
        return hmac.compare_digest(actual, _decode_base64(encoded_digest))
    except (TypeError, ValueError):
        return False
def _upgrade_legacy_password(email, password):
    """Replace one successfully verified plaintext password under the storage lock."""
    upgraded = {"value": False}

    def upgrade(users):
        record = next(
            (
                item for item in users
                if isinstance(item, dict)
                and _normalized_email(item.get("email")) == email
            ),
            None,
        )
        if record is None:
            return
        stored = str(record.get("password", "") or "")
        if _password_is_pbkdf2(stored) or not hmac.compare_digest(stored, password):
            return
        record["password"] = _password_hash(password)
        upgraded["value"] = True

    locked_json_mutation(USERS_FILE, [], upgrade, list)
    return upgraded["value"]


def _session_token(email, session_version=0):
    payload = f"{email}|{int(time.time())}|{int(session_version or 0)}"
    signature = hmac.new(_SESSION_SECRET, payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return base64.urlsafe_b64encode(f"{payload}|{signature}".encode("utf-8")).decode("ascii").rstrip("=")


def _session_claims(token):
    try:
        payload = _decode_base64(str(token or "")).decode("utf-8")
        parts = payload.rsplit("|", 3)
        if len(parts) == 4:
            email, issued_at, session_version, signature = parts
            signed = f"{email}|{issued_at}|{session_version}"
        else:
            email, issued_at, signature = payload.rsplit("|", 2)
            session_version = "0"
            signed = f"{email}|{issued_at}"
        expected = hmac.new(_SESSION_SECRET, signed.encode("utf-8"), hashlib.sha256).hexdigest()
        age = int(time.time()) - int(issued_at)
        if age < 0 or age > SESSION_MAX_AGE or not hmac.compare_digest(signature, expected):
            return None
        return _normalized_email(email), int(session_version)
    except (TypeError, ValueError, UnicodeDecodeError):
        return None


def _session_email(token):
    claims = _session_claims(token)
    return claims[0] if claims else ""


def current_user(request):
    """Return only safe identity fields for a valid login session."""
    claims = _session_claims(request.cookies.get(SESSION_COOKIE, ""))
    if not claims:
        return None
    email, token_session_version = claims
    record = next(
        (item for item in load_users() if isinstance(item, dict) and _normalized_email(item.get("email")) == email),
        None,
    )
    if record is None:
        return None
    try:
        current_session_version = _integer(record.get("session_version", 0))
    except (TypeError, ValueError):
        return None
    if token_session_version != current_session_version:
        return None
    return {
        "account_id": str(record.get("account_id", "")).strip(),
        "company": str(record.get("company", "")).strip(),
        "email": email,
    }


def safe_next_path(value):
    """Accept an application-local path and reject open-redirect forms."""
    candidate = str(value or "").strip()
    if not candidate.startswith("/") or candidate.startswith("//") or "\\" in candidate:
        return "/"
    parsed = urlsplit(candidate)
    if parsed.scheme or parsed.netloc:
        return "/"
    return parsed.path + (f"?{parsed.query}" if parsed.query else "")


def session_cookie_secure(request=None):
    """Use Secure cookies for production/HTTPS while preserving local HTTP development."""
    configured = str(os.environ.get("TRADE_PAPER_SESSION_COOKIE_SECURE", "") or "").strip().casefold()
    if configured in {"1", "true", "yes", "on"}:
        return True

    environment = str(os.environ.get("TRADE_PAPER_ENV", "") or "").strip().casefold()
    if environment in _PRODUCTION_ENVIRONMENTS:
        return True
    if request is None:
        return False

    forwarded_proto = str(request.headers.get("x-forwarded-proto", "") or "").split(",", 1)[0].strip().casefold()
    return request.url.scheme.casefold() == "https" or forwarded_proto == "https"


class AuthenticationMiddleware:
    """Central session guard for application HTTP routes."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        request = Request(scope)
        path = request.url.path
        is_public = path in PUBLIC_PATHS or path == "/static" or path.startswith("/static/") or request.method == "OPTIONS"
        user = current_user(request)
        if path == "/" and request.method == "GET" and user is None:
            response = landing_page()
            await response(scope, receive, send)
            return
        if not is_public and user is None:
            requested = path + (f"?{request.url.query}" if request.url.query else "")
            response = RedirectResponse(f"/login?next={quote(requested, safe='')}", status_code=303)
            await response(scope, receive, send)
            return
        if user is not None:
            scope["trade_paper_user"] = user
            setup_allowed = (
                path in COMPANY_SETUP_PATHS
                or path == "/static"
                or path.startswith("/static/")
                or request.method == "OPTIONS"
            )
            if (
                not setup_allowed
                and not company_setup_complete(user["account_id"], _account_companies_file())
            ):
                requested = path + (f"?{request.url.query}" if request.url.query else "")
                response = RedirectResponse(
                    f"/company?setup=1&next={quote(requested, safe='')}",
                    status_code=303,
                )
                await response(scope, receive, send)
                return
        if user is not None:
            from app import subscription
            if subscription.is_document_creation(request):
                summary = subscription.usage_summary(user["account_id"])
                if not summary["allowed"]:
                    response = subscription.usage_limit_response(summary)
                    await response(scope, receive, send)
                    return
                response_status = {"value": 500}
                recorded = {"value": False}
                async def usage_send(message):
                    if message.get("type") == "http.response.start":
                        response_status["value"] = int(message.get("status", 500))
                    if (
                        message.get("type") == "http.response.body"
                        and not message.get("more_body", False)
                        and 200 <= response_status["value"] < 400
                        and not recorded["value"]
                    ):
                        subscription.record_document_usage(user["account_id"], path)
                        recorded["value"] = True
                    await send(message)
                await self.app(scope, receive, usage_send)
                return
        await self.app(scope, receive, send)


def _auth_page(mode, *, error="", registered=False, reset=False, company="", email="", next_path="", status_code=200):
    register = mode == "register"
    title = "Create your account" if register else "Welcome back"
    subtitle = "Register your company to start using Trade Paper AI." if register else "Sign in to continue to Trade Paper AI."
    error_html = f'<div class="message error" role="alert">{_escape(error)}</div>' if error else ""
    success_html = '<div class="message success" role="status">Registration successful. Please sign in.</div>' if registered else ""
    if reset:
        success_html = '<div class="message success" role="status">Password reset successful. Please sign in.</div>'
    company_html = f'<label for="company">Company Name</label><input id="company" name="company" value="{_escape(company, True)}" autocomplete="organization" required>' if register else ""
    confirm_html = '<label for="confirm_password">Confirm Password</label><input id="confirm_password" name="confirm_password" type="password" autocomplete="new-password" minlength="8" required>' if register else ""
    password_help = '<p id="password-help" class="field-help">Use at least 8 characters. Letters and numbers are recommended.</p>' if register else ""
    password_constraints = ' minlength="8" aria-describedby="password-help"' if register else ""
    password_autocomplete = "new-password" if register else "current-password"
    alternate = '<p class="alternate">Already have an account? <a href="/login">Sign in</a></p>' if register else '<p class="alternate"><a href="/forgot-password">Forgot password?</a><br><br>New to Trade Paper AI? <a href="/register">Create an account</a></p>'
    next_html = f'<input type="hidden" name="next" value="{_escape(next_path, True)}">' if not register and next_path else ""
    return HTMLResponse(f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{title} · {APP_NAME}</title><style>
*{{box-sizing:border-box}}body{{margin:0;background:#F3F4F6;color:#111827;font-family:Arial,sans-serif}}.auth-page{{min-height:100vh;display:grid;place-items:center;padding:28px}}.auth-card{{width:min(440px,100%);padding:34px;background:#fff;border:1px solid #E5E7EB;border-radius:18px;box-shadow:0 18px 44px rgba(15,23,42,.1)}}.brand{{margin:0 0 30px;text-align:center}}.brand span{{display:block;color:#64748B;font-size:13px;font-weight:800;letter-spacing:.08em;text-transform:uppercase}}h1{{margin:9px 0 8px;font-size:30px}}.subtitle{{margin:0;color:#6B7280;line-height:1.5}}form{{display:grid;gap:9px}}label{{margin-top:7px;font-size:14px;font-weight:700}}input{{width:100%;min-height:46px;padding:11px 13px;border:1px solid #CBD5E1;border-radius:10px;background:#fff;color:#111827;font:inherit}}input:focus{{border-color:#2563EB;outline:3px solid #DBEAFE}}.field-help{{margin:0;color:#64748B;font-size:13px;line-height:1.45}}button{{min-height:46px;margin-top:13px;border:0;border-radius:10px;background:#111827;color:#fff;font-size:15px;font-weight:800;cursor:pointer}}button:hover{{background:#1F2937}}button:focus-visible,a:focus-visible{{outline:3px solid #2563EB;outline-offset:3px}}.message{{margin-bottom:16px;padding:12px 14px;border-radius:10px;font-size:14px;font-weight:700}}.error{{border:1px solid #FECACA;background:#FEF2F2;color:#991B1B}}.success{{border:1px solid #BBF7D0;background:#F0FDF4;color:#166534}}.alternate{{margin:22px 0 0;text-align:center;color:#64748B;font-size:14px}}.alternate a{{color:#1D4ED8;font-weight:700}}@media(max-width:520px){{.auth-page{{padding:16px}}.auth-card{{padding:25px 20px}}}}
</style></head><body><main class="auth-page"><section class="auth-card"><header class="brand"><span>{APP_NAME}</span><h1>{title}</h1><p class="subtitle">{subtitle}</p></header>{success_html}{error_html}<form method="post" action="/{mode}" data-native-submit="true">{next_html}{company_html}<label for="email">Email</label><input id="email" name="email" type="email" value="{_escape(email, True)}" autocomplete="email" required><label for="password">Password</label><input id="password" name="password" type="password" autocomplete="{password_autocomplete}"{password_constraints} required>{password_help}{confirm_html}<button type="submit">{"Register" if register else "Login"}</button></form>{alternate}</section></main></body></html>''', status_code=status_code)


def _escape(value, attribute=False):
    import html
    return html.escape(str(value or ""), quote=attribute)


@router.get("/login")
def login_page(registered: int = 0, reset: int = 0, next: str = ""):
    return _auth_page("login", registered=registered == 1, reset=reset == 1, next_path=safe_next_path(next) if next else "")


@router.post("/login")
def login(email: str = Form(""), password: str = Form(""), next_path: str = Form("", alias="next"), request: Request = None):
    normalized_email = _normalized_email(email)
    rate_key = _login_rate_key(request, normalized_email)
    retry_after = _login_rate_retry_after(rate_key)
    if retry_after:
        response = _auth_page(
            "login",
            error="Invalid email or password.",
            email=email,
            next_path=safe_next_path(next_path) if next_path else "",
            status_code=429,
        )
        response.headers["Retry-After"] = str(retry_after)
        return response
    user = next(
        (record for record in load_users() if isinstance(record, dict) and _normalized_email(record.get("email")) == normalized_email),
        None,
    )
    if not normalized_email or not password or user is None or not _password_matches(password, user.get("password", "")):
        _record_login_failure(rate_key)
        return _auth_page("login", error="Invalid email or password.", email=email, next_path=safe_next_path(next_path) if next_path else "", status_code=401)
    if not _password_is_pbkdf2(user.get("password", "")):
        _upgrade_legacy_password(normalized_email, password)
    _clear_login_failures(rate_key)
    response = RedirectResponse(url=safe_next_path(next_path), status_code=303)
    response.set_cookie(
        SESSION_COOKIE,
        _session_token(normalized_email, user.get("session_version", 0)),
        max_age=SESSION_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=session_cookie_secure(request),
        path="/",
    )
    return response


@router.post("/logout")
def logout(request: Request = None):
    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie(
        SESSION_COOKIE,
        path="/",
        httponly=True,
        samesite="lax",
        secure=session_cookie_secure(request),
    )
    return response


PASSWORD_RESET_REQUEST_MESSAGE = (
    "If an account exists for that email, password reset instructions will be sent."
)


def _password_reset_token_hash(token):
    return hashlib.sha256(str(token or "").encode("utf-8")).hexdigest()


def _deliver_password_reset(email, token):
    """Deliver without exposing provider errors or reset secrets to the request."""
    try:
        return email_delivery.deliver_password_reset(email, token)
    except Exception:
        return False


def _forgot_password_page(message="", error="", email="", status_code=200):
    message_html = f'<div class="message success" role="status">{_escape(message)}</div>' if message else ""
    error_html = f'<div class="message error" role="alert">{_escape(error)}</div>' if error else ""
    return HTMLResponse(f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Forgot password · {APP_NAME}</title><style>
*{{box-sizing:border-box}}body{{margin:0;background:#F3F4F6;color:#111827;font-family:Arial,sans-serif}}.auth-page{{min-height:100vh;display:grid;place-items:center;padding:28px}}.auth-card{{width:min(440px,100%);padding:34px;background:#fff;border:1px solid #E5E7EB;border-radius:18px;box-shadow:0 18px 44px rgba(15,23,42,.1)}}h1{{margin:0 0 8px;font-size:30px}}.subtitle{{margin:0 0 24px;color:#6B7280;line-height:1.5}}form{{display:grid;gap:9px}}label{{font-size:14px;font-weight:700}}input{{min-height:46px;padding:11px 13px;border:1px solid #CBD5E1;border-radius:10px;font:inherit}}button{{min-height:46px;margin-top:10px;border:0;border-radius:10px;background:#111827;color:#fff;font-weight:800;cursor:pointer}}.message{{margin-bottom:16px;padding:12px 14px;border-radius:10px;font-size:14px;font-weight:700}}.error{{border:1px solid #FECACA;background:#FEF2F2;color:#991B1B}}.success{{border:1px solid #BBF7D0;background:#F0FDF4;color:#166534}}.alternate{{margin:22px 0 0;text-align:center}}a{{color:#1D4ED8;font-weight:700}}
</style></head><body><main class="auth-page"><section class="auth-card"><h1>Reset your password</h1><p class="subtitle">Enter your login email. If an account exists, reset instructions will be sent.</p>{message_html}{error_html}<form method="post" action="/forgot-password" data-native-submit="true"><label for="email">Email</label><input id="email" name="email" type="email" value="{_escape(email, True)}" autocomplete="email" required><button type="submit">Send reset instructions</button></form><p class="alternate"><a href="/login">Back to sign in</a></p></section></main></body></html>''', status_code=status_code)


@router.get("/forgot-password")
def forgot_password_page():
    return _forgot_password_page()


@router.post("/forgot-password")
def request_password_reset(email: str = Form(""), request: Request = None):
    normalized_email = _normalized_email(email)
    if not _EMAIL_PATTERN.match(normalized_email):
        return _forgot_password_page(
            error="Please enter a valid email address.", email=email, status_code=400,
        )

    rate_key = _password_reset_rate_key(request, normalized_email)
    allowed = _password_reset_rate_allowed(rate_key)
    token = secrets.token_urlsafe(32)
    token_hash = _password_reset_token_hash(token)
    now = int(_PASSWORD_RESET_CLOCK())
    delivery_email = ""
    issued = {"value": False}

    if allowed:
        users = load_users()
        existing = next(
            (record for record in users if isinstance(record, dict) and _normalized_email(record.get("email")) == normalized_email),
            None,
        )
        last_requested = _integer((existing or {}).get("password_reset_requested_at", 0))
        if existing is not None and now - last_requested >= PASSWORD_RESET_REQUEST_INTERVAL_SECONDS:
            def issue(records):
                record = next(
                    (item for item in records if isinstance(item, dict) and _normalized_email(item.get("email")) == normalized_email),
                    None,
                )
                if record is None:
                    return
                current_requested = _integer(record.get("password_reset_requested_at", 0))
                if now - current_requested < PASSWORD_RESET_REQUEST_INTERVAL_SECONDS:
                    return
                record["password_reset_token_hash"] = token_hash
                record["password_reset_expires_at"] = now + PASSWORD_RESET_TTL_SECONDS
                record["password_reset_requested_at"] = now
                issued["value"] = True

            locked_json_mutation(USERS_FILE, [], issue, list)
            if issued["value"]:
                delivery_email = normalized_email

    if delivery_email:
        _deliver_password_reset(delivery_email, token)
    return _forgot_password_page(message=PASSWORD_RESET_REQUEST_MESSAGE)


def _valid_password_reset_record(token):
    token_hash = _password_reset_token_hash(token)
    now = int(_PASSWORD_RESET_CLOCK())
    return next(
        (
            record for record in load_users()
            if isinstance(record, dict)
            and hmac.compare_digest(str(record.get("password_reset_token_hash", "") or ""), token_hash)
            and _integer(record.get("password_reset_expires_at", 0)) >= now
        ),
        None,
    )


def _reset_password_page(token="", error="", status_code=200):
    error_html = f'<div class="message error" role="alert">{_escape(error)}</div>' if error else ""
    form_html = ""
    if token:
        form_html = f'''<form method="post" action="/reset-password" data-native-submit="true"><input type="hidden" name="token" value="{_escape(token, True)}"><label for="password">New Password</label><input id="password" name="password" type="password" autocomplete="new-password" minlength="8" required><p class="field-help">Use at least 8 characters. Letters and numbers are recommended.</p><label for="confirm_password">Confirm Password</label><input id="confirm_password" name="confirm_password" type="password" autocomplete="new-password" minlength="8" required><button type="submit">Reset password</button></form>'''
    return HTMLResponse(f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Reset password · {APP_NAME}</title><style>
*{{box-sizing:border-box}}body{{margin:0;background:#F3F4F6;color:#111827;font-family:Arial,sans-serif}}.auth-page{{min-height:100vh;display:grid;place-items:center;padding:28px}}.auth-card{{width:min(440px,100%);padding:34px;background:#fff;border:1px solid #E5E7EB;border-radius:18px}}h1{{margin:0 0 20px}}form{{display:grid;gap:9px}}label{{font-size:14px;font-weight:700}}input{{min-height:46px;padding:11px 13px;border:1px solid #CBD5E1;border-radius:10px;font:inherit}}button{{min-height:46px;margin-top:10px;border:0;border-radius:10px;background:#111827;color:#fff;font-weight:800}}.field-help{{margin:0;color:#64748B;font-size:13px}}.message{{margin-bottom:16px;padding:12px 14px;border-radius:10px;font-size:14px;font-weight:700}}.error{{border:1px solid #FECACA;background:#FEF2F2;color:#991B1B}}.alternate{{margin:22px 0 0;text-align:center}}a{{color:#1D4ED8;font-weight:700}}
</style></head><body><main class="auth-page"><section class="auth-card"><h1>Choose a new password</h1>{error_html}{form_html}<p class="alternate"><a href="/forgot-password">Request a new link</a></p></section></main></body></html>''', status_code=status_code)


@router.get("/reset-password")
def reset_password_page(token: str = ""):
    if not token or _valid_password_reset_record(token) is None:
        return _reset_password_page(
            error="This password reset link is invalid or has expired.", status_code=400,
        )
    return _reset_password_page(token=token)


@router.post("/reset-password")
def reset_password(
    token: str = Form(""),
    password: str = Form(""),
    confirm_password: str = Form(""),
):
    if not token or _valid_password_reset_record(token) is None:
        return _reset_password_page(
            error="This password reset link is invalid or has expired.", status_code=400,
        )
    if password != confirm_password:
        return _reset_password_page(
            token=token, error="Password and Confirm Password must match.", status_code=400,
        )
    if len(password) < REGISTRATION_PASSWORD_MIN_LENGTH:
        return _reset_password_page(
            token=token,
            error="Password must be at least 8 characters. Letters and numbers are recommended.",
            status_code=400,
        )

    token_hash = _password_reset_token_hash(token)
    now = int(_PASSWORD_RESET_CLOCK())
    consumed = {"value": False}

    def consume(records):
        record = next(
            (
                item for item in records
                if isinstance(item, dict)
                and hmac.compare_digest(str(item.get("password_reset_token_hash", "") or ""), token_hash)
                and _integer(item.get("password_reset_expires_at", 0)) >= now
            ),
            None,
        )
        if record is None:
            return
        record["password"] = _password_hash(password)
        record["session_version"] = _integer(record.get("session_version", 0)) + 1
        record.pop("password_reset_token_hash", None)
        record.pop("password_reset_expires_at", None)
        record.pop("password_reset_requested_at", None)
        consumed["value"] = True

    locked_json_mutation(USERS_FILE, [], consume, list)
    if not consumed["value"]:
        return _reset_password_page(
            error="This password reset link is invalid or has expired.", status_code=400,
        )
    return RedirectResponse(url="/login?reset=1", status_code=303)


@router.get("/register")
def register_page():
    return _auth_page("register")


@router.post("/register")
def register(
    company: str = Form(""),
    email: str = Form(""),
    password: str = Form(""),
    confirm_password: str = Form(""),
):
    company = str(company or "").strip()
    normalized_email = _normalized_email(email)
    if not company:
        return _auth_page("register", error="Please enter your Company Name.", company=company, email=email, status_code=400)
    if not _EMAIL_PATTERN.match(normalized_email):
        return _auth_page("register", error="Please enter a valid email address.", company=company, email=email, status_code=400)
    if not password:
        return _auth_page("register", error="Please enter a password.", company=company, email=email, status_code=400)
    if password != confirm_password:
        return _auth_page("register", error="Password and Confirm Password must match.", company=company, email=email, status_code=400)
    if len(password) < REGISTRATION_PASSWORD_MIN_LENGTH:
        return _auth_page(
            "register",
            error="Password must be at least 8 characters. Letters and numbers are recommended.",
            company=company,
            email=email,
            status_code=400,
        )

    duplicate = {"found": False}
    def add_user(users):
        if any(isinstance(record, dict) and _normalized_email(record.get("email")) == normalized_email for record in users):
            duplicate["found"] = True
            return
        users.append({
            "account_id": str(uuid.uuid4()),
            "company": company,
            "email": normalized_email,
            "password": _password_hash(password),
            "session_version": 0,
            "plan": "Free",
            "subscription_status": "Trial",
        })

    locked_json_mutation(USERS_FILE, [], add_user, list)
    if duplicate["found"]:
        return _auth_page("register", error="An account with this email already exists.", company=company, email=email, status_code=409)
    load_users()
    return RedirectResponse(url="/login?registered=1", status_code=303)
