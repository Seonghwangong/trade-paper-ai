"""Single source of truth for public release metadata."""

import os
from urllib.parse import urlsplit

APP_NAME = "Trade Paper AI"
APP_VERSION = "3.5.0"
VERSION = APP_VERSION
BUILD_NAME = "Founding Beta"
RELEASE_TYPE = "Founding Beta"
RELEASE_STAGE = "Founding Beta"
LAST_UPDATED = "2026-07-22"
EXPECTED_ROUTE_COUNT = 219
BUSINESS_PHONE = "010-7166-7770"

RELEASE_NOTES = (
    "Complete shipment-centered trade document workflow.",
    "Read-only Dashboard workflow, health, activity, and notification summaries.",
    "Safe JSON storage, validation, and referential delete protection.",
    "Guided continuity with Continue Last Work, clearer completion, and unsaved-change protection.",
    "Selection-first forms with browser favorites, recent-value suggestions, and touch-friendly actions.",
    "Reusable browser-only Smart Templates for frequent document configurations.",
    "Fast case-insensitive global search with live result counts and clear controls.",
    "Smart Dashboard statistics, recent invoices, and focused creation shortcuts.",
    "Clear workflow guidance with current, completed, next-step, and progress states.",
    "Field-level Favorites pickers for frequently reused trade terms and ports.",
    "Quiet real-time validation guidance with status and quick-jump controls.",
    "Buyer-specific recent context suggestions that never force input values.",
    "Searchable recent Item Library with quick insert into the current row.",
    "Post-save Invoice actions for Packing, another Invoice, PDF, and List navigation.",
    "Debounced local Invoice drafts with restore, start-new, and clear controls.",
    "Real-time Invoice document progress with completed and remaining summaries.",
    "Atomic JSON saves with validated single-backup recovery and safe structure checks.",
    "Unified controls, tables, loading feedback, status messages, and release metadata.",
    "First public MVP release with a unified product identity and release information.",
    "Consistent PDF download, open, print, copy-link, and safe filename actions.",
    "First-user onboarding with guided empty states and Dashboard setup progress.",
    "RC1 consistency, navigation, responsive-layout, and visible-copy review.",
    "Guided post-save success, recommended actions, completion celebration, and consistent delete confirmation.",
    "Login and registration MVP with duplicate-email validation and secure password hashing.",
    "Authenticated route protection, safe post-login redirects, logout, and visible user identity.",
)


def contact_email(source=None):
    """Return a configured customer contact without inventing a public address."""
    settings = os.environ if source is None else source
    return next((
        str(settings.get(name, "") or "").strip()
        for name in (
            "TRADE_PAPER_CONTACT_EMAIL",
            "TRADE_PAPER_EMAIL_REPLY_TO",
            "TRADE_PAPER_EMAIL_FROM_ADDRESS",
        )
        if str(settings.get(name, "") or "").strip()
    ), "")


def contact_url(source=None):
    """Return an explicit contact URL or the configured deployment origin."""
    settings = os.environ if source is None else source
    configured = next((
        str(settings.get(name, "") or "").strip()
        for name in ("TRADE_PAPER_CONTACT_URL", "TRADE_PAPER_PUBLIC_BASE_URL")
        if str(settings.get(name, "") or "").strip()
    ), "")
    parsed = urlsplit(configured)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
    ):
        return ""
    return configured


def build_release_summary(checklist):
    """Build presentation metadata from a read-only readiness checklist."""
    normalized = {str(label): bool(passed) for label, passed in checklist}
    return {
        "product": APP_NAME,
        "version": APP_VERSION,
        "release_stage": RELEASE_STAGE,
        "status": "Ready" if normalized and all(normalized.values()) else "Not Ready",
        "checks": normalized,
        "notes": RELEASE_NOTES,
    }
