from app.landing import landing_page
from app.release import APP_VERSION, RELEASE_STAGE
from pathlib import Path
from PIL import Image
import json
import re
import pytest
from playwright.sync_api import sync_playwright
from tests.test_auth_browser import auth_server
from app.subscription import PLANS, plan_price_label


def test_home_is_concise_and_keeps_public_entry_points():
    html = landing_page().body.decode()
    assert html.count('<section ') == 4
    for path in ('/try-sample', '/register', '/starter', '/getting-started', '/login', '/founding-beta'):
        assert f'href="{path}"' in html
    for obsolete in ('Founding Beta', 'Customer Logos', 'Coming Soon', 'in Minutes, Not Hours', 'First 10 companies'):
        assert obsolete not in html
    assert 'First-use help' in html
    assert 'does not update automatically' in html
    assert 'previous downloads stay unchanged' in html
    assert 'customs filing or certification' in html
    assert '__' not in html


def test_korean_home_keeps_language_on_first_use_links():
    html = landing_page('ko').body.decode()
    assert '<html lang="ko">' in html
    assert '두 서류로 이어 쓰세요.' in html
    for path in ('/try-sample', '/register', '/starter', '/getting-started', '/login', '/founding-beta'):
        assert f'href="{path}?lang=ko"' in html
    assert '월 29,000원' in html
    assert '월 5문서' in html
    assert '베타' not in html
    assert '<html lang="en">' in landing_page('<script>').body.decode()


def test_home_plan_and_faq_schema_match_visible_content():
    html = landing_page().body.decode()
    assert plan_price_label('Starter') in html
    assert PLANS['Starter']['price'] == 29000
    assert '5 documents per month' in html
    assert 'Each Invoice and Packing List counts as one document' in html
    payloads = [json.loads(v) for v in re.findall(r'<script type="application/ld\+json">(.*?)</script>', html)]
    application = next(item for item in payloads if item.get('@type') == 'SoftwareApplication')
    starter = next(item for item in application['offers'] if item['name'] == 'Starter')
    assert starter['price'] == '29000'
    assert starter['priceCurrency'] == 'KRW'
    faq = next(item for item in payloads if item.get('@type') == 'FAQPage')
    assert len(faq['mainEntity']) == 4
    for entry in faq['mainEntity']:
        assert f"<summary>{entry['name']}</summary>" in html
        assert entry['acceptedAnswer']['text'] in html
    for label in ('google-site-verification', 'naver-site-verification', 'rel="canonical"', 'prefers-reduced-motion'):
        assert label in html


def test_home_footer_retains_contact_and_policy_links():
    html = landing_page().body.decode()
    assert f'Version {APP_VERSION} · {RELEASE_STAGE}' not in html
    for path in ('/about', '/contact', '/privacy', '/terms', '/refund-policy'):
        assert f'href="{path}"' in html


def test_landing_footer_exposes_exact_business_information():
    html = landing_page().body.decode()
    assert 'aria-label="사업자 정보"' in html
    for label, value in (
        ("상호", "지엘피(GLP)"),
        ("대표자", "공성환"),
        ("사업자등록번호", "357-45-01167"),
        ("사업장 주소", "경상남도 창원시 의창구 지귀로120번길 19, 2층 203호(봉곡동)"),
        ("전화번호", "050-6466-7770"),
    ):
        assert f"<dt>{label}</dt><dd>{value}</dd>" in html
    assert html.count('href="/feedback"') == 1


@pytest.mark.browser
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
