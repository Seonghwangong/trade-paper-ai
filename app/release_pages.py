from fastapi import APIRouter
from app import public_billing
from fastapi.responses import HTMLResponse

from app.release import (
    APP_NAME, APP_VERSION, BUILD_NAME, BUSINESS_PHONE, LAST_UPDATED, RELEASE_STAGE, RELEASE_TYPE,
    contact_email, contact_url,
)
from app.ui import button, html_escape, page_shell, section_card, toolbar


router = APIRouter()


def release_navigation():
    return toolbar(
        button("Dashboard", "/", "secondary"),
        button("Getting Started", "/getting-started", "secondary"),
        button("About", "/about", "secondary"),
        button("Release Notes", "/release-notes", "secondary"),
        button("Version History", "/version-history", "secondary"),
        button("Try Demo (login required)", "/demo", "secondary"),
        button("Contact", "/contact", "secondary"),
    )


def information_page(title, subtitle, content, *, lang="en", navigation=None):
    content = public_billing.customer_copy(content)
    styles = """
*{box-sizing:border-box}body{margin:0;background:#F3F4F6;color:#111827;font-family:Arial,sans-serif}.tp-page{width:min(920px,calc(100% - 32px));margin:40px auto}.card{background:#fff;border:1px solid #E5E7EB;border-radius:16px;padding:24px;margin:18px 0}.card h2{margin-top:0}.card p,.card li{color:#475569;line-height:1.7}.tp-toolbar,.tp-toolbar-actions{display:flex;gap:10px;flex-wrap:wrap}.tp-btn{display:inline-flex;align-items:center;justify-content:center;min-height:42px;padding:10px 16px;border-radius:12px;background:#111827;color:#fff;text-decoration:none;font-weight:700}.tp-btn-secondary{background:#E5E7EB;color:#111827}.tp-release-footer{width:min(920px,calc(100% - 32px));margin:34px auto 20px;padding:20px 0;border-top:1px solid #D1D5DB;color:#6B7280;text-align:center;font-size:13px;line-height:1.7}.tp-release-footer strong{display:block;color:#374151}.tp-release-footer-nav{display:flex;justify-content:center;gap:12px;flex-wrap:wrap;margin-top:9px}.tp-release-footer-nav a{color:#475569}.version{display:inline-block;padding:8px 11px;border-radius:999px;background:#DCFCE7;color:#166534;font-weight:700}@media(max-width:640px){.tp-page{margin:20px auto}.tp-btn{width:100%}}
"""
    return HTMLResponse(page_shell(title, content, subtitle=subtitle, navigation=release_navigation() if navigation is None else navigation, styles=styles, lang=lang))


@router.get("/release-notes")
def release_notes_page():
    content = (
        f'<p class="version">Version {APP_VERSION} · {RELEASE_STAGE}</p>'
        + section_card("October 27, 2026 · Paired PDF review 3.6.4", '<p>From a linked Invoice or Packing List email screen, prepare both PDFs and review them side by side. The same frozen files are attached together after confirmation. Copies are private and available for one hour; source changes require a new review. Shared-field differences must be resolved first. Repeat submissions of the same review do not repeat delivery; an unconfirmed attempt requires checking delivery history before any new attempt.</p><p lang="ko">연결된 Invoice·Packing List의 이메일 화면에서 두 PDF를 준비해 나란히 확인하고, 확인한 사본 그대로 함께 보낼 수 있습니다. 사본은 1시간 동안 이용할 수 있으며 원본이 바뀌면 다시 검토해야 합니다. 공통 항목의 차이는 먼저 수정하고 실제 박스·중량도 확인해야 합니다. 같은 확인 화면의 중복 발송 요청은 차단합니다.</p>')
        + section_card("October 27, 2026 · Easier first steps 3.6.3", '<p>Choose file import, no-signup sample PDFs or guided manual entry in the English/Korean getting-started page. Signup keeps the selected file-import destination and language; first-login onboarding now names that next step clearly. Account and company requirements are unchanged.</p><p lang="ko">첫 사용 안내에서 파일 작성·가입 없는 예시·직접 입력을 바로 선택할 수 있습니다. 가입 후 선택한 파일 작성 경로와 언어를 유지하며, 첫 로그인 안내에서도 다음 행동을 분명하게 표시합니다.</p>')
        + section_card("October 27, 2026 · Attachment review 3.6.2", '<p>Before sending a document, see its filename, shipment reference and buyer, with account-owned Invoice/Packing List comparison links and warnings for shared-field differences. Related PDFs are explicitly not additional attachments. Attachments are generated at send time; delivery receipts show the prepared filename and UTC time. Original downloads do not update automatically.</p><p lang="ko">이메일 발송 전에 첨부 파일명·출하·거래처와 연결된 Invoice·Packing List를 확인하고 차이가 있으면 비교할 수 있습니다. 발송 요청한 파일의 생성 시각도 표시합니다. 미리보기와 발송 첨부는 각각 생성되며 이전 PDF는 바뀌지 않습니다.</p>')
        + section_card("October 27, 2026 · Guided file workspace 3.6.1", "<p>A quieter navy-and-gray import workspace with visible file, review and saved steps. Try a fictional sample, compare required fields and totals, and continue to Packing List after saving. A failed replacement file keeps your existing draft.</p><p lang=\"ko\">파일 가져오기부터 초안 확인·저장까지 현재 단계를 보여줍니다. 가상 예시로 시작할 수 있으며, 새 파일을 읽지 못해도 기존 초안은 유지합니다. 저장 후 Packing List로 이어갈 수 있습니다.</p>")
        + section_card("October 25, 2026 · File import 3.6.0", "<p>Start an editable Invoice draft from XLSX/CSV, PDFs or printed English/Korean photos. Review parties, every item and numeric values before saving. Maximum 8 MB, 5 PDF pages and 100 items. Formula cells, handwriting and unfamiliar layouts may require correction. Original files are not retained. Saving uses the existing document allowance; continue to Packing List after review.</p><p lang=\"ko\">엑셀·PDF·사진에서 송장 초안을 채우고 원본과 확인한 뒤 저장할 수 있습니다. 모든 서식을 완벽히 읽는 기능은 아니며, 박스·중량은 Packing List에서 확인합니다.</p>")
        + section_card("October 25, 2026 · Public Release 3.5.1", "<p>A shorter English/Korean home with a product preview, Free and Starter plans, and concise FAQ. Current product labels no longer use Founding Beta. First-use help remains available; existing applications and billing controls are unchanged.</p>")
        + section_card("October 23, 2026 · Packing workflow and Korean workspace", '<ul><li>Reuse account-owned packing settings to calculate carton count and net/gross kg totals. Verify actual packing; the same carton tare is assumed for a partially filled last carton.</li><li>Compare and apply Invoice changes to one linked Packing List. Changed or ambiguous items require reviewed packing totals. Stale previews are rejected. Existing PDFs and downstream documents remain unchanged.</li><li>Choose Korean for workspace navigation and core company, buyer, product, Invoice and Packing screens. Stored document data and PDF language are not translated.</li></ul><p lang="ko">포장 기준 저장·재사용 계산, 송장 변경 비교·반영, 주요 작업 화면 한국어 선택을 추가했습니다. 포장 실측값을 확인하고 변경 후 PDF를 새로 다운로드하세요.</p>')
        + section_card("September 24, 2026 · Invoice and PDF improvements", '<ul><li>Invoice totals, lists and PDFs now display the document currency. Selecting a buyer with a default currency fills it into the Invoice. Review unit prices when changing currency; amounts are not automatically converted.</li><li>Fractional quantities remain numeric after editing. Invalid, negative and non-finite quantities or prices are rejected before saving.</li><li>Invoice and Packing List PDF previews show actionable errors instead of opening failed responses as files. A link is available when a preview popup is blocked.</li><li>Demo buyers start with USD; sample Invoice progress updates after the sample values load.</li><li>Packing List screens explain that later Invoice edits do not automatically update an existing Packing List. Review both documents and download fresh PDFs after changes.</li></ul><div lang="ko"><p><b>한국어 요약:</b> Invoice 통화 표시와 소수 수량 처리를 수정하고, PDF 생성 실패 시 안내를 개선했습니다. 저장된 Packing List는 Invoice 수정 시 자동 갱신되지 않으므로 두 문서를 함께 검토해 주세요. 통화 변경 시 자동 환산은 하지 않습니다.</p></div>')
        + section_card("September 23, 2026 · Account and plan improvements", '<ul><li>Login sessions now survive routine deployments until their normal expiry.</li><li>Free accounts no longer show a subscription cancellation action that could interrupt document creation.</li><li>The monthly limit message explains the UTC reset schedule and links to the Dashboard and Contact page.</li><li>Professional plan inquiries now link directly to <a href="/contact">Contact us</a>.</li><li>Toss test card registration is restricted to platform administrators. Online paid checkout remains unavailable.</li></ul>')
        + section_card("September 23, 2026 · Onboarding and email improvements", '<ul><li><a href="/getting-started">Read the English and Korean getting-started guide</a> before creating an account.</li><li>Continue to the demo after signup and login, and read the guide even before company setup is complete.</li><li>Keep existing company details while trying sample data.</li><li>Review PDF attachments before emailing documents, and retain form input if a send attempt fails.</li><li>Use clearer mobile links from the product tour to the guide and signup.</li></ul>')
        + section_card("What's New", "<ul><li>Account-owned business records and protected application routes</li><li>Password recovery with expiring, hashed reset tokens</li><li>Safe post-login return and POST-only Logout</li><li>Stored XSS protection for customer master data screens</li></ul>")
        + section_card("Current Capabilities", "<p>Company, customer, buyer, and product master data; commercial, shipping, customs, and certificate documents; Shipment Hub workflow guidance; Dashboard; Global Search; validation; protected deletion; account isolation; password recovery; safe JSON storage; snapshots; PDF generation; and PDF email delivery.</p>")
        + section_card("Performance", "<p>Request-scoped JSON reuse reduces repeated Dashboard and Shipment Detail reads without stale global caching.</p>")
        + section_card("Workflow", "<p>Commercial Invoice → Packing List → Shipping Instruction → Booking Confirmation → Bill of Lading → Customs Declaration.</p>")
        + section_card("Known Limitations", '<ul><li>Review exported documents against your actual shipment and applicable requirements.</li><li>JSON storage requires a single application worker.</li><li>Team invitations and role management require the Professional plan; contact us for availability.</li><li>See the <a href="/starter">Starter page</a> for current paid checkout terms and availability.</li></ul>')
        + section_card("Deployment Checklist", "<ul><li>Serve the application through HTTPS.</li><li>Configure and verify storage backups.</li><li>Configure and verify an email delivery provider (SMTP or Resend API) for password recovery and document email.</li><li>Publish a customer Contact email or URL.</li><li>Use an existing writable DATA DIR.</li><li>Set a stable Session Secret of at least 32 characters.</li></ul>")
    )
    return information_page(f"Version {APP_VERSION} Release Notes", RELEASE_STAGE, content)


@router.get("/about")
@router.get("/About", include_in_schema=False)
def about_page():
    content = (
        section_card("Application", f"<p><b>Application Name</b><br>{APP_NAME}</p><p><b>Version</b><br>{APP_VERSION}</p><p><b>Build</b><br>{BUILD_NAME}</p><p><b>Release Date</b><br>{LAST_UPDATED}</p>")
        + section_card("Features", "<ul><li>Shipment-centered document workflow</li><li>Dashboard, Global Search, and workflow guidance</li><li>Safe validation, storage, deletion, PDF output, and PDF email delivery</li><li>English and Korean getting-started guide with a sample document walkthrough</li><li>Comfort-focused browser productivity tools</li></ul>")
        + section_card("Release Status", f"<p><b>{RELEASE_STAGE}</b><br>{RELEASE_TYPE}</p>")
        + section_card("Release Notes", f'<p><a href="/release-notes">Read the Version {APP_VERSION} Release Notes</a></p>')
        + section_card("Project Philosophy", '<p><b>Fast.<br>Safe.<br>Comfortable.</b></p>')
    )
    return information_page("About Trade Paper AI", RELEASE_TYPE, content)


@router.get("/version-history")
def version_history_page():
    content = (
        section_card("0.9 RC", "<p>Release-candidate workflow, stability, integrity, and UX foundation.</p>")
        + section_card("1.0", "<p>First production-ready release with complete shipment workflow visibility.</p>")
        + section_card("1.1", "<p>Customer Experience release with first-time guidance, demo prefills, next actions, and contextual help.</p>")
        + section_card("1.2", "<p>Comfort First release with faster keyboard workflows and clearer save feedback.</p>")
        + section_card("1.3", "<p>Comfort Productivity release reducing repetitive item entry and navigation.</p>")
        + section_card("1.4", "<p>Guided Continuity release connecting recent work, completion state, and safe navigation.</p>")
        + section_card("1.5", "<p>Select First release with browser favorites, recent-value suggestions, and comfortable touch targets.</p>")
        + section_card("1.6", "<p>Smart Templates release for quickly reusing frequent document configurations.</p>")
        + section_card("1.7", "<p>Faster Search release with live filtering, complete results, and clearer controls.</p>")
        + section_card("1.8", "<p>Smart Dashboard release with project statistics, recent Invoices, and focused Quick Actions.</p>")
        + section_card("1.9", "<p>Smart Workflow release with clearer current state, progress, and contextual Next Step guidance.</p>")
        + section_card("2.0", "<p>Favorites release for quickly selecting frequently reused trade terms and ports.</p>")
        + section_card("2.1", "<p>Smart Validation release with real-time status and Quick Jump guidance before Save.</p>")
        + section_card("2.2", "<p>Smart Context release with Buyer-specific, optional recent-value suggestions.</p>")
        + section_card("2.3", "<p>Smart Item Library release for searching and reusing recent product configurations.</p>")
        + section_card("2.4", "<p>Smart Next Actions release connecting a saved Invoice to its most useful follow-up tasks.</p>")
        + section_card("2.5", "<p>Smart Draft Recovery release protecting unfinished Invoice input in the current browser.</p>")
        + section_card("2.6", "<p>Smart Progress release showing Invoice completion and remaining input in real time.</p>")
        + section_card("2.7", "<p>Smart Data Safety release with atomic saves, validated backups, recovery, and structure checks.</p>")
        + section_card("2.8", "<p>Final Polish release unifying visual rhythm, controls, feedback, loading, and accessibility.</p>")
        + section_card("3.0.0", "<p>First Public MVP release of Trade Paper AI.</p>")
        + section_card("3.1.0", "<p>Export & Share release with safer filenames and consistent PDF actions.</p>")
        + section_card("3.2.0", "<p>First User Experience release with onboarding, setup progress, and guided empty states.</p>")
        + section_card("3.2.1 RC1", "<p>Release Candidate quality review covering consistency, navigation, responsive behavior, and visible wording.</p>")
        + section_card("3.3.0", "<p>First User Success release with guided success messages, next actions, and workflow celebration.</p>")
        + section_card("3.4.0", "<p>Login MVP with responsive registration, credential validation, and secure password storage.</p>")
        + section_card("3.6.3 · Easier First Steps", "<p>File-first quick start, preserved sample links and clear onboarding continuation.</p>")
        + section_card("3.6.4 · Paired PDF Review", "<p>Private frozen Invoice/Packing List PDF pairs with final review, stale-source checks and a single delivery attempt per review.</p>")
        + section_card("3.6.2 · Email Attachment Review", "<p>Account-scoped document references, Invoice/Packing comparison warnings, and actual prepared-file metadata on delivery receipts.</p>")
        + section_card("3.6.1 · Guided File Workspace", "<p>Calmer review layout, fictional sample, explicit file replacement, draft preservation on import failure, and clearer saved-document actions.</p>")
        + section_card("3.6.0 · File Import", "<p>Reviewed Invoice drafts from spreadsheets, PDFs and printed photos, with local photo/PDF reading and no original-file retention.</p>")
        + section_card("3.5.1 · Public Release", "<p>Shorter bilingual public home, current plan guidance, and first-use help instead of beta recruitment. Historical release entries remain below.</p>")
        + section_card("3.5.0 · Founding Beta", "<p>Account-isolated business records, protected routes, safe login redirects, logout, password recovery, authenticated identity display, and the current shipment-centered document workflow.</p>")
    )
    return information_page("Version History", f"Current: Version {APP_VERSION} · {RELEASE_STAGE}", content)


def getting_started_navigation(lang):
    english_current = ' aria-current="page"' if lang != "ko" else ""
    korean_current = ' aria-current="page"' if lang == "ko" else ""
    return (
        '<nav aria-label="Language / 언어" class="tp-toolbar">'
        f'<a href="/getting-started" lang="en" hreflang="en"{english_current}>English</a>'
        f'<a href="/getting-started?lang=ko" lang="ko" hreflang="ko"{korean_current}>한국어</a></nav>'
    )


def _quick_start_page(lang="en"):
    ko = lang == "ko"
    choose = lambda en, kr: kr if ko else en
    target = f"%2Fimport-document%3Flang%3D{lang}"
    file_links = (f'<a class="tp-btn" href="/import-document?lang={lang}">{choose("Start from a file", "파일로 시작하기")}</a> '
                  f'<a class="tp-btn tp-btn-secondary" href="/register?next={target}&amp;lang={lang}">{choose("Create free account", "무료 계정 만들기")}</a>')
    file_card = section_card(choose("Have a file? Start here", "파일이 있다면 여기서 시작하세요"),
        '<p>' + choose("Bring an XLSX/CSV, PDF or clear printed document photo. Review the extracted Invoice draft, save, then continue to Packing List.",
        "엑셀·CSV·PDF·선명한 인쇄 서류 사진을 가져오세요. 읽어온 Invoice 초안을 확인하고 저장한 뒤 Packing List로 이어갑니다.") + '</p>' + file_links +
        '<p>' + choose("Account and sign-in required. If company setup appears, complete it and return to the importer. No file handy? Choose the fictional sample inside the importer.",
        "가입·로그인이 필요합니다. 회사 정보 설정이 나오면 완료한 뒤 파일 작성으로 이어가세요. 파일이 없으면 작성 화면의 ‘가상 예시로 먼저 보기’를 선택할 수 있습니다.") + '</p>' +
        '<details><summary>' + choose("File limits and review", "파일 범위와 확인할 내용") + '</summary><p>' +
        choose("Up to 8 MB, five PDF pages and 100 items. Complex layouts, handwriting and formula cells may need correction. Check names, quantities and prices against the original. Originals are not retained; saving a sample uses one document from your allowance.",
        "최대 8MB·PDF 5쪽·품목 100개입니다. 복잡한 서식·손글씨·수식 셀은 수정이 필요할 수 있습니다. 이름·수량·단가를 원본과 대조하세요. 원본 파일은 보관하지 않으며, 가상 예시도 저장하면 문서 한도를 사용합니다.") + '</p></details>')
    sample_card = section_card(choose("Want to see the result first?", "결과물부터 보고 싶다면"),
        '<div id="sample-documents"><p>' + choose("Change fictional quantities and packing values, then open two separate PDFs. No account needed and nothing is saved to a workspace.",
        "가상 수량과 포장 정보를 바꾸고 PDF 두 장을 확인하세요. 가입이 필요 없고 작업공간에 저장되지 않습니다.") + '</p>' +
        f'<a class="tp-btn" href="/try-sample?lang={lang}">{choose("Try without signup", "가입 없이 예시 보기")}</a>' +
        '<details><summary>' + choose("Open ready-made sample PDFs", "완성된 샘플 PDF 열기") + '</summary><p><a href="/static/samples/commercial-invoice.pdf" target="_blank" rel="noopener">Invoice PDF</a> · <a href="/static/samples/packing-list.pdf" target="_blank" rel="noopener">Packing List PDF</a></p></details></div>')
    manual_card = section_card(choose("Prefer entering details yourself?", "직접 입력하고 싶다면"),
        '<p>' + choose("Save company, buyer and product details for reuse. The guided demo walks you through one fictional shipment.",
        "회사·바이어·품목을 저장해 다음 작성에 다시 사용하세요. 안내형 데모에서 가상 거래 한 건을 순서대로 만들 수 있습니다.") + '</p>' +
        f'<a class="tp-btn tp-btn-secondary" href="/demo?lang={lang}">{choose("Open guided demo", "직접 입력 데모 열기")}</a>' +
        '<details><summary>' + choose("Your first document workflow", "직접 입력 순서 보기") + '</summary><ol><li>' +
        choose("Review company details; keep existing information.", "회사 정보를 확인하고 기존 정보는 유지하세요.") + '</li><li>' +
        choose("Save a buyer and product, then review and save an Invoice.", "바이어·품목을 저장하고 Invoice 내용을 검토해 저장하세요.") + '</li><li>' +
        choose("Continue to Packing List and check actual cartons and weights.", "Packing List로 이어가 실제 박스 수와 중량을 확인하세요.") + '</li><li>' +
        choose("Open both PDFs and check references, items and quantities.", "두 PDF를 열어 참조 번호·품목·수량을 대조하세요.") + '</li></ol><p>' +
        choose("Email sending is optional. If testing delivery, use your own address and review the attachment first.", "이메일 발송은 선택 사항입니다. 시험 발송 시 본인 주소를 사용하고 첨부파일부터 확인하세요.") + '</p></details>')
    closing = ('<p>' + choose("Free includes five saved documents per month. Each Invoice and Packing List counts separately. After changes, compare the saved Packing List with its Invoice and download fresh PDFs; previous downloads never update automatically.",
        "무료는 월 5문서이며 Invoice와 Packing List를 각각 한 문서로 셉니다. 수정 후에는 저장된 Packing List와 Invoice를 비교하고 PDF를 다시 받으세요. 이전 PDF는 자동으로 바뀌지 않습니다.") + '</p>' +
        f'<p><a href="/starter?lang={lang}">{choose("Plans and subscription terms", "요금·구독 조건")}</a> · <a href="/contact">{choose("Get first-use help", "첫 사용 도움받기")}</a></p>')
    return information_page(choose("Getting Started with Trade Paper AI", "Trade Paper AI 시작하기"),
        choose("Choose the easiest way to create your first documents", "내게 편한 방법으로 첫 문서를 만드세요"),
        file_card + sample_card + manual_card + closing, lang=lang, navigation=getting_started_navigation(lang))


def korean_getting_started_page():
    return _quick_start_page("ko")


@router.get("/getting-started")
def getting_started_page(lang: str = "en"):
    return _quick_start_page("ko" if lang == "ko" else "en")


@router.get("/demo")
def demo_page():
    content = (
        section_card(
            "Demo Preview",
            "<p><b>Temporary values — nothing is saved until you press Save.</b></p>"
            "<p>Follow steps 1–6 to create, review, and email your documents. Shipment setup is optional. Each form uses your account, and saved sample documents count toward your plan limit.</p>",
        )
        + section_card(
            "Step 1 · Company",
            '<p><b>Current step:</b> Confirm how company details flow into later documents.</p><p>If company details are already saved, keep them and continue to Buyer. Otherwise, review the temporary <b>Busan Comfort Trading</b> prefill and press Save.</p><a class="tp-btn" href="/company?demo=1">Start with Company</a>',
        )
        + section_card(
            "Step 2 · Buyer",
            '<p><b>What this shows:</b> Reusable buyer details reduce repeated party entry.</p><p>Review <b>Sakura Retail Co.</b> and save it when ready.</p><a class="tp-btn" href="/buyer-form?demo=1">Continue to Buyer</a>',
        )
        + section_card(
            "Step 3 · Product",
            '<p><b>What this shows:</b> Product, HS Code, origin, and unit price can be reused.</p><p>Review the <b>Notebook Computer</b> prefill and save it.</p><a class="tp-btn" href="/product-form?demo=1">Continue to Product</a>',
        )
        + section_card(
            "Step 4 · Invoice",
            '<p><b>What this shows:</b> The saved Company, Buyer, and Product are brought together with minimal typing.</p><p>Quantity starts at 1. Review the temporary values, then save the real Invoice.</p><a class="tp-btn" href="/invoice?demo=1">Create Demo Invoice</a>',
        )
        + section_card(
            "Step 5 · Packing",
            '<p><b>What this shows:</b> Snapshot data carries forward through the real Invoice → Packing workflow.</p><p>Open the Invoice List and choose <b>Create Packing</b> for the Invoice you just saved. No fake Packing data is created.</p><a class="tp-btn" href="/invoice-list">Open Invoice List</a>',
        )
        + section_card(
            "Step 6 · Review PDFs and Send Email",
            '<p>Open your saved Invoice and Packing List in their lists and choose <b>Download PDF</b>. Check the company, buyer, items, quantities, and packing details.</p><p>Then choose <b>Send Email</b> for each document. For this sample test, replace any prefilled recipient with your own email address, review the subject, message, and attachment, then press <b>Send Email</b>. Check your inbox for the PDF.</p><a class="tp-btn" href="/invoice-list">Open Invoice List</a><a class="tp-btn tp-btn-secondary" href="/packing-list">Open Packing List</a>',
        )
        + section_card(
            "Optional · Shipment Hub",
            '<p><b>What this shows:</b> Create a Shipment using the saved Invoice and Packing references, then use Shipment Detail as the hub for the remaining trade documents.</p><a class="tp-btn" href="/shipment-form">Create Shipment</a><a class="tp-btn tp-btn-secondary" href="/shipment-list">Open Shipment List</a>',
        )
    )
    return information_page("Trade Paper AI Demo", "Create, review, and email your first sample documents", content)


@router.get("/contact")
def contact_page():
    email = contact_email()
    website = contact_url()
    channels = [
        f'<p><b>Phone</b><br><a href="tel:{html_escape(BUSINESS_PHONE, attribute=True)}">{html_escape(BUSINESS_PHONE)}</a></p>'
    ]
    if email:
        channels.append(f'<p><b>Email</b><br><a href="mailto:{html_escape(email, attribute=True)}">{html_escape(email)}</a></p>')
    if website:
        channels.append(f'<p><b>Website</b><br><a href="{html_escape(website, attribute=True)}">{html_escape(website)}</a></p>')
    if not email and not website:
        channels.append("<p>Email and website contact channels are not configured for this deployment. Please use the business phone number above.</p>")
    content = section_card("Contact", "".join(channels))
    return information_page("Contact Trade Paper AI", "Questions and product inquiries", content)


@router.get("/privacy")
def privacy_page():
    content = (
        section_card(
            "Overview",
            "<p><b>Effective date: August 10, 2026</b></p>"
            "<p>This Privacy Policy explains how Trade Paper AI collects, uses, stores, and protects information when you use the service.</p>",
        )
        + section_card(
            "Information We Collect",
            "<ul>"
            "<li><b>Account information:</b> company name, login email address, and password credentials.</li>"
            "<li><b>Business information:</b> company contact details and the buyer, customer, and product records you enter.</li>"
            "<li><b>Export document information:</b> commercial, shipping, customs, certificate, cargo, party, and reference information used to create documents and PDFs.</li>"
            "<li><b>Security information:</b> signed session data, password reset status, reset-token hashes, request times, and limited IP-based rate-limit information.</li>"
            "</ul>",
        )
        + section_card(
            "File Imports",
            "<p>For document import, PDFs and photos are read on your device and the extracted text is sent to this service to prepare a draft. Spreadsheets are sent to this service for parsing. Original upload files and extracted source text are not retained after processing. Only the document fields you choose to save become account records. This import feature does not send your files to an external AI/OCR service or send email.</p>",
        )
        + section_card(
            "How We Use Information",
            "<ul>"
            "<li>Provide and protect your account.</li>"
            "<li>Save company, buyer, product, and export document records.</li>"
            "<li>Create, edit, search, display, and export documents as PDFs.</li>"
            "<li>Preserve document snapshots so previously saved document values remain consistent.</li>"
            "<li>Process password reset requests and, when configured, send service emails.</li>"
            "<li>Prevent abuse, diagnose failures, and maintain service security.</li>"
            "</ul>",
        )
        + section_card(
            "Passwords, Sessions, and Email",
            "<p>Passwords are protected using one-way password hashing. Compatible older credentials are upgraded after a successful sign-in. We do not display stored password values.</p>"
            "<p>Authentication uses a signed session cookie with a limited lifetime. Production cookies are configured with HttpOnly, SameSite, and Secure protections.</p>"
            "<p>Your login email is used for account access and password recovery. If email delivery is enabled, the configured email delivery provider receives the recipient address and reset message needed to send the email. Password reset tokens are stored by Trade Paper AI only as hashes and expire after 30 minutes.</p>",
        )
        + section_card(
            "Payment Information",
            "<p>Online payment processing is not active yet. If it is activated, payment method credentials such as full card details or bank authentication information will be handled by the payment provider and will not be entered into or stored by the Trade Paper AI application.</p>"
            "<p>Trade Paper AI may retain limited billing records needed to provide and support a subscription, such as an order reference, plan, price, currency, billing cycle, payment status, provider transaction reference, and relevant timestamps.</p>",
        )
        + section_card(
            "Export Documents and Snapshots",
            "<p>Export documents may contain names, addresses, contact details, cargo information, prices, weights, routes, and other trade information supplied by you. Snapshot values are stored with documents to preserve their saved state and PDF consistency even if related source records later change.</p>"
            "<p>You are responsible for having authority to enter information about buyers, consignees, contacts, and other parties.</p>",
        )
        + section_card(
            "Storage and Retention",
            "<p>Account and business records are retained while the account is active or while needed to provide the service. The service creates limited backup copies when stored JSON data changes, so a prior version may remain in a backup until it is replaced through normal operation.</p>"
            "<p>You can delete supported business records within the service when they are not required by related documents. Account deletion is handled by request. Some information may be retained when reasonably necessary for security, dispute resolution, or legal obligations.</p>",
        )
        + section_card(
            "Security",
            "<p>Trade Paper AI uses account isolation, password hashing, signed sessions, reset-token hashing, rate limits, validation, and atomic storage safeguards. No system is completely secure, and you should use a unique password and protect access to your email account and device.</p>",
        )
        + section_card(
            "Your Choices and Contact",
            "<p>You may review and update supported information in the service. To request access assistance, correction, or deletion of your account and associated data, use the <a href=\"/contact\">Contact page</a>. We may need to verify that the request comes from the account owner before acting on it.</p>",
        )
    )
    return information_page("Privacy Policy", "Trade Paper AI · Effective August 10, 2026 · Updated October 25, 2026", content)


@router.get("/terms")
def terms_page():
    content = (
        section_card(
            "Agreement and Service Notice",
            "<p><b>Effective date: August 10, 2026</b></p>"
            "<p>These Terms of Service govern your use of the Trade Paper AI service. By creating an account or using the service, you agree to these Terms.</p>"
            "<p>Features, availability, document layouts, and operating limits may change as the service is improved.</p>",
        )
        + section_card(
            "Service Description",
            "<p>Trade Paper AI provides account-based tools for maintaining company, buyer, customer, and product information and for creating commercial, shipping, customs, and certificate documents. The service can preserve document snapshots, generate PDFs, provide search and workflow navigation, and support password recovery and service email.</p>"
            "<p>The service does not submit documents to customs authorities, carriers, banks, insurers, or other third parties. A generated document is not confirmation that any authority or recipient has accepted it.</p>",
        )
        + section_card(
            "Accounts",
            "<ul>"
            "<li>You must provide accurate registration information and keep your login email current.</li>"
            "<li>You are responsible for protecting your password, email account, session, and device.</li>"
            "<li>You must notify us through the Contact page if you believe your account has been accessed without permission.</li>"
            "<li>You may not access or attempt to access another customer&apos;s account or records.</li>"
            "</ul>",
        )
        + section_card(
            "Your Data and Documents",
            "<p>You retain responsibility for the business information and document content you enter. You grant Trade Paper AI permission to process and store that information only as needed to operate, secure, and support the service.</p>"
            "<p>You must have the right to use personal and business information entered about buyers, consignees, contacts, and other parties. You are responsible for keeping your own copies of important final documents.</p>"
            "<p>Snapshots are designed to preserve saved document values. Changes to master or upstream records may not update an existing snapshot or previously generated PDF.</p>",
        )
        + section_card(
            "Your Responsibilities",
            "<p>You are responsible for reviewing all names, addresses, classifications, quantities, prices, weights, origins, destinations, references, and other document details before use. Trade Paper AI does not provide legal, tax, customs, insurance, banking, or compliance advice.</p>"
            "<p>You must determine whether each document is suitable for your transaction and whether additional forms, approvals, signatures, filings, or professional review are required.</p>",
        )
        + section_card(
            "Prohibited Use",
            "<p>You may not use the service to break the law, submit false or misleading trade information, infringe another person&apos;s rights, distribute malware, interfere with security or availability, probe for vulnerabilities, bypass access controls or rate limits, or access data without authorization.</p>",
        )
        + section_card(
            "Paid Plans, Cancellation, and Refunds",
            "<p>The Starter plan is listed at ₩29,000 per month. Online payment processing is not active yet, so applying for or viewing Starter does not collect payment or activate a paid plan.</p>"
            "<p>If online monthly billing is activated, the checkout will disclose the payment and renewal terms before purchase. Subscription cancellation stops future billing according to the timing shown at cancellation; any withdrawal, cancellation, or refund rights required by applicable law remain available.</p>"
            "<p>See the <a href=\"/refund-policy\">Cancellation and Refund Policy</a> for the request process. Billing questions and onboarding arrangements are handled through the <a href=\"/contact\">Contact page</a>.</p>",
        )
        + section_card(
            "Availability and Changes",
            "<p>We may maintain, update, limit, suspend, or discontinue beta features when reasonably necessary for security, reliability, legal compliance, or product development. We will try to provide reasonable notice of material changes when practical, but uninterrupted availability is not guaranteed.</p>",
        )
        + section_card(
            "Disclaimers and Limitation of Liability",
            "<p>The service is provided on an &quot;as available&quot; basis to the extent permitted by law. We do not guarantee that generated documents are complete, error-free, legally sufficient, or accepted by a third party.</p>"
            "<p>To the extent permitted by law, Trade Paper AI is not liable for indirect, incidental, special, consequential, or business-interruption losses arising from use of the service, reliance on generated documents, loss of access, or third-party rejection. Nothing in these Terms excludes liability that cannot legally be excluded.</p>",
        )
        + section_card(
            "Termination and Contact",
            "<p>You may stop using the service at any time and may request account deletion through the <a href=\"/contact\">Contact page</a>. We may restrict access for serious or repeated violations of these Terms or when necessary to protect the service and its users.</p>"
            "<p>Questions about these Terms should be submitted through the Contact page.</p>",
        )
    )
    return information_page("Terms of Service", "Trade Paper AI · Effective August 10, 2026 · Updated October 25, 2026", content)


# Legal and Toss Payments review is required before online payment activation.
@router.get("/refund-policy")
def refund_policy_page():
    content = (
        section_card(
            "Current Payment Status",
            "<p><b>Effective date: August 15, 2026</b></p>"
            "<p>Online payment processing is not active yet. Viewing the Starter page, requesting first-use help, or requesting purchase information does not collect payment or create a paid subscription.</p>",
        )
        + section_card(
            "Starter Monthly Subscription",
            "<p>When online payment becomes available, Starter is intended to be offered as a monthly subscription at ₩29,000. The checkout will show the charge, billing cycle, renewal terms, and the effect of cancellation before you confirm a purchase.</p>",
        )
        + section_card(
            "Cancel a Subscription",
            "<p>You may request cancellation through the <a href=\"/contact\">Contact page</a>. Include the email address used for your account and an order or payment reference if one is available. Never send your password or full payment-card details.</p>"
            "<p>Cancellation will stop future charges according to the cancellation timing disclosed at checkout or when the request is confirmed. We will explain any remaining access period when processing the request.</p>",
        )
        + section_card(
            "Withdrawal and Refund Requests",
            "<p>You may request a payment cancellation, withdrawal, or refund through the Contact page. We will review the request promptly based on the purchase terms, service usage, the reason for the request, and applicable law.</p>"
            "<p>Duplicate or incorrect charges and a paid service that cannot be provided for reasons attributable to Trade Paper AI will be handled in accordance with applicable law. This policy does not impose a blanket exclusion of refunds.</p>",
        )
        + section_card(
            "Refund Method and Timing",
            "<p>Approved refunds will normally be returned through the original payment method. The time for the credit to appear may depend on the payment provider, card issuer, or bank. Any mandatory refund method or deadline under applicable law takes priority.</p>",
        )
        + section_card(
            "Legal Rights and Contact",
            "<p>Nothing in this policy limits rights that cannot be excluded under applicable consumer-protection law. If this policy conflicts with mandatory law, the law prevails.</p>"
            "<p>For cancellation, refund, or billing questions, use the <a href=\"/contact\">Contact page</a>. You can also review the <a href=\"/terms\">Terms of Service</a> and <a href=\"/privacy\">Privacy Policy</a>.</p>",
        )
    )
    return information_page("Cancellation and Refund Policy", "Trade Paper AI Starter Monthly Subscription", content)
