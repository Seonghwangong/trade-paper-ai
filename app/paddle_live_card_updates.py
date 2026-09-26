"""Authenticated zero-value card-update receipts, never paid-period evidence."""
import hashlib

from app.paddle_live_store import BillingConflict, _id, _json

RESULTS = {'payment_method_recorded', 'payment_method_existing'}


def initialize(db):
    db.execute('''CREATE TABLE IF NOT EXISTS live_payment_methods (
        transaction_id TEXT PRIMARY KEY, subscription_id TEXT NOT NULL,
        customer_id TEXT NOT NULL, account_id TEXT NOT NULL,
        terms TEXT NOT NULL, terms_digest TEXT NOT NULL)''')
    db.execute('''CREATE TABLE IF NOT EXISTS live_payment_method_receipts (
        event_id TEXT PRIMARY KEY, transaction_id TEXT NOT NULL)''')


def validate_terms(value, price_id):
    if (not isinstance(value, dict) or set(value) != {'origin', 'price_id', 'product_id', 'currency', 'total', 'tax_mode', 'unit_amount'}
            or value['origin'] != 'subscription_payment_method_change'
            or value['price_id'] != price_id or value['currency'] != 'KRW' or value['total'] != '0'
            or value['tax_mode'] not in ('internal', 'external') or value['unit_amount'] != '29000'):
        raise ValueError('Invalid card-update terms')
    _id(value['price_id'], 'pri'); _id(value['product_id'], 'pro')


def validate_zero_transaction(data, offer, *, statuses=('completed',)):
    from app.paddle_live_offer import validate_price
    try:
        _id(data['id'], 'txn'); _id(data['subscription_id'], 'sub'); _id(data['customer_id'], 'ctm')
        if (data['origin'] != 'subscription_payment_method_change' or data['status'] not in statuses
                or data['collection_mode'] != 'automatic' or data['discount_id'] is not None
                or data['currency_code'] != offer.currency):
            raise ValueError('Invalid card-update transaction')
        items = data['items']
        if not isinstance(items, list) or len(items) != 1 or type(items[0]['quantity']) is not int or items[0]['quantity'] != 1:
            raise ValueError('Invalid card-update items')
        validate_price(items[0]['price'], offer, with_product=False)
        totals = data['details']['totals']
        money = {key: '0' for key in ('subtotal', 'discount', 'tax', 'total')}
        if (totals['currency_code'] != offer.currency or any(totals[key] != '0' for key in
                ('subtotal', 'discount', 'tax', 'total', 'grand_total', 'grand_total_tax',
                 'credit', 'credit_to_balance', 'balance'))
                or any(data['details']['adjusted_totals'][key] != totals[key] for key in
                       ('subtotal', 'tax', 'total', 'grand_total', 'grand_total_tax', 'currency_code'))):
            raise ValueError('Card update must not contain money')
        lines = data['details']['line_items']
        if not isinstance(lines, list) or len(lines) != 1:
            raise ValueError('Invalid card-update lines')
        line = lines[0]
        if (line['price_id'] != offer.price_id or line['product']['id'] != offer.product_id
                or type(line['quantity']) is not int or line['quantity'] != 1
                or line['totals'] != money or line['unit_totals'] != money):
            raise ValueError('Invalid card-update calculated amounts')
        for item in (items[0], line):
            proration = item.get('proration')
            if proration is not None and (not isinstance(proration, dict) or proration.get('rate') != '0'):
                raise ValueError('Nonzero card-update proration')
        if not isinstance(data['payments'], list) or any(p['amount'] != '0' for p in data['payments']):
            raise ValueError('Card update contains payment amounts')
    except (KeyError, TypeError, IndexError, AttributeError):
        raise ValueError('Incomplete card-update transaction') from None
    return {'origin': data['origin'], 'price_id': offer.price_id, 'product_id': offer.product_id,
            'currency': offer.currency, 'total': '0', 'tax_mode': offer.tax_mode, 'unit_amount': offer.amount}


def record(db, data, offer, event_id):
    terms = validate_zero_transaction(data, offer)
    txn, sub, customer = data['id'], data['subscription_id'], data['customer_id']
    owner = db.execute('SELECT account_id FROM bindings WHERE subscription_id=? AND customer_id=?',
                       (sub, customer)).fetchone()
    if not owner:
        raise BillingConflict('Existing signed subscription ownership required for card update')
    if (db.execute('SELECT 1 FROM checkouts WHERE transaction_id=?', (txn,)).fetchone()
            or db.execute('SELECT 1 FROM live_renewals WHERE transaction_id=?', (txn,)).fetchone()):
        raise BillingConflict('Card update conflicts with paid transaction identity')
    if db.execute('SELECT 1 FROM live_adjustment_events WHERE transaction_id=? '
                  'AND (subscription_id<>? OR customer_id<>?)', (txn, sub, customer)).fetchone():
        raise BillingConflict('Card update conflicts with adjustment ownership')
    raw = _json(terms)
    expected = (txn, sub, customer, owner[0], raw, hashlib.sha256(raw.encode()).hexdigest())
    previous = db.execute('SELECT * FROM live_payment_methods WHERE transaction_id=?', (txn,)).fetchone()
    if previous and previous != expected:
        raise BillingConflict('Card-update ownership or terms changed')
    if not previous:
        db.execute('INSERT INTO live_payment_methods VALUES (?, ?, ?, ?, ?, ?)', expected)
    db.execute('INSERT INTO live_payment_method_receipts VALUES (?, ?)', (event_id, txn))
    return 'payment_method_existing' if previous else 'payment_method_recorded'
