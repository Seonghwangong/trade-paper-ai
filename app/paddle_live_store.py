"""Live billing ledger used by the default-off signed webhook adapter.

Only server-created checkout IDs may be registered. Signed transaction completion
establishes ownership; signed snapshots and matching paid periods determine access. No method
writes users.json or makes API requests. The opt-in runtime reads access decisions.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import time

from app.paddle_sandbox_core import MAX_BODY, verify
from app.paddle_subscription_policy import _instant, evaluate_snapshot

SUBSCRIPTION_EVENTS = {"subscription." + kind for kind in (
    "created", "activated", "updated", "resumed", "paused", "past_due", "canceled", "trialing"
)}


class BillingConflict(Exception):
    """Retry after ownership is established, or reconcile conflicting state."""


def _id(value, prefix):
    if not isinstance(value, str) or not re.fullmatch(prefix + r"_[a-z0-9]{26}", value):
        raise ValueError("Invalid provider identifier")
    return value


def _account(value):
    if (not isinstance(value, str) or not value.strip() or value != value.strip()
            or len(value) > 200 or value.startswith("sandbox:")):
        raise ValueError("Trusted live account required")
    return value


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


class PaddleLiveStore:
    def __init__(self, path, *, price_id, environment, read_only=False):
        if environment != "live":
            raise ValueError("Explicit live environment required")
        self.price_id = _id(price_id, "pri")
        self.path = Path(path)
        self.read_only = read_only
        if read_only:
            with self.connect() as db:
                meta = dict(db.execute("SELECT key, value FROM paddle_live_meta"))
                if meta != {"schema": "1", "environment": "live", "price_id": self.price_id}:
                    raise ValueError("Live database configuration mismatch")
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if tables and "paddle_live_meta" not in tables:
                raise ValueError("Refusing non-Live database")
            db.execute("CREATE TABLE IF NOT EXISTS paddle_live_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            meta = dict(db.execute("SELECT key, value FROM paddle_live_meta"))
            expected = {"schema": "1", "environment": "live", "price_id": self.price_id}
            if meta and meta != expected:
                raise ValueError("Live database configuration mismatch")
            db.executemany("INSERT OR IGNORE INTO paddle_live_meta VALUES (?, ?)", expected.items())
            db.execute("""CREATE TABLE IF NOT EXISTS checkouts (
                transaction_id TEXT PRIMARY KEY, account_id TEXT NOT NULL UNIQUE,
                price_id TEXT NOT NULL)""")
            db.execute("""CREATE TABLE IF NOT EXISTS bindings (
                subscription_id TEXT PRIMARY KEY, customer_id TEXT NOT NULL,
                account_id TEXT NOT NULL UNIQUE, transaction_id TEXT NOT NULL UNIQUE)""")
            db.execute("""CREATE TABLE IF NOT EXISTS events (
                event_id TEXT PRIMARY KEY, digest TEXT NOT NULL,
                occurred_at TEXT NOT NULL, result TEXT NOT NULL)""")
            db.execute("""CREATE TABLE IF NOT EXISTS snapshots (
                subscription_id TEXT PRIMARY KEY, occurred_at TEXT NOT NULL,
                snapshot TEXT NOT NULL)""")
            # Additive schema-1 extension. Old ledgers remain readable before
            # their first writable open migrates this table.
            db.execute("""CREATE TABLE IF NOT EXISTS live_operations (
                account_id TEXT NOT NULL, kind TEXT NOT NULL,
                started REAL NOT NULL, target_id TEXT,
                result TEXT, PRIMARY KEY (account_id, kind))""")
            from app.paddle_live_adjustments import initialize
            initialize(db)
            from app.paddle_live_renewals import initialize as initialize_renewals
            initialize_renewals(db)
            from app.paddle_live_access import initialize as initialize_access
            initialize_access(db)

    @contextmanager
    def connect(self):
        target = self.path.resolve().as_uri() + "?mode=ro" if self.read_only else str(self.path)
        db = sqlite3.connect(target, timeout=2, uri=self.read_only)
        try:
            with db:
                yield db
        finally:
            db.close()

    def register_checkout(self, transaction_id, account_id):
        """Server-only: register an authenticated Live API transaction response.

        account_id must come from the authenticated server session. Never accept
        these identifiers from a browser callback, email or custom_data. One
        initial checkout per account; retries/resubscription need reconciliation.
        """
        _id(transaction_id, "txn")
        _account(account_id)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute('SELECT 1 FROM live_renewals WHERE transaction_id=?', (transaction_id,)).fetchone():
                raise BillingConflict('Transaction already recorded as a renewal')
            rows = db.execute("SELECT transaction_id, account_id, price_id FROM checkouts "
                              "WHERE transaction_id=? OR account_id=?", (transaction_id, account_id)).fetchall()
            expected = (transaction_id, account_id, self.price_id)
            if rows:
                if rows == [expected]:
                    return "existing"
                raise BillingConflict("Checkout ownership already reserved")
            db.execute("INSERT INTO checkouts VALUES (?, ?, ?)", expected)
        return "registered"

    def apply_signed_event(self, raw, signature, *, secret, offer=None, now=None):
        """Authenticate raw bytes before touching the ledger.

        The HTTP adapter must bound the streamed body and supply only the
        private LIVE destination secret. Secrets do not encode their environment.
        A signed sandbox event must never be delivered with this Live secret.
        """
        if not isinstance(raw, bytes) or len(raw) > MAX_BODY:
            raise ValueError("Invalid event body size")
        if not isinstance(secret, str) or not secret:
            raise ValueError("Live signing secret required")
        now = time.time() if now is None else now
        verify(raw, signature, secret, now)
        try:
            event = json.loads(raw)
            event_id = _id(event["event_id"], "evt")
            occurred = _instant(event["occurred_at"])
            when = occurred.isoformat(timespec="microseconds")
            kind = event["event_type"]
            if not isinstance(kind, str):
                raise ValueError("Invalid event type")
        except (KeyError, TypeError, UnicodeDecodeError, json.JSONDecodeError):
            raise ValueError("Invalid event envelope") from None
        digest = hashlib.sha256(raw).hexdigest()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM events WHERE event_id=?", (event_id,)).fetchone():
                return "duplicate"
            if kind == "transaction.completed":
                result = self._bind_completed(db, event.get("data"), offer, event_id)
            elif kind in SUBSCRIPTION_EVENTS:
                result = self._save_snapshot(db, event.get("data"), when, occurred)
            elif kind in ('adjustment.created', 'adjustment.updated'):
                from app.paddle_live_adjustments import apply
                result = apply(db, event.get('data'), event_id, when)
            else:
                return "ignored"
            if result == "unregistered":
                # No ownership inference, and no event consumed before registration.
                return result
            db.execute("INSERT INTO events VALUES (?, ?, ?, ?)", (event_id, digest, when, result))
            return result

    def _bind_completed(self, db, data, offer, event_id):
        if offer is None or offer.price_id != self.price_id:
            raise ValueError("Live offer contract required")
        # Imported lazily to keep identifier validation shared without a module cycle.
        from app.paddle_live_offer import validate_completed_transaction
        validate_completed_transaction(data, offer)
        if data.get('origin') == 'subscription_recurring':
            from app.paddle_live_renewals import record
            return record(db, data, offer, event_id)
        try:
            txn = _id(data["id"], "txn")
            row = db.execute("SELECT account_id, price_id FROM checkouts WHERE transaction_id=?", (txn,)).fetchone()
            if not row:
                return "unregistered"
            sub, customer = _id(data["subscription_id"], "sub"), _id(data["customer_id"], "ctm")
            if row[1] != self.price_id:
                raise ValueError("Unexpected completed transaction")
        except (KeyError, TypeError, IndexError):
            raise ValueError("Invalid completed transaction") from None
        expected = (sub, customer, row[0], txn)
        existing = db.execute("SELECT subscription_id, customer_id, account_id, transaction_id "
                              "FROM bindings WHERE subscription_id=? OR account_id=? OR transaction_id=?",
                              (sub, row[0], txn)).fetchall()
        if existing and existing != [expected]:
            raise BillingConflict("Subscription ownership conflict")
        if not existing:
            db.execute("INSERT INTO bindings VALUES (?, ?, ?, ?)", expected)
        from app.paddle_live_access import record_initial
        record_initial(db, data, event_id)
        return "bound"

    def _save_snapshot(self, db, data, when, occurred):
        try:
            sub, customer = _id(data["id"], "sub"), _id(data["customer_id"], "ctm")
        except (KeyError, TypeError):
            raise ValueError("Invalid subscription identity") from None
        binding = db.execute("SELECT 1 FROM bindings WHERE subscription_id=? AND customer_id=?", (sub, customer)).fetchone()
        if not binding:
            raise BillingConflict("Trusted checkout binding required")
        evaluate_snapshot(data, subscription_id=sub, customer_id=customer, price_id=self.price_id, now=occurred)
        # Persist only fields used by access policy, not customer PII/custom_data.
        try:
            snapshot = {key: data[key] for key in (
                "id", "customer_id", "status", "collection_mode", "billing_cycle",
                "scheduled_change", "current_billing_period"
            )}
            snapshot["billing_cycle"] = {key: data["billing_cycle"][key] for key in ("interval", "frequency")}
            if snapshot["current_billing_period"] is not None:
                snapshot["current_billing_period"] = {key: data["current_billing_period"][key] for key in ("starts_at", "ends_at")}
            if snapshot["scheduled_change"] is not None:
                snapshot["scheduled_change"] = {key: data["scheduled_change"][key] for key in ("action", "effective_at")}
            snapshot["items"] = [{"price": {"id": self.price_id}, "quantity": 1}]
        except (KeyError, TypeError):
            raise ValueError("Incomplete subscription snapshot") from None
        value = _json(snapshot)
        previous = db.execute("SELECT occurred_at, snapshot FROM snapshots WHERE subscription_id=?", (sub,)).fetchone()
        if previous:
            if when < previous[0]:
                return "stale"
            if when == previous[0]:
                if value == previous[1]:
                    return "equivalent"
                raise BillingConflict("Equal-time subscription snapshots require reconciliation")
        db.execute("INSERT OR REPLACE INTO snapshots VALUES (?, ?, ?)", (sub, when, value))
        return "applied"

    def account_state(self, account_id, *, now=None):
        """Return reservation and decision in one consistent read; no user JSON writes."""
        _account(account_id)
        now = datetime.now(timezone.utc) if now is None else now
        with self.connect() as db:
            db.execute("BEGIN")
            row = db.execute("SELECT b.subscription_id, b.customer_id, s.snapshot FROM checkouts c "
                             "LEFT JOIN bindings b ON b.transaction_id=c.transaction_id "
                             "LEFT JOIN snapshots s ON s.subscription_id=b.subscription_id "
                             "WHERE c.account_id=?", (account_id,)).fetchone()
            if row is None:
                extended = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                                      "AND name='live_operations'").fetchone()
                pending = extended and db.execute("SELECT 1 FROM live_operations "
                    "WHERE account_id=? AND kind='checkout'", (account_id,)).fetchone()
                return bool(pending), None
            return True, self.decision_in_transaction(db, row, now=now)

    def decision_in_transaction(self, db, row, *, now):
        """Shared runtime/manage policy in the caller's consistent read transaction."""
        if row is None or row[2] is None:
            return None
        from app.paddle_live_access import gate
        from app.paddle_live_adjustments import needs_review
        snapshot = json.loads(row[2])
        decision = evaluate_snapshot(snapshot, subscription_id=row[0], customer_id=row[1],
                                     price_id=self.price_id, now=now)
        decision = gate(db, snapshot, decision, self.price_id)
        if needs_review(db, row[0]):
            decision = replace(decision, starter_access=False, access_until=None)
        return decision

    def access_for_account(self, account_id, *, now=None):
        """Read current decision from this account's bound snapshot; no JSON writes."""
        return self.account_state(account_id, now=now)[1]
