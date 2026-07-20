from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from app.release import APP_NAME, APP_VERSION, BUILD_NAME, LAST_UPDATED, RELEASE_STAGE, RELEASE_TYPE
from app.ui import button, page_shell, section_card, toolbar


router = APIRouter()


def release_navigation():
    return toolbar(
        button("Dashboard", "/", "secondary"),
        button("About", "/about", "secondary"),
        button("Release Notes", "/release-notes", "secondary"),
        button("Version History", "/version-history", "secondary"),
        button("Try Demo", "/demo", "secondary"),
        button("Contact", "/contact", "secondary"),
    )


def information_page(title, subtitle, content):
    styles = """
*{box-sizing:border-box}body{margin:0;background:#F3F4F6;color:#111827;font-family:Arial,sans-serif}.tp-page{width:min(920px,calc(100% - 32px));margin:40px auto}.card{background:#fff;border:1px solid #E5E7EB;border-radius:16px;padding:24px;margin:18px 0}.card h2{margin-top:0}.card p,.card li{color:#475569;line-height:1.7}.tp-toolbar,.tp-toolbar-actions{display:flex;gap:10px;flex-wrap:wrap}.tp-btn{display:inline-flex;align-items:center;justify-content:center;min-height:42px;padding:10px 16px;border-radius:12px;background:#111827;color:#fff;text-decoration:none;font-weight:700}.tp-btn-secondary{background:#E5E7EB;color:#111827}.tp-release-footer{width:min(920px,calc(100% - 32px));margin:34px auto 20px;padding:20px 0;border-top:1px solid #D1D5DB;color:#6B7280;text-align:center;font-size:13px;line-height:1.7}.tp-release-footer strong{display:block;color:#374151}.tp-release-footer-nav{display:flex;justify-content:center;gap:12px;flex-wrap:wrap;margin-top:9px}.tp-release-footer-nav a{color:#475569}.version{display:inline-block;padding:8px 11px;border-radius:999px;background:#DCFCE7;color:#166534;font-weight:700}@media(max-width:640px){.tp-page{margin:20px auto}.tp-btn{width:100%}}
"""
    return HTMLResponse(page_shell(title, content, subtitle=subtitle, navigation=release_navigation(), styles=styles))


@router.get("/release-notes")
def release_notes_page():
    content = (
        f'<p class="version">Version {APP_VERSION} · {RELEASE_STAGE}</p>'
        + section_card("What's New", "<ul><li>Friendly action-specific success messages</li><li>Recommended next steps after first-time setup actions</li><li>First workflow completion celebration and Quick Actions</li><li>Consistent accessible Delete confirmation</li></ul>")
        + section_card("Current Capabilities", "<p>Master data, commercial and shipping documents, Shipment Hub workflow guidance, Dashboard, Global Search, validation, protected deletion, safe JSON storage, and PDF generation.</p>")
        + section_card("Performance", "<p>Request-scoped JSON reuse reduces repeated Dashboard and Shipment Detail reads without stale global caching.</p>")
        + section_card("Workflow", "<p>Commercial Invoice → Packing List → Shipping Instruction → Booking Confirmation → Bill of Lading → Customs Declaration.</p>")
        + section_card("Known Limitations", "<ul><li>Single-process JSON storage is intended for the first public MVP.</li><li>Authentication, multi-user collaboration, and cloud synchronization are not included.</li></ul>")
        + section_card("Future Plans", "<p>Authentication, collaboration, integrations, cloud synchronization, and expanded compliance coverage.</p>")
    )
    return information_page("Version 3.3 Release Notes", "First User Success", content)


@router.get("/about")
@router.get("/About", include_in_schema=False)
def about_page():
    content = (
        section_card("Application", f"<p><b>Application Name</b><br>{APP_NAME}</p><p><b>Version</b><br>{APP_VERSION}</p><p><b>Build</b><br>{BUILD_NAME}</p><p><b>Release Date</b><br>{LAST_UPDATED}</p>")
        + section_card("Features", "<ul><li>Shipment-centered document workflow</li><li>Dashboard, Global Search, and workflow guidance</li><li>Safe validation, storage, deletion, and PDF output</li><li>Comfort-focused browser productivity tools</li></ul>")
        + section_card("Release Notes", '<p><a href="/release-notes">Read the Version 3.3 Release Notes</a></p>')
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
        + section_card("Future Versions", "<p>Planned areas include authentication, collaboration, integrations, and expanded compliance documents.</p>")
    )
    return information_page("Version History", "Trade Paper AI release journey", content)


@router.get("/demo")
def demo_page():
    content = (
        section_card(
            "Sample Data",
            "<ul><li><b>Demo Company</b> — Trade Paper Demo Co.</li><li><b>Demo Buyer</b> — Demo Buyer Ltd.</li><li><b>Demo Product</b> — Sample Export Product</li><li><b>Demo Shipment</b> — Demo Shipment to Singapore</li></ul>",
        )
        + section_card(
            "Try It",
            '<div class="tp-toolbar-actions"><a class="tp-btn" href="/shipment-form?demo=1">Create Demo Shipment</a><a class="tp-btn tp-btn-secondary" href="/company?demo=1">Demo Company</a><a class="tp-btn tp-btn-secondary" href="/buyer-form?demo=1">Demo Buyer</a><a class="tp-btn tp-btn-secondary" href="/product-form?demo=1">Demo Product</a></div><p>Demo values are temporary form prefills. Nothing is saved until you choose Save.</p>',
        )
    )
    return information_page("Try Trade Paper AI", "Explore Version 3.3 without changing stored data", content)


@router.get("/contact")
def contact_page():
    content = section_card(
        "Contact",
        "<p><b>Email</b><br>hello@tradepaper.ai (placeholder)</p><p><b>Website</b><br>www.tradepaper.ai (placeholder)</p>",
    )
    return information_page("Contact Trade Paper AI", "Questions and product inquiries", content)


@router.get("/privacy")
def privacy_page():
    content = section_card("Privacy Policy", "<p>This Version 1.0 placeholder will be replaced with the approved production privacy policy.</p>")
    return information_page("Privacy Policy", "Legal placeholder", content)


@router.get("/terms")
def terms_page():
    content = section_card("Terms of Service", "<p>This Version 1.0 placeholder will be replaced with the approved production terms of service.</p>")
    return information_page("Terms of Service", "Legal placeholder", content)
