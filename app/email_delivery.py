from __future__ import annotations

from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formataddr
import html
import logging
import os
import smtplib
import ssl
import time
from typing import Mapping, Protocol
from urllib.parse import quote, urlsplit


logger = logging.getLogger("trade-paper-ai.email-delivery")

SUPPORTED_EMAIL_BACKENDS = frozenset({"disabled", "smtp", "api"})
PRODUCTION_ENVIRONMENTS = frozenset({"production", "prod", "staging", "stage"})
DEFAULT_EMAIL_TIMEOUT_SECONDS = 5.0
MIN_EMAIL_TIMEOUT_SECONDS = 3.0
MAX_EMAIL_TIMEOUT_SECONDS = 5.0
SMTP_MAX_ATTEMPTS = 2


class EmailDeliveryError(Exception):
    """Base exception for provider-neutral delivery failures."""


class EmailConfigurationError(EmailDeliveryError):
    """Raised when delivery configuration cannot be used safely."""


@dataclass(frozen=True)
class DeliveryMessage:
    recipient: str
    subject: str
    text_body: str
    html_body: str
    purpose: str


class ApiEmailAdapter(Protocol):
    """Vendor-neutral contract for a future HTTP API provider adapter."""

    def send(self, message: DeliveryMessage, *, timeout: float) -> bool:
        ...


def _environment(source: Mapping[str, str] | None = None) -> Mapping[str, str]:
    return os.environ if source is None else source


def _setting(source: Mapping[str, str], name: str, default: str = "") -> str:
    return str(source.get(name, default) or "").strip()


def _required(source: Mapping[str, str], name: str) -> str:
    value = _setting(source, name)
    if not value:
        raise EmailConfigurationError(f"{name} is required for email delivery.")
    return value


def _boolean(value: str, name: str) -> bool:
    normalized = str(value or "").strip().casefold()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise EmailConfigurationError(f"{name} must be true or false.")


def email_backend(source: Mapping[str, str] | None = None) -> str:
    backend = _setting(_environment(source), "TRADE_PAPER_EMAIL_BACKEND", "disabled").casefold()
    if backend not in SUPPORTED_EMAIL_BACKENDS:
        raise EmailConfigurationError("TRADE_PAPER_EMAIL_BACKEND must be disabled, smtp, or api.")
    return backend


def email_timeout(source: Mapping[str, str] | None = None) -> float:
    raw = _setting(
        _environment(source), "TRADE_PAPER_EMAIL_TIMEOUT_SECONDS",
        str(DEFAULT_EMAIL_TIMEOUT_SECONDS),
    )
    try:
        value = float(raw)
    except ValueError as exc:
        raise EmailConfigurationError("TRADE_PAPER_EMAIL_TIMEOUT_SECONDS must be numeric.") from exc
    if not MIN_EMAIL_TIMEOUT_SECONDS <= value <= MAX_EMAIL_TIMEOUT_SECONDS:
        raise EmailConfigurationError("TRADE_PAPER_EMAIL_TIMEOUT_SECONDS must be between 3 and 5 seconds.")
    return value


def public_base_url(source: Mapping[str, str] | None = None) -> str:
    environment = _environment(source)
    configured = _required(environment, "TRADE_PAPER_PUBLIC_BASE_URL")
    parsed = urlsplit(configured)
    deployment = _setting(environment, "TRADE_PAPER_ENV").casefold()
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise EmailConfigurationError("TRADE_PAPER_PUBLIC_BASE_URL must be an absolute HTTP(S) origin.")
    if parsed.username is not None or parsed.password is not None:
        raise EmailConfigurationError("TRADE_PAPER_PUBLIC_BASE_URL must not contain credentials.")
    if parsed.query or parsed.fragment or parsed.path not in {"", "/"}:
        raise EmailConfigurationError("TRADE_PAPER_PUBLIC_BASE_URL must contain only an origin.")
    if deployment in PRODUCTION_ENVIRONMENTS and parsed.scheme != "https":
        raise EmailConfigurationError("TRADE_PAPER_PUBLIC_BASE_URL must use HTTPS in production and staging.")
    return f"{parsed.scheme}://{parsed.netloc}"


def password_reset_url(token: str, source: Mapping[str, str] | None = None) -> str:
    return f"{public_base_url(source)}/reset-password?token={quote(str(token or ''), safe='')}"


def build_password_reset_message(
    recipient: str,
    token: str,
    source: Mapping[str, str] | None = None,
) -> DeliveryMessage:
    reset_url = password_reset_url(token, source)
    subject = "Reset your Trade Paper AI password"
    text_body = (
        "Password Reset\n\n"
        "We received a request to reset your Trade Paper AI password.\n\n"
        f"Reset your password: {reset_url}\n\n"
        "This link expires in 30 minutes.\n"
        "If you did not request this, you can safely ignore this email."
    )
    safe_url = html.escape(reset_url, quote=True)
    html_body = (
        "<h1>Password Reset</h1>"
        "<p>We received a request to reset your Trade Paper AI password.</p>"
        f'<p><a href="{safe_url}">Reset your password</a></p>'
        "<p>This link expires in 30 minutes.</p>"
        "<p>If you did not request this, you can safely ignore this email.</p>"
    )
    return DeliveryMessage(
        recipient=str(recipient or "").strip(), subject=subject,
        text_body=text_body, html_body=html_body, purpose="password_reset",
    )


def _safe_address(value: str, setting_name: str) -> str:
    address = str(value or "").strip()
    if not address or "\r" in address or "\n" in address:
        raise EmailConfigurationError(f"{setting_name} is invalid.")
    return address


def _smtp_message(message: DeliveryMessage, source: Mapping[str, str]) -> EmailMessage:
    from_address = _safe_address(_required(source, "TRADE_PAPER_EMAIL_FROM_ADDRESS"), "TRADE_PAPER_EMAIL_FROM_ADDRESS")
    from_name = _required(source, "TRADE_PAPER_EMAIL_FROM_NAME")
    reply_to = _safe_address(_required(source, "TRADE_PAPER_EMAIL_REPLY_TO"), "TRADE_PAPER_EMAIL_REPLY_TO")
    recipient = _safe_address(message.recipient, "recipient")
    email = EmailMessage()
    email["From"] = formataddr((from_name, from_address))
    email["To"] = recipient
    email["Reply-To"] = reply_to
    email["Subject"] = message.subject
    email.set_content(message.text_body)
    email.add_alternative(message.html_body, subtype="html")
    return email


def _smtp_settings(source: Mapping[str, str]):
    host = _required(source, "TRADE_PAPER_SMTP_HOST")
    username = _required(source, "TRADE_PAPER_SMTP_USERNAME")
    password = _required(source, "TRADE_PAPER_SMTP_PASSWORD")
    try:
        port = int(_required(source, "TRADE_PAPER_SMTP_PORT"))
    except ValueError as exc:
        raise EmailConfigurationError("TRADE_PAPER_SMTP_PORT must be an integer.") from exc
    if not 1 <= port <= 65535:
        raise EmailConfigurationError("TRADE_PAPER_SMTP_PORT is invalid.")
    starttls = _boolean(_required(source, "TRADE_PAPER_SMTP_STARTTLS"), "TRADE_PAPER_SMTP_STARTTLS")
    return host, port, username, password, starttls


def validate_email_configuration(source: Mapping[str, str] | None = None) -> str:
    """Fail fast for an explicitly enabled backend without sending a message."""
    environment = _environment(source)
    backend = email_backend(environment)
    if backend == "disabled":
        return backend
    public_base_url(environment)
    email_timeout(environment)
    _safe_address(
        _required(environment, "TRADE_PAPER_EMAIL_FROM_ADDRESS"),
        "TRADE_PAPER_EMAIL_FROM_ADDRESS",
    )
    _required(environment, "TRADE_PAPER_EMAIL_FROM_NAME")
    _safe_address(
        _required(environment, "TRADE_PAPER_EMAIL_REPLY_TO"),
        "TRADE_PAPER_EMAIL_REPLY_TO",
    )
    if backend == "api":
        raise EmailConfigurationError(
            "The api email backend contract has no provider adapter configured."
        )
    _, _, _, _, starttls = _smtp_settings(environment)
    deployment = _setting(environment, "TRADE_PAPER_ENV").casefold()
    if deployment in PRODUCTION_ENVIRONMENTS and not starttls:
        raise EmailConfigurationError(
            "TRADE_PAPER_SMTP_STARTTLS must be enabled in production and staging."
        )
    return backend


def _transient_smtp_error(error: Exception) -> bool:
    return isinstance(error, (
        smtplib.SMTPConnectError,
        smtplib.SMTPServerDisconnected,
        ConnectionError,
        TimeoutError,
    )) and not isinstance(error, smtplib.SMTPAuthenticationError)


def _send_smtp(
    message: DeliveryMessage,
    source: Mapping[str, str],
    *,
    smtp_factory=smtplib.SMTP,
    clock=time.monotonic,
    sleeper=time.sleep,
) -> bool:
    timeout = email_timeout(source)
    host, port, username, password, starttls = _smtp_settings(source)
    mime_message = _smtp_message(message, source)
    deadline = clock() + timeout
    for attempt in range(SMTP_MAX_ATTEMPTS):
        remaining = deadline - clock()
        if remaining <= 0:
            return False
        try:
            with smtp_factory(host, port, timeout=remaining) as smtp:
                if starttls:
                    smtp.starttls(context=ssl.create_default_context())
                smtp.login(username, password)
                smtp.send_message(mime_message)
            return True
        except Exception as error:
            if not _transient_smtp_error(error) or attempt + 1 >= SMTP_MAX_ATTEMPTS:
                return False
            remaining = deadline - clock()
            if remaining <= 0:
                return False
            sleeper(min(0.1, remaining))
    return False


def deliver_email(
    message: DeliveryMessage,
    source: Mapping[str, str] | None = None,
    *,
    smtp_factory=smtplib.SMTP,
    api_adapter: ApiEmailAdapter | None = None,
    clock=time.monotonic,
    sleeper=time.sleep,
) -> bool:
    environment = _environment(source)
    try:
        backend = email_backend(environment)
        if backend == "disabled":
            return False
        timeout = email_timeout(environment)
        if backend == "smtp":
            return _send_smtp(
                message, environment, smtp_factory=smtp_factory,
                clock=clock, sleeper=sleeper,
            )
        if api_adapter is None:
            return False
        return bool(api_adapter.send(message, timeout=timeout))
    except Exception as error:
        # Never include recipient, message content, credentials, token, or URL.
        logger.warning(
            "Email delivery failed: backend=%s purpose=%s error_type=%s",
            _setting(environment, "TRADE_PAPER_EMAIL_BACKEND", "disabled").casefold(),
            str(message.purpose or "unspecified"),
            type(error).__name__,
        )
        return False


def deliver_password_reset(recipient: str, token: str) -> bool:
    try:
        backend = email_backend()
        if backend == "disabled":
            return False
        message = build_password_reset_message(recipient, token)
        return deliver_email(message)
    except Exception as error:
        logger.warning(
            "Password reset email preparation failed: error_type=%s",
            type(error).__name__,
        )
        return False
