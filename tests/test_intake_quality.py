import asyncio
import json

import pytest
from starlette.requests import Request

from app import analytics, auth, founding_beta, main
from app.intake_quality import application_status, needs_intake_review

SPAM = 'Reward PROMOCODE https://example.invalid/promo'


def admin_request():
    return Request({'type': 'http', 'headers': [], 'query_string': b'',
                    'trade_paper_user': {'account_id': 'admin', 'is_admin': True}})


@pytest.mark.parametrize('company', ['한국수출 주식회사', 'Coinbase Export Services',
                                    'Transfer Logistics', 'https://export.example',
                                    'Promo Code Packaging Ltd', 'Reward Manufacturing'])
def test_real_company_names_are_not_flagged(company):
    assert not needs_intake_review(company)


@pytest.mark.parametrize('company', [SPAM, 'Transfer reward Coinbase SIGN IN https://example.invalid/login'])
def test_promotional_link_in_company_name_requires_review(company):
    assert needs_intake_review(company)


def test_legacy_review_is_read_only_and_restore_keeps_original_data(tmp_path, monkeypatch):
    path = tmp_path / 'beta_applications.json'
    rows = [{'company_name': SPAM, 'email': 'review@example.test', 'status': 'New'},
            {'company_name': '정상 수출', 'status': 'New'}]
    path.write_text(json.dumps(rows))
    before = path.read_bytes()
    monkeypatch.setattr(founding_beta, 'BETA_APPLICATION_FILE', path)
    page = founding_beta.founding_beta_admin(admin_request(), status_filter='Needs review').body.decode()
    assert '1 new applications awaiting first contact' in page
    assert '1 applications need review' in page
    assert 'aria-label="Draft welcome email for' not in page and 'mailto:review@example.test' not in page
    assert path.read_bytes() == before
    summary = main.operations_dashboard_summary(rows, [])
    assert summary['beta_counts']['New'] == 1
    assert summary['recent_applications'] == [rows[1]]
    founding_beta.update_founding_beta_status(0, admin_request(), 'Spam')
    assert application_status(json.loads(path.read_text())[0]) == 'Spam'
    founding_beta.update_founding_beta_status(0, admin_request(), 'New')
    restored = json.loads(path.read_text())
    assert restored[0] == {**rows[0], 'intake_reviewed': True}
    assert restored[1] == rows[1]
    assert application_status(restored[0]) == 'New'
    assert 'aria-label="Draft welcome email for' in founding_beta.founding_beta_admin(admin_request()).body.decode()


def test_new_suspicious_application_is_retained_for_review(tmp_path, monkeypatch):
    path = tmp_path / 'beta_applications.json'
    monkeypatch.setattr(founding_beta, 'BETA_APPLICATION_FILE', path)
    response = founding_beta.submit_founding_beta(SPAM, 'A', 'a@example.test', 'Korea', '', '')
    assert response.status_code == 303
    row = json.loads(path.read_text())[0]
    assert row['company_name'] == SPAM and row['status'] == 'Needs review'


def test_honeypot_and_promotional_registration_do_not_write_users(tmp_path, monkeypatch):
    path = tmp_path / 'users.json'
    path.write_text('[]')
    monkeypatch.setattr(auth, 'USERS_FILE', path)
    for company, trap in [('Normal Export', 'bot'), (SPAM, '')]:
        response = auth.register(company, 'valid@example.test', 'strongpass', 'strongpass', '/demo', trap)
        assert response.status_code == 400
        assert 'valid@example.test' in response.body.decode()
        assert 'value="/demo"' in response.body.decode()
        assert path.read_text() == '[]'
    assert not (tmp_path / 'account_companies.json').exists()


def test_korean_company_can_register_with_empty_trap(tmp_path, monkeypatch):
    path = tmp_path / 'users.json'
    monkeypatch.setattr(auth, 'USERS_FILE', path)
    response = auth.register('한국 수출', 'owner@example.test', 'strongpass', 'strongpass', '/demo', '')
    assert response.status_code == 303
    assert json.loads(path.read_text())[0]['company'] == '한국 수출'


def test_beta_honeypot_keeps_form_entries_but_writes_nothing(tmp_path, monkeypatch):
    path = tmp_path / 'beta_applications.json'
    monkeypatch.setattr(founding_beta, 'BETA_APPLICATION_FILE', path)
    response = founding_beta.submit_founding_beta('정상 수출', '김', 'a@example.test', '한국', '', '', '', 'ko', admin_request(), 'bot')
    assert response.status_code == 409
    assert '정상 수출' in response.body.decode()
    assert '신청서를 새로 열어' in response.body.decode()
    assert not path.exists()


@pytest.mark.parametrize('path,page', list(analytics.VISITOR_PATHS.items()))
@pytest.mark.parametrize('method,status,account,count', [('GET', 200, '', 1), ('HEAD', 200, '', 0),
                                                      ('GET', 404, '', 0), ('GET', 200, 'owner', 0),
                                                      ('GET', 304, '', 0)])
def test_public_page_metrics_exclude_internal_failed_and_cached_requests(tmp_path, monkeypatch, path, page, method, status, account, count):
    file = tmp_path / 'visits.json'
    monkeypatch.setattr(analytics, 'VISITOR_ANALYTICS_FILE', file)
    async def app(scope, receive, send):
        if account:
            scope['trade_paper_user'] = {'account_id': account}
        await send({'type': 'http.response.start', 'status': status, 'headers': []})
        await send({'type': 'http.response.body', 'body': b'ok'})
    async def run():
        async def send(message):
            pass
        async def receive():
            return {'type': 'http.request', 'body': b''}
        await main.ProductAnalyticsMiddleware(app)({'type': 'http', 'method': method, 'path': path,
            'headers': [(b'referer', b'https://disquiet.io/product/private?email=secret')],
            'query_string': b'utm_source=disquiet&private=secret'}, receive, send)
    asyncio.run(run())
    rows = json.loads(file.read_text()) if file.exists() else []
    assert len(rows) == count
    if rows:
        assert rows[0]['page'] == page and rows[0]['source'] == 'Disquiet'
        assert set(rows[0]) == {'time', 'page', 'source'}
        assert 'secret' not in file.read_text()
