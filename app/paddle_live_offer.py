"""Fail-closed catalog contract for the advertised monthly Starter offer."""
from dataclasses import dataclass
import os
import re

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


def _money(value):
    if not isinstance(value, str) or not re.fullmatch(r'0|[1-9][0-9]*', value):
        raise ValueError('Invalid transaction amount')
    return int(value)


def validate_completed_transaction(data, offer):
    """Recheck the immutable paid terms from Paddle's signed completion event.

    ``details`` and its line items are Paddle's source of truth for calculated
    totals.  This rejects discounts, credits, adjustments, localized price
    overrides and catalog changes that may occur after the checkout was opened.
    """
    try:
        if (data['status'] != 'completed' or data['collection_mode'] != 'automatic'
                or data['currency_code'] != offer.currency or data['discount_id'] is not None):
            raise ValueError('Unexpected completed transaction')
        _id(data['subscription_id'], 'sub')
        _id(data['customer_id'], 'ctm')

        items = data['items']
        if (not isinstance(items, list) or len(items) != 1
                or type(items[0]['quantity']) is not int or items[0]['quantity'] != 1
                or items[0].get('proration') is not None):
            raise ValueError('Unexpected completed transaction items')
        validate_price(items[0]['price'], offer, with_product=False)

        details, totals = data['details'], data['details']['totals']
        amount = _money(offer.amount)
        tax, total = _money(totals['tax']), _money(totals['total'])
        subtotal = _money(totals['subtotal'])
        if (totals['currency_code'] != offer.currency
                or subtotal + tax != total or totals['discount'] != '0'
                or totals['credit'] != '0' or totals['credit_to_balance'] != '0'
                or totals['balance'] != '0' or totals['grand_total'] != totals['total']
                or totals['grand_total_tax'] != totals['tax']):
            raise ValueError('Unexpected completed transaction totals')
        if (offer.tax_mode not in ('internal', 'external')
                or (offer.tax_mode == 'internal' and total != amount)
                or (offer.tax_mode == 'external' and subtotal != amount)):
            raise ValueError('Unexpected completed transaction tax')

        lines = details['line_items']
        if not isinstance(lines, list) or len(lines) != 1:
            raise ValueError('Unexpected completed transaction lines')
        line = lines[0]
        if (line['price_id'] != offer.price_id or line['quantity'] != 1
                or type(line['quantity']) is not int or line['proration'] is not None
                or line['product']['id'] != offer.product_id
                or line['product']['type'] != 'standard' or line['product']['status'] != 'active'):
            raise ValueError('Unexpected completed transaction line')
        expected_line = {'subtotal': totals['subtotal'], 'discount': '0',
                         'tax': totals['tax'], 'total': totals['total']}
        if line['totals'] != expected_line or line['unit_totals'] != expected_line:
            raise ValueError('Unexpected completed transaction line totals')

        adjusted = details['adjusted_totals']
        for key in ('subtotal', 'tax', 'total', 'grand_total', 'grand_total_tax', 'currency_code'):
            if adjusted[key] != totals[key]:
                raise ValueError('Adjusted transaction needs reconciliation')

        captured = [payment for payment in data['payments'] if payment['status'] == 'captured']
        if len(captured) != 1 or captured[0]['amount'] != totals['grand_total']:
            raise ValueError('Completed transaction payment mismatch')
    except (KeyError, TypeError, IndexError, AttributeError):
        raise ValueError('Incomplete completed transaction') from None
