"""Conservative intake review signals; never delete or disable accounts."""
import re

APPLICATION_STATUSES = ("New", "Needs review", "Contacted", "Demo Scheduled", "Beta Customer", "Closed", "Spam")
# A business URL or a crypto-related company name alone is not a spam signal.
_LINK = re.compile(r"(?:https?://|www\.)\S+|\bgraph\.org/\S+", re.I)
_REWARD = re.compile(r"\bpromo\s*code\b|\bpromocode\b|\bclaim\b.{0,40}\b(?:prize|reward|bonus)\b|\btransfer\b.{0,160}\bsign\s*in\b", re.I)


def needs_intake_review(company):
    value = str(company or "")
    return bool(_LINK.search(value) and _REWARD.search(value))


def application_status(record):
    value = str(record.get("status", "") or "").strip()
    status = value if value in APPLICATION_STATUSES else "New"
    if status == "New" and record.get("intake_reviewed") is not True and needs_intake_review(record.get("company_name")):
        return "Needs review"
    return status


def honeypot_filled(value):
    return isinstance(value, str) and bool(value.strip())


HONEYPOT_HTML = '<div hidden aria-hidden="true"><label for="website_confirm">Leave this field empty</label><input id="website_confirm" name="website_confirm" type="text" tabindex="-1" autocomplete="off"></div>'
