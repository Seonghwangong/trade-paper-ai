import json

from starlette.requests import Request

from app import auth, guest_sample


def korean_request():
    return Request({"type": "http", "method": "POST", "path": "/register",
                    "query_string": b"lang=ko", "headers": [],
                    "client": ("127.0.0.1", 1), "scheme": "http"})


def test_practice_signup_preserves_language_and_destination():
    html = guest_sample.practice_page("ko").body.decode()
    assert '/register?next=%2Fdemo&amp;lang=ko' in html
    form = auth.register_page(next="/demo", lang="ko").body.decode()
    assert '<html lang="ko">' in form
    assert 'action="/register?lang=ko"' in form
    assert 'name="next" value="/demo"' in form
    assert '월 5문서 무료 · 카드 등록 불필요' in form
    assert '/login?next=%2Fdemo&lang=ko' in form
    assert '/register?lang=en&amp;next=%2Fdemo' in form


def test_korean_registration_errors_success_and_login(tmp_path, monkeypatch):
    users = tmp_path / "users.json"
    users.write_text("[]")
    monkeypatch.setattr(auth, "USERS_FILE", users)
    request = korean_request()
    rejected = auth.register("가상 회사", "test@example.com", "longpassword", "wrong",
                             next_path="/demo", website_confirm="", request=request)
    assert rejected.status_code == 400
    assert '비밀번호와 비밀번호 확인이 일치해야 합니다.' in rejected.body.decode()
    assert 'value="가상 회사"' in rejected.body.decode()
    assert json.loads(users.read_text()) == []
    created = auth.register("가상 회사", "test@example.com", "longpassword", "longpassword",
                            next_path="/demo", website_confirm="", request=request)
    assert created.headers["location"] == "/login?registered=1&lang=ko&next=%2Fdemo"
    assert '가입이 완료됐습니다.' in auth.login_page(registered=1, lang="ko").body.decode()
    failed = auth.login("test@example.com", "wrong", "/demo", request)
    assert failed.status_code == 401
    assert '이메일 또는 비밀번호를 확인해 주세요.' in failed.body.decode()
    assert 'action="/login?lang=ko"' in failed.body.decode()
    auth._reset_login_rate_limiter()


def test_language_does_not_weaken_redirect_or_html_escaping():
    html = auth.register_page(next="https://evil.example", lang='<script>alert(1)</script>').body.decode()
    assert '<html lang="en">' in html
    assert 'evil.example' not in html
    assert '<script>alert(1)</script>' not in html
    html = auth._auth_page("register", company='<script>alert(1)</script>', lang="ko").body.decode()
    assert '&lt;script&gt;' in html
    assert '<script>alert(1)</script>' not in html
