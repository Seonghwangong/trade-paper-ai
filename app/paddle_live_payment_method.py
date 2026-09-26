"""Read-only handoff to Paddle's existing subscription payment-method portal.

Never create a portal session, payment transaction or local billing record here.
The temporary provider URL is returned only after explicit owner authorization.
"""
import os
import re
from urllib.parse import parse_qsl, urlsplit

from fastapi import HTTPException

from app import paddle_live_runtime as runtime
from app.paddle_live_actions import LiveClient, ProviderUnavailable
from app.paddle_live_adjustments import needs_review
from app.paddle_live_store import _id


def enabled():
    return os.environ.get('TRADE_PAPER_PADDLE_LIVE_PAYMENT_METHOD') == '1'


def binding(account):
    store = runtime.store(read_only=True)
    with store.connect() as db:
        db.execute('BEGIN')
        row = db.execute('SELECT subscription_id, customer_id FROM bindings WHERE account_id=?',
                         (account,)).fetchone()
        if row is None:
            raise HTTPException(404, 'No connected billing record for this account.')
        if needs_review(db, row[0]):
            raise HTTPException(409, 'Contact billing support before changing payment details.')
        return _id(row[0], 'sub'), _id(row[1], 'ctm')


def validated_url(value, subscription_id):
    # No external redirect, sandbox host, userinfo, alternate path, extra query
    # or fragment may be passed through from an unexpected provider response.
    if not isinstance(value, str) or len(value) > 4096 or any(c.isspace() for c in value):
        raise ValueError('Invalid payment-method URL')
    parsed = urlsplit(value)
    pairs = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
    if (parsed.scheme != 'https' or parsed.netloc != 'buyer-portal.paddle.com'
            or parsed.path != '/subscriptions/' + subscription_id + '/update-payment-method'
            or parsed.fragment or len(pairs) != 1 or pairs[0][0] != 'token'
            or not re.fullmatch(r'[A-Za-z0-9._~-]{1,2048}', pairs[0][1])):
        raise ValueError('Invalid payment-method URL')
    return value


def payment_method_url(account):
    if not enabled():
        raise HTTPException(404, 'Not found')
    sub, customer = binding(account)
    client = LiveClient(os.environ.get('TRADE_PAPER_PADDLE_LIVE_API_KEY', ''))
    data = client.subscription(sub)
    try:
        if (data['id'] != sub or data['customer_id'] != customer
                or data['collection_mode'] != 'automatic'
                or data['status'] not in ('active', 'past_due')):
            raise ValueError('Unexpected subscription')
        target = validated_url(data['management_urls']['update_payment_method'], sub)
    except (ValueError, KeyError, TypeError):
        raise ProviderUnavailable('Payment-method portal is unavailable') from None
    # A signed dispute or ownership change during the provider read must also
    # fail closed; no read transaction is held over a network request.
    if binding(account) != (sub, customer):
        raise HTTPException(409, 'Billing changed. Refresh or contact billing support.')
    return target
