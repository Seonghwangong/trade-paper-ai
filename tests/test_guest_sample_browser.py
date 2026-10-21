from io import BytesIO
import json
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright, expect
from pypdf import PdfReader

from tests.test_auth_browser import auth_server


@pytest.mark.browser
@pytest.mark.parametrize("browser_name", ["chromium", "webkit"])
def test_guest_edits_downloads_invalidates_and_recovers(auth_server, browser_name, tmp_path):
    base, data_dir = auth_server
    with sync_playwright() as playwright:
        browser = getattr(playwright, browser_name).launch(headless=True)
        context = browser.new_context(viewport={"width": 390, "height": 844})
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        try:
            page.goto(base + "/?utm_source=reddit")
            page.get_by_role("link", name="Try without signup", exact=True).click()
            expect(page.get_by_role("heading", name="Try one shipment. No signup.")).to_be_visible()
            page.get_by_role("link", name="한국어", exact=True).click()
            page.get_by_label("수량 (개)", exact=True).fill("120")
            page.get_by_label("개당 가격 (USD)").fill("9.5")
            page.get_by_label("박스 수").fill("6")
            page.get_by_label("포장 제외 순중량 (kg)").fill("24")
            page.get_by_label("포장 포함 총중량 (kg)").fill("23")
            generate = page.get_by_role("button", name="샘플 PDF 두 장 만들기")
            generate.click()
            expect(page.locator("#status")).to_have_text("총중량은 순중량 이상이어야 합니다.")
            expect(page.locator("#results")).to_be_hidden()
            page.get_by_label("포장 포함 총중량 (kg)").fill("30")
            generate.click()
            expect(page.locator("#results")).to_be_visible()
            expect(page.locator("#invoice-total")).to_have_text("USD 1,140.00")
            for name in ("invoice", "packing"):
                with page.expect_download() as download_info:
                    page.locator(f"#{name}-download").click()
                download = download_info.value
                assert download.failure() is None
                target = tmp_path / f"{name}.pdf"
                download.save_as(target)
                text = PdfReader(BytesIO(target.read_bytes())).pages[0].extract_text()
                assert "120" in text and "FICTIONAL PRACTICE" in text
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            page.screenshot(path=str(tmp_path / "practice-mobile.png"), full_page=True)
            page.get_by_label("수량 (개)", exact=True).fill("130")
            expect(page.locator("#results")).to_be_hidden()
            expect(page.locator("#invoice-download")).not_to_have_attribute("href")
            page.route("**/try-sample/documents?*", lambda route: route.fulfill(status=503, body="Unavailable"))
            generate.click()
            expect(page.locator("#status")).to_have_class("error")
            expect(generate).to_be_enabled()
            expect(page.get_by_label("수량 (개)", exact=True)).to_have_value("130")
            page.unroute("**/try-sample/documents?*")
            generate.click()
            expect(page.locator("#results")).to_be_visible()
            page.get_by_role("button", name="예시로 초기화").click()
            expect(page.locator("#results")).to_be_hidden()
            expect(page.locator("#invoice-total")).to_have_text("USD 800.00")
            assert not errors
            assert json.loads((data_dir / "users.json").read_text()) == []
            for filename in ["invoices.json", "packing_lists.json", "subscription_usage.json"]:
                if (data_dir / filename).exists():
                    assert json.loads((data_dir / filename).read_text()) in ([], {})
        finally:
            context.close()
            browser.close()
