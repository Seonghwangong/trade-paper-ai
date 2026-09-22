import json

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app import auth, founding_beta, landing
from app.validation import DataValidationError


def _request(admin=True):
    return Request({"type": "http", "headers": [], "trade_paper_user": {"account_id": "test-account", "is_admin": admin}})


@pytest.mark.parametrize("identity", [{}, {"account_id": "ordinary-user"}, {"account_id": "ordinary-user", "is_admin": False}])
def test_beta_admin_denies_read_and_write_before_storage_access(monkeypatch, identity):
    request = Request({"type": "http", "headers": [], "trade_paper_user": identity})
    def forbidden_storage(*args, **kwargs):
        pytest.fail("Unauthorized request reached applicant storage")
    monkeypatch.setattr(founding_beta, "load_json_strict", forbidden_storage)
    monkeypatch.setattr(founding_beta, "locked_json_mutation", forbidden_storage)
    with pytest.raises(HTTPException) as read_error:
        founding_beta.founding_beta_admin(request)
    assert read_error.value.status_code == 403
    with pytest.raises(HTTPException) as write_error:
        founding_beta.update_founding_beta_status(0, request, "Beta Customer")
    assert write_error.value.status_code == 403


def test_founding_beta_application_is_saved_atomically_with_backup(tmp_path, monkeypatch):
    application_file = tmp_path / "beta_applications.json"
    monkeypatch.setattr(founding_beta, "BETA_APPLICATION_FILE", application_file)

    response = founding_beta.submit_founding_beta(
        "Alpha Export", "Kim", "kim@alpha.example", "Korea",
        "Machine parts", "11–50",
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/founding-beta/thank-you"
    first = json.loads(application_file.read_text(encoding="utf-8"))
    assert first[0] == {
        "company_name": "Alpha Export",
        "contact_name": "Kim",
        "email": "kim@alpha.example",
        "country": "Korea",
        "exports": "Machine parts",
        "monthly_export_documents": "11–50",
        "status": "New",
        "submitted_at": first[0]["submitted_at"],
    }
    assert "account_id" not in first[0]

    founding_beta.submit_founding_beta(
        "Beta Export", "Lee", "lee@beta.example", "Japan", "", "1–10"
    )
    backup = tmp_path / "beta_applications.backup.json"
    assert backup.exists()
    assert json.loads(backup.read_text(encoding="utf-8")) == first
    assert len(json.loads(application_file.read_text(encoding="utf-8"))) == 2


@pytest.mark.parametrize("field", ["company_name", "contact_name", "email", "country"])
def test_founding_beta_required_fields_are_validated(tmp_path, monkeypatch, field):
    monkeypatch.setattr(founding_beta, "BETA_APPLICATION_FILE", tmp_path / "beta_applications.json")
    values = {
        "company_name": "Alpha Export", "contact_name": "Kim",
        "email": "kim@alpha.example", "country": "Korea",
    }
    values[field] = ""
    with pytest.raises(DataValidationError):
        founding_beta.submit_founding_beta(**values)
    assert not founding_beta.BETA_APPLICATION_FILE.exists()


def test_founding_beta_rejects_invalid_email_and_monthly_range(tmp_path, monkeypatch):
    monkeypatch.setattr(founding_beta, "BETA_APPLICATION_FILE", tmp_path / "beta_applications.json")
    with pytest.raises(DataValidationError):
        founding_beta.submit_founding_beta("A", "B", "invalid", "Korea")
    with pytest.raises(DataValidationError):
        founding_beta.submit_founding_beta("A", "B", "a@b.example", "Korea", "", "100+")


def test_founding_beta_page_thank_you_and_landing_links():
    form = founding_beta.founding_beta_page().body.decode()
    for label in (
        "Company Name", "Contact Name", "Email", "Country",
        "What do you export?", "Monthly export documents", "1–10", "11–50", "51+",
    ):
        assert label in form
    assert "does not create an account" in form

    thanks = founding_beta.founding_beta_thank_you().body.decode()
    for text in (
        "Founding Beta", "✓ First 10 companies", "✓ Founding price for 6 months",
        "✓ Direct onboarding", "✓ Priority support",
        "We'll contact you within 2 business days.",
    ):
        assert text in thanks

    landing_body = landing.landing_page().body.decode()
    assert landing_body.count('href="/founding-beta"') >= 3
    assert "/founding-beta" in auth.PUBLIC_PATHS
    assert "/founding-beta/thank-you" in auth.PUBLIC_PATHS
    assert "/admin/founding-beta" not in auth.PUBLIC_PATHS


def test_founding_beta_admin_lists_latest_first_and_searches(tmp_path, monkeypatch):
    application_file = tmp_path / "beta_applications.json"
    application_file.write_text(json.dumps([
        {
            "submitted_at": "2026-07-22T01:00:00+00:00", "company_name": "Alpha Export",
            "contact_name": "Kim", "email": "kim@alpha.example", "country": "Korea",
            "exports": "Parts", "monthly_export_documents": "1–10",
        },
        {
            "submitted_at": "2026-07-23T01:00:00+00:00", "company_name": "Beta Trading",
            "contact_name": "Lee", "email": "lee@beta.example", "country": "Japan",
            "exports": "Textiles", "monthly_export_documents": "51+", "status": "Contacted",
        },
    ]), encoding="utf-8")
    monkeypatch.setattr(founding_beta, "BETA_APPLICATION_FILE", application_file)

    body = founding_beta.founding_beta_admin(_request()).body.decode()
    for heading in (
        "Application Date", "Company", "Contact Name", "Email", "Country",
        "Export Item", "Monthly Documents", "Status",
    ):
        assert heading in body
    assert body.index("Beta Trading") < body.index("Alpha Export")
    assert '<option value="New" selected>New</option>' in body
    assert '<option value="Contacted" selected>Contacted</option>' in body
    assert 'href="mailto:lee@beta.example?subject=Trade+Paper+AI+Founding+Beta"' in body
    assert 'aria-label="Copy email for Beta Trading"' in body
    assert 'data-email="lee@beta.example"' in body

    for query, visible, hidden in (
        ("alpha", "Alpha Export", "Beta Trading"),
        ("Lee", "Beta Trading", "Alpha Export"),
        ("kim@alpha.example", "Alpha Export", "Beta Trading"),
    ):
        result = founding_beta.founding_beta_admin(_request(), query).body.decode()
        assert visible in result
        assert hidden not in result


def test_founding_beta_admin_status_update_changes_only_status_and_creates_backup(tmp_path, monkeypatch):
    application_file = tmp_path / "beta_applications.json"
    account_file = tmp_path / "users.json"
    original = [{
        "submitted_at": "2026-07-22T01:00:00+00:00", "company_name": "Alpha Export",
        "contact_name": "Kim", "email": "kim@alpha.example", "country": "Korea",
        "exports": "Parts", "monthly_export_documents": "1–10",
    }]
    application_file.write_text(json.dumps(original), encoding="utf-8")
    account_file.write_text('[{"account_id":"account-a"}]', encoding="utf-8")
    account_before = account_file.read_bytes()
    monkeypatch.setattr(founding_beta, "BETA_APPLICATION_FILE", application_file)

    response = founding_beta.update_founding_beta_status(0, _request(), "Beta Customer")
    assert response.status_code == 303
    assert response.headers["location"] == "/admin/founding-beta?updated=1"
    updated = json.loads(application_file.read_text(encoding="utf-8"))
    assert updated[0] == {**original[0], "status": "Beta Customer"}
    backup = tmp_path / "beta_applications.backup.json"
    assert json.loads(backup.read_text(encoding="utf-8")) == original
    assert account_file.read_bytes() == account_before

    with pytest.raises(DataValidationError):
        founding_beta.update_founding_beta_status(0, _request(), "Approved")
    assert json.loads(application_file.read_text(encoding="utf-8")) == updated
    assert "Status updated successfully." in founding_beta.founding_beta_admin(_request(), updated=1).body.decode()
