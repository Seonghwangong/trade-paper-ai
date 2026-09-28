from __future__ import annotations

from datetime import datetime, timezone
import re
from urllib.parse import quote, urlencode

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app import auth
from app.storage import data_path, load_json_strict, locked_json_mutation
from app.ui import html_escape, page_shell, section_card
from app.validation import DataValidationError, require_text


router = APIRouter()
BETA_APPLICATION_FILE = data_path("beta_applications.json")
MONTHLY_DOCUMENT_OPTIONS = ("1–10", "11–50", "51+")
APPLICATION_STATUSES = ("New", "Contacted", "Demo Scheduled", "Beta Customer", "Closed")
REFERRAL_SOURCES = ("Product Hunt", "Disquiet", "ExportersIndia", "Reddit", "Search engine", "Recommendation", "Other")
_EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def _styles():
    return """
*{box-sizing:border-box}body{margin:0;background:#F3F4F6;color:#111827;font-family:Arial,sans-serif}.tp-page{width:min(720px,calc(100% - 32px));margin:40px auto}.intro{text-align:center;margin-bottom:26px}.intro p{color:#64748B;line-height:1.6}.card{background:#fff;border:1px solid #E5E7EB;border-radius:18px;padding:28px;box-shadow:0 14px 35px rgba(15,23,42,.07)}form{display:grid;gap:9px}label{margin-top:8px;font-weight:750}input,select,textarea{width:100%;min-height:46px;padding:11px 13px;border:1px solid #CBD5E1;border-radius:10px;background:#fff;color:#111827;font:inherit}textarea{min-height:100px;resize:vertical}input:focus,select:focus,textarea:focus{border-color:#2563EB;outline:3px solid #DBEAFE}button,.back{display:inline-flex;min-height:48px;align-items:center;justify-content:center;margin-top:16px;padding:12px 18px;border:0;border-radius:11px;background:#111827;color:#fff;text-decoration:none;font-size:16px;font-weight:800;cursor:pointer}.required{color:#B91C1C}.benefits{list-style:none;padding:0;margin:20px 0}.benefits li{padding:8px 0;color:#334155}.promise{font-size:18px;font-weight:800}.next-steps{padding-left:24px;line-height:1.7}.next-steps li{margin:12px 0}.card p{line-height:1.6}.tp-release-footer{width:min(720px,calc(100% - 32px));margin:34px auto 20px;padding:20px 0;border-top:1px solid #D1D5DB;color:#6B7280;text-align:center;font-size:13px;line-height:1.7}.tp-release-footer strong{display:block;color:#374151}.tp-release-footer-nav{display:flex;justify-content:center;gap:12px;flex-wrap:wrap;margin-top:9px}.tp-release-footer-nav a{color:#475569}@media(max-width:600px){.tp-page{margin:20px auto}.card{padding:22px}}
"""


def _language_navigation(path: str, lang: str) -> str:
    english_current = ' aria-current="page"' if lang != "ko" else ""
    korean_current = ' aria-current="page"' if lang == "ko" else ""
    return (
        '<nav aria-label="Language / 언어" style="display:flex;gap:20px;justify-content:flex-end;margin-bottom:20px">'
        f'<a href="{path}" lang="en" hreflang="en"{english_current}>English</a>'
        f'<a href="{path}?lang=ko" lang="ko" hreflang="ko"{korean_current}>한국어</a></nav>'
    )


def _korean_application(options: str) -> str:
    labels = {"Disquiet": "디스콰이엇", "Search engine": "검색", "Recommendation": "지인 추천", "Other": "기타"}
    sources = "".join(f'<option value="{value}">{labels.get(value, value)}</option>' for value in REFERRAL_SOURCES)
    return f"""
<div class="intro"><h2>수출 서류에 같은 정보를 반복 입력하고 계신가요?</h2><p>Commercial Invoice와 Packing List를 직접 작성하는 소규모 수출업체·무역팀을 위한 체험입니다.</p><p>저장한 바이어·품목 정보를 재사용해 Invoice에서 Packing List로 이어서 작성하고, 두 PDF를 검토해 보세요.</p></div>
<section class="card" style="margin-bottom:20px"><h2>샘플 거래 한 건으로 시작해 보세요</h2><ol class="next-steps"><li>수출 품목과 작성하는 서류 수를 알려 주세요.</li><li>샘플 Invoice와 Packing List를 만들어 보세요. 필요한 경우 첫 사용을 안내해 드립니다.</li><li>다시 입력해야 했던 정보나 막히는 단계를 알려 주세요.</li></ol><p>첫 체험에는 가상 정보를 사용해 주세요. 실제 고객 정보나 비공개 거래 자료는 신청서에 넣지 마세요.</p><p>Free 플랜은 월 5개 문서를 지원하며, 저장한 샘플 문서도 포함됩니다. 온라인 유료 결제는 아직 활성화되지 않았습니다. 이 신청으로 계정이 만들어지거나 요금이 청구되지는 않습니다.</p><p><a href="/getting-started#sample-documents">가입 없이 샘플 Invoice·Packing List 먼저 보기</a></p><p>먼저 둘러보고 싶으신가요? <a href="/getting-started">체험 안내</a>를 읽거나 <a href="/register?next=%2Fdemo">계정을 만들어 데모를 시작</a>하세요. 계정이 있다면 <a href="/login?next=%2Fdemo">로그인</a>하세요.</p></section>
<section class="card"><form method="post" action="/founding-beta" data-native-submit="true">
<input type="hidden" name="lang" value="ko">
<label for="company_name">회사명 <span class="required">*</span></label><input id="company_name" name="company_name" autocomplete="organization" required>
<label for="contact_name">담당자 이름 <span class="required">*</span></label><input id="contact_name" name="contact_name" autocomplete="name" required>
<label for="email">이메일 <span class="required">*</span></label><input id="email" name="email" type="email" autocomplete="email" required>
<label for="country">국가 <span class="required">*</span></label><input id="country" name="country" autocomplete="country-name" required>
<label for="exports">어떤 품목을 수출하시나요? (선택)</label><textarea id="exports" name="exports" aria-describedby="exports-help"></textarea><p id="exports-help">바이어 정보, 품목 수량, 포장 정보 등 반복 입력이 많은 단계도 알려 주세요. 일반적인 설명이면 충분합니다.</p>
<label for="monthly_export_documents">월 수출 서류 작성량 (선택)</label><select id="monthly_export_documents" name="monthly_export_documents"><option value="">선택 안 함</option>{options}</select>
<label for="referral_source">어디에서 알게 되셨나요? (선택)</label><select id="referral_source" name="referral_source"><option value="">선택 안 함</option>{sources}</select>
<button type="submit">베타 체험 신청하기</button>
</form></section>"""


@router.get("/founding-beta", response_class=HTMLResponse)
def founding_beta_page(lang: str = "en"):
    options = "".join(
        f'<option value="{html_escape(value, attribute=True)}">{html_escape(value)}</option>'
        for value in MONTHLY_DOCUMENT_OPTIONS
    )
    source_options = "".join(f'<option value="{value}">{value}</option>' for value in REFERRAL_SOURCES)
    language = "ko" if lang == "ko" else "en"
    navigation = _language_navigation("/founding-beta", language)
    if language == "ko":
        return HTMLResponse(page_shell("베타 체험 신청", _korean_application(options), styles=_styles(), navigation=navigation, lang="ko"))
    content = f"""
<div class="intro"><h2>Stop retyping the same details between export documents</h2><p>For small exporters and trade teams who prepare Commercial Invoices and Packing Lists themselves.</p><p>Reuse buyer and product details, continue from an Invoice to a Packing List, then review both PDFs.</p></div>
<section class="card" style="margin-bottom:20px"><h2>Try one sample shipment with us</h2><ol class="next-steps"><li>Tell us what you export and how many documents you prepare.</li><li>Walk through a sample Invoice and Packing List, with direct onboarding if you need help.</li><li>Tell us where you had to retype information or found a step unclear.</li></ol><p>Use fictional details for your first test. Do not submit confidential customer or shipment information.</p><p>The Free plan includes 5 documents per month. Saving sample documents counts toward that limit. Online paid checkout is not active; this application does not create an account or charge you.</p><p><a href="/getting-started#sample-documents">Preview a sample Invoice and Packing List before signing up</a>. No account required.</p><p>Prefer to explore first? <a href="/getting-started">Read the walkthrough</a>, or <a href="/register?next=%2Fdemo">create an account to try the demo</a>. Already registered? <a href="/login?next=%2Fdemo">Log in to the demo</a>.</p></section>
<section class="card"><form method="post" action="/founding-beta" data-native-submit="true">
<label for="company_name">Company Name <span class="required">*</span></label><input id="company_name" name="company_name" autocomplete="organization" required>
<label for="contact_name">Contact Name <span class="required">*</span></label><input id="contact_name" name="contact_name" autocomplete="name" required>
<label for="email">Email <span class="required">*</span></label><input id="email" name="email" type="email" autocomplete="email" required>
<label for="country">Country <span class="required">*</span></label><input id="country" name="country" autocomplete="country-name" required>
<label for="exports">What do you export?</label><textarea id="exports" name="exports" aria-describedby="exports-help"></textarea><p id="exports-help">Optional: add the step where you repeat the most typing, such as buyer details, item quantities, or packing information. A general description is enough.</p>
<label for="monthly_export_documents">Monthly export documents</label><select id="monthly_export_documents" name="monthly_export_documents"><option value="">Select</option>{options}</select>
<label for="referral_source">How did you hear about us? (optional)</label><select id="referral_source" name="referral_source"><option value="">Prefer not to say</option>{source_options}</select>
<button type="submit">Apply for Founding Beta</button>
</form></section>"""
    return HTMLResponse(page_shell("Founding Beta Application", content, styles=_styles(), navigation=navigation))


@router.post("/founding-beta")
def submit_founding_beta(
    company_name: str = Form(""),
    contact_name: str = Form(""),
    email: str = Form(""),
    country: str = Form(""),
    exports: str = Form(""),
    monthly_export_documents: str = Form(""),
    referral_source: str = Form(""),
    lang: str = Form("en"),
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
    source = referral_source.strip() if isinstance(referral_source, str) else ""
    if source and source not in REFERRAL_SOURCES:
        raise DataValidationError("How did you hear about us?", "The selected source is invalid.", "Choose one of the available options or leave it blank.")
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
    if source:
        application["referral_source"] = source
    if lang == "ko":
        application["preferred_language"] = "ko"
    locked_json_mutation(
        BETA_APPLICATION_FILE, [], lambda applications: applications.append(application), list
    )
    destination = "/founding-beta/thank-you?lang=ko" if lang == "ko" else "/founding-beta/thank-you"
    return RedirectResponse(destination, status_code=303)


@router.get("/founding-beta/thank-you", response_class=HTMLResponse)
def founding_beta_thank_you(lang: str = "en"):
    language = "ko" if lang == "ko" else "en"
    navigation = _language_navigation("/founding-beta/thank-you", language)
    if language == "ko":
        content = section_card(
            "Founding Beta",
            '<ul class="benefits"><li>✓ 초기 10개 기업</li><li>✓ 초기 고객 가격 6개월 적용</li><li>✓ 첫 사용 직접 안내</li><li>✓ 우선 지원</li></ul>'
            '<p class="promise">영업일 기준 2일 이내에 연락드리겠습니다.</p>'
            '<h2>기다리는 동안 샘플을 살펴보세요</h2>'
            '<p>베타 신청으로 계정이 생성되지는 않습니다. <a href="/getting-started#sample-documents">샘플 Invoice·Packing List</a>는 가입 없이 볼 수 있습니다.</p>'
            '<ol class="next-steps"><li><a href="/register?next=%2Fdemo">계정을 만들거나</a>, 기존 계정으로 <a href="/login?next=%2Fdemo">데모에 로그인</a>하세요.</li>'
            '<li>샘플 회사·바이어·품목 정보를 검토하세요.</li><li>Invoice를 만들고 Packing List로 이어서 작성한 뒤 PDF를 확인하세요.</li></ol>'
            '<p>저장 버튼을 누르면 계정에 문서가 저장됩니다. 첫 체험에는 가상 정보를 사용해 주세요. Free 플랜은 월 5개 문서이며, 저장한 샘플도 포함됩니다.</p>'
            '<p><a href="/getting-started">단계별 체험 안내 보기</a> · <a href="/contact">문의하기</a></p>'
            '<a class="back" href="/">Trade Paper AI로 돌아가기</a>',
        )
        return HTMLResponse(page_shell("신청이 접수되었습니다", content, styles=_styles(), navigation=navigation, lang="ko"))
    content = section_card(
        "Founding Beta",
        '<ul class="benefits"><li>✓ First 10 companies</li><li>✓ Founding price for 6 months</li><li>✓ Direct onboarding</li><li>✓ Priority support</li></ul>'
        '<p class="promise">We\'ll contact you within 2 business days.</p>'
        '<h2>Start your sample walkthrough</h2>'
        '<p>Your application does not create an account. You can try the product while you wait for our reply.</p>'
        '<ol class="next-steps"><li><a href="/register?next=%2Fdemo">Create an account</a>, or <a href="/login?next=%2Fdemo">log in to the demo</a> if you already have one.</li>'
        '<li>Review the sample company, buyer, and product details before saving.</li>'
        '<li>Create an Invoice, then continue to a Packing List and review the PDFs.</li></ol>'
        '<p>The demo saves documents to your account when you press Save. Use sample data for your first walkthrough.</p>'
        '<p><a href="/getting-started">Read the step-by-step guide</a> for the full workflow and free-plan limits.</p>'
        '<p>Need help? <a href="/contact">Contact us</a>.</p>'
        '<a class="back" href="/">Back to Trade Paper AI</a>',
    )
    return HTMLResponse(page_shell("Thank You", content, subtitle="Your Founding Beta application has been received.", styles=_styles(), navigation=navigation))


def _admin_styles():
    return _styles() + """
.tp-page{width:min(1380px,calc(100% - 32px))}.admin-nav{display:flex;justify-content:space-between;gap:12px;align-items:center;flex-wrap:wrap;margin-bottom:22px}.admin-nav a{color:#1D4ED8;font-weight:750}.search{display:flex;gap:10px;flex:1 1 420px}.search{flex-wrap:wrap;min-width:0}.search input{margin:0;flex:1 1 240px;width:auto}.search select{flex:1 1 180px;width:auto;margin:0}.follow-up-summary a{color:#1D4ED8;font-weight:750}.search button{min-height:46px;margin:0}.feedback{margin:0 0 16px;padding:12px 14px;border:1px solid #BBF7D0;border-radius:10px;background:#F0FDF4;color:#166534;font-weight:750}.feedback:empty{display:none}.table-wrap{overflow-x:auto;border:1px solid #E5E7EB;border-radius:16px;background:#fff}table{width:100%;border-collapse:collapse;min-width:1120px}th{padding:13px;background:#111827;color:#fff;text-align:left;font-size:13px}td{padding:13px;border-bottom:1px solid #E5E7EB;vertical-align:top;word-break:break-word}td form{display:flex;grid-template-columns:none;gap:8px;min-width:220px}td select{min-height:40px;margin:0;padding:8px}td button{flex-shrink:0;white-space:nowrap;min-height:40px;margin:0;padding:8px 12px;font-size:13px}.email-actions{display:flex;align-items:center;gap:8px;flex-wrap:wrap}.email-actions a{color:#1D4ED8;font-weight:700}.copy-email{min-height:34px;padding:6px 9px;border:1px solid #CBD5E1;border-radius:8px;background:#F8FAFC;color:#334155;font-size:12px;font-weight:750;cursor:pointer}.empty{text-align:center;color:#64748B;padding:30px}.count{color:#475569;font-weight:750}.table-hint{display:none}@media(max-width:600px){.table-hint{display:block;color:#475569}.search input,.search select{min-width:0}}
"""


def _status_options(current):
    selected_status = current if current in APPLICATION_STATUSES else "New"
    return "".join(
        f'<option value="{html_escape(status, attribute=True)}"'
        f'{" selected" if status == selected_status else ""}>{html_escape(status)}</option>'
        for status in APPLICATION_STATUSES
    )


@router.get("/admin/founding-beta", response_class=HTMLResponse)
def founding_beta_admin(request: Request, search: str = "", updated: int = 0, status_filter: str = "", sort: str = "newest"):
    auth.require_admin(request)
    selected_filter = str(status_filter or "").strip()
    if selected_filter and selected_filter not in APPLICATION_STATUSES:
        raise DataValidationError("Status", "The selected filter is invalid.", "Choose one of the available statuses.")
    selected_sort = str(sort or "newest").strip()
    if selected_sort not in ("newest", "oldest"):
        raise DataValidationError("Sort", "The selected order is invalid.", "Choose newest or oldest first.")
    records = load_json_strict(BETA_APPLICATION_FILE, [], list)
    def record_status(record):
        value = str(record.get("status", "") or "").strip()
        return value if value in APPLICATION_STATUSES else "New"

    new_count = sum(1 for record in records if isinstance(record, dict) and record_status(record) == "New")
    filter_options = '<option value="">All statuses</option>' + "".join(
        f'<option value="{value}"{" selected" if value == selected_filter else ""}>{value}</option>'
        for value in APPLICATION_STATUSES
    )
    query = str(search or "").strip()
    entries = [
        (index, record)
        for index, record in enumerate(records)
        if isinstance(record, dict) and (not selected_filter or record_status(record) == selected_filter) and (
            not query
            or any(
                query.casefold() in str(record.get(field, "") or "").casefold()
                for field in ("company_name", "contact_name", "email")
            )
        )
    ]
    def submission_order(entry):
        index, record = entry
        try:
            submitted = datetime.fromisoformat(str(record.get("submitted_at", "")))
            if submitted.tzinfo is None:
                submitted = submitted.replace(tzinfo=timezone.utc)
            timestamp = submitted.timestamp()
            return (0, timestamp if selected_sort == "oldest" else -timestamp, index)
        except (ValueError, TypeError, OverflowError):
            return (1, index if selected_sort == "oldest" else -index, index)

    entries.sort(key=submission_order)
    sort_options = "".join(
        f'<option value="{value}"{" selected" if value == selected_sort else ""}>{label}</option>'
        for value, label in (("newest", "Newest first"), ("oldest", "Oldest first"))
    )
    return_query = urlencode({"search": query, "status_filter": selected_filter, "sort": selected_sort})
    rows = ""
    for index, record in entries:
        status = str(record.get("status", "") or "").strip()
        email = str(record.get("email", "") or "").strip()
        mailto = f"mailto:{quote(email, safe='@._+-')}?{urlencode({'subject': 'Trade Paper AI Founding Beta'})}"
        company = str(record.get("company_name", "") or "")
        contact_name = str(record.get("contact_name", "") or "").strip() or "there"
        source = record.get("referral_source")
        source_label = source if source in REFERRAL_SOURCES else "Not provided"
        draft_body = (
            f"Hi {contact_name},\r\n\r\n"
            "Thank you for applying to the Trade Paper AI Founding Beta.\r\n\r\n"
            "Preview a matching Invoice and Packing List before signing up (no account needed): "
            "https://www.tradepaper.ai/getting-started#sample-documents\r\n\r\n"
            "Read the step-by-step guide: https://www.tradepaper.ai/getting-started\r\n\r\n"
            "To try the workflow, create an account at https://www.tradepaper.ai/register?next=%2Fdemo, "
            "then sign in at https://www.tradepaper.ai/login?next=%2Fdemo. "
            "Your beta application does not create an account.\r\n\r\n"
            "Start with sample company, buyer, and product details. Create an Invoice, "
            "continue to a Packing List, and review the PDFs. "
            "The demo saves documents to your account when you press Save.\r\n\r\n"
            "Which part of preparing export documents takes the most time for your team? "
            "Reply if you would like help with your first walkthrough.\r\n\r\n"
            "Thank you,\r\nSeonghwan\r\nTrade Paper AI"
        )
        draft_subject = "Your Trade Paper AI beta walkthrough"
        if record.get("preferred_language") == "ko":
            draft_subject = "[Trade Paper AI] 베타 신청 감사합니다 — 샘플 체험 안내"
            draft_body = (
                f"안녕하세요, {contact_name}님.\r\n\r\n"
                "Trade Paper AI 베타 체험에 신청해 주셔서 감사합니다.\r\n\r\n"
                "먼저 가입 없이 샘플 Invoice와 Packing List를 살펴보세요: "
                "https://www.tradepaper.ai/getting-started#sample-documents\r\n\r\n"
                "단계별 체험 안내: https://www.tradepaper.ai/getting-started\r\n\r\n"
                "베타 신청으로 계정이 만들어지지는 않습니다. 직접 체험하려면 "
                "https://www.tradepaper.ai/register?next=%2Fdemo 에서 계정을 만들고 "
                "https://www.tradepaper.ai/login?next=%2Fdemo 에서 로그인해 주세요.\r\n\r\n"
                "가상 회사·바이어·품목 정보로 Invoice를 작성한 뒤 Packing List로 이어서 만들고 PDF를 검토해 보세요. "
                "저장 버튼을 누르면 계정에 문서가 저장되며, Free 플랜의 월 5개 문서 한도에 포함됩니다.\r\n\r\n"
                "현재 수출 서류를 작성하면서 가장 반복 입력이 많은 부분은 무엇인가요? "
                "첫 체험에 도움이 필요하면 답장해 주세요.\r\n\r\n감사합니다.\r\n공성환 | Trade Paper AI"
            )
        draft_url = f"mailto:{quote(email, safe='@._+-')}?{urlencode({'subject': draft_subject, 'body': draft_body}, quote_via=quote)}"
        rows += f"""
<tr><td>{html_escape(record.get('submitted_at', ''))}</td>
<td>{html_escape(company)}</td>
<td>{html_escape(record.get('contact_name', ''))}</td>
<td><div class="email-actions"><a href="{html_escape(mailto, attribute=True)}">{html_escape(email)}</a><button class="copy-email" type="button" data-email="{html_escape(email, attribute=True)}" aria-label="Copy email for {html_escape(company, attribute=True)}">Copy</button><a href="{html_escape(draft_url, attribute=True)}" aria-label="Draft welcome email for {html_escape(company, attribute=True)}">Draft welcome email</a></div></td>
<td>{html_escape(record.get('country', ''))}</td>
<td>{html_escape(record.get('exports', ''))}</td>
<td>{html_escape(record.get('monthly_export_documents', ''))}</td>
<td>{html_escape(source_label)}</td>
<td><form method="post" action="/admin/founding-beta/{index}/status?{html_escape(return_query, attribute=True)}" data-native-submit="true"><select name="status" aria-label="Status for {html_escape(record.get('company_name', ''), attribute=True)}">{_status_options(status)}</select><button type="submit">Update</button></form></td></tr>"""
    if not rows:
        rows = '<tr><td class="empty" colspan="9">No Founding Beta applications found.</td></tr>'
    feedback = "Status updated successfully." if updated == 1 else ""
    content = f"""
<p class="follow-up-summary"><a href="/admin/founding-beta?status_filter=New&amp;sort=oldest">{new_count} new applications awaiting first contact</a></p>
<div class="admin-nav"><a href="/">← Dashboard</a><form class="search" action="/admin/founding-beta" method="get"><input type="search" name="search" value="{html_escape(query, attribute=True)}" placeholder="Search company, contact, or email" aria-label="Search applications"><select name="status_filter" aria-label="Filter applications by status">{filter_options}</select><select name="sort" aria-label="Application order">{sort_options}</select><button type="submit">Search</button></form><span class="count">{len(entries)} applications</span></div>
<p>Draft welcome email opens your email app for review. Send it there, then update the application status to Contacted.</p>
<p>Referral source is optional and supplied by the applicant. It is separate from anonymous page-view analytics.</p>
<div id="admin-feedback" class="feedback" role="status" aria-live="polite">{feedback}</div>
<p class="table-hint">Swipe the table sideways to see email actions and application status.</p>
<div class="table-wrap" role="region" aria-label="Beta applications" tabindex="0"><table><thead><tr><th>Application Date</th><th>Company</th><th>Contact Name</th><th>Email</th><th>Country</th><th>Export Item</th><th>Monthly Documents</th><th>Referral source</th><th>Status</th></tr></thead><tbody>{rows}</tbody></table></div>
<script>(function(){{const feedback=document.getElementById('admin-feedback');async function copyEmail(value){{if(navigator.clipboard&&navigator.clipboard.writeText){{try{{await navigator.clipboard.writeText(value);return;}}catch(error){{}}}}const input=document.createElement('textarea');input.value=value;input.setAttribute('readonly','');input.style.position='fixed';input.style.opacity='0';document.body.appendChild(input);input.select();document.execCommand('copy');input.remove();}}document.querySelectorAll('.copy-email').forEach(function(button){{button.addEventListener('click',function(){{feedback.textContent='Email copied.';copyEmail(button.dataset.email||'');}});}});}})();</script>"""
    return HTMLResponse(page_shell("Founding Beta Admin", content, subtitle="Manage application follow-up status.", styles=_admin_styles()))


@router.post("/admin/founding-beta/{index}/status")
def update_founding_beta_status(index: int, request: Request, status: str = Form("")):
    auth.require_admin(request)
    normalized_status = str(status or "").strip()
    if normalized_status not in APPLICATION_STATUSES:
        raise DataValidationError("Status", "The selected status is invalid.", "Choose one of the available statuses.")

    def update(records):
        if index < 0 or index >= len(records) or not isinstance(records[index], dict):
            raise HTTPException(status_code=404, detail="Founding Beta application not found")
        records[index]["status"] = normalized_status

    return_params = {"updated": "1"}
    search = request.query_params.get("search", "").strip()
    status_filter = request.query_params.get("status_filter", "").strip()
    sort = request.query_params.get("sort", "").strip()
    if search:
        return_params["search"] = search
    if status_filter in APPLICATION_STATUSES:
        return_params["status_filter"] = status_filter
    if sort in ("newest", "oldest"):
        return_params["sort"] = sort
    locked_json_mutation(BETA_APPLICATION_FILE, [], update, list)
    return RedirectResponse("/admin/founding-beta?" + urlencode(return_params), status_code=303)
