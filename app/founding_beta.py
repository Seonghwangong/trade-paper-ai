from __future__ import annotations

from datetime import datetime, timezone
import re
from urllib.parse import quote, urlencode

from fastapi import APIRouter, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse

from app.storage import data_path, load_json_strict, locked_json_mutation
from app.ui import html_escape, page_shell, section_card
from app.validation import DataValidationError, require_text


router = APIRouter()
BETA_APPLICATION_FILE = data_path("beta_applications.json")
MONTHLY_DOCUMENT_OPTIONS = ("1–10", "11–50", "51+")
APPLICATION_STATUSES = ("New", "Contacted", "Demo Scheduled", "Beta Customer", "Closed")
_EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def _styles():
    return """
*{box-sizing:border-box}body{margin:0;background:#F3F4F6;color:#111827;font-family:Arial,sans-serif}.tp-page{width:min(720px,calc(100% - 32px));margin:40px auto}.intro{text-align:center;margin-bottom:26px}.intro p{color:#64748B;line-height:1.6}.card{background:#fff;border:1px solid #E5E7EB;border-radius:18px;padding:28px;box-shadow:0 14px 35px rgba(15,23,42,.07)}form{display:grid;gap:9px}label{margin-top:8px;font-weight:750}input,select,textarea{width:100%;min-height:46px;padding:11px 13px;border:1px solid #CBD5E1;border-radius:10px;background:#fff;color:#111827;font:inherit}textarea{min-height:100px;resize:vertical}input:focus,select:focus,textarea:focus{border-color:#2563EB;outline:3px solid #DBEAFE}button,.back{display:inline-flex;min-height:48px;align-items:center;justify-content:center;margin-top:16px;padding:12px 18px;border:0;border-radius:11px;background:#111827;color:#fff;text-decoration:none;font-size:16px;font-weight:800;cursor:pointer}.required{color:#B91C1C}.benefits{list-style:none;padding:0;margin:20px 0}.benefits li{padding:8px 0;color:#334155}.promise{font-size:18px;font-weight:800}.tp-release-footer{width:min(720px,calc(100% - 32px));margin:34px auto 20px;padding:20px 0;border-top:1px solid #D1D5DB;color:#6B7280;text-align:center;font-size:13px;line-height:1.7}.tp-release-footer strong{display:block;color:#374151}.tp-release-footer-nav{display:flex;justify-content:center;gap:12px;flex-wrap:wrap;margin-top:9px}.tp-release-footer-nav a{color:#475569}@media(max-width:600px){.tp-page{margin:20px auto}.card{padding:22px}}
"""


@router.get("/founding-beta", response_class=HTMLResponse)
def founding_beta_page():
    options = "".join(
        f'<option value="{html_escape(value, attribute=True)}">{html_escape(value)}</option>'
        for value in MONTHLY_DOCUMENT_OPTIONS
    )
    content = f"""
<div class="intro"><p>Launched July 22, 2026 · Applications are open for our first customer cohort.</p><p>Apply in about 30 seconds. This form collects application details only and does not create an account.</p></div>
<section class="card"><form method="post" action="/founding-beta" data-native-submit="true">
<label for="company_name">Company Name <span class="required">*</span></label><input id="company_name" name="company_name" autocomplete="organization" required>
<label for="contact_name">Contact Name <span class="required">*</span></label><input id="contact_name" name="contact_name" autocomplete="name" required>
<label for="email">Email <span class="required">*</span></label><input id="email" name="email" type="email" autocomplete="email" required>
<label for="country">Country <span class="required">*</span></label><input id="country" name="country" autocomplete="country-name" required>
<label for="exports">What do you export?</label><textarea id="exports" name="exports"></textarea>
<label for="monthly_export_documents">Monthly export documents</label><select id="monthly_export_documents" name="monthly_export_documents"><option value="">Select</option>{options}</select>
<button type="submit">Apply for Founding Beta</button>
</form></section>"""
    return HTMLResponse(page_shell("Founding Beta Application", content, styles=_styles()))


@router.post("/founding-beta")
def submit_founding_beta(
    company_name: str = Form(""),
    contact_name: str = Form(""),
    email: str = Form(""),
    country: str = Form(""),
    exports: str = Form(""),
    monthly_export_documents: str = Form(""),
):
    company_name = require_text("Company Name", company_name)
    contact_name = require_text("Contact Name", contact_name)
    email = require_text("Email", email).strip()
    country = require_text("Country", country)
    if not _EMAIL_PATTERN.fullmatch(email):
        raise DataValidationError("Email", "Enter a valid email address.", "Use an address such as name@company.com.")
    monthly = str(monthly_export_documents or "").strip()
    if monthly and monthly not in MONTHLY_DOCUMENT_OPTIONS:
        raise DataValidationError("Monthly export documents", "The selected range is invalid.", "Choose one of the available ranges.")
    application = {
        "company_name": company_name,
        "contact_name": contact_name,
        "email": email,
        "country": country,
        "exports": str(exports or "").strip(),
        "monthly_export_documents": monthly,
        "status": "New",
        "submitted_at": datetime.now(timezone.utc).isoformat(),
    }
    locked_json_mutation(
        BETA_APPLICATION_FILE, [], lambda applications: applications.append(application), list
    )
    return RedirectResponse("/founding-beta/thank-you", status_code=303)


@router.get("/founding-beta/thank-you", response_class=HTMLResponse)
def founding_beta_thank_you():
    content = section_card(
        "Founding Beta",
        '<ul class="benefits"><li>✓ First 10 companies</li><li>✓ Founding price for 6 months</li><li>✓ Direct onboarding</li><li>✓ Priority support</li></ul>'
        '<p class="promise">We\'ll contact you within 2 business days.</p><a class="back" href="/">Back to Trade Paper AI</a>',
    )
    return HTMLResponse(page_shell("Thank You", content, subtitle="Your Founding Beta application has been received.", styles=_styles()))


def _admin_styles():
    return _styles() + """
.tp-page{width:min(1380px,calc(100% - 32px))}.admin-nav{display:flex;justify-content:space-between;gap:12px;align-items:center;flex-wrap:wrap;margin-bottom:22px}.admin-nav a{color:#1D4ED8;font-weight:750}.search{display:flex;gap:10px;flex:1 1 420px}.search input{margin:0}.search button{min-height:46px;margin:0}.feedback{margin:0 0 16px;padding:12px 14px;border:1px solid #BBF7D0;border-radius:10px;background:#F0FDF4;color:#166534;font-weight:750}.feedback:empty{display:none}.table-wrap{overflow-x:auto;border:1px solid #E5E7EB;border-radius:16px;background:#fff}table{width:100%;border-collapse:collapse;min-width:1120px}th{padding:13px;background:#111827;color:#fff;text-align:left;font-size:13px}td{padding:13px;border-bottom:1px solid #E5E7EB;vertical-align:top;word-break:break-word}td form{display:flex;grid-template-columns:none;gap:8px;min-width:220px}td select{min-height:40px;margin:0;padding:8px}td button{min-height:40px;margin:0;padding:8px 12px;font-size:13px}.email-actions{display:flex;align-items:center;gap:8px;flex-wrap:wrap}.email-actions a{color:#1D4ED8;font-weight:700}.copy-email{min-height:34px;padding:6px 9px;border:1px solid #CBD5E1;border-radius:8px;background:#F8FAFC;color:#334155;font-size:12px;font-weight:750;cursor:pointer}.empty{text-align:center;color:#64748B;padding:30px}.count{color:#475569;font-weight:750}
"""


def _status_options(current):
    selected_status = current if current in APPLICATION_STATUSES else "New"
    return "".join(
        f'<option value="{html_escape(status, attribute=True)}"'
        f'{" selected" if status == selected_status else ""}>{html_escape(status)}</option>'
        for status in APPLICATION_STATUSES
    )


@router.get("/admin/founding-beta", response_class=HTMLResponse)
def founding_beta_admin(search: str = "", updated: int = 0):
    records = load_json_strict(BETA_APPLICATION_FILE, [], list)
    query = str(search or "").strip()
    entries = [
        (index, record)
        for index, record in enumerate(records)
        if isinstance(record, dict) and (
            not query
            or any(
                query.casefold() in str(record.get(field, "") or "").casefold()
                for field in ("company_name", "contact_name", "email")
            )
        )
    ]
    entries.reverse()
    rows = ""
    for index, record in entries:
        status = str(record.get("status", "") or "").strip()
        email = str(record.get("email", "") or "").strip()
        mailto = f"mailto:{quote(email, safe='@._+-')}?{urlencode({'subject': 'Trade Paper AI Founding Beta'})}"
        company = str(record.get("company_name", "") or "")
        rows += f"""
<tr><td>{html_escape(record.get('submitted_at', ''))}</td>
<td>{html_escape(company)}</td>
<td>{html_escape(record.get('contact_name', ''))}</td>
<td><div class="email-actions"><a href="{html_escape(mailto, attribute=True)}">{html_escape(email)}</a><button class="copy-email" type="button" data-email="{html_escape(email, attribute=True)}" aria-label="Copy email for {html_escape(company, attribute=True)}">Copy</button></div></td>
<td>{html_escape(record.get('country', ''))}</td>
<td>{html_escape(record.get('exports', ''))}</td>
<td>{html_escape(record.get('monthly_export_documents', ''))}</td>
<td><form method="post" action="/admin/founding-beta/{index}/status" data-native-submit="true"><select name="status" aria-label="Status for {html_escape(record.get('company_name', ''), attribute=True)}">{_status_options(status)}</select><button type="submit">Update</button></form></td></tr>"""
    if not rows:
        rows = '<tr><td class="empty" colspan="8">No Founding Beta applications found.</td></tr>'
    feedback = "Status updated successfully." if updated == 1 else ""
    content = f"""
<div class="admin-nav"><a href="/">← Dashboard</a><form class="search" action="/admin/founding-beta" method="get"><input type="search" name="search" value="{html_escape(query, attribute=True)}" placeholder="Search company, contact, or email"><button type="submit">Search</button></form><span class="count">{len(entries)} applications</span></div>
<div id="admin-feedback" class="feedback" role="status" aria-live="polite">{feedback}</div>
<div class="table-wrap"><table><thead><tr><th>Application Date</th><th>Company</th><th>Contact Name</th><th>Email</th><th>Country</th><th>Export Item</th><th>Monthly Documents</th><th>Status</th></tr></thead><tbody>{rows}</tbody></table></div>
<script>(function(){{const feedback=document.getElementById('admin-feedback');async function copyEmail(value){{if(navigator.clipboard&&navigator.clipboard.writeText){{try{{await navigator.clipboard.writeText(value);return;}}catch(error){{}}}}const input=document.createElement('textarea');input.value=value;input.setAttribute('readonly','');input.style.position='fixed';input.style.opacity='0';document.body.appendChild(input);input.select();document.execCommand('copy');input.remove();}}document.querySelectorAll('.copy-email').forEach(function(button){{button.addEventListener('click',function(){{feedback.textContent='Email copied.';copyEmail(button.dataset.email||'');}});}});}})();</script>"""
    return HTMLResponse(page_shell("Founding Beta Admin", content, subtitle="Manage application follow-up status.", styles=_admin_styles()))


@router.post("/admin/founding-beta/{index}/status")
def update_founding_beta_status(index: int, status: str = Form("")):
    normalized_status = str(status or "").strip()
    if normalized_status not in APPLICATION_STATUSES:
        raise DataValidationError("Status", "The selected status is invalid.", "Choose one of the available statuses.")

    def update(records):
        if index < 0 or index >= len(records) or not isinstance(records[index], dict):
            raise HTTPException(status_code=404, detail="Founding Beta application not found")
        records[index]["status"] = normalized_status

    locked_json_mutation(BETA_APPLICATION_FILE, [], update, list)
    return RedirectResponse("/admin/founding-beta?updated=1", status_code=303)
