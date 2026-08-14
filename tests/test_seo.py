import json
from pathlib import Path
from xml.etree import ElementTree

import pytest
from playwright.sync_api import sync_playwright
from starlette.requests import Request

from app import auth, landing, main
from tests.test_auth_browser import auth_server


def _request(path):
    return Request({"type": "http", "method": "GET", "path": path, "headers": [], "scheme": "https", "server": ("trade.example.com", 443), "query_string": b""})


def test_landing_has_canonical_social_manifest_and_valid_structured_data(monkeypatch):
    monkeypatch.setenv("TRADE_PAPER_PUBLIC_BASE_URL", "https://trade.example.com")
    html = landing.landing_page().body.decode()
    assert '<link rel="canonical" href="https://trade.example.com/">' in html
    assert '<link rel="manifest" href="/static/site.webmanifest">' in html
    assert '<meta property="og:url" content="https://trade.example.com/">' in html
    assert '<meta property="og:image" content="https://trade.example.com/static/product-hunt/hero.png">' in html
    assert '<meta name="twitter:card" content="summary_large_image">' in html
    blocks = []
    marker = '<script type="application/ld+json">'
    for chunk in html.split(marker)[1:]:
        blocks.append(json.loads(chunk.split("</script>", 1)[0]))
    assert [block["@type"] for block in blocks] == ["Organization", "SoftwareApplication", "FAQPage"]
    assert blocks[0]["url"] == "https://trade.example.com/"
    assert blocks[1]["softwareVersion"] == "3.5.0"
    visible_questions = [question for question, _ in landing.FAQ_ENTRIES]
    assert [entry["name"] for entry in blocks[2]["mainEntity"]] == visible_questions


def test_robots_and_sitemap_use_the_validated_public_base_url(monkeypatch):
    monkeypatch.setenv("TRADE_PAPER_PUBLIC_BASE_URL", "https://trade.example.com")
    robots = main.robots_txt(_request("/robots.txt")).body.decode()
    assert "User-agent: *" in robots
    assert "Sitemap: https://trade.example.com/sitemap.xml" in robots
    sitemap = main.sitemap_xml(_request("/sitemap.xml")).body.decode()
    assert "__PUBLIC_BASE_URL__" not in sitemap
    root = ElementTree.fromstring(sitemap)
    locations = [element.text for element in root.findall("{http://www.sitemaps.org/schemas/sitemap/0.9}url/{http://www.sitemaps.org/schemas/sitemap/0.9}loc")]
    assert locations == [
        "https://trade.example.com/", "https://trade.example.com/#pricing",
        "https://trade.example.com/founding-beta", "https://trade.example.com/#faq",
        "https://trade.example.com/login", "https://trade.example.com/register",
    ]
    assert {"/robots.txt", "/sitemap.xml"}.issubset(auth.PUBLIC_PATHS)


def test_web_manifest_is_valid_and_uses_existing_icon():
    root = Path(__file__).parents[1]
    manifest = json.loads((root / "app" / "static" / "site.webmanifest").read_text(encoding="utf-8"))
    assert manifest["name"] == "Trade Paper AI"
    assert manifest["start_url"] == "/"
    icon = manifest["icons"][0]
    assert icon["src"] == "/static/product-hunt/icon.png"
    assert (root / "app" / icon["src"].lstrip("/")).is_file()


@pytest.mark.parametrize("browser_name", ["chromium", "webkit"])
def test_seo_endpoints_and_metadata_in_browser(auth_server, browser_name):
    base_url, _ = auth_server
    with sync_playwright() as playwright:
        browser_type = getattr(playwright, browser_name)
        if not Path(browser_type.executable_path).exists():
            pytest.fail(f"{browser_name} browser binary is not installed")
        browser = browser_type.launch(headless=True)
        page = browser.new_page()
        try:
            assert page.goto(f"{base_url}/").ok
            assert page.locator('link[rel="canonical"]').count() == 1
            assert page.locator('script[type="application/ld+json"]').count() == 3
            for path, content_type in (("/robots.txt", "text/plain"), ("/sitemap.xml", "application/xml"), ("/static/site.webmanifest", "application/manifest+json")):
                response = page.request.get(f"{base_url}{path}")
                assert response.ok, path
                assert content_type in response.headers["content-type"]
        finally:
            browser.close()
