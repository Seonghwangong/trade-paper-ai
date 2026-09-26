"""Read-only paid-period gate, plus atomic initial completion period evidence."""
from dataclasses import replace
import hashlib
import json

from app.paddle_live_store import BillingConflict, _json
from app.paddle_subscription_policy import _instant


def initialize(db):
    db.execute('''CREATE TABLE IF NOT EXISTS live_initial_periods (
        transaction_id TEXT PRIMARY KEY, event_id TEXT NOT NULL,
        starts_at TEXT NOT NULL, ends_at TEXT NOT NULL)''')


def period(value):
    try:
        start, end = _instant(value['starts_at']), _instant(value['ends_at'])
    except (KeyError, TypeError):
        raise ValueError('Incomplete paid billing period') from None
    if not 0 < (end - start).total_seconds() <= 32 * 86400:
        raise ValueError('Unsupported paid billing period')
    return start.isoformat(timespec='microseconds'), end.isoformat(timespec='microseconds')


def record_initial(db, data, event_id):
    # Only called after financial validation and exact server checkout binding.
    # Nullable initial periods are valid provider data, but cannot grant access.
    if data.get('billing_period') is None:
        return
    starts, ends = period(data['billing_period'])
    previous = db.execute('SELECT starts_at, ends_at FROM live_initial_periods WHERE transaction_id=?',
                          (data['id'],)).fetchone()
    if previous and previous != (starts, ends):
        raise BillingConflict('Initial paid period changed')
    if not previous:
        db.execute('INSERT INTO live_initial_periods VALUES (?, ?, ?, ?)',
                   (data['id'], event_id, starts, ends))


def payment_matches(db, snapshot, price_id):
    """No inference from event time, first-seen snapshot, or historical maximum."""
    starts, ends = period(snapshot['current_billing_period'])
    sub, customer = snapshot['id'], snapshot['customer_id']
    binding = db.execute('SELECT account_id, transaction_id FROM bindings '
                         'WHERE subscription_id=? AND customer_id=?', (sub, customer)).fetchone()
    if not binding:
        return False
    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if 'live_initial_periods' in tables:
        paid = db.execute("SELECT p.starts_at, p.ends_at FROM live_initial_periods p "
                          "JOIN events e ON e.event_id=p.event_id AND e.result='bound' "
                          "WHERE p.transaction_id=?", (binding[1],)).fetchone()
        if paid == (starts, ends):
            return True
    if {'live_renewals', 'live_renewal_receipts'} <= tables:
        from app.paddle_live_renewals import validate_terms
        rows = db.execute("SELECT r.terms, r.terms_digest FROM live_renewals r "
                          "WHERE r.subscription_id=? AND r.customer_id=? AND r.account_id=? "
                          "AND EXISTS (SELECT 1 FROM live_renewal_receipts p JOIN events e ON e.event_id=p.event_id "
                          "WHERE p.transaction_id=r.transaction_id AND e.result='renewal_recorded')",
                          (sub, customer, binding[0]))
        for raw, digest in rows:
            terms = json.loads(raw)
            validate_terms(terms, price_id)
            if hashlib.sha256(_json(terms).encode()).hexdigest() != digest:
                raise ValueError('Paid renewal evidence digest mismatch')
            if period(terms) == (starts, ends):
                return True
    return False


def gate(db, snapshot, decision, price_id):
    if decision.provider_status == 'active' and not payment_matches(db, snapshot, price_id):
        return replace(decision, starter_access=False, access_until=None)
    return decision
