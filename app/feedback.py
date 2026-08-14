from __future__ import annotations

from datetime import datetime, timezone
from io import BytesIO
import os
from pathlib import Path
import re
import tempfile
import uuid

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from PIL import Image, UnidentifiedImageError

from app import auth
from app.storage import data_path, load_json_strict, locked_json_mutation
from app.ui import html_escape, page_shell, section_card
from app.validation import DataValidationError, require_text


router = APIRouter()
FEEDBACK_FILE = data_path("feedback.json")
FEEDBACK_CATEGORIES = ("Bug", "Feature Request", "UI/UX", "Workflow", "Performance", "Other")
FEEDBACK_STATUSES = ("New", "Reviewing", "Planned", "Completed")
MAX_SCREENSHOT_BYTES = 3 * 1024 * 1024
MAX_SCREENSHOT_PIXELS = 16_000_000
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
<section class="card"><form method="post" action="/feedback" enctype="multipart/form-data" data-native-submit="true">
<label for="name">Name</label><input id="name" name="name" autocomplete="name">
<label for="email">Email</label><input id="email" name="email" type="email" autocomplete="email">
<span class="field-label">Rating</span><div class="rating" role="radiogroup" aria-label="Rating">{ratings}</div>
<label for="category">Category</label><select id="category" name="category"><option value="">Select</option>{categories}</select>
<label for="feedback">Feedback <span class="required">*</span></label><textarea id="feedback" name="feedback" required></textarea>
<label for="screenshot">Screenshot <span class="field-help">(optional · PNG, JPEG, or WebP · max 3 MB)</span></label><input id="screenshot" name="screenshot" type="file" accept="image/png,image/jpeg,image/webp">
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
    screenshot: UploadFile = File(None),
    request: Request = None,
):
    message = require_text("Feedback", feedback)
    name = name if isinstance(name, str) else ""
    email = email if isinstance(email, str) else ""
    rating = rating if isinstance(rating, str) else ""
    category = category if isinstance(category, str) else ""
    normalized_email = email.strip()
    if normalized_email and not _EMAIL_PATTERN.fullmatch(normalized_email):
        raise DataValidationError("Email", "Enter a valid email address.", "Use an address such as name@company.com.")
    normalized_rating = str(rating or "").strip()
    if normalized_rating and normalized_rating not in {"1", "2", "3", "4", "5"}:
        raise DataValidationError("Rating", "The selected rating is invalid.", "Choose a rating from 1 to 5.")
    normalized_category = str(category or "").strip()
    if normalized_category and normalized_category not in FEEDBACK_CATEGORIES:
        raise DataValidationError("Category", "The selected category is invalid.", "Choose one of the available categories.")
    screenshot_id = _save_screenshot(screenshot)
    identity = (request.scope.get("trade_paper_user") or {}) if request is not None else {}
    record = {
        "submitted_at": datetime.now(timezone.utc).isoformat(),
        "name": name.strip(),
        "email": normalized_email,
        "rating": normalized_rating,
        "category": normalized_category,
        "feedback": message,
        "account_id": str(identity.get("account_id", "") or ""),
        "status": "New",
        "reply_note": "",
        "screenshot_id": screenshot_id,
    }
    try:
        locked_json_mutation(FEEDBACK_FILE, [], lambda records: records.append(record), list)
    except Exception:
        if screenshot_id:
            (_upload_dir() / screenshot_id).unlink(missing_ok=True)
        raise
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
.tp-page{width:min(1500px,calc(100% - 32px))}.admin-nav{display:flex;justify-content:space-between;gap:12px;align-items:center;flex-wrap:wrap;margin-bottom:22px}.admin-nav a{color:#1D4ED8;font-weight:750}.search{display:flex;gap:10px;flex:1 1 430px}.search input,.search button{margin:0}.search button{min-height:46px}.count{color:#475569;font-weight:750}.notice{padding:12px;margin-bottom:14px;border-radius:10px;background:#F0FDF4;color:#166534;font-weight:750}.table-wrap{overflow-x:auto;border:1px solid #E5E7EB;border-radius:16px;background:#fff}table{width:100%;min-width:1350px;border-collapse:collapse}th{padding:13px;background:#111827;color:#fff;text-align:left;font-size:13px}td{padding:13px;border-bottom:1px solid #E5E7EB;vertical-align:top;word-break:break-word}.message{min-width:260px;white-space:pre-wrap}.manage-form{display:grid;min-width:260px;gap:7px}.manage-form textarea{min-height:72px}.manage-form button{margin:0}.screenshot-link{color:#1D4ED8;font-weight:750}.empty{text-align:center;color:#64748B;padding:30px}
"""


def _screenshot_link(index, record):
    if not record.get("screenshot_id"):
        return "—"
    return f'<a class="screenshot-link" href="/admin/feedback/{index}/screenshot">View Screenshot</a>'


@router.get("/admin/feedback", response_class=HTMLResponse)
def feedback_admin(request: Request, search: str = "", updated: int = 0):
    auth.require_admin(request)
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
    indexed = [(index, record) for index, record in enumerate(records) if record in entries]
    indexed.reverse()
    rows = "".join(
        f"<tr><td>{html_escape(record.get('submitted_at', ''))}</td><td>{html_escape(record.get('name', ''))}</td><td>{html_escape(record.get('email', ''))}</td><td>{html_escape(record.get('rating', ''))}</td><td>{html_escape(record.get('category', ''))}</td><td class=\"message\">{html_escape(record.get('feedback', ''))}</td><td>{_screenshot_link(index, record)}</td><td><form class=\"manage-form\" method=\"post\" action=\"/admin/feedback/{index}/status\"><select name=\"status\" aria-label=\"Status for feedback {index}\">{_status_options(record.get('status', 'New'))}</select><textarea name=\"reply_note\" aria-label=\"Reply note for feedback {index}\" placeholder=\"Internal reply note\">{html_escape(record.get('reply_note', ''))}</textarea><button type=\"submit\">Save</button></form></td></tr>"
        for index, record in indexed
    )
    if not rows:
        rows = '<tr><td class="empty" colspan="8">No feedback found.</td></tr>'
    content = f"""
<div class="admin-nav"><a href="/">← Dashboard</a><form class="search" action="/admin/feedback" method="get"><input type="search" name="search" value="{html_escape(query, attribute=True)}" placeholder="Search email, name, category, or feedback"><button type="submit">Search</button></form><span class="count">{len(entries)} feedback items</span></div>
{'<div class="notice" role="status">Feedback updated.</div>' if updated else ''}<div class="table-wrap"><table><thead><tr><th>Submitted</th><th>Name</th><th>Email</th><th>Rating</th><th>Category</th><th>Feedback</th><th>Screenshot</th><th>Status / Internal Reply</th></tr></thead><tbody>{rows}</tbody></table></div>"""
    return HTMLResponse(page_shell("Feedback Admin", content, subtitle="Review Founding Beta product feedback.", styles=_admin_styles()))


def _upload_dir():
    return Path(FEEDBACK_FILE).parent / "feedback_uploads"


def _save_screenshot(upload):
    if not getattr(upload, "filename", "") or not hasattr(upload, "file"):
        return ""
    content_type = str(upload.content_type or "").casefold()
    if content_type not in {"image/png", "image/jpeg", "image/webp"}:
        raise DataValidationError("Screenshot", "The screenshot format is invalid.", "Upload a PNG, JPEG, or WebP image.")
    raw = upload.file.read(MAX_SCREENSHOT_BYTES + 1)
    if len(raw) > MAX_SCREENSHOT_BYTES:
        raise DataValidationError("Screenshot", "The screenshot is too large.", "Upload an image no larger than 3 MB.")
    try:
        source = Image.open(BytesIO(raw))
        source.verify()
        source = Image.open(BytesIO(raw))
        if source.width * source.height > MAX_SCREENSHOT_PIXELS:
            raise DataValidationError("Screenshot", "The screenshot dimensions are too large.", "Upload a smaller screenshot.")
        clean = source.convert("RGBA" if source.mode in {"RGBA", "LA"} else "RGB")
        output = BytesIO()
        clean.save(output, format="PNG", optimize=True)
    except (UnidentifiedImageError, OSError) as exc:
        raise DataValidationError("Screenshot", "The screenshot file is invalid.", "Upload a valid PNG, JPEG, or WebP image.") from exc
    directory = _upload_dir()
    directory.mkdir(parents=True, exist_ok=True)
    identifier = f"{uuid.uuid4().hex}.png"
    descriptor, temporary = tempfile.mkstemp(prefix=".feedback-", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(output.getvalue())
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, directory / identifier)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return identifier


def _status_options(current):
    selected = current if current in FEEDBACK_STATUSES else "New"
    return "".join(f'<option value="{status}"{" selected" if status == selected else ""}>{status}</option>' for status in FEEDBACK_STATUSES)


@router.post("/admin/feedback/{index}/status")
def update_feedback(index: int, request: Request, status: str = Form(""), reply_note: str = Form("")):
    auth.require_admin(request)
    if status not in FEEDBACK_STATUSES:
        raise DataValidationError("Status", "The selected status is invalid.", "Choose a valid feedback status.")
    def update(records):
        if not 0 <= index < len(records) or not isinstance(records[index], dict):
            raise HTTPException(status_code=404, detail="Feedback not found")
        records[index]["status"] = status
        records[index]["reply_note"] = str(reply_note or "").strip()
    locked_json_mutation(FEEDBACK_FILE, [], update, list)
    return RedirectResponse("/admin/feedback?updated=1", status_code=303)


@router.get("/admin/feedback/{index}/screenshot")
def feedback_screenshot(index: int, request: Request):
    auth.require_admin(request)
    records = load_json_strict(FEEDBACK_FILE, [], list)
    if not 0 <= index < len(records) or not isinstance(records[index], dict):
        raise HTTPException(status_code=404, detail="Feedback not found")
    identifier = str(records[index].get("screenshot_id", "") or "")
    if not re.fullmatch(r"[0-9a-f]{32}\.png", identifier):
        raise HTTPException(status_code=404, detail="Screenshot not found")
    path = _upload_dir() / identifier
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Screenshot not found")
    return FileResponse(path, media_type="image/png", filename="feedback-screenshot.png")
