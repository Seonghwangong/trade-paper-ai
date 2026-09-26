from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timezone
from io import BytesIO
import json
import sqlite3

import pytest

from app import paddle_live_actions as actions, paddle_live_reconcile as recovery
from app.paddle_live_store import PaddleLiveStore
from tests.test_paddle_live_store import store, bound, send, event, completion, OFFER, NOW, SUB, CUSTOMER, TXN, PRICE
from tests.test_paddle_live_adjustments import adjustment, evidence
from tests.test_paddle_subscription_policy import snapshot
from tests.test_paddle_live_manage import ready, http


class Provider:
    def __init__(self):
        self.sub = snapshot()
        self.adjusted = [adjustment(action='chargeback', status='reversed')['data']]
        self.payment = completion()['data']
        self.calls = []
        self.hook = lambda: None

    def subscription(self, identifier):
        assert identifier == SUB
        self.calls.append(('subscription', identifier))
        return deepcopy(self.sub)

    def adjustments(self, identifier):
        assert identifier == SUB
        self.calls.append(('adjustments', identifier))
        return deepcopy(self.adjusted)

    def transaction(self, identifier):
        assert identifier == TXN
        self.calls.append(('transaction', identifier))
        self.hook()
        return deepcopy(self.payment)


@pytest.fixture
def held(store):
    bound(store)
    send(store, event(2))
    send(store, adjustment(action='chargeback'))
    return store, Provider()


def review(held, **kwargs):
    store, provider = held
    return recovery.reconcile(store, provider, OFFER, 'account-A',
                              operator='billing-operator', case='case-42', now=NOW, **kwargs)


def audit(store):
    with store.connect() as db:
        return (db.execute('SELECT * FROM live_review_releases').fetchall(),
                db.execute('SELECT * FROM live_review_coverage').fetchall())


def test_preview_is_byte_preserving_and_apply_audited_without_altering_signed_history(held):
    store, provider = held
    before, raw = evidence(store), store.path.read_bytes()
    ro = PaddleLiveStore(store.path, price_id=PRICE, environment='live', read_only=True)
    preview = review((ro, provider))
    assert preview['result'] == 'preview' and preview['review_events'] == 1
    assert store.path.read_bytes() == raw
    assert not store.access_for_account('account-A', now=NOW).starter_access
    assert review(held, expected=preview['digest'])['result'] == 'released'
    assert evidence(store) == before
    rows, coverage = audit(store)
    assert rows[0][1:5] == ('account-A', SUB, 'billing-operator', 'case-42')
    recorded = json.loads(rows[0][7])
    assert recorded['policy'] == 'restored-payment-v1'
    assert recorded['adjustments']['adj_' + 'a' * 26]['status'] == 'reversed'
    assert coverage == [('evt_' + f'{10:026d}', preview['digest'])]
    assert ro.access_for_account('account-A', now=NOW).starter_access
    assert not ro.access_for_account('account-A', now=datetime(2026, 10, 1, tzinfo=timezone.utc)).starter_access
    # Exactly two subscription/history/payment rounds per invocation, read only.
    assert len(provider.calls) == 12


def test_release_covers_event_ids_and_new_delayed_evidence_reholds(held):
    store, _ = held
    review(held, expected=review(held)['digest'])
    assert send(store, adjustment(action='chargeback')) == 'duplicate'
    assert store.access_for_account('account-A', now=NOW).starter_access
    send(store, adjustment(11, action='chargeback', status='reversed', day=24))
    assert not store.access_for_account('account-A', now=NOW).starter_access
    review(held, expected=review(held)['digest'])
    assert store.access_for_account('account-A', now=NOW).starter_access
    assert len(audit(store)[0]) == 2 and len(audit(store)[1]) == 2


@pytest.mark.parametrize('action,status', [('refund', 'approved'), ('credit', 'approved'),
    ('chargeback', 'approved'), ('chargeback_warning', 'approved'), ('refund', 'pending_approval')])
def test_unresolved_provider_history_blocks_even_without_local_notification(held, action, status):
    store, provider = held
    provider.adjusted.append(adjustment(id='adj_' + 'z' * 26, action=action, status=status)['data'])
    with pytest.raises(recovery.ReviewBlocked):
        review(held)
    assert audit(store) == ([], [])
    assert not store.access_for_account('account-A', now=NOW).starter_access


def test_approved_reversal_requires_reversed_original_and_fully_restored_payment(held):
    store, provider = held
    provider.adjusted.append(adjustment(id='adj_' + 'z' * 26, action='chargeback_reverse')['data'])
    assert review(held)['result'] == 'preview'
    provider.adjusted[0]['status'] = 'rejected'
    with pytest.raises(recovery.ReviewBlocked):
        review(held)
    provider.adjusted[0]['status'] = 'reversed'
    provider.payment['details']['adjusted_totals']['grand_total'] = '0'
    with pytest.raises(recovery.ReviewBlocked):
        review(held)
    assert audit(store) == ([], [])


@pytest.mark.parametrize('change', ['missing', 'duplicate', 'wrong-customer', 'wrong-subscription',
    'wrong-transaction', 'wrong-action', 'wrong-currency', 'bad-total', 'unknown-status'])
def test_canonical_evidence_must_be_complete_and_owned(held, change):
    store, provider = held
    item = provider.adjusted[0]
    if change == 'missing':
        provider.adjusted = []
    elif change == 'duplicate':
        provider.adjusted.append(deepcopy(item))
    else:
        field, value = {'wrong-customer': ('customer_id', 'ctm_' + 'z' * 26),
            'wrong-subscription': ('subscription_id', 'sub_' + 'z' * 26),
            'wrong-transaction': ('transaction_id', 'txn_' + 'z' * 26),
            'wrong-action': ('action', 'credit'), 'wrong-currency': ('currency_code', 'USD'),
            'bad-total': ('totals', {'currency_code': 'KRW', 'total': True}),
            'unknown-status': ('status', 'unknown')}[change]
        item[field] = value
    with pytest.raises(recovery.ReviewBlocked):
        review(held)
    assert audit(store) == ([], [])


@pytest.mark.parametrize('field,value', [('status', 'canceled'), ('status', 'past_due'),
    ('customer_id', 'ctm_' + 'z' * 26),
    ('scheduled_change', {'action': 'cancel', 'effective_at': '2026-10-01T00:00:00Z'})])
def test_subscription_must_be_active_and_match_signed_state(held, field, value):
    store, provider = held
    provider.sub[field] = value
    with pytest.raises(recovery.ReviewBlocked):
        review(held)
    assert audit(store) == ([], [])


@pytest.mark.parametrize('field,value', [('customer_id', 'ctm_' + 'z' * 26), ('status', 'paid'),
                                       ('subscription_id', 'sub_' + 'z' * 26), ('payments', [])])
def test_canonical_payment_must_be_fully_captured_and_owned(held, field, value):
    store, provider = held
    provider.payment[field] = value
    with pytest.raises(recovery.ReviewBlocked):
        review(held)
    assert audit(store) == ([], [])


def test_operator_preview_digest_cannot_cross_account_or_case(held):
    store, provider = held
    digest = review(held)['digest']
    with pytest.raises(recovery.ReviewBlocked):
        recovery.reconcile(store, provider, OFFER, 'account-B', operator='billing-operator',
                            case='case-42', expected=digest, now=NOW)
    with pytest.raises(recovery.ReviewBlocked):
        recovery.reconcile(store, provider, OFFER, 'account-A', operator='billing-operator',
                            case='case-other', expected=digest, now=NOW)
    assert audit(store) == ([], [])


def test_apply_refetches_provider_and_rejects_stale_preview(held):
    store, provider = held
    digest = review(held)['digest']
    provider.adjusted[0]['status'] = 'rejected'
    with pytest.raises(recovery.ReviewBlocked, match='Preview changed'):
        review(held, expected=digest)
    assert audit(store) == ([], [])


def test_provider_change_between_scans_is_not_released(held):
    store, provider = held
    provider.hook = lambda: provider.adjusted[0].update(status='rejected')
    with pytest.raises(recovery.ReviewBlocked, match='Provider state changed'):
        review(held)
    assert audit(store) == ([], [])


@pytest.mark.parametrize('during', ['adjustment', 'subscription'])
def test_webhook_racing_provider_reads_invalidates_release(held, during):
    store, provider = held
    digest = review(held)['digest']
    provider.hook = lambda: send(store, adjustment(11, action='chargeback', day=26)
                                if during == 'adjustment' else event(3, day=26))
    with pytest.raises(recovery.ReviewBlocked, match='Local evidence changed'):
        review(held, expected=digest)
    assert audit(store) == ([], [])


def test_provider_outage_leaves_evidence_and_hold_intact(held):
    store, provider = held
    before = evidence(store)
    def fail():
        raise actions.ProviderUnavailable('provider unavailable')
    provider.hook = fail
    with pytest.raises(actions.ProviderUnavailable):
        review(held)
    assert evidence(store) == before and audit(store) == ([], [])


def test_audit_stores_review_evidence_without_raw_customer_or_card_details(held):
    store, provider = held
    provider.adjusted[0]['reason'] = 'PRIVATE-REASON'
    provider.sub['custom_data'] = {'private': 'PRIVATE-CUSTOMER'}
    provider.payment['payments'][0]['method_details'] = {'secret': 'PRIVATE-CARD'}
    review(held, expected=review(held)['digest'])
    recorded = str(audit(store))
    assert not any(text in recorded for text in ('PRIVATE-REASON', 'PRIVATE-CUSTOMER', 'PRIVATE-CARD'))


def test_partial_audit_write_failure_rolls_back_release(held):
    store, _ = held
    digest = review(held)['digest']
    with store.connect() as db:
        db.execute("CREATE TRIGGER reject_coverage BEFORE INSERT ON live_review_coverage "
                   "BEGIN SELECT RAISE(ABORT, 'injected'); END")
    with pytest.raises(sqlite3.IntegrityError):
        review(held, expected=digest)
    assert audit(store) == ([], [])
    assert not store.access_for_account('account-A', now=NOW).starter_access


def test_concurrent_release_writes_one_atomic_audit(held):
    store, _ = held
    digest = review(held)['digest']
    def apply(_):
        try:
            return review((store, Provider()), expected=digest)['result']
        except recovery.ReviewBlocked:
            return 'blocked'
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(apply, range(16)))
    assert results.count('released') == 1 and results.count('blocked') == 15
    assert len(audit(store)[0]) == len(audit(store)[1]) == 1


def test_legacy_review_hold_remains_until_additive_migration(held):
    store, provider = held
    with store.connect() as db:
        db.execute('DROP TABLE live_review_coverage')
        db.execute('DROP TABLE live_review_releases')
    raw = store.path.read_bytes()
    ro = PaddleLiveStore(store.path, price_id=PRICE, environment='live', read_only=True)
    assert not ro.access_for_account('account-A', now=NOW).starter_access
    digest = review((ro, provider))['digest']
    assert store.path.read_bytes() == raw
    migrated = PaddleLiveStore(store.path, price_id=PRICE, environment='live')
    review((migrated, provider), expected=digest)
    assert ro.access_for_account('account-A', now=NOW).starter_access


def test_expiry_during_provider_fetch_cannot_release(held, monkeypatch):
    ticks = iter([0, 1, 2])
    monkeypatch.setattr(recovery.time, 'monotonic', lambda: next(ticks))
    with pytest.raises(recovery.ReviewBlocked, match='expired'):
        recovery.reconcile(*held, OFFER, 'account-A', operator='billing-operator', case='case-42',
                            now=datetime(2026, 9, 30, 23, 59, 59, tzinfo=timezone.utc))
    assert audit(held[0]) == ([], [])


def test_slow_review_fails_closed(held, monkeypatch):
    ticks = iter([0, 91])
    monkeypatch.setattr(recovery.time, 'monotonic', lambda: next(ticks))
    with pytest.raises(recovery.ReviewBlocked, match='freshness'):
        review(held)


def test_freshness_is_rechecked_after_acquiring_write_lock(held, monkeypatch):
    digest = review(held)['digest']
    ticks = iter([0, 89, 91])
    monkeypatch.setattr(recovery.time, 'monotonic', lambda: next(ticks))
    with pytest.raises(recovery.ReviewBlocked, match='freshness'):
        review(held, expected=digest)
    assert audit(held[0]) == ([], [])


def test_http_management_returns_to_active_without_provider_mutation(ready):
    from app import paddle_live_manage as manage
    send(ready.store, adjustment(action='chargeback'))
    provider = Provider()
    # Ready fixture uses the same signed subscription fields.
    with ready.store.connect() as db:
        provider.sub = json.loads(db.execute('SELECT snapshot FROM snapshots').fetchone()[0])
    def run(expected=None):
        return recovery.reconcile(ready.store, provider, OFFER, 'A', operator='billing-operator',
                                   case='case-42', expected=expected, now=NOW)
    assert http(manage.PATH + '/status').json()['phase'] == 'review'
    run(run()['digest'])
    state = http(manage.PATH + '/status').json()
    assert state['starter_access'] and not state['billing_review'] and state['can_cancel']
    assert ready.client.cancels == 0


def test_paginated_provider_scan_uses_fixed_host_and_cursor_not_next_url(monkeypatch):
    paths = []
    pages = iter([
        {'data': [{'id': 'adj_' + 'a' * 26}], 'meta': {'pagination': {'has_more': True, 'next': 'https://evil.test'}}},
        {'data': [{'id': 'adj_' + 'b' * 26}], 'meta': {'pagination': {'has_more': False}}},
    ])
    class Response(BytesIO):
        status = 200
    class Opener:
        def open(self, req, timeout):
            paths.append(req.full_url)
            assert req.get_method() == 'GET' and timeout == 15
            return Response(json.dumps(next(pages)).encode())
    monkeypatch.setattr(actions, 'build_opener', lambda *args: Opener())
    result = actions.LiveClient('pdl_live_apikey_synthetic').adjustments(SUB)
    assert len(result) == 2
    assert all(p.startswith(actions.API + '/adjustments?subscription_id=' + SUB) for p in paths)
    assert paths[1].endswith('&after=adj_' + 'a' * 26)


@pytest.mark.parametrize('page', [
    {'data': [], 'meta': {'pagination': {'has_more': True}}},
    {'data': [], 'meta': {'pagination': {'has_more': 'false'}}},
    {'data': [], 'meta': {}},
    {'data': [{'id': 'adj_' + 'a' * 26}] * 2, 'meta': {'pagination': {'has_more': False}}},
    {'data': [{'id': '../unsafe'}], 'meta': {'pagination': {'has_more': False}}},
])
def test_incomplete_or_invalid_pagination_fails_closed(monkeypatch, page):
    client = actions.LiveClient('pdl_live_apikey_synthetic')
    monkeypatch.setattr(client, '_response', lambda *args: deepcopy(page))
    with pytest.raises(actions.ProviderUnavailable):
        client.adjustments(SUB)


def test_pagination_cap_does_not_return_partial_evidence(monkeypatch):
    client = actions.LiveClient('pdl_live_apikey_synthetic')
    pages = iter(range(20))
    monkeypatch.setattr(client, '_response', lambda *args: {
        'data': [{'id': 'adj_' + f'{next(pages):026d}'}], 'meta': {'pagination': {'has_more': True}}})
    with pytest.raises(actions.ProviderUnavailable):
        client.adjustments(SUB)


def test_cli_default_off_does_not_open_store_or_call_provider(monkeypatch, capsys):
    monkeypatch.delenv('TRADE_PAPER_PADDLE_LIVE_RECONCILE', raising=False)
    monkeypatch.setattr(recovery, 'LiveClient', lambda _: pytest.fail('provider opened'))
    with pytest.raises(SystemExit) as caught:
        recovery.main(['--account', 'A', '--operator', 'billing', '--case', 'case-1'])
    assert caught.value.code == 2 and 'disabled' in capsys.readouterr().err


def test_cli_preview_and_apply_use_existing_store_and_redact_failures(held, monkeypatch, capsys):
    from app import paddle_live_runtime as runtime
    store, provider = held
    monkeypatch.setenv('TRADE_PAPER_PADDLE_LIVE_RECONCILE', '1')
    monkeypatch.setattr(runtime, 'store', lambda **kw: PaddleLiveStore(
        store.path, price_id=PRICE, environment='live', read_only=kw.get('read_only', False)))
    monkeypatch.setattr(runtime, 'offer', lambda: OFFER)
    monkeypatch.setattr(recovery, 'LiveClient', lambda _: provider)
    run = recovery.reconcile
    monkeypatch.setattr(recovery, 'reconcile', lambda *args, **kw: run(*args, **kw, now=NOW))
    args = ['--account', 'account-A', '--operator', 'billing-operator', '--case', 'case-42']
    recovery.main(args)
    preview = json.loads(capsys.readouterr().out)
    assert audit(store) == ([], [])
    recovery.main(args + ['--apply', preview['digest']])
    assert json.loads(capsys.readouterr().out)['result'] == 'released'
    def fail(**kwargs):
        raise ValueError('PRIVATE-CONFIG')
    monkeypatch.setattr(runtime, 'store', fail)
    with pytest.raises(SystemExit) as caught:
        recovery.main(args)
    output = capsys.readouterr()
    assert caught.value.code == 2 and 'PRIVATE-CONFIG' not in output.err
