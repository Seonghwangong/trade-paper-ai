from app.landing import landing_page
from app.release import APP_VERSION, RELEASE_STAGE
from pathlib import Path
from PIL import Image
import pytest
from playwright.sync_api import sync_playwright

from tests.test_auth_browser import auth_server


def test_landing_matches_required_first_document_workflow_and_conversion_ctas():
    html = landing_page().body.decode()
    workflow = ("Company", "Buyer", "Product", "Invoice", "Packing", "SI", "Shipment", "Booking", "B/L", "CO", "Email", "Done")
    positions = [html.index(f"<li>{step}</li>") for step in workflow]

    assert positions == sorted(positions)
    assert 'aria-label="Company, Buyer, Product, Invoice, Packing, SI, Shipment, Booking, B/L, CO, Email, Done"' in html
    assert '<a class="primary" href="/register">Start Free</a>' in html
    assert '<a class="secondary" href="#demo">Watch 15-Second Demo</a>' in html
    assert '<a class="secondary" href="/founding-beta">Join Founding Beta</a>' in html
    assert '<a class="nav-link" href="/login">Sign in</a>' in html


def test_landing_hero_communicates_comfort_first_and_current_trust_values():
    html = landing_page().body.decode()
    assert "Founding Beta · Export operations workspace" in html
    assert "Create Export Documents<br>in Minutes, Not Hours." in html
    assert "Create, manage and send Commercial Invoice, Packing List," in html
    assert "Shipping Instruction, Bill of Lading and more" in html
    assert "in one connected workflow." in html
    assert "Enter your information once. Keep every export document connected." in html
    assert 'aria-label="Product trust highlights"' in html
    for trust_value in (
        "✓ Unicode PDF",
        "✓ Stable Snapshots",
        "✓ Account Isolation",
        "✓ Guided Workflow",
        "✓ Founding Beta",
    ):
        assert f"<span>{trust_value}</span>" in html


def test_landing_describes_only_implemented_conversion_values():
    html = landing_page().body.decode()
    for text in (
        "Why Trade Paper AI",
        "No duplicate work",
        "Connected documents",
        "Built for exporters",
        "Founding Beta",
        "Frequently Asked Questions",
        "Export Wizard",
        "Customer Logos",
        "Coming Soon",
        "There are no customer testimonials yet.",
        "Subscription plans",
        "Account Isolation",
        "Audit Log",
        "Backup",
        "Email Security",
        "Ready to simplify export documentation?",
        "Contact Trade Paper AI",
        "Nothing is saved until you choose Save.",
    ):
        assert text in html
    for question in (
        "What documents are supported?",
        "Are previous PDFs affected?",
        "Does Trade Paper AI support Unicode?",
        "Is my company data isolated?",
        "What happens in Demo Mode?",
        "Can I archive a document?",
        "Is payment active?",
    ):
        assert f"<summary>{question}</summary>" in html


def test_landing_footer_exposes_beta_and_support_navigation():
    html = landing_page().body.decode()
    assert f"Version {APP_VERSION} · {RELEASE_STAGE} · Built for Exporters." in html
    for label, path in (
        ("Apply for Founding Beta", "/founding-beta"),
        ("Send Feedback", "/feedback"),
        ("Demo", "/demo"),
        ("About", "/about"),
        ("Release Notes", "/release-notes"),
        ("Version History", "/version-history"),
        ("Contact", "/contact"),
        ("Privacy", "/privacy"),
        ("Terms", "/terms"),
        ("Sign In", "/login"),
    ):
        assert f'<a href="{path}">{label}</a>' in html


def test_landing_footer_exposes_exact_business_information():
    html = landing_page().body.decode()
    assert 'aria-label="사업자 정보"' in html
    for label, value in (
        ("상호", "지엘피(GLP)"),
        ("대표자", "공성환"),
        ("사업자등록번호", "357-45-01167"),
        ("사업장 주소", "경상남도 창원시 의창구 지귀로120번길 19, 2층 203호(봉곡동)"),
        ("전화번호", "010-7166-7770"),
    ):
        assert f"<dt>{label}</dt><dd>{value}</dd>" in html
    assert html.count('href="/feedback"') == 1


@pytest.mark.parametrize("browser_name", ["chromium", "webkit"])
@pytest.mark.parametrize("viewport", [{"width": 1280, "height": 900}, {"width": 390, "height": 844}])
def test_landing_business_footer_has_no_horizontal_overflow(auth_server, browser_name, viewport):
    base_url, _ = auth_server
    with sync_playwright() as playwright:
        browser_type = getattr(playwright, browser_name)
        if not Path(browser_type.executable_path).exists():
            pytest.fail(f"{browser_name} browser binary is not installed")
        browser = browser_type.launch(headless=True)
        page = browser.new_page(viewport=viewport)
        try:
            assert page.goto(f"{base_url}/").ok
            business = page.locator(".business-info")
            assert business.is_visible()
            assert business.get_by_text("경상남도 창원시 의창구 지귀로120번길 19, 2층 203호(봉곡동)", exact=True).is_visible()
            assert page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")
        finally:
            browser.close()


def test_landing_demo_gif_is_exactly_fifteen_seconds():
    path = Path(__file__).parents[1] / "app" / "static" / "trade-paper-demo-15s.gif"
    image = Image.open(path)
    duration = 0
    for frame in range(image.n_frames):
        image.seek(frame)
        duration += image.info["duration"]
    assert duration == 15_000
    assert 'src="/static/trade-paper-demo-15s.gif"' in landing_page().body.decode()
