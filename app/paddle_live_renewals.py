"""Signed recurring payment evidence; never an entitlement writer."""
import hashlib
import re

from app.paddle_live_store import BillingConflict, _id, _json
from app.paddle_subscription_policy import _instant

def initialize(db):
    db.execute('''CREATE TABLE IF NOT EXISTS live_renewals (
        transaction_id TEXT PRIMARY KEY, subscription_id TEXT NOT NULL,
        customer_id TEXT NOT NULL, account_id TEXT NOT NULL,
        terms TEXT NOT NULL, terms_digest TEXT NOT NULL)''')
    db.execute('CREATE INDEX IF NOT EXISTS renewal_subscription ON live_renewals (subscription_id)')
    db.execute('''CREATE TABLE IF NOT EXISTS live_renewal_receipts (
        event_id TEXT PRIMARY KEY, transaction_id TEXT NOT NULL)''')
    db.execute('CREATE INDEX IF NOT EXISTS renewal_receipt_transaction ON live_renewal_receipts (transaction_id)')


def validate_terms(value, price_id):
    if (set(value) != {'origin', 'price_id', 'product_id', 'unit_amount', 'tax_mode',
                       'currency', 'subtotal', 'tax', 'total', 'starts_at', 'ends_at'}
            or value['origin'] != 'subscription_recurring' or value['price_id'] != price_id
            or value['tax_mode'] not in ('internal', 'external')
            or not re.fullmatch(r'[A-Z]{3}', value['currency'])):
        raise ValueError('Invalid saved renewal terms')
    _id(value['product_id'], 'pro')
    amounts = [value[k] for k in ('unit_amount', 'subtotal', 'tax', 'total')]
    if any(not isinstance(v, str) or not re.fullmatch(r'0|[1-9][0-9]{0,19}', v) for v in amounts):
        raise ValueError('Invalid renewal amount')
    unit, net, tax, gross = map(int, amounts)
    if unit <= 0 or net + tax != gross or unit != (gross if value['tax_mode'] == 'internal' else net):
        raise ValueError('Invalid renewal totals')
    start, end = _instant(value['starts_at']), _instant(value['ends_at'])
    if not 0 < (end - start).total_seconds() <= 32 * 86400:
        raise ValueError('Invalid monthly renewal period')


def record(db, data, offer, event_id):
    # Caller already authenticated the raw event and validated completed money,
    # item, capture, catalog and no-proration/no-credit conditions.
    txn, sub, customer = _id(data['id'], 'txn'), _id(data['subscription_id'], 'sub'), _id(data['customer_id'], 'ctm')
    owner = db.execute('SELECT account_id FROM bindings WHERE subscription_id=? AND customer_id=?',
                       (sub, customer)).fetchone()
    if not owner:
        raise BillingConflict('Existing subscription ownership required for renewal')
    if db.execute('SELECT 1 FROM checkouts WHERE transaction_id=?', (txn,)).fetchone():
        raise BillingConflict('Renewal conflicts with an initial checkout reservation')
    if db.execute('SELECT 1 FROM live_adjustment_events WHERE transaction_id=? '
                  'AND (subscription_id<>? OR customer_id<>?)', (txn, sub, customer)).fetchone():
        raise BillingConflict('Renewal conflicts with signed adjustment ownership')
    try:
        totals = data['details']['totals']
        terms = {'origin': data['origin'], 'price_id': offer.price_id, 'product_id': offer.product_id,
                 'unit_amount': offer.amount, 'tax_mode': offer.tax_mode, 'currency': offer.currency,
                 'subtotal': totals['subtotal'], 'tax': totals['tax'], 'total': totals['total'],
                 'starts_at': _instant(data['billing_period']['starts_at']).isoformat(timespec='microseconds'),
                 'ends_at': _instant(data['billing_period']['ends_at']).isoformat(timespec='microseconds')}
        validate_terms(terms, offer.price_id)
    except (KeyError, TypeError, AttributeError):
        raise ValueError('Incomplete renewal period') from None
    canonical = _json(terms)
    expected = (txn, sub, customer, owner[0], canonical, hashlib.sha256(canonical.encode()).hexdigest())
    previous = db.execute('SELECT * FROM live_renewals WHERE transaction_id=?', (txn,)).fetchone()
    if previous and previous != expected:
        raise BillingConflict('Renewal identity or paid terms changed')
    if not previous:
        db.execute('INSERT INTO live_renewals VALUES (?, ?, ?, ?, ?, ?)', expected)
    db.execute('INSERT INTO live_renewal_receipts VALUES (?, ?)', (event_id, txn))
    return 'renewal_existing' if previous else 'renewal_recorded'
