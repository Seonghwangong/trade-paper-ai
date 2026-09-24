from copy import deepcopy
from datetime import datetime, timezone
import json

from fastapi import HTTPException
import pytest

from app import subscription
from app.paddle_subscription_policy import evaluate_snapshot
from tests.test_subscription import _files, _request

SUB = 'sub_' + 'a' * 26
CUSTOMER = 'ctm_' + 'b' * 26
PRICE = 'pri_' + 'c' * 26
NOW = datetime(2026, 9, 24, tzinfo=timezone.utc)


def snapshot():
    return {
        'id': SUB, 'customer_id': CUSTOMER, 'status': 'active',
        'collection_mode': 'automatic',
        'billing_cycle': {'interval': 'month', 'frequency': 1},
        'items': [{'price': {'id': PRICE}, 'quantity': 1}],
        'scheduled_change': None,
        'current_billing_period': {'starts_at': '2026-09-01T00:00:00Z', 'ends_at': '2026-10-01T00:00:00Z'},
    }


def decision(data, now=NOW, **binding):
    return evaluate_snapshot(data, now=now, subscription_id=binding.get('subscription_id', SUB),
                             customer_id=binding.get('customer_id', CUSTOMER), price_id=PRICE)


def test_active_access_and_scheduled_cancellation_preserve_remaining_period():
    data = snapshot()
    data['scheduled_change'] = {'action': 'cancel', 'effective_at': '2026-10-01T00:00:00Z'}
    before = deepcopy(data)
    result = decision(data)
    assert result.starter_access and result.cancellation_pending
    assert result.access_until == datetime(2026, 10, 1, tzinfo=timezone.utc)
    assert data == before
    assert not decision(data, now=result.access_until).starter_access
    assert not decision(data, now=datetime(2026, 8, 31, tzinfo=timezone.utc)).starter_access


@pytest.mark.parametrize('status', ['canceled', 'paused', 'past_due', 'trialing'])
def test_unpaid_or_inactive_status_never_grants_starter(status):
    data = snapshot()
    data.update(status=status, current_billing_period=None)
    assert not decision(data).starter_access


@pytest.mark.parametrize('action', ['cancel', 'pause'])
def test_effective_change_caps_access_even_when_snapshot_still_active(action):
    data = snapshot()
    data['scheduled_change'] = {'action': action, 'effective_at': '2026-09-23T00:00:00Z'}
    assert not decision(data).starter_access


@pytest.mark.parametrize('field,value', [
    ('id', 'sub_' + 'z' * 26), ('customer_id', 'ctm_' + 'z' * 26),
    ('items', []), ('items', [{'price': {'id': PRICE}, 'quantity': True}]),
    ('items', [{'price': {'id': 'pri_' + 'z' * 26}, 'quantity': 1}]),
    ('collection_mode', 'manual'), ('billing_cycle', {'interval': 'year', 'frequency': 1}),
    ('billing_cycle', {'interval': 'month', 'frequency': True}), ('status', 'unknown'),
    ('current_billing_period', None), ('scheduled_change', {}),
    ('scheduled_change', {'action': 'cancel', 'effective_at': '2026-10-01'}),
    ('current_billing_period', {'starts_at': '2026-10-01T00:00:00Z', 'ends_at': '2026-09-01T00:00:00Z'}),
])
def test_wrong_owner_catalog_and_malformed_snapshots_are_rejected(field, value):
    data = snapshot()
    data[field] = value
    with pytest.raises(ValueError):
        decision(data)


def test_browser_account_metadata_never_overrides_binding():
    data = snapshot()
    data['custom_data'] = {'account_id': 'victim'}
    assert decision(data).starter_access
    with pytest.raises(ValueError):
        decision(data, customer_id='ctm_' + 'z' * 26)
    with pytest.raises(ValueError):
        decision(data, subscription_id='../../another-subscription')
    with pytest.raises(ValueError):
        decision(data, now=datetime(2026, 9, 24))


@pytest.mark.parametrize('marker', [
    {'billing_provider': 'paddle'}, {'paddle_subscription_id': SUB}, {'billing_provider': 'other'},
])
@pytest.mark.parametrize('action', ['cancel', 'free', 'admin'])
def test_provider_accounts_cannot_be_changed_locally(tmp_path, monkeypatch, marker, action):
    users, history, _ = _files(tmp_path, monkeypatch)
    records = json.loads(users.read_text())
    records[0].update(plan='Starter', subscription_status='Active', **marker)
    users.write_text(json.dumps(records))
    before_users, before_history = users.read_bytes(), history.read_bytes()
    with pytest.raises(HTTPException) as error:
        if action == 'cancel':
            subscription.cancel_subscription(_request())
        elif action == 'free':
            subscription.change_plan(_request(), 'Free')
        else:
            subscription.update_subscription_status('A', _request('B'), 'Cancelled')
    assert error.value.status_code == 409
    assert users.read_bytes() == before_users and history.read_bytes() == before_history
    assert not (tmp_path / 'audit_log.json').exists()
    page = subscription.subscription_page(_request()).body.decode()
    assert 'Contact billing support' in page
    assert 'action="/subscription/cancel"' not in page
    assert 'action="/subscription/plan"' not in page
