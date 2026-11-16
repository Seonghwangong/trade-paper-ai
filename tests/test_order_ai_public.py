import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
import json
import sqlite3
from uuid import uuid4

import pytest
from fastapi import HTTPException
from app import order_ai as ai, order_ai_usage as usage, paddle_live_runtime as runtime
from app.paddle_subscription_policy import AccessDecision
from tests.test_order_ai import response_data, request, body


@pytest.fixture
def public(monkeypatch, tmp_path):
    monkeypatch.setenv('TRADE_PAPER_ORDER_AI_ENABLED', '1')
    monkeypatch.setenv('TRADE_PAPER_ORDER_AI_PUBLIC', '1')
    monkeypatch.setenv('TRADE_PAPER_ORDER_AI_API_KEY', 'offline-test-placeholder')
    monkeypatch.setenv('TRADE_PAPER_PADDLE_LIVE_ACCESS', '1')
    monkeypatch.setattr(ai, 'USAGE_FILE', tmp_path / 'usage.sqlite3')
    monkeypatch.setattr(runtime, 'account_state', lambda *a, **k: (False, None))
    async def no_network(*a):
        pytest.fail('Unexpected external AI request')
    monkeypatch.setattr(ai, '_generate', no_network)
    return ai.USAGE_FILE


def call_data():
    return body() | {'request_id': str(uuid4())}


def test_public_requires_login_server_switch_and_key(public, monkeypatch):
    assert ai.available('new-user') and not ai.available('')
    monkeypatch.delenv('TRADE_PAPER_ORDER_AI_API_KEY')
    assert not ai.available('new-user') and not ai.public_enabled()


def test_free_lifetime_success_and_failure_have_separate_budget(public, monkeypatch):
    calls = []
    async def generate(text):
        calls.append(text)
        if len(calls) == 1:
            raise HTTPException(502, 'ai_unavailable')
        return response_data()
    monkeypatch.setattr(ai, '_generate', generate)
    with pytest.raises(HTTPException):
        asyncio.run(ai.extract(request({}), 'free', call_data()))
    assert usage.summary(public, 'free')['remaining'] == 1
    result = asyncio.run(ai.extract(request({}), 'free', call_data()))
    assert result['ai_usage']['remaining'] == 0
    with pytest.raises(HTTPException) as err:
        asyncio.run(ai.extract(request({}), 'free', call_data()))
    assert err.value.detail == 'ai_allowance' and len(calls) == 2
    with sqlite3.connect(public) as db:
        assert db.execute('SELECT count(*) FROM requests').fetchone()[0] == 2
        # Advancing the global UTC month cannot reset the one-time trial.
        db.execute('DELETE FROM requests')
    assert usage.summary(public, 'free')['remaining'] == 0
    assert usage.summary(public, 'other')['remaining'] == 1


def test_duplicate_and_concurrent_submission_never_reserve_twice(public):
    request_id = str(uuid4())
    first = usage.reserve(public, 'a', request_id)
    with pytest.raises(HTTPException) as err:
        usage.reserve(public, 'a', request_id)
    assert err.value.detail == 'ai_duplicate'
    with pytest.raises(HTTPException) as err:
        usage.reserve(public, 'a', str(uuid4()))
    assert err.value.detail == 'ai_busy'
    usage.finish(public, first, success=True)
    # The same UUID belongs to a separate account namespace.
    assert usage.reserve(public, 'b', request_id) != first


def test_parallel_free_calls_allow_one_reservation(public):
    def attempt(_):
        try:
            return usage.reserve(public, 'a', str(uuid4()))
        except HTTPException as err:
            assert err.detail in ('ai_busy', 'ai_allowance')
    with ThreadPoolExecutor(max_workers=5) as pool:
        assert sum(bool(x) for x in pool.map(attempt, range(5))) == 1


def test_paid_period_cancellation_and_renewal(public, monkeypatch):
    start = datetime(2026, 11, 5, tzinfo=timezone.utc)
    state = [AccessDecision(True, 'active', start + timedelta(days=30), False, start)]
    monkeypatch.setattr(runtime, 'account_state', lambda *a, **k: (True, state[0]))
    assert usage.summary(public, 'paid')['limit'] == 20
    token = usage.reserve(public, 'paid', str(uuid4()))
    usage.finish(public, token, success=True)
    # Cancellation changes the end, not the quota bucket.
    state[0] = AccessDecision(True, 'active', start + timedelta(days=29), True, start)
    assert usage.summary(public, 'paid')['remaining'] == 19
    state[0] = AccessDecision(False, 'past_due', None, False)
    assert usage.summary(public, 'paid')['kind'] == 'free'
    state[0] = AccessDecision(True, 'active', start + timedelta(days=60), False, start + timedelta(days=30))
    assert usage.summary(public, 'paid')['remaining'] == 20


def test_starter_allowance_20_does_not_expand_service_budget(public, monkeypatch):
    start = datetime(2026, 11, 1, tzinfo=timezone.utc)
    monkeypatch.setattr(runtime, 'account_state', lambda *a, **k:
        (True, AccessDecision(True, 'active', start + timedelta(days=30), False, start)))
    token = usage.reserve(public, 'paid', str(uuid4()))
    usage.finish(public, token, success=True)
    with sqlite3.connect(public) as db:
        row = db.execute('SELECT owner,bucket,started FROM public_ai_requests').fetchone()
        db.executemany('INSERT INTO public_ai_requests VALUES (?,?,?,?,?)',
                       [(str(i), *row, 'succeeded') for i in range(19)])
    with pytest.raises(HTTPException) as err:
        usage.reserve(public, 'paid', str(uuid4()))
    assert err.value.detail == 'ai_allowance'


def test_global_beta_request_cap_includes_failed_calls(public):
    for i in range(5):
        token = usage.reserve(public, str(i), str(uuid4()))
        usage.finish(public, token, success=False)
    with pytest.raises(HTTPException) as err:
        usage.reserve(public, 'sixth', str(uuid4()))
    assert err.value.detail == 'ai_limit'
    assert usage.summary(public, 'sixth')['remaining'] == 1


def test_crash_reservation_does_not_silently_refund(public):
    usage.reserve(public, 'a', str(uuid4()))
    with sqlite3.connect(public) as db:
        db.execute('UPDATE public_ai_requests SET started=0')
    with pytest.raises(HTTPException) as err:
        usage.reserve(public, 'a', str(uuid4()))
    assert err.value.detail == 'ai_allowance'


def test_invalid_request_id_or_consent_makes_no_request_log(public):
    data = call_data(); data['request_id'] = 'bad'
    with pytest.raises(HTTPException) as err:
        asyncio.run(ai.extract(request({}), 'a', data))
    assert err.value.detail == 'ai_request_id'
    assert not public.exists()


def test_unavailable_billing_does_not_break_standard_import_bootstrap(public, monkeypatch):
    def fail(*a, **k):
        raise runtime.LiveBillingUnavailable()
    monkeypatch.setattr(runtime, 'account_state', fail)
    assert ai.usage('a') == {'unavailable': True}
    with pytest.raises(HTTPException):
        usage.reserve(public, 'a', str(uuid4()))
    assert not public.exists()


def test_landing_only_announces_enabled_public_beta(public, monkeypatch):
    from app.landing import landing_page
    page = landing_page('ko').body.decode()
    assert 'AI 읽기 최초 1회 포함' in page and '결제주기당 AI 읽기 20회' in page
    assert '전체 월 20회' in page and '/import-document?lang=ko' in page
    monkeypatch.delenv('TRADE_PAPER_ORDER_AI_PUBLIC')
    assert 'AI 읽기 최초 1회 포함' not in landing_page('ko').body.decode()
