from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from app.release import (
    APP_NAME, APP_VERSION, BUILD_NAME, LAST_UPDATED, RELEASE_STAGE, RELEASE_TYPE,
    contact_email, contact_url,
)
from app.ui import button, html_escape, page_shell, section_card, toolbar


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
        + section_card("What's New", "<ul><li>Account-owned business records and protected application routes</li><li>Password recovery with expiring, hashed reset tokens</li><li>Safe post-login return and POST-only Logout</li><li>Stored XSS protection for customer master data screens</li></ul>")
        + section_card("Current Capabilities", "<p>Company, customer, buyer, and product master data; commercial, shipping, customs, and certificate documents; Shipment Hub workflow guidance; Dashboard; Global Search; validation; protected deletion; account isolation; password recovery; safe JSON storage; snapshots; and PDF generation.</p>")
        + section_card("Performance", "<p>Request-scoped JSON reuse reduces repeated Dashboard and Shipment Detail reads without stale global caching.</p>")
        + section_card("Workflow", "<p>Commercial Invoice → Packing List → Shipping Instruction → Booking Confirmation → Bill of Lading → Customs Declaration.</p>")
        + section_card("Known Limitations", "<ul><li>Founding Beta availability and features may change during testing.</li><li>JSON storage requires a single application worker.</li><li>Role-based collaboration and third-party integrations are not included.</li></ul>")
        + section_card("Deployment Checklist", "<ul><li>Serve the application through HTTPS.</li><li>Configure and verify storage backups.</li><li>Configure SMTP when password reset email delivery is required.</li><li>Publish a customer Contact email or URL.</li><li>Use an existing writable DATA DIR.</li><li>Set a stable Session Secret of at least 32 characters.</li></ul>")
    )
    return information_page(f"Version {APP_VERSION} Release Notes", RELEASE_STAGE, content)


@router.get("/about")
@router.get("/About", include_in_schema=False)
def about_page():
    content = (
        section_card("Application", f"<p><b>Application Name</b><br>{APP_NAME}</p><p><b>Version</b><br>{APP_VERSION}</p><p><b>Build</b><br>{BUILD_NAME}</p><p><b>Release Date</b><br>{LAST_UPDATED}</p>")
        + section_card("Features", "<ul><li>Shipment-centered document workflow</li><li>Dashboard, Global Search, and workflow guidance</li><li>Safe validation, storage, deletion, and PDF output</li><li>Comfort-focused browser productivity tools</li></ul>")
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
        + section_card("3.5.0 · Founding Beta", "<p>Account-isolated business records, protected routes, safe login redirects, logout, password recovery, authenticated identity display, and the current shipment-centered document workflow.</p>")
    )
    return information_page("Version History", f"Current: Version {APP_VERSION} · {RELEASE_STAGE}", content)


@router.get("/demo")
def demo_page():
    content = (
        section_card(
            "Demo Preview",
            "<p><b>Temporary values — nothing is saved until you press Save.</b></p>"
            "<p>Follow the six steps in order. Each form uses the real Trade Paper AI workflow and keeps you in control of every save.</p>",
        )
        + section_card(
            "Step 1 · Company",
            '<p><b>Current step:</b> Confirm how company details flow into later documents.</p><p>Open the temporary <b>Busan Comfort Trading</b> prefill, review it, then press Save.</p><a class="tp-btn" href="/company?demo=1">Start with Company</a>',
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
            "Step 6 · Shipment Hub",
            '<p><b>What this shows:</b> Create a Shipment using the saved Invoice and Packing references, then use Shipment Detail as the hub for the remaining trade documents.</p><a class="tp-btn" href="/shipment-form">Create Shipment</a><a class="tp-btn tp-btn-secondary" href="/shipment-list">Open Shipment List</a>',
        )
    )
    return information_page("Trade Paper AI Demo", "A 15-minute Comfort First workflow", content)


@router.get("/contact")
def contact_page():
    email = contact_email()
    website = contact_url()
    channels = []
    if email:
        channels.append(f'<p><b>Email</b><br><a href="mailto:{html_escape(email, attribute=True)}">{html_escape(email)}</a></p>')
    if website:
        channels.append(f'<p><b>Website</b><br><a href="{html_escape(website, attribute=True)}">{html_escape(website)}</a></p>')
    if not channels:
        channels.append("<p>Public contact details have not been configured for this deployment. Founding Beta participants should use their existing onboarding contact channel.</p>")
    content = section_card("Contact", "".join(channels))
    return information_page("Contact Trade Paper AI", "Questions and product inquiries", content)


@router.get("/privacy")
def privacy_page():
    content = (
        section_card(
            "Overview",
            "<p><b>Effective date: August 10, 2026</b></p>"
            "<p>This Privacy Policy explains how Trade Paper AI collects, uses, stores, and protects information when you use the beta service.</p>",
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
            "Export Documents and Snapshots",
            "<p>Export documents may contain names, addresses, contact details, cargo information, prices, weights, routes, and other trade information supplied by you. Snapshot values are stored with documents to preserve their saved state and PDF consistency even if related source records later change.</p>"
            "<p>You are responsible for having authority to enter information about buyers, consignees, contacts, and other parties.</p>",
        )
        + section_card(
            "Storage and Retention",
            "<p>Account and business records are retained while the account is active or while needed to provide the beta service. The service creates limited backup copies when stored JSON data changes, so a prior version may remain in a backup until it is replaced through normal operation.</p>"
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
    return information_page("Privacy Policy", "Trade Paper AI Beta · Effective August 10, 2026", content)


@router.get("/terms")
def terms_page():
    content = (
        section_card(
            "Agreement and Beta Notice",
            "<p><b>Effective date: August 10, 2026</b></p>"
            "<p>These Terms of Service govern your use of the Trade Paper AI beta service. By creating an account or using the service, you agree to these Terms.</p>"
            "<p>The service is in beta. Features, availability, document layouts, and operating limits may change as the service is tested and improved.</p>",
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
            "<p>Final online cancellation and refund terms must be published before checkout activation. Until then, billing questions and Founding Beta arrangements are handled through the <a href=\"/contact\">Contact page</a>.</p>",
        )
        + section_card(
            "Availability and Changes",
            "<p>We may maintain, update, limit, suspend, or discontinue beta features when reasonably necessary for security, reliability, legal compliance, or product development. We will try to provide reasonable notice of material changes when practical, but uninterrupted availability is not guaranteed.</p>",
        )
        + section_card(
            "Disclaimers and Limitation of Liability",
            "<p>The beta service is provided on an &quot;as available&quot; basis to the extent permitted by law. We do not guarantee that generated documents are complete, error-free, legally sufficient, or accepted by a third party.</p>"
            "<p>To the extent permitted by law, Trade Paper AI is not liable for indirect, incidental, special, consequential, or business-interruption losses arising from use of the service, reliance on generated documents, loss of access, or third-party rejection. Nothing in these Terms excludes liability that cannot legally be excluded.</p>",
        )
        + section_card(
            "Termination and Contact",
            "<p>You may stop using the service at any time and may request account deletion through the <a href=\"/contact\">Contact page</a>. We may restrict access for serious or repeated violations of these Terms or when necessary to protect the service and its users.</p>"
            "<p>Questions about these Terms should be submitted through the Contact page.</p>",
        )
    )
    return information_page("Terms of Service", "Trade Paper AI Beta · Effective August 10, 2026", content)
