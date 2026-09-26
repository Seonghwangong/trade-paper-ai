from copy import deepcopy
from datetime import datetime, timedelta, timezone
from io import BytesIO
import json
from urllib.parse import parse_qs, urlsplit

import pytest

from app import paddle_live_actions as actions, paddle_live_monitor as monitor
from app.paddle_live_backup import create_backup
from tests.test_paddle_live_store import store, bound, send, event, completion, PRICE, SUB, TXN, NOW
from tests.test_paddle_live_adjustments import adjustment

CHECKED = NOW + timedelta(hours=1)


class Provider:
    def __init__(self, events=None):
        self.rows = [completion(), event(2)] if events is None else events
        self.calls = []

    def events(self, since, until):
        self.calls.append((since, until))
        return deepcopy(self.rows)


def codes(result):
    return {item['code'] for item in result['issues']}


@pytest.fixture
def monitored(store, monkeypatch):
    from app import paddle_live_backup as backup
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return CHECKED
    monkeypatch.setattr(backup, 'datetime', Clock)
    bound(store)
    send(store, event(2))
    saved = create_backup(store.path, store.path.parent / 'live.zip', price_id=PRICE)
    return store, saved


def check(monitored, **kwargs):
    store, saved = monitored
    options = {'client': Provider(), 'archive': saved['archive'],
               'archive_sha256': saved['archive_sha256'], 'now': CHECKED}
    options.update(kwargs)
    return monitor.diagnose(store.path, price_id=PRICE, **options)


def test_full_healthy_check_is_read_only_and_has_no_private_rows(monitored):
    store, saved = monitored
    raw = store.path.read_bytes()
    result = check(monitored)
    assert result['status'] == 'ok' and not result['issues']
    assert result['provider']['status'] == 'complete' and result['provider']['tracked'] == 2
    assert result['backup']['status'] == 'valid'
    assert store.path.read_bytes() == raw
    assert not any(value in json.dumps(result) for value in ('account-A', SUB, TXN, 'customer_id', '"snapshot":'))


def test_missing_provider_event_is_reported_but_never_applied(monitored):
    store, _ = monitored
    missing = adjustment(day=24)
    provider = Provider([completion(), event(2), missing])
    before = store.path.read_bytes()
    result = check(monitored, client=provider)
    assert result['status'] == 'critical' and 'provider_events_missing_locally' in codes(result)
    issue = next(i for i in result['issues'] if i['code'] == 'provider_events_missing_locally')
    assert issue['event_ids'] == [missing['event_id']]
    assert store.path.read_bytes() == before
    assert store.access_for_account('account-A', now=CHECKED).starter_access
    send(store, missing)
    result = check(monitored, client=provider)
    assert 'provider_events_missing_locally' not in codes(result)
    assert 'billing_reviews_pending' in codes(result)


def test_missing_samples_bounded_but_count_complete(monitored):
    result = check(monitored, client=Provider([event(i) for i in range(100, 130)]))
    issue = next(i for i in result['issues'] if i['code'] == 'provider_events_missing_locally')
    assert issue['count'] == 30 and len(issue['event_ids']) == 20


def test_recorded_event_receipt_must_match_provider_timestamp_and_type(monitored):
    altered = event(2)
    altered['occurred_at'] = '2026-09-24T00:01:00Z'
    result = check(monitored, client=Provider([altered]))
    assert 'provider_receipt_conflict' in codes(result)
    result = check(monitored, client=Provider([completion(n=2)]))
    assert 'provider_receipt_conflict' in codes(result)


def test_provider_grace_window_and_empty_window_do_not_infer_an_outage(monitored):
    provider = Provider([])
    result = check(monitored, client=provider)
    assert result['status'] == 'ok' and result['provider']['events'] == 0
    assert provider.calls == [((CHECKED - timedelta(hours=24)).isoformat(),
                               (CHECKED - timedelta(seconds=300)).isoformat())]


@pytest.mark.parametrize('bad', [None, {}, [event(2), event(2)], [event(2, day=25)],
    [{'event_id': 'bad', 'event_type': 'subscription.updated', 'data': {}}]])
def test_incomplete_provider_response_never_reports_healthy(monitored, bad):
    class Broken:
        def events(self, *args):
            return bad
    result = check(monitored, client=Broken())
    assert result['status'] == 'critical' and 'provider_check_incomplete' in codes(result)
    assert result['provider']['status'] in ('unavailable', 'invalid')


def test_provider_outage_redacts_error_but_keeps_local_backup_findings(monitored):
    class Broken:
        def events(self, *args):
            raise actions.ProviderUnavailable('PRIVATE-KEY-AND-CONTENT')
    result = check(monitored, client=Broken())
    assert result['backup']['status'] == 'valid' and result['ledger']['status'] == 'valid'
    assert result['status'] == 'critical' and 'PRIVATE' not in json.dumps(result)


def test_skipped_provider_or_missing_backup_is_explicit_warning(monitored):
    result = check(monitored, client=None, archive=None, archive_sha256=None)
    assert result['status'] == 'warning'
    assert codes(result) == {'provider_check_skipped', 'verified_backup_not_configured'}


def test_unknown_ownership_and_renewals_are_not_silently_skipped(monitored):
    unknown = event(3)
    unknown['data']['id'] = 'sub_' + 'z' * 26
    renewal = completion(4, id='txn_' + 'r' * 26)
    result = check(monitored, client=Provider([unknown, renewal]))
    assert result['status'] == 'warning'
    assert codes(result) == {'provider_events_without_local_ownership', 'renewal_completion_needs_reconciliation'}
    assert result['provider']['unscoped'] == result['provider']['renewal'] == 1


def test_unrelated_products_are_excluded_without_leaking_payload(monitored):
    other = event(3)
    other['data']['id'] = 'sub_' + 'z' * 26
    other['data']['items'][0]['price']['id'] = 'pri_' + 'z' * 26
    other['data']['custom_data'] = {'secret': 'PRIVATE-CONTENT'}
    result = check(monitored, client=Provider([other]))
    assert result['status'] == 'ok' and result['provider']['unrelated'] == 1
    assert 'PRIVATE' not in json.dumps(result)


def test_ambiguous_operations_age_and_confirmation_overdue(monitored):
    store, _ = monitored
    with store.connect() as db:
        db.execute("INSERT INTO live_operations VALUES ('pending', 'checkout', ?, NULL, NULL)",
                   (CHECKED.timestamp() - 901,))
        db.execute("INSERT INTO live_operations VALUES ('recent', 'checkout', ?, NULL, NULL)",
                   (CHECKED.timestamp() - 10,))
        db.execute("INSERT INTO live_operations VALUES ('account-A', 'cancel', ?, ?, 'scheduled')",
                   (CHECKED.timestamp() - 901, SUB))
    result = check(monitored)
    assert result['status'] == 'critical'
    issues = {i['code']: i for i in result['issues']}
    assert issues['ambiguous_operations_overdue']['count'] == 1
    assert issues['cancellation_confirmation_overdue']['count'] == 1
    assert 'pending' not in json.dumps(result['issues'])


def test_confirmed_cancel_does_not_report_missing_confirmation(monitored):
    store, _ = monitored
    canceled = event(3)
    canceled['data'].update(status='canceled', current_billing_period=None)
    canceled['occurred_at'] = '2026-09-24T00:10:00Z'
    send(store, canceled)
    with store.connect() as db:
        db.execute("INSERT INTO live_operations VALUES ('account-A', 'cancel', ?, ?, 'canceled')",
                   (CHECKED.timestamp() - 901, SUB))
    result = check(monitored, client=Provider([canceled]))
    assert result['status'] == 'ok'


def test_expired_active_snapshot_and_missing_snapshot_are_flagged(monitored):
    store, _ = monitored
    result = check(monitored, client=Provider([]), now=datetime(2026, 10, 2, tzinfo=timezone.utc))
    assert 'subscription_confirmation_overdue' in codes(result)
    with store.connect() as db:
        db.execute('DELETE FROM snapshots')
    assert 'bound_subscriptions_without_snapshot' in codes(check(monitored))


def test_future_operation_or_event_time_is_flagged(monitored):
    store, _ = monitored
    with store.connect() as db:
        db.execute("INSERT INTO live_operations VALUES ('future', 'checkout', ?, NULL, NULL)",
                   (CHECKED.timestamp() + 301,))
    send(store, event(3, day=25))
    result = check(monitored)
    issue = next(i for i in result['issues'] if i['code'] == 'ledger_clock_ahead')
    assert issue['count'] == 2 and issue['severity'] == 'critical'


def test_backup_staleness_future_time_missing_and_corruption(monitored):
    _, saved = monitored
    result = check(monitored, client=Provider([]), now=CHECKED + timedelta(hours=25))
    assert result['backup']['status'] == 'stale' and 'backup_too_old' in codes(result)
    result = check(monitored, client=Provider([]), now=CHECKED - timedelta(minutes=10))
    assert result['backup']['status'] == 'future_dated'
    result = check(monitored, archive_sha256='0' * 64)
    assert result['backup']['status'] == 'unavailable_or_invalid'
    result = check(monitored, archive=saved['archive'] + '.missing')
    assert 'backup_unavailable_or_invalid' in codes(result)


def test_missing_and_invalid_ledger_do_not_create_or_migrate(tmp_path, store):
    path = tmp_path / 'missing.sqlite3'
    result = monitor.diagnose(path, price_id=PRICE, now=CHECKED)
    assert result['status'] == 'critical' and not path.exists()
    with store.connect() as db:
        db.execute("UPDATE paddle_live_meta SET value='sandbox' WHERE key='environment'")
    raw = store.path.read_bytes()
    result = monitor.diagnose(store.path, price_id=PRICE, now=CHECKED)
    assert result['status'] == 'critical' and store.path.read_bytes() == raw


def test_event_transport_is_get_only_bounded_and_uses_fixed_query(monkeypatch):
    calls = []
    pages = iter([
        {'data': [event(1)], 'meta': {'pagination': {'has_more': True, 'next': 'https://evil.test'}}},
        {'data': [event(2)], 'meta': {'pagination': {'has_more': False}}},
    ])
    class Response(BytesIO):
        status = 200
    class Opener:
        def open(self, req, timeout):
            calls.append(req)
            assert req.get_method() == 'GET' and timeout == 15
            return Response(json.dumps(next(pages)).encode())
    monkeypatch.setattr(actions, 'build_opener', lambda *args: Opener())
    result = actions.LiveClient('pdl_live_apikey_synthetic').events(NOW.isoformat(), CHECKED.isoformat())
    assert len(result) == 2
    for request in calls:
        url = urlsplit(request.full_url)
        assert url.netloc == 'api.paddle.com' and url.path == '/events'
        query = parse_qs(url.query)
        assert query['from'] == [NOW.isoformat()] and query['to'] == [CHECKED.isoformat()]
        assert query['per_page'] == ['20'] and query['order_by'] == ['id[ASC]']
    assert parse_qs(urlsplit(calls[1].full_url).query)['after'] == [event(1)['event_id']]


@pytest.mark.parametrize('page', [
    {'data': [], 'meta': {'pagination': {'has_more': True}}},
    {'data': [event(1), event(1)], 'meta': {'pagination': {'has_more': False}}},
    {'data': [event(1, day=25)], 'meta': {'pagination': {'has_more': False}}},
    {'data': [], 'meta': {'pagination': {'has_more': 'false'}}},
    {'data': [event(1, kind='api_key.created')], 'meta': {'pagination': {'has_more': False}}},
])
def test_event_transport_rejects_partial_or_malformed_pages(monkeypatch, page):
    client = actions.LiveClient('pdl_live_apikey_synthetic')
    monkeypatch.setattr(client, '_response', lambda *args: page)
    with pytest.raises(actions.ProviderUnavailable):
        client.events(NOW.isoformat(), CHECKED.isoformat())


def test_event_transport_page_and_time_caps_fail_closed(monkeypatch):
    client = actions.LiveClient('pdl_live_apikey_synthetic')
    pages = iter(range(1, 51))
    monkeypatch.setattr(client, '_response', lambda *args: {
        'data': [event(next(pages))], 'meta': {'pagination': {'has_more': True}}})
    with pytest.raises(actions.ProviderUnavailable):
        client.events(NOW.isoformat(), CHECKED.isoformat())
    ticks = iter([0, 61])
    monkeypatch.setattr(actions.time, 'monotonic', lambda: next(ticks))
    with pytest.raises(actions.ProviderUnavailable):
        client.events(NOW.isoformat(), CHECKED.isoformat())


@pytest.mark.parametrize('options', [{'lookback_hours': 2161}, {'grace_seconds': 0},
    {'backup_age_hours': 0}, {'operation_age_seconds': -1}, {'now': datetime(2026, 9, 24)},
    {'lookback_hours': 1, 'grace_seconds': 3600}])
def test_invalid_thresholds_are_rejected(monitored, options):
    with pytest.raises(ValueError):
        check(monitored, **options)


def test_cli_exit_codes_and_default_off_provider(monitored, monkeypatch, capsys):
    store, saved = monitored
    args = ['--ledger', str(store.path), '--price-id', PRICE, '--backup', saved['archive'],
            '--backup-sha256', saved['archive_sha256']]
    run = monitor.diagnose
    monkeypatch.setattr(monitor, 'diagnose', lambda *args, **kw: run(*args, **kw, now=CHECKED))
    assert monitor.main(args) == 1
    assert json.loads(capsys.readouterr().out)['provider']['status'] == 'skipped'
    monkeypatch.delenv('TRADE_PAPER_PADDLE_LIVE_MONITOR', raising=False)
    with pytest.raises(SystemExit) as caught:
        monitor.main(args + ['--with-provider'])
    assert caught.value.code == 2
    capsys.readouterr()
    monkeypatch.setenv('TRADE_PAPER_PADDLE_LIVE_MONITOR', '1')
    monkeypatch.setattr(monitor, 'LiveClient', lambda _: Provider())
    assert monitor.main(args + ['--with-provider']) == 0
    assert json.loads(capsys.readouterr().out)['status'] == 'ok'
    assert monitor.main(['--ledger', 'PRIVATE-MISSING', '--price-id', PRICE]) == 2
    assert 'PRIVATE-MISSING' not in capsys.readouterr().out
