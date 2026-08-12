from __future__ import annotations

from datetime import datetime, timezone
import re

from fastapi import APIRouter, Form
from fastapi.responses import HTMLResponse, RedirectResponse

from app.storage import data_path, load_json_strict, locked_json_mutation
from app.ui import html_escape, page_shell, section_card
from app.validation import DataValidationError, require_text


router = APIRouter()
FEEDBACK_FILE = data_path("feedback.json")
FEEDBACK_CATEGORIES = ("Bug", "Feature Request", "UI/UX", "Workflow", "Performance", "Other")
_EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def _styles():
    return """
*{box-sizing:border-box}body{margin:0;background:#F3F4F6;color:#111827;font-family:Arial,sans-serif}.tp-page{width:min(760px,calc(100% - 32px));margin:40px auto}.intro{text-align:center;color:#64748B;line-height:1.6;margin-bottom:24px}.card{background:#fff;border:1px solid #E5E7EB;border-radius:18px;padding:28px;box-shadow:0 14px 35px rgba(15,23,42,.07)}form{display:grid;gap:9px}label,.field-label{margin-top:8px;font-weight:750}input,select,textarea{width:100%;min-height:46px;padding:11px 13px;border:1px solid #CBD5E1;border-radius:10px;background:#fff;color:#111827;font:inherit}textarea{min-height:150px;resize:vertical}input:focus,select:focus,textarea:focus{border-color:#2563EB;outline:3px solid #DBEAFE}.rating{display:flex;gap:5px;flex-wrap:wrap}.rating input{position:absolute;opacity:0;width:1px;height:1px}.rating label{display:grid;width:48px;height:46px;place-items:center;margin:0;border:1px solid #CBD5E1;border-radius:10px;background:#fff;color:#D97706;font-size:24px;cursor:pointer}.rating input:checked+label{border-color:#D97706;background:#FFFBEB}.rating input:focus-visible+label{outline:3px solid #DBEAFE}button,.back{display:inline-flex;min-height:48px;align-items:center;justify-content:center;margin-top:16px;padding:12px 18px;border:0;border-radius:11px;background:#111827;color:#fff;text-decoration:none;font-size:16px;font-weight:800;cursor:pointer}.required{color:#B91C1C}.tp-release-footer{width:min(760px,calc(100% - 32px));margin:34px auto 20px;padding:20px 0;border-top:1px solid #D1D5DB;color:#6B7280;text-align:center;font-size:13px;line-height:1.7}.tp-release-footer strong{display:block;color:#374151}.tp-release-footer-nav{display:flex;justify-content:center;gap:12px;flex-wrap:wrap;margin-top:9px}.tp-release-footer-nav a{color:#475569}@media(max-width:600px){.tp-page{margin:20px auto}.card{padding:22px}}
"""


@router.get("/feedback", response_class=HTMLResponse)
def feedback_page():
    categories = "".join(
        f'<option value="{html_escape(category, attribute=True)}">{html_escape(category)}</option>'
        for category in FEEDBACK_CATEGORIES
    )
    ratings = "".join(
        f'<input id="rating-{rating}" name="rating" type="radio" value="{rating}"><label for="rating-{rating}" aria-label="{rating} star rating">★</label>'
        for rating in range(1, 6)
    )
    content = f"""
<p class="intro">Tell us what is working and what would make Trade Paper AI better. This form records feedback only.</p>
<section class="card"><form method="post" action="/feedback" data-native-submit="true">
<label for="name">Name</label><input id="name" name="name" autocomplete="name">
<label for="email">Email</label><input id="email" name="email" type="email" autocomplete="email">
<span class="field-label">Rating</span><div class="rating" role="radiogroup" aria-label="Rating">{ratings}</div>
<label for="category">Category</label><select id="category" name="category"><option value="">Select</option>{categories}</select>
<label for="feedback">Feedback <span class="required">*</span></label><textarea id="feedback" name="feedback" required></textarea>
<button type="submit">Send Feedback</button>
</form></section>"""
    return HTMLResponse(page_shell("Feedback Center", content, styles=_styles()))


@router.post("/feedback")
def submit_feedback(
    feedback: str = Form(""),
    name: str = Form(""),
    email: str = Form(""),
    rating: str = Form(""),
    category: str = Form(""),
):
    message = require_text("Feedback", feedback)
    normalized_email = str(email or "").strip()
    if normalized_email and not _EMAIL_PATTERN.fullmatch(normalized_email):
        raise DataValidationError("Email", "Enter a valid email address.", "Use an address such as name@company.com.")
    normalized_rating = str(rating or "").strip()
    if normalized_rating and normalized_rating not in {"1", "2", "3", "4", "5"}:
        raise DataValidationError("Rating", "The selected rating is invalid.", "Choose a rating from 1 to 5.")
    normalized_category = str(category or "").strip()
    if normalized_category and normalized_category not in FEEDBACK_CATEGORIES:
        raise DataValidationError("Category", "The selected category is invalid.", "Choose one of the available categories.")
    record = {
        "submitted_at": datetime.now(timezone.utc).isoformat(),
        "name": str(name or "").strip(),
        "email": normalized_email,
        "rating": normalized_rating,
        "category": normalized_category,
        "feedback": message,
    }
    locked_json_mutation(FEEDBACK_FILE, [], lambda records: records.append(record), list)
    return RedirectResponse("/feedback/thank-you", status_code=303)


@router.get("/feedback/thank-you", response_class=HTMLResponse)
def feedback_thank_you():
    content = section_card(
        "Thank you for your feedback",
        '<p>Your feedback has been received and will help us improve the Founding Beta.</p><a class="back" href="/">Back to Trade Paper AI</a>',
    )
    return HTMLResponse(page_shell("Thank You", content, subtitle="Trade Paper AI Feedback Center", styles=_styles()))


def _admin_styles():
    return _styles() + """
.tp-page{width:min(1380px,calc(100% - 32px))}.admin-nav{display:flex;justify-content:space-between;gap:12px;align-items:center;flex-wrap:wrap;margin-bottom:22px}.admin-nav a{color:#1D4ED8;font-weight:750}.search{display:flex;gap:10px;flex:1 1 430px}.search input,.search button{margin:0}.search button{min-height:46px}.count{color:#475569;font-weight:750}.table-wrap{overflow-x:auto;border:1px solid #E5E7EB;border-radius:16px;background:#fff}table{width:100%;min-width:1050px;border-collapse:collapse}th{padding:13px;background:#111827;color:#fff;text-align:left;font-size:13px}td{padding:13px;border-bottom:1px solid #E5E7EB;vertical-align:top;word-break:break-word}.message{min-width:300px;white-space:pre-wrap}.empty{text-align:center;color:#64748B;padding:30px}
"""


@router.get("/admin/feedback", response_class=HTMLResponse)
def feedback_admin(search: str = ""):
    records = load_json_strict(FEEDBACK_FILE, [], list)
    query = str(search or "").strip()
    entries = [
        record for record in records
        if isinstance(record, dict) and (
            not query
            or any(
                query.casefold() in str(record.get(field, "") or "").casefold()
                for field in ("email", "name", "category", "feedback")
            )
        )
    ]
    entries.reverse()
    rows = "".join(
        f"<tr><td>{html_escape(record.get('submitted_at', ''))}</td><td>{html_escape(record.get('name', ''))}</td><td>{html_escape(record.get('email', ''))}</td><td>{html_escape(record.get('rating', ''))}</td><td>{html_escape(record.get('category', ''))}</td><td class=\"message\">{html_escape(record.get('feedback', ''))}</td></tr>"
        for record in entries
    )
    if not rows:
        rows = '<tr><td class="empty" colspan="6">No feedback found.</td></tr>'
    content = f"""
<div class="admin-nav"><a href="/">← Dashboard</a><form class="search" action="/admin/feedback" method="get"><input type="search" name="search" value="{html_escape(query, attribute=True)}" placeholder="Search email, name, category, or feedback"><button type="submit">Search</button></form><span class="count">{len(entries)} feedback items</span></div>
<div class="table-wrap"><table><thead><tr><th>Submitted</th><th>Name</th><th>Email</th><th>Rating</th><th>Category</th><th>Feedback</th></tr></thead><tbody>{rows}</tbody></table></div>"""
    return HTMLResponse(page_shell("Feedback Admin", content, subtitle="Review Founding Beta product feedback.", styles=_admin_styles()))
