"""Privacy-minimized product-flow analytics.

Only an allow-listed event name, timestamp, and internal account identifier are
stored. Request bodies and arbitrary metadata are deliberately unsupported.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit

from app.storage import data_path, load_json_strict, locked_json_mutation


ANALYTICS_FILE = data_path("analytics.json")
VISITOR_ANALYTICS_FILE = data_path("visitor_analytics.json")
EVENTS = (
    "Signup", "Login", "Onboarding Started", "Onboarding Completed",
    "Export Wizard Started", "Export Wizard Completed", "Invoice Created",
    "Email Sent", "Team Invite",
    "Feedback Submitted",
)
ALLOWED_FIELDS = ("time", "event", "account_id")
VISITOR_FIELDS = ("time", "page", "source")
VISITOR_PAGES = ("Landing", "Pricing", "FAQ", "Signup")
VISITOR_SOURCES = ("Direct", "Google", "Product Hunt", "Reddit", "Other")


def record_event(event, account_id="", now=None, path=None, once=False):
    if event not in EVENTS:
        raise ValueError("Unsupported analytics event")
    owner = str(account_id or "").strip()
    entry = {
        "time": (now or datetime.now(timezone.utc)).isoformat(),
        "event": event,
        "account_id": owner,
    }
    entry = {field: entry[field] for field in ALLOWED_FIELDS}

    def append(rows):
        if once and any(
            isinstance(row, dict)
            and row.get("event") == event
            and str(row.get("account_id", "") or "") == owner
            for row in rows
        ):
            return
        rows.append(entry)

    locked_json_mutation(path or ANALYTICS_FILE, [], append, list)
    return entry


def events(account_id=None, path=None):
    rows = [
        {field: str(row.get(field, "") or "") for field in ALLOWED_FIELDS}
        for row in load_json_strict(path or ANALYTICS_FILE, [], list)
        if isinstance(row, dict) and row.get("event") in EVENTS
    ]
    if account_id is not None:
        owner = str(account_id or "").strip()
        rows = [row for row in rows if row["account_id"] == owner]
    return sorted(rows, key=lambda row: row["time"], reverse=True)


def _rate(numerator, denominator):
    return round((numerator / denominator) * 100, 1) if denominator else 0.0


def analytics_metrics(account_id=None, now=None, path=None):
    current = now or datetime.now(timezone.utc)
    rows = events(account_id, path)
    counts = {event: sum(row["event"] == event for row in rows) for event in EVENTS}
    start = current.date() - timedelta(days=29)
    trend = []
    for offset in range(30):
        day = start + timedelta(days=offset)
        prefix = day.isoformat()
        daily = {event: sum(row["event"] == event and row["time"].startswith(prefix) for row in rows) for event in EVENTS}
        trend.append({"date": prefix, "events": daily, "total": sum(daily.values())})
    return {
        "counts": counts,
        "signups": counts["Signup"],
        "wizard_completion_rate": _rate(counts["Export Wizard Completed"], counts["Export Wizard Started"]),
        "email_send_rate": _rate(counts["Email Sent"], counts["Invoice Created"]),
        "document_creation_rate": _rate(counts["Invoice Created"], counts["Signup"]),
        "trend": trend,
    }


def classify_source(referer="", query_string=""):
    query = parse_qs(str(query_string or ""), keep_blank_values=False)
    campaign = " ".join(query.get("utm_source", [])).casefold()
    try:
        host = (urlsplit(str(referer or "")).hostname or "").casefold()
    except ValueError:
        host = ""
    value = f"{campaign} {host}"
    if "producthunt" in value or "product hunt" in value:
        return "Product Hunt"
    if "reddit" in value:
        return "Reddit"
    if "google" in value:
        return "Google"
    if not campaign and not host:
        return "Direct"
    return "Other"


def record_visit(page, source="Direct", now=None, path=None):
    if page not in VISITOR_PAGES:
        raise ValueError("Unsupported visitor page")
    if source not in VISITOR_SOURCES:
        raise ValueError("Unsupported visitor source")
    entry = {
        "time": (now or datetime.now(timezone.utc)).isoformat(),
        "page": page,
        "source": source,
    }
    entry = {field: entry[field] for field in VISITOR_FIELDS}
    locked_json_mutation(path or VISITOR_ANALYTICS_FILE, [], lambda rows: rows.append(entry), list)
    return entry


def visitor_metrics(now=None, path=None):
    current = now or datetime.now(timezone.utc)
    rows = [
        {field: str(row.get(field, "") or "") for field in VISITOR_FIELDS}
        for row in load_json_strict(path or VISITOR_ANALYTICS_FILE, [], list)
        if isinstance(row, dict)
        and row.get("page") in VISITOR_PAGES
        and row.get("source") in VISITOR_SOURCES
    ]
    page_counts = {page: sum(row["page"] == page for row in rows) for page in VISITOR_PAGES}
    source_counts = {source: sum(row["source"] == source for row in rows) for source in VISITOR_SOURCES}
    onboarding_started = analytics_metrics(now=current)["counts"]["Onboarding Started"]
    start = current.date() - timedelta(days=29)
    trend = []
    for offset in range(30):
        day = start + timedelta(days=offset)
        prefix = day.isoformat()
        daily = {page: sum(row["page"] == page and row["time"].startswith(prefix) for row in rows) for page in VISITOR_PAGES}
        trend.append({"date": prefix, "pages": daily, "total": sum(daily.values())})
    return {
        "visits": len(rows),
        "pages": page_counts,
        "sources": source_counts,
        "landing_to_signup_rate": min(100.0, _rate(page_counts["Signup"], page_counts["Landing"])),
        "signup_to_onboarding_rate": min(100.0, _rate(onboarding_started, page_counts["Signup"])),
        "landing_to_onboarding_rate": min(100.0, _rate(onboarding_started, page_counts["Landing"])),
        "trend": trend,
    }
