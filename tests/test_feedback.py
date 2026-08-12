import json

import pytest

from app import auth, feedback, landing
from app.ui import release_footer
from app.validation import DataValidationError


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
        "feedback": "The workflow is clear.",
    }
    assert "account_id" not in first[0]

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
        "Feature Request", "UI/UX", "Workflow", "Performance", "Other",
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
    body = feedback.feedback_admin().body.decode()
    assert body.index("Faster lists please") < body.index("Clear workflow")
    for heading in ("Submitted", "Name", "Email", "Rating", "Category", "Feedback"):
        assert heading in body
    for query, visible, hidden in (
        ("kim@", "Clear workflow", "Faster lists please"),
        ("Lee", "Faster lists please", "Clear workflow"),
        ("Performance", "Faster lists please", "Clear workflow"),
        ("clear", "Clear workflow", "Faster lists please"),
    ):
        result = feedback.feedback_admin(query).body.decode()
        assert visible in result
        assert hidden not in result
