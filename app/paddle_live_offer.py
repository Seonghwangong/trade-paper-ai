"""Fail-closed catalog contract for the advertised monthly Starter offer."""
from dataclasses import dataclass
import os

from app.paddle_live_store import _id


@dataclass(frozen=True)
class Offer:
    price_id: str
    product_id: str
    tax_mode: str
    amount: str = '29000'
    currency: str = 'KRW'


def expected_offer(price_id):
    from app.subscription import PLANS
    plan = PLANS['Starter']
    # KRW is zero-decimal; keep display and provider minor-unit amount aligned.
    if plan['price'] != 29_000 or plan['currency'] != 'KRW' or plan['billing_cycle'] != 'Monthly':
        raise ValueError('Starter catalog contract needs review')
    product = _id(os.environ.get('TRADE_PAPER_PADDLE_LIVE_PRODUCT_ID', ''), 'pro')
    tax = os.environ.get('TRADE_PAPER_PADDLE_LIVE_TAX_MODE', '')
    if tax not in ('internal', 'external'):
        raise ValueError('Explicit Live tax mode required')
    return Offer(_id(price_id, 'pri'), product, tax)


def validate_price(data, offer, *, with_product=True):
    try:
        cycle, quantity = data['billing_cycle'], data['quantity']
        if (data['id'] != offer.price_id or data['product_id'] != offer.product_id
                or data['status'] != 'active' or data['type'] != 'standard'
                or data['unit_price'] != {'amount': offer.amount, 'currency_code': offer.currency}
                or data['tax_mode'] != offer.tax_mode or data['unit_price_overrides'] != []
                or data['trial_period'] is not None
                or cycle['interval'] != 'month' or type(cycle['frequency']) is not int or cycle['frequency'] != 1
                or type(quantity['minimum']) is not int or quantity['minimum'] != 1
                or type(quantity['maximum']) is not int or quantity['maximum'] != 1):
            raise ValueError('Unexpected Starter price')
        if with_product:
            product = data['product']
            if product['id'] != offer.product_id or product['status'] != 'active' or product['type'] != 'standard':
                raise ValueError('Unexpected Starter product')
    except (KeyError, TypeError, IndexError):
        raise ValueError('Incomplete Starter price') from None


def validate_transaction_offer(data, offer):
    try:
        if data['currency_code'] != offer.currency or data['discount_id'] is not None or data['subscription_id'] is not None:
            raise ValueError('Unexpected transaction terms')
        items = data['items']
        if not isinstance(items, list) or len(items) != 1 or type(items[0]['quantity']) is not int or items[0]['quantity'] != 1:
            raise ValueError('Unexpected transaction items')
        validate_price(items[0]['price'], offer, with_product=False)
    except (KeyError, TypeError, IndexError):
        raise ValueError('Incomplete transaction terms') from None
