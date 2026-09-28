"""Public form errors must let a visitor correct input without starting over."""
import asyncio
from html.parser import HTMLParser
from io import BytesIO
import json
from urllib.parse import urlencode

from fastapi import FastAPI
from PIL import Image
import pytest

from app import feedback, founding_beta
from app.main import data_validation_error
from app.validation import DataValidationError


class FormContents(HTMLParser):
    def __init__(self, markup):
        super().__init__(convert_charrefs=True)
        self.values = {}
        self.tags = []
        self.select = None
        self.textarea = None
        self.feed(markup)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        self.tags.append((tag, attrs))
        name = attrs.get("name")
        if tag == "input" and name:
            if attrs.get("type") == "radio" and "checked" not in attrs:
                return
            self.values[name] = attrs.get("value", "")
        elif tag == "select":
            self.select = name
            self.values[name] = ""
        elif tag == "option" and "selected" in attrs:
            self.values[self.select] = attrs.get("value", "")
        elif tag == "textarea":
            self.textarea = name
            self.values[name] = ""

    def handle_data(self, data):
        if self.textarea:
            self.values[self.textarea] += data

    def handle_endtag(self, tag):
        if tag == "textarea":
            self.textarea = None
        elif tag == "select":
            self.select = None


def request(path, values, *, accept="text/html", upload=None, method="POST"):
    app = FastAPI()
    app.include_router(founding_beta.router)
    app.include_router(feedback.router)
    app.add_exception_handler(DataValidationError, data_validation_error)
    content_type = "application/x-www-form-urlencoded"
    payload = urlencode(values).encode()
    if upload:
        filename, media_type, content = upload
        boundary = "public-form-recovery-boundary"
        chunks = []
        for name, value in values.items():
            chunks.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
        chunks.append(f'--{boundary}\r\nContent-Disposition: form-data; name="screenshot"; filename="{filename}"\r\nContent-Type: {media_type}\r\n\r\n'.encode() + content + b"\r\n")
        payload = b"".join(chunks) + f"--{boundary}--\r\n".encode()
        content_type = f"multipart/form-data; boundary={boundary}"
    messages = []

    async def run():
        async def receive():
            return {"type": "http.request", "body": payload, "more_body": False}

        async def send(message):
            messages.append(message)

        scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
                 "method": method, "scheme": "http", "path": path, "raw_path": path.encode(),
                 "query_string": b"", "root_path": "", "server": ("test", 80), "client": ("test", 1),
                 "headers": [(b"content-type", content_type.encode()), (b"accept", accept.encode())]}
        await app(scope, receive, send)

    asyncio.run(run())
    start = next(m for m in messages if m["type"] == "http.response.start")
    body = b"".join(m.get("body", b"") for m in messages).decode()
    return start["status"], dict(start["headers"]), body


@pytest.fixture
def stores(tmp_path, monkeypatch):
    paths = {"beta": tmp_path / "beta_applications.json", "feedback": tmp_path / "feedback.json"}
    monkeypatch.setattr(founding_beta, "BETA_APPLICATION_FILE", paths["beta"])
    monkeypatch.setattr(feedback, "FEEDBACK_FILE", paths["feedback"])
    return paths


@pytest.mark.parametrize("language", ["en", "ko"])
def test_beta_email_error_keeps_fields_and_language_then_saves_once(stores, language):
    values = {"company_name": 'Sample "Export" & Co.', "contact_name": "가상 담당자",
              "email": "wrong-address", "country": "Korea", "exports": "Parts\nTwo cartons & samples",
              "monthly_export_documents": "11–50", "referral_source": "Disquiet", "lang": language}
    status, headers, body = request("/founding-beta", values)
    assert headers[b"cache-control"] == b"no-store"
    assert status == 409
    form = FormContents(body)
    for name, value in values.items():
        if name != "lang":
            assert form.values[name] == value
    assert f'<html lang="{language}">' in body
    assert 'href="#email"' in body
    assert ("아직 신청이 접수되지 않았습니다" if language == "ko" else "Your application has not been submitted") in body
    if language == "ko":
        assert form.values["lang"] == "ko"
    assert not stores["beta"].exists()

    corrected = dict(form.values, email="tester@example.com")
    status, headers, _ = request("/founding-beta", corrected)
    assert status == 303
    assert headers[b"location"].decode() == "/founding-beta/thank-you" + ("?lang=ko" if language == "ko" else "")
    rows = json.loads(stores["beta"].read_text())
    assert len(rows) == 1
    for key in ("company_name", "contact_name", "country", "exports", "monthly_export_documents", "referral_source"):
        assert rows[0][key] == values[key]
    assert rows[0].get("preferred_language", "en") == language
    # A later unrelated visitor must not see the failed submission.
    assert "tester@example.com" not in request("/founding-beta", {}, method="GET")[2]


@pytest.mark.parametrize("bad_field,bad_value", [
    ("company_name", "   "), ("contact_name", ""), ("country", ""),
    ("monthly_export_documents", "unknown"), ("referral_source", "unknown"),
])
def test_beta_required_and_selection_errors_keep_other_work(stores, bad_field, bad_value):
    values = {"company_name": "Sample Export", "contact_name": "Tester", "email": "a@example.com",
              "country": "Korea", "exports": "Keep this description", "monthly_export_documents": "1–10",
              "referral_source": "Recommendation", "lang": "ko"}
    values[bad_field] = bad_value
    status, _, body = request("/founding-beta", values)
    form = FormContents(body)
    assert status == 409 and form.values["exports"] == "Keep this description"
    assert f'href="#{bad_field}"' in body
    assert not stores["beta"].exists()
    if bad_field in {"monthly_export_documents", "referral_source"}:
        assert form.values[bad_field] == ""
        assert all(attrs.get("value") != "unknown" for tag, attrs in form.tags if tag == "option")


@pytest.mark.parametrize("path,values,text_field", [
    ("/founding-beta", {"company_name": "Sample", "contact_name": "Tester", "country": "Korea"}, "exports"),
    ("/feedback", {"name": "Tester", "rating": "4", "category": "Workflow"}, "feedback"),
])
def test_retained_input_cannot_break_out_of_form_fields(stores, path, values, text_field):
    payload = '</textarea><script>alert("x")</script><input name="injected" value="yes">&'
    values.update(email='wrong" autofocus onfocus="alert(1)', **{text_field: payload})
    status, _, body = request(path, values)
    assert status == 409
    form = FormContents(body)
    assert form.values[text_field] == payload
    assert form.values["email"] == values["email"]
    assert "injected" not in form.values
    assert not any(tag == "script" or "onfocus" in attrs for tag, attrs in form.tags)
    assert not any(path.exists() for path in stores.values())


@pytest.mark.parametrize("bad_field,bad_value", [("email", "invalid"), ("rating", "6"), ("category", "invalid"), ("feedback", "   ")])
def test_feedback_validation_keeps_text_and_valid_choices(stores, bad_field, bad_value):
    values = {"feedback": "Keep my feedback\n포장 정보 확인", "name": "Tester", "email": "a@example.com", "rating": "4", "category": "Workflow"}
    values[bad_field] = bad_value
    status, _, body = request("/feedback", values)
    form = FormContents(body)
    assert status == 409 and "Your feedback has not been submitted" in body
    for field, value in values.items():
        if field not in {"category", "rating"} or field != bad_field:
            assert form.values[field] == value
    assert not stores["feedback"].exists()


@pytest.mark.parametrize("upload", [
    ("note.txt", "text/plain", b"not an image"),
    ("broken.png", "image/png", b"not an image"),
    ("large.png", "image/png", b"x" * (feedback.MAX_SCREENSHOT_BYTES + 1)),
])
def test_bad_screenshot_keeps_feedback_and_corrected_submission_saves_once(stores, upload):
    values = {"feedback": "Keep screenshot feedback", "name": "Tester", "email": "a@example.com", "rating": "5", "category": "Bug"}
    status, headers, body = request("/feedback", values, upload=upload)
    assert headers[b"cache-control"] == b"no-store"
    assert status == 409
    form = FormContents(body)
    for key, value in values.items():
        assert form.values[key] == value
    assert form.values["screenshot"] == ""
    assert "select it again" in body
    assert not stores["feedback"].exists()
    assert not feedback._upload_dir().exists()

    image = BytesIO()
    Image.new("RGB", (12, 12), "blue").save(image, format="PNG")
    corrected = {key: form.values[key] for key in values}
    status, headers, _ = request("/feedback", corrected, upload=("new.png", "image/png", image.getvalue()))
    assert status == 303 and headers[b"location"] == b"/feedback/thank-you"
    rows = json.loads(stores["feedback"].read_text())
    assert len(rows) == 1 and rows[0]["feedback"] == values["feedback"]
    assert len(list(feedback._upload_dir().glob("*.png"))) == 1


@pytest.mark.parametrize("path,values", [
    ("/founding-beta", {"company_name": "Sample", "contact_name": "Tester", "country": "Korea", "email": "invalid"}),
    ("/feedback", {"feedback": "Keep feedback", "email": "invalid"}),
])
def test_json_clients_keep_structured_validation_contract(stores, path, values):
    status, headers, body = request(path, values, accept="application/json")
    assert status == 409 and b"application/json" in headers[b"content-type"]
    assert json.loads(body)["field"] == "Email"
    assert not any(path.exists() for path in stores.values())
