import json
from pathlib import Path
from xml.etree import ElementTree

import pytest
from playwright.sync_api import sync_playwright
from starlette.requests import Request

from app import auth, landing, main
from app.release import APP_VERSION
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
    assert blocks[1]["softwareVersion"] == APP_VERSION
    visible_questions = [landing.COPY["en"][f"q{i}"] for i in range(1, 5)]
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
        "https://trade.example.com/", "https://trade.example.com/getting-started",
        "https://trade.example.com/founding-beta", "https://trade.example.com/starter",
        "https://trade.example.com/terms",
        "https://trade.example.com/privacy", "https://trade.example.com/refund-policy",
        "https://trade.example.com/contact",
        "https://trade.example.com/try-sample", "https://trade.example.com/about",
        "https://trade.example.com/release-notes", "https://trade.example.com/version-history",
        "https://trade.example.com/?lang=ko", "https://trade.example.com/getting-started?lang=ko",
        "https://trade.example.com/founding-beta?lang=ko", "https://trade.example.com/starter?lang=ko",
        "https://trade.example.com/try-sample?lang=ko",
    ]
    assert {"/robots.txt", "/sitemap.xml"}.issubset(auth.PUBLIC_PATHS)


async def _anonymous_get(url):
    from types import SimpleNamespace
    from urllib.parse import urlsplit
    parsed = urlsplit(url)
    messages = []
    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}
    async def send(message):
        messages.append(message)
    scope = dict(_request(parsed.path).scope, query_string=parsed.query.encode(),
                 raw_path=parsed.path.encode(), http_version="1.1")
    await main.app(scope, receive, send)
    content = b"".join(m.get("body", b"") for m in messages)
    return SimpleNamespace(status_code=messages[0]["status"], content=content,
                           text=content.decode(), headers={k.decode(): v.decode() for k, v in messages[0]["headers"]})


def test_public_sitemap_targets_are_anonymous_canonical_pages(monkeypatch):
    import asyncio
    from urllib.parse import urlsplit
    from app import seo

    monkeypatch.setenv("TRADE_PAPER_PUBLIC_BASE_URL", "https://trade.example.com")
    monkeypatch.setattr(auth, "current_user", lambda request: None)
    monkeypatch.setattr(main.analytics_module, "record_visit", lambda *a, **kw: None)

    async def check():
        sitemap = await _anonymous_get("/sitemap.xml")
        urls = [e.text for e in ElementTree.fromstring(sitemap.text).iter("{http://www.sitemaps.org/schemas/sitemap/0.9}loc")]
        assert len(urls) == len(set(urls)) == 17
        for url in urls:
            response = await _anonymous_get(url)
            assert response.status_code == 200, (url, response.status_code)
            assert response.text.count('rel="canonical"') == 1, url
            assert f'<link rel="canonical" href="{url}">' in response.text, url
            assert "noindex" not in response.headers.get("x-robots-tag", ""), url
            assert int(response.headers["content-length"]) == len(response.content)
            if urlsplit(url).path in seo.LOCALIZED_PAGES:
                for lang in ("en", "ko", "x-default"):
                    assert f'hreflang="{lang}"' in response.text, url
        tracked = await _anonymous_get("/try-sample?lang=ko&utm_source=community")
        assert 'rel="canonical" href="https://trade.example.com/try-sample?lang=ko"' in tracked.text
        assert 'name="viewport"' in tracked.text

    asyncio.run(check())


def test_search_exclusions_preserve_login_destination_and_private_guards(monkeypatch):
    import asyncio

    monkeypatch.setattr(auth, "current_user", lambda request: None)
    monkeypatch.setattr(main.analytics_module, "record_visit", lambda *a, **kw: None)

    async def check():
        for path in ("/login?next=/demo", "/register?next=/release-notes", "/forgot-password", "/reset-password"):
            response = await _anonymous_get(path)
            assert "noindex" in response.headers["x-robots-tag"], path
            if "next=" in path:
                assert 'name="next"' in response.text
        for path in ("/demo", "/product-suggestions?q=", "/invoice", "/admin/dashboard"):
            response = await _anonymous_get(path)
            assert response.status_code == 303, path
            assert response.headers["location"].startswith("/login?next="), path
        analytics = await _anonymous_get("/analytics/visit?page=")
        assert analytics.status_code == 405
        assert "noindex" in analytics.headers["x-robots-tag"]
        robots = await _anonymous_get("/robots.txt")
        assert "Disallow: /analytics/" in robots.text
        assert "Disallow: /login" not in robots.text  # Crawlers must be able to see noindex.

    asyncio.run(check())


def test_web_manifest_is_valid_and_uses_existing_icon():
    root = Path(__file__).parents[1]
    manifest = json.loads((root / "app" / "static" / "site.webmanifest").read_text(encoding="utf-8"))
    assert manifest["name"] == "Trade Paper AI"
    assert manifest["start_url"] == "/"
    icon = manifest["icons"][0]
    assert icon["src"] == "/static/product-hunt/icon.png"
    assert (root / "app" / icon["src"].lstrip("/")).is_file()


@pytest.mark.browser
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
