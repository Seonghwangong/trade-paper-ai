"""Synthetic whole-lifecycle drills through the app's ASGI routes.

Provider transport is in memory; every ledger/restore lives in pytest tmp_path.
No real credentials, requests, money, production flags or JSON writes.
"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import hashlib
import hmac
import json
import time

import pytest

from app import subscription
from app import paddle_live_checkout as buy, paddle_live_manage as manage
from app import paddle_live_runtime as runtime
from app import paddle_live_operation_recovery as operations
from app import paddle_live_reconcile as reviews
from app.paddle_live_actions import LiveActions, ProviderUnavailable
from app.paddle_live_backup import create_backup, stage_restore, inspect_ledger
from app.paddle_live_store import PaddleLiveStore
from tests.test_paddle_live_checkout import ready, headers as checkout_headers
from tests.test_paddle_live_actions import offer_config
from tests.test_paddle_live_store import store, completion, event, OFFER, NOW, PRICE, TXN
from tests.test_paddle_live_manage import http, headers as cancel_headers
from tests.test_paddle_live_adjustments import adjustment
from tests.test_paddle_live_renewals import renewal, RENEWAL


@pytest.fixture
def journey(ready, monkeypatch, tmp_path):
    ledger, provider, user = ready
    state = SimpleNamespace(store=ledger, provider=provider, user=user, now=NOW,
                            restores=0, root=tmp_path)
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return state.now
    monkeypatch.setattr(manage, 'datetime', Clock)
    monkeypatch.setattr(runtime, 'data_path', lambda name: state.store.path)
    monkeypatch.setattr(buy, 'configured_service', lambda operation: SimpleNamespace(
        checkout=lambda account: LiveActions(state.store, provider).checkout(account, now=state.now.timestamp())))
    monkeypatch.setattr(manage, 'configured_service', lambda operation: SimpleNamespace(
        cancel=lambda account: LiveActions(state.store, provider).cancel(account, now=state.now)))
    state.json_before = {p: p.read_bytes() for p in (
        subscription.USERS_FILE, subscription.BILLING_HISTORY_FILE, subscription.USAGE_EVENTS_FILE)}
    # Any accidentally introduced real provider request must fail the drill.
    monkeypatch.setattr('app.paddle_live_actions.LiveClient._request',
                        lambda *args, **kwargs: pytest.fail('Unexpected external provider request'))
    return state


def deliver(payload, *, expected=200):
    raw = json.dumps(payload).encode()
    stamp = str(int(time.time()))
    sig = hmac.new(b'synthetic-live-secret', stamp.encode() + b':' + raw, hashlib.sha256).hexdigest()
    response = http('/webhooks/paddle-live', 'POST',
                    {'Paddle-Signature': f'ts={stamp};h1={sig}'}, raw)
    assert response.status == expected, response.text
    return response.json().get('result')


def status():
    response = http(manage.PATH + '/status')
    assert response.status == 200, response.text
    return response.json()


def access(state, wanted):
    assert status()['starter_access'] is wanted
    assert (subscription.subscription_for_account('A', now=state.now)['plan'] == 'Starter') is wanted
    assert state.store.access_for_account('unrelated', now=state.now) is None
    assert {p: p.read_bytes() for p in state.json_before} == state.json_before


def restore(state):
    """Explicitly switch ONLY this test's temporary runtime to the staged copy."""
    state.restores += 1
    root = state.root / f'restore-{state.restores}'
    saved = create_backup(state.store.path, state.root / f'ledger-{state.restores}.zip', price_id=PRICE)
    stage_restore(saved['archive'], root, price_id=PRICE, expected_sha256=saved['archive_sha256'])
    assert json.loads((root / 'RESTORE_NOT_ACTIVATED.json').read_text())['activated'] is False
    state.store = PaddleLiveStore(root / 'paddle_live.sqlite3', price_id=PRICE, environment='live')
    return inspect_ledger(state.store.path, PRICE)['tables']


def recover_operation(state, monkeypatch, kind):
    with monkeypatch.context() as maintenance:
        maintenance.setenv('TRADE_PAPER_PADDLE_LIVE_OPERATION_RECOVERY', '1')
        maintenance.setenv('TRADE_PAPER_PADDLE_LIVE_ACCESS', '0')
        maintenance.setenv('TRADE_PAPER_PADDLE_LIVE_CHECKOUT', '0')
        kwargs = dict(operator='synthetic-operator', case='lifecycle-drill', now=state.now)
        if kind == 'checkout':
            kwargs['target'] = TXN
        preview = operations.recover(state.store, state.provider, OFFER, 'A', kind, **kwargs)
        result = operations.recover(state.store, state.provider, OFFER, 'A', kind,
                                    expected=preview['digest'], **kwargs)
        assert result['result'] == 'recovered'
    return result


@pytest.mark.parametrize('uncertain_checkout', [False, True], ids=['checkout-ok', 'checkout-timeout'])
@pytest.mark.parametrize('subscription_first', [False, True], ids=['payment-first', 'subscription-first'])
@pytest.mark.parametrize('renewal_first', [False, True], ids=['period-first', 'renewal-first'])
def test_checkout_renewal_dispute_restore_and_ambiguous_cancel(
        journey, monkeypatch, uncertain_checkout, subscription_first, renewal_first):
    s = journey
    provider = s.provider
    original_create = provider.create_checkout
    def create(price, *, intent):
        result = original_create(price, intent=intent)
        provider.checkout_data.update(origin='api', created_at=s.now.isoformat(), payments=[])
        if uncertain_checkout:
            raise ProviderUnavailable('Synthetic lost checkout response')
        return result
    provider.create_checkout = create
    response = http(buy.PATH, 'POST', checkout_headers())
    assert response.status == (502 if uncertain_checkout else 200), response.text
    assert provider.creates == 1
    access(s, False)
    if uncertain_checkout:
        correlation = provider.checkout_data['custom_data']
        created = provider.checkout_data['created_at']
        provider.checkout_data = completion()['data']
        provider.checkout_data.update(custom_data=correlation, origin='api', created_at=created)
        deliver(completion(), expected=409)
        restore(s)
        assert http(buy.PATH, 'POST', checkout_headers()).status == 409
        assert provider.creates == 1
        assert recover_operation(s, monkeypatch, 'checkout')['provider_result'] == 'awaiting_completion'
        access(s, False)
    if subscription_first:
        deliver(event(2), expected=409)
    assert deliver(completion()) == 'bound'
    access(s, False)
    assert deliver(event(2)) == 'applied'
    access(s, True)
    assert deliver(completion()) == 'duplicate'
    assert deliver(event(2)) == 'duplicate'
    restore(s)
    access(s, True)

    # A new signed period is insufficient without its own captured payment.
    s.now = datetime(2026, 10, 1, tzinfo=timezone.utc)
    next_payment = renewal(20)
    next_payment['occurred_at'] = s.now.isoformat()
    next_period = event(21)
    next_period['occurred_at'] = s.now.isoformat()
    next_period['data']['current_billing_period'] = deepcopy(next_payment['data']['billing_period'])
    provider.current = deepcopy(next_period['data'])
    if renewal_first:
        assert deliver(next_payment) == 'renewal_recorded'
        access(s, False)  # Old signed period expired.
        assert deliver(next_period) == 'applied'
    else:
        assert deliver(next_period) == 'applied'
        access(s, False)
        assert status()['phase'] == 'awaiting_payment'
        assert deliver(next_payment) == 'renewal_recorded'
    access(s, True)
    assert deliver(next_payment) == 'duplicate'

    # A chargeback on the renewal remains held across signed redelivery/restore.
    s.now += timedelta(hours=1)
    dispute = adjustment(30, action='chargeback', transaction_id=RENEWAL)
    dispute['occurred_at'] = s.now.isoformat()
    assert deliver(dispute) == 'adjustment_review'
    access(s, False)
    assert status()['phase'] == 'review'
    restore(s)
    assert deliver(next_payment) == 'duplicate'
    access(s, False)
    reversed_dispute = deepcopy(dispute)
    reversed_dispute['data']['status'] = 'reversed'
    provider.adjustments = lambda sub: [deepcopy(reversed_dispute['data'])]
    payments = {TXN: completion()['data'], RENEWAL: next_payment['data']}
    provider.transaction = lambda txn: deepcopy(payments[txn])
    kwargs = dict(operator='synthetic-operator', case='reversed-chargeback', now=s.now)
    preview = reviews.reconcile(s.store, provider, OFFER, 'A', **kwargs)
    assert reviews.reconcile(s.store, provider, OFFER, 'A', expected=preview['digest'], **kwargs)['result'] == 'released'
    access(s, True)
    assert restore(s)['live_review_releases'] == 1
    access(s, True)
    assert deliver(dispute) == 'duplicate'
    access(s, True)

    # Provider accepted cancellation, but the network lost its response.
    original_cancel = provider.cancel_at_period_end
    def cancel(sub):
        original_cancel(sub)
        raise ProviderUnavailable('Synthetic lost cancellation response')
    provider.cancel_at_period_end = cancel
    response = http(manage.PATH + '/cancel', 'POST', cancel_headers())
    assert response.status == 502, response.text
    assert status()['cancellation'] == 'pending'
    assert provider.cancels == 1
    restore(s)
    assert recover_operation(s, monkeypatch, 'cancel')['provider_result'] == 'scheduled'
    assert status()['cancellation'] == 'awaiting_update'
    access(s, True)
    assert http(manage.PATH + '/cancel', 'POST', cancel_headers()).status == 200
    assert provider.cancels == 1
    scheduled = event(40, data=deepcopy(provider.current))
    scheduled['occurred_at'] = s.now.isoformat()
    assert deliver(scheduled) == 'applied'
    assert status()['cancellation'] == 'scheduled'
    access(s, True)
    tables = restore(s)
    assert tables['live_operation_recoveries'] == 1 + int(uncertain_checkout)
    assert tables['live_renewals'] == 1
    s.now = datetime(2026, 11, 1, tzinfo=timezone.utc)
    access(s, False)  # No dependency on receiving the final canceled notification.
    ended = event(50, data=deepcopy(provider.current))
    ended['occurred_at'] = s.now.isoformat()
    ended['data'].update(status='canceled', scheduled_change=None, current_billing_period=None)
    assert deliver(ended) == 'applied'
    stale = deepcopy(next_period)
    stale['event_id'] = 'evt_' + f'{51:026d}'
    assert deliver(stale) == 'stale'
    access(s, False)
    assert status()['cancellation'] == 'canceled'
    assert provider.creates == provider.cancels == 1
