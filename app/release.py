"""Single source of truth for public release metadata."""

APP_NAME = "Trade Paper AI"
APP_VERSION = "3.3.0"
VERSION = APP_VERSION
BUILD_NAME = "First User Success"
RELEASE_TYPE = "First User Success"
RELEASE_STAGE = "General Availability"
LAST_UPDATED = "2026-07-22"
EXPECTED_ROUTE_COUNT = 198

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
)


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
