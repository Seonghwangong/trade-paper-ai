import logging
import smtplib

import app.auth as auth
from app import email_delivery


def _environment(backend="smtp", deployment="production"):
    return {
        "TRADE_PAPER_EMAIL_BACKEND": backend,
        "TRADE_PAPER_ENV": deployment,
        "TRADE_PAPER_PUBLIC_BASE_URL": "https://trade.example.com",
        "TRADE_PAPER_EMAIL_FROM_ADDRESS": "no-reply@trade.example.com",
        "TRADE_PAPER_EMAIL_FROM_NAME": "Trade Paper AI",
        "TRADE_PAPER_EMAIL_REPLY_TO": "support@trade.example.com",
        "TRADE_PAPER_EMAIL_TIMEOUT_SECONDS": "5",
        "TRADE_PAPER_SMTP_HOST": "smtp.example.com",
        "TRADE_PAPER_SMTP_PORT": "587",
        "TRADE_PAPER_SMTP_USERNAME": "smtp-user",
        "TRADE_PAPER_SMTP_PASSWORD": "smtp-secret-value",
        "TRADE_PAPER_SMTP_STARTTLS": "true",
        "TRADE_PAPER_EMAIL_API_KEY": "api-secret-value",
    }


class SuccessfulSMTP:
    instances = []

    def __init__(self, host, port, timeout):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.login_values = None
        self.message = None
        self.starttls_called = False
        self.__class__.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def starttls(self, context):
        self.starttls_called = context is not None

    def login(self, username, password):
        self.login_values = (username, password)

    def send_message(self, message):
        self.message = message


def test_disabled_backend_is_safe_noop(monkeypatch):
    monkeypatch.setenv("TRADE_PAPER_EMAIL_BACKEND", "disabled")
    monkeypatch.delenv("TRADE_PAPER_PUBLIC_BASE_URL", raising=False)
    assert email_delivery.deliver_password_reset("owner@example.com", "secret-token") is False


def test_smtp_success_and_password_reset_templates():
    SuccessfulSMTP.instances = []
    environment = _environment()
    token = "reset-token-value"
    message = email_delivery.build_password_reset_message("owner@example.com", token, environment)

    assert message.purpose == "password_reset"
    assert "Password Reset" in message.text_body
    assert "https://trade.example.com/reset-password?token=reset-token-value" in message.text_body
    assert "expires in 30 minutes" in message.text_body
    assert "safely ignore" in message.text_body
    assert "<h1>Password Reset</h1>" in message.html_body
    assert '<a href="https://trade.example.com/reset-password?token=reset-token-value">' in message.html_body

    assert email_delivery.deliver_email(message, environment, smtp_factory=SuccessfulSMTP) is True
    smtp = SuccessfulSMTP.instances[0]
    assert (smtp.host, smtp.port) == ("smtp.example.com", 587)
    assert 0 < smtp.timeout <= 5
    assert smtp.starttls_called is True
    assert smtp.login_values == ("smtp-user", "smtp-secret-value")
    assert smtp.message["To"] == "owner@example.com"
    assert smtp.message.get_body(preferencelist=("plain",)).get_content().startswith("Password Reset")
    assert "<h1>Password Reset</h1>" in smtp.message.get_body(preferencelist=("html",)).get_content()


def test_smtp_retries_connection_and_timeout_but_not_authentication():
    environment = _environment()
    message = email_delivery.DeliveryMessage(
        "owner@example.com", "Subject", "Text", "<p>HTML</p>", "password_reset",
    )

    for error_factory in (
        lambda: smtplib.SMTPConnectError(421, b"temporary"),
        lambda: TimeoutError("timeout"),
        lambda: ConnectionError("connection"),
    ):
        calls = []

        def transient(*args, **kwargs):
            calls.append((args, kwargs))
            raise error_factory()

        assert email_delivery.deliver_email(
            message, environment, smtp_factory=transient, sleeper=lambda seconds: None,
        ) is False
        assert len(calls) == 2

    auth_calls = []

    def authentication_failure(*args, **kwargs):
        auth_calls.append((args, kwargs))
        raise smtplib.SMTPAuthenticationError(535, b"invalid credentials")

    assert email_delivery.deliver_email(
        message, environment, smtp_factory=authentication_failure,
    ) is False
    assert len(auth_calls) == 1


def test_public_base_url_policy_does_not_use_request_hosts():
    production = _environment()
    assert email_delivery.public_base_url(production) == "https://trade.example.com"
    insecure = {**production, "TRADE_PAPER_PUBLIC_BASE_URL": "http://trade.example.com"}
    try:
        email_delivery.public_base_url(insecure)
        assert False, "production HTTP origin must be rejected"
    except email_delivery.EmailConfigurationError:
        pass

    development = {
        **production,
        "TRADE_PAPER_ENV": "development",
        "TRADE_PAPER_PUBLIC_BASE_URL": "http://127.0.0.1:8000/",
    }
    assert email_delivery.public_base_url(development) == "http://127.0.0.1:8000"
    for invalid in (
        "https://user:password@trade.example.com",
        "https://trade.example.com/app",
        "https://trade.example.com?host=evil.example.com",
    ):
        try:
            email_delivery.public_base_url({**production, "TRADE_PAPER_PUBLIC_BASE_URL": invalid})
            assert False, "unsafe public URL must be rejected"
        except email_delivery.EmailConfigurationError:
            pass


def test_enabled_email_backend_configuration_fails_fast_and_requires_tls():
    smtp = _environment()
    assert email_delivery.validate_email_configuration(smtp) == "smtp"
    assert email_delivery.validate_email_configuration({
        "TRADE_PAPER_EMAIL_BACKEND": "disabled",
        "TRADE_PAPER_ENV": "production",
    }) == "disabled"

    missing = dict(smtp)
    missing.pop("TRADE_PAPER_SMTP_PASSWORD")
    try:
        email_delivery.validate_email_configuration(missing)
        assert False, "missing SMTP secrets must fail startup validation"
    except email_delivery.EmailConfigurationError as error:
        assert "TRADE_PAPER_SMTP_PASSWORD" in str(error)
        assert "smtp-secret-value" not in str(error)

    insecure_smtp = {**smtp, "TRADE_PAPER_SMTP_STARTTLS": "false"}
    try:
        email_delivery.validate_email_configuration(insecure_smtp)
        assert False, "production SMTP must require STARTTLS"
    except email_delivery.EmailConfigurationError as error:
        assert "STARTTLS" in str(error)

    try:
        email_delivery.validate_email_configuration({**smtp, "TRADE_PAPER_EMAIL_BACKEND": "api"})
        assert False, "unimplemented API backend must fail fast"
    except email_delivery.EmailConfigurationError as error:
        assert "no provider adapter" in str(error)


def test_api_contract_is_vendor_neutral_and_provider_errors_are_safe(caplog):
    environment = _environment("api")
    message = email_delivery.DeliveryMessage(
        "owner@example.com", "Subject", "private body", "<p>private body</p>", "password_reset",
    )
    assert email_delivery.deliver_email(message, environment) is False

    class Adapter:
        def __init__(self):
            self.received = None

        def send(self, received, *, timeout):
            self.received = (received, timeout)
            return True

    adapter = Adapter()
    assert email_delivery.deliver_email(message, environment, api_adapter=adapter) is True
    assert adapter.received == (message, 5.0)

    class BrokenAdapter:
        def send(self, received, *, timeout):
            raise RuntimeError("reset-token-value https://trade.example.com/reset-password api-secret-value")

    caplog.set_level(logging.WARNING, logger="trade-paper-ai.email-delivery")
    assert email_delivery.deliver_email(message, environment, api_adapter=BrokenAdapter()) is False
    logs = caplog.text
    for secret in (
        "reset-token-value", "reset-password", "private body",
        "api-secret-value", "smtp-secret-value", "owner@example.com",
    ):
        assert secret not in logs
    assert "error_type=RuntimeError" in logs


def test_forgot_password_absorbs_delivery_exception_and_keeps_generic_response(
    tmp_path, monkeypatch,
):
    users_file = tmp_path / "users.json"
    users_file.write_text(
        '[{"account_id":"account-a","company":"A","email":"owner@example.com","password":"legacy"}]',
        encoding="utf-8",
    )
    monkeypatch.setattr(auth, "USERS_FILE", users_file)
    monkeypatch.setattr(auth, "_PASSWORD_RESET_CLOCK", lambda: 1_700_000_000)
    monkeypatch.setattr(
        auth.email_delivery, "deliver_password_reset",
        lambda email, token: (_ for _ in ()).throw(RuntimeError("provider failed")),
    )
    auth._reset_password_reset_rate_limiter()

    response = auth.request_password_reset("owner@example.com")
    assert response.status_code == 200
    assert auth.PASSWORD_RESET_REQUEST_MESSAGE in response.body.decode()
    stored = users_file.read_text()
    assert "password_reset_token_hash" in stored
    assert "provider failed" not in response.body.decode()
