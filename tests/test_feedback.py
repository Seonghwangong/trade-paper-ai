import json
from io import BytesIO
from pathlib import Path
import re

import pytest
from fastapi import HTTPException, UploadFile
from PIL import Image
from playwright.sync_api import sync_playwright
from starlette.requests import Request

from app import auth, feedback, landing
from app.ui import release_footer
from app.validation import DataValidationError
from tests.test_auth_browser import auth_server


def _request(account_id="account-a", admin=True):
    return Request({"type": "http", "method": "GET", "path": "/admin/feedback", "headers": [], "trade_paper_user": {"account_id": account_id, "is_admin": admin}})


def test_feedback_submit_saves_separately_and_creates_backup(tmp_path, monkeypatch):
    feedback_file = tmp_path / "feedback.json"
    users_file = tmp_path / "users.json"
    users_file.write_text('[{"account_id":"account-a"}]', encoding="utf-8")
    users_before = users_file.read_bytes()
    monkeypatch.setattr(feedback, "FEEDBACK_FILE", feedback_file)

    response = feedback.submit_feedback(
        "The workflow is clear.", "Kim", "kim@example.com", "5", "Workflow"
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/feedback/thank-you"
    first = json.loads(feedback_file.read_text(encoding="utf-8"))
    assert first[0] == {
        "submitted_at": first[0]["submitted_at"], "name": "Kim",
        "email": "kim@example.com", "rating": "5", "category": "Workflow",
        "feedback": "The workflow is clear.", "account_id": "", "status": "New",
        "reply_note": "", "screenshot_id": "",
    }

    feedback.submit_feedback("Please add a shortcut.", "", "", "", "Feature Request")
    backup = tmp_path / "feedback.backup.json"
    assert json.loads(backup.read_text(encoding="utf-8")) == first
    assert len(json.loads(feedback_file.read_text(encoding="utf-8"))) == 2
    assert users_file.read_bytes() == users_before


def test_feedback_required_rating_and_category_validation(tmp_path, monkeypatch):
    monkeypatch.setattr(feedback, "FEEDBACK_FILE", tmp_path / "feedback.json")
    with pytest.raises(DataValidationError):
        feedback.submit_feedback("")
    with pytest.raises(DataValidationError):
        feedback.submit_feedback("Message", rating="6")
    with pytest.raises(DataValidationError):
        feedback.submit_feedback("Message", category="Sales")
    with pytest.raises(DataValidationError):
        feedback.submit_feedback("Message", email="invalid")
    assert not feedback.FEEDBACK_FILE.exists()


def test_feedback_form_thank_you_and_footer_links():
    form = feedback.feedback_page().body.decode()
    for text in (
        "Name", "Email", "Rating", "Feedback", "Category", "Bug",
        "Feature Request", "UI/UX", "Workflow", "Performance", "Other", "Screenshot",
    ):
        assert text in form
    for rating in range(1, 6):
        assert f'value="{rating}"' in form
        assert f'aria-label="{rating} star rating"' in form
    thanks = feedback.feedback_thank_you().body.decode()
    assert "Thank you for your feedback" in thanks
    assert "Your feedback has been received" in thanks
    assert landing.landing_page().body.decode().count('href="/feedback"') == 1
    assert '<a href="/feedback">Feedback</a>' in release_footer()
    assert "/feedback" in auth.PUBLIC_PATHS
    assert "/feedback/thank-you" in auth.PUBLIC_PATHS
    assert "/admin/feedback" not in auth.PUBLIC_PATHS


def test_feedback_admin_lists_latest_first_and_searches(tmp_path, monkeypatch):
    feedback_file = tmp_path / "feedback.json"
    feedback_file.write_text(json.dumps([
        {"submitted_at": "2026-07-22", "name": "Kim", "email": "kim@example.com", "rating": "5", "category": "Workflow", "feedback": "Clear workflow"},
        {"submitted_at": "2026-07-23", "name": "Lee", "email": "lee@example.com", "rating": "3", "category": "Performance", "feedback": "Faster lists please"},
    ]), encoding="utf-8")
    monkeypatch.setattr(feedback, "FEEDBACK_FILE", feedback_file)
    body = feedback.feedback_admin(_request()).body.decode()
    assert body.index("Faster lists please") < body.index("Clear workflow")
    for heading in ("Submitted", "Name", "Email", "Rating", "Category", "Feedback"):
        assert heading in body
    for query, visible, hidden in (
        ("kim@", "Clear workflow", "Faster lists please"),
        ("Lee", "Faster lists please", "Clear workflow"),
        ("Performance", "Faster lists please", "Clear workflow"),
        ("clear", "Clear workflow", "Faster lists please"),
    ):
        result = feedback.feedback_admin(_request(), query).body.decode()
        assert visible in result
        assert hidden not in result


def test_feedback_screenshot_status_reply_and_admin_isolation(tmp_path, monkeypatch):
    feedback_file = tmp_path / "feedback.json"
    monkeypatch.setattr(feedback, "FEEDBACK_FILE", feedback_file)
    image = Image.new("RGB", (20, 20), "red")
    payload = BytesIO()
    image.save(payload, format="JPEG", exif=b"private-metadata")
    upload = UploadFile(filename="customer-name.jpg", file=BytesIO(payload.getvalue()), headers={"content-type": "image/jpeg"})
    feedback.submit_feedback("Screenshot feedback", rating="4", category="UI/UX", screenshot=upload, request=_request("account-a", False))
    record = json.loads(feedback_file.read_text(encoding="utf-8"))[0]
    assert record["account_id"] == "account-a"
    assert record["status"] == "New"
    assert record["screenshot_id"].endswith(".png")
    assert "customer-name" not in record["screenshot_id"]
    saved = feedback._upload_dir() / record["screenshot_id"]
    clean = Image.open(saved)
    assert clean.format == "PNG"
    assert not clean.getexif()

    response = feedback.update_feedback(0, _request(), "Planned", "Review with the product team")
    assert response.status_code == 303
    updated = json.loads(feedback_file.read_text(encoding="utf-8"))[0]
    assert updated["status"] == "Planned"
    assert updated["reply_note"] == "Review with the product team"
    assert updated["feedback"] == "Screenshot feedback"
    screenshot = feedback.feedback_screenshot(0, _request())
    assert screenshot.media_type == "image/png"
    with pytest.raises(HTTPException) as denied:
        feedback.feedback_admin(_request(admin=False))
    assert denied.value.status_code == 403


@pytest.mark.parametrize("browser_name", ["chromium", "webkit"])
def test_feedback_in_app_browser_flow(auth_server, browser_name):
    base_url, _ = auth_server
    with sync_playwright() as playwright:
        browser_type = getattr(playwright, browser_name)
        if not Path(browser_type.executable_path).exists():
            pytest.fail(f"{browser_name} browser binary is not installed")
        browser = browser_type.launch(headless=True)
        page = browser.new_page()
        email = f"browser-{browser_name}@example.com"
        screenshot = BytesIO()
        Image.new("RGB", (32, 24), "blue").save(screenshot, format="PNG")
        try:
            page.goto(f"{base_url}/register")
            page.get_by_label("Company Name").fill("Feedback Company")
            page.get_by_label("Email").fill(email)
            page.get_by_label("Password", exact=True).fill("Test1234")
            page.get_by_label("Confirm Password").fill("Test1234")
            page.get_by_role("button", name="Register").click()
            page.goto(f"{base_url}/login")
            page.get_by_label("Email").fill(email)
            page.get_by_label("Password").fill("Test1234")
            page.get_by_role("button", name="Login").click()
            page.wait_for_url(re.compile(rf"{base_url}/company\?setup=1&next=%2Fonboarding"))
            page.locator("#address").fill("Seoul")
            page.get_by_role("button", name="Save Company").click()
            page.wait_for_url(re.compile(rf"{base_url}/onboarding"))
            page.goto(f"{base_url}/")
            page.get_by_role("link", name="Feedback", exact=True).click()
            page.get_by_role("radio", name="5 star rating").check()
            page.get_by_label("Category").select_option("Bug")
            page.locator("#feedback").fill(f"In-app feedback {browser_name}")
            page.get_by_label("Screenshot").set_input_files({"name": "screen.png", "mimeType": "image/png", "buffer": screenshot.getvalue()})
            page.get_by_role("button", name="Send Feedback").click()
            page.wait_for_url(f"{base_url}/feedback/thank-you")
            page.goto(f"{base_url}/admin/feedback?search=In-app%20feedback%20{browser_name}")
            row = page.locator("tr", has_text=f"In-app feedback {browser_name}")
            assert row.get_by_text(f"In-app feedback {browser_name}", exact=True).is_visible()
            assert row.get_by_role("link", name="View Screenshot").is_visible()
            row.locator('select[name="status"]').select_option("Reviewing")
            row.locator('textarea[name="reply_note"]').fill("Internal follow-up")
            row.get_by_role("button", name="Save", exact=True).click()
            page.wait_for_url(f"{base_url}/admin/feedback?updated=1")
            assert page.get_by_role("status").get_by_text("Feedback updated.").is_visible()
            updated_row = page.locator("tr", has_text=f"In-app feedback {browser_name}")
            assert updated_row.locator('select[name="status"]').input_value() == "Reviewing"
            assert updated_row.locator('textarea[name="reply_note"]').input_value() == "Internal follow-up"
        finally:
            browser.close()
