"""Signed adjustment evidence and conservative, persistent access review.

No refunds or cancellations are issued here. Approved adjustments (including
partial/tax refunds and reversals) need operator reconciliation before access can
resume. A subscription update alone must never clear this evidence.
"""
import re

from app.paddle_live_store import BillingConflict, _id

EVENTS = {'adjustment.created', 'adjustment.updated'}
ACTIONS = {'refund', 'credit', 'chargeback', 'chargeback_warning',
           'chargeback_reverse', 'chargeback_warning_reverse', 'credit_reverse'}


def initialize(db):
    db.execute('''CREATE TABLE IF NOT EXISTS live_adjustment_events (
        event_id TEXT PRIMARY KEY, adjustment_id TEXT NOT NULL,
        subscription_id TEXT NOT NULL, customer_id TEXT NOT NULL,
        transaction_id TEXT NOT NULL, action TEXT NOT NULL, status TEXT NOT NULL,
        adjustment_type TEXT, currency TEXT NOT NULL, total TEXT NOT NULL,
        occurred_at TEXT NOT NULL, requires_review INTEGER NOT NULL CHECK(requires_review IN (0, 1)))''')
    db.execute('CREATE INDEX IF NOT EXISTS adjustment_subscription_review '
               'ON live_adjustment_events (subscription_id, requires_review)')
    db.execute('CREATE INDEX IF NOT EXISTS adjustment_identity ON live_adjustment_events (adjustment_id)')
    db.execute('CREATE INDEX IF NOT EXISTS adjustment_transaction ON live_adjustment_events (transaction_id)')


def needs_review(db, subscription_id):
    # Additive schema: old ledgers remain read-only until the writable migration.
    if not subscription_id or not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                                            "AND name='live_adjustment_events'").fetchone():
        return False
    return bool(db.execute('SELECT 1 FROM live_adjustment_events '
                           'WHERE subscription_id=? AND requires_review=1 LIMIT 1',
                           (subscription_id,)).fetchone())


def apply(db, data, event_id, when):
    try:
        adj = _id(data['id'], 'adj')
        sub, customer = _id(data['subscription_id'], 'sub'), _id(data['customer_id'], 'ctm')
        txn = _id(data['transaction_id'], 'txn')
        action, status, kind = data['action'], data['status'], data['type']
        currency, total = data['currency_code'], data['totals']['total']
        if (action not in ACTIONS or status not in {'pending_approval', 'approved', 'rejected', 'reversed'}
                or kind not in ('full', 'partial', None)
                or not isinstance(currency, str) or not re.fullmatch(r'[A-Z]{3}', currency)
                or currency != data['totals']['currency_code']
                or not isinstance(total, str) or not re.fullmatch(r'-?(0|[1-9][0-9]{0,19})', total)):
            raise ValueError('Invalid adjustment terms')
    except (KeyError, TypeError, IndexError):
        raise ValueError('Incomplete adjustment') from None

    # A signed subscription/customer pair may identify a renewal adjustment even
    # though only the initial checkout transaction was created by this server.
    # This NEVER establishes ownership from an email or custom_data.
    binding = db.execute('SELECT account_id FROM bindings WHERE subscription_id=? AND customer_id=?',
                         (sub, customer)).fetchone()
    if not binding:
        raise BillingConflict('Trusted subscription binding required for adjustment')
    reserved = db.execute('SELECT account_id FROM checkouts WHERE transaction_id=?', (txn,)).fetchone()
    if reserved and reserved != binding:
        raise BillingConflict('Adjustment transaction ownership conflict')
    identities = db.execute('SELECT DISTINCT subscription_id, customer_id, transaction_id, action '
                            'FROM live_adjustment_events WHERE adjustment_id=?', (adj,)).fetchall()
    if identities and identities != [(sub, customer, txn, action)]:
        raise BillingConflict('Adjustment identity conflict')
    other = db.execute('SELECT 1 FROM live_adjustment_events WHERE transaction_id=? '
                       'AND (subscription_id<>? OR customer_id<>?)', (txn, sub, customer)).fetchone()
    if other:
        raise BillingConflict('Adjustment transaction conflict')

    # Evidence is append-only: late pending/rejected events and reversal events
    # cannot lift an existing hold. Reversals received first also require review.
    review = status in ('approved', 'reversed')
    db.execute('INSERT INTO live_adjustment_events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
               (event_id, adj, sub, customer, txn, action, status, kind, currency, total, when, int(review)))
    return 'adjustment_review' if review else 'adjustment_recorded'
