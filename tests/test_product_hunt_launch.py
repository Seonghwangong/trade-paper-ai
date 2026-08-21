from pathlib import Path
import re

from PIL import Image
import pytest
from playwright.sync_api import sync_playwright

from app.landing import landing_page
from tests.test_auth_browser import auth_server


ROOT = Path(__file__).parents[1]
MEDIA = ROOT / "app" / "static" / "product-hunt"


def test_product_hunt_copy_and_social_preview_are_launch_ready():
    html = landing_page().body.decode()
    assert '<meta name="description" content="Create and manage export documents in one connected workflow.">' in html
    assert '<meta property="og:title" content="Trade Paper AI">' in html
    assert '<meta property="og:image" content="/static/product-hunt/hero.png">' in html
    launch = (ROOT / "docs" / "product-hunt-launch.md").read_text(encoding="utf-8")
    assert "Create and manage export documents in one connected workflow." in launch
    for heading in ("Short description", "Long description", "FAQ", "Maker comment", "Launch checklist"):
        assert f"## {heading}" in launch


def test_product_hunt_launch_links_use_public_marketing_routes():
    launch = (ROOT / "docs" / "product-hunt-launch.md").read_text(encoding="utf-8")
    links = launch.split("## Launch links", 1)[1]
    for destination in ("/#demo", "/#pricing", "/starter", "/register"):
        assert f"`{destination}`" in links
    assert "- Interactive demo: `/demo`" not in links
    assert "- Pricing: `/pricing`" not in links
    assert "available after sign-in" in links

    media_kit = (ROOT / "docs" / "media-kit.md").read_text(encoding="utf-8")
    assert "₩29,000 / month" in media_kit
    assert "Professional remains contact-based" in media_kit
    assert "Do not imply that external payment processing is active" in media_kit


def test_product_hunt_media_kit_has_all_local_assets_and_expected_dimensions():
    expected = {
        "logo.png": (1024, 1024), "icon.png": (240, 240),
        "hero.png": (1270, 760), "demo-15s.gif": (960, 540),
        **{f"screenshot-{number}.png": (1270, 760) for number in range(1, 6)},
    }
    media_doc = (ROOT / "docs" / "media-kit.md").read_text(encoding="utf-8")
    for filename, dimensions in expected.items():
        path = MEDIA / filename
        assert path.is_file()
        assert Image.open(path).size == dimensions
        assert filename in media_doc


def test_product_hunt_demo_is_exactly_fifteen_seconds():
    image = Image.open(MEDIA / "demo-15s.gif")
    duration = 0
    for frame in range(image.n_frames):
        image.seek(frame)
        duration += image.info["duration"]
    assert duration == 15_000


def test_product_hunt_media_kit_markdown_links_are_not_broken():
    document = ROOT / "docs" / "media-kit.md"
    targets = re.findall(r"\[[^]]+\]\(([^)]+)\)", document.read_text(encoding="utf-8"))
    assert targets
    for target in targets:
        assert (document.parent / target).resolve().is_file(), target


@pytest.mark.parametrize("browser_name", ["chromium", "webkit"])
def test_product_hunt_landing_and_media_assets_in_browser(auth_server, browser_name):
    base_url, _ = auth_server
    with sync_playwright() as playwright:
        browser_type = getattr(playwright, browser_name)
        if not Path(browser_type.executable_path).exists():
            pytest.fail(f"{browser_name} browser binary is not installed")
        browser = browser_type.launch(headless=True)
        page = browser.new_page()
        try:
            response = page.goto(f"{base_url}/")
            assert response and response.ok
            assert page.locator('meta[property="og:image"]').get_attribute("content") == "/static/product-hunt/hero.png"
            for filename in ("logo.png", "icon.png", "hero.png", "demo-15s.gif", *(f"screenshot-{number}.png" for number in range(1, 6))):
                asset = page.request.get(f"{base_url}/static/product-hunt/{filename}")
                assert asset.ok, filename
                assert asset.body(), filename
        finally:
            browser.close()
