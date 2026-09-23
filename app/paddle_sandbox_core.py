"""Isolated Paddle webhook prototype. Never imports or updates production users."""
import hashlib
import hmac
import json
import re
import sqlite3
import time
from datetime import datetime, timezone
from contextlib import contextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request

MAX_BODY = 262144
EVENTS = {"subscription." + s for s in (
    "created", "activated", "updated", "resumed", "paused", "past_due", "canceled", "trialing"
)}
STATUSES = {"active": "Active", "trialing": "Trial", "paused": "Expired",
            "past_due": "Expired", "canceled": "Cancelled"}


def verify(raw, signature, secret, now):
    parts = [p.strip().partition("=") for p in signature.split(";")]
    stamps = [v for k, sep, v in parts if k == "ts" and sep]
    hashes = [v for k, sep, v in parts if k == "h1" and sep]
    if len(stamps) != 1 or not re.fullmatch(r"[0-9]{1,12}", stamps[0]):
        raise HTTPException(401, "Invalid signature")
    if abs(now - int(stamps[0])) > 5:
        raise HTTPException(401, "Expired signature")
    expected = hmac.new(secret.encode(), stamps[0].encode() + b":" + raw, hashlib.sha256).hexdigest()
    if not any(re.fullmatch(r"[0-9a-f]{64}", v) and hmac.compare_digest(v, expected) for v in hashes):
        raise HTTPException(401, "Invalid signature")


def timestamp(value):
    if not isinstance(value, str):
        raise ValueError("Missing event time")
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("Timezone required")
    return dt.astimezone(timezone.utc).isoformat(timespec="microseconds")


class SandboxStore:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript('''
              CREATE TABLE IF NOT EXISTS bindings (
                subscription_id TEXT PRIMARY KEY, customer_id TEXT NOT NULL,
                account_id TEXT NOT NULL UNIQUE);
              CREATE TABLE IF NOT EXISTS checkouts (
                transaction_id TEXT PRIMARY KEY, account_id TEXT NOT NULL UNIQUE,
                price_id TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS events (
                event_id TEXT PRIMARY KEY, digest TEXT NOT NULL, result TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS states (
                account_id TEXT PRIMARY KEY, subscription_id TEXT NOT NULL,
                provider_status TEXT NOT NULL, app_status TEXT NOT NULL,
                occurred_at TEXT NOT NULL, scheduled_action TEXT);
            ''')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(str(self.path), timeout=10)
        try:
            with db:
                yield db
        finally:
            db.close()

    def bind(self, subscription_id, customer_id, account_id):
        """Trusted local setup ONLY, after verifying ownership on the provider side.

        Never call using browser custom_data/account_id or an unverified email match.
        Existing bindings cannot be overwritten through this method.
        """
        if not all(isinstance(v, str) and v.strip() for v in (subscription_id, customer_id, account_id)):
            raise ValueError("Complete trusted binding required")
        with self.connect() as db:
            db.execute("INSERT INTO bindings VALUES (?, ?, ?)",
                       (subscription_id, customer_id, account_id))

    def register_checkout(self, transaction_id, account_id, price_id):
        """Trusted server setup only, before exposing a server-created checkout.

        transaction_id must come from the authenticated Sandbox API response,
        account_id from the authenticated server session (sandbox: namespace).
        Never call with a transaction ID supplied by a browser.
        """
        if (not isinstance(transaction_id, str) or not re.fullmatch(r"txn_[a-z0-9]{26}", transaction_id)
                or not isinstance(account_id, str) or not account_id.startswith("sandbox:")
                or not account_id[8:].strip() or len(account_id) > 200
                or not isinstance(price_id, str) or not price_id.startswith("pri_")):
            raise ValueError("Trusted sandbox checkout required")
        with self.connect() as db:
            db.execute("INSERT INTO checkouts VALUES (?, ?, ?)",
                       (transaction_id, account_id, price_id))

    def _complete_checkout(self, payload, raw, price_id):
        # Simulated payloads may be edited in the dashboard: never establish ownership.
        if not payload["event_id"].startswith("evt_"):
            return "ignored"
        data = payload.get("data")
        txn = data.get("id") if isinstance(data, dict) else None
        if not isinstance(txn, str):
            raise HTTPException(400, "Invalid transaction event")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            checkout = db.execute("SELECT account_id, price_id FROM checkouts WHERE transaction_id=?",
                                  (txn,)).fetchone()
            if not checkout:
                return "ignored"
            try:
                timestamp(payload.get("occurred_at"))
                sub, customer = data["subscription_id"], data["customer_id"]
                items = data["items"]
                if (data["status"] != "completed" or data["collection_mode"] != "automatic"
                        or checkout[1] != price_id or len(items) != 1
                        or items[0]["price"]["id"] != price_id
                        or type(items[0]["quantity"]) is not int or items[0]["quantity"] != 1
                        or not isinstance(sub, str) or not re.fullmatch(r"sub_[a-z0-9]{26}", sub)
                        or not isinstance(customer, str) or not re.fullmatch(r"ctm_[a-z0-9]{26}", customer)):
                    raise ValueError()
            except (KeyError, TypeError, ValueError, IndexError):
                raise HTTPException(400, "Invalid checkout completion") from None
            digest = hashlib.sha256(raw).hexdigest()
            previous = db.execute("SELECT digest FROM events WHERE event_id=?",
                                  (payload["event_id"],)).fetchone()
            if previous:
                if previous[0] != digest:
                    raise HTTPException(409, "Event ID conflict")
                return "duplicate"
            existing = db.execute("SELECT subscription_id, customer_id, account_id FROM bindings "
                                  "WHERE subscription_id=? OR account_id=?", (sub, checkout[0])).fetchall()
            expected = (sub, customer, checkout[0])
            if existing and existing != [expected]:
                raise HTTPException(409, "Checkout ownership conflict")
            if not existing:
                db.execute("INSERT INTO bindings VALUES (?, ?, ?)", expected)
            db.execute("INSERT INTO events VALUES (?, ?, ?)",
                       (payload["event_id"], digest, "bound"))
        # Completion establishes ownership only. Subscription events drive shadow state.
        return "bound"

    def state(self, account_id):
        with self.connect() as db:
            db.row_factory = sqlite3.Row
            row = db.execute("SELECT * FROM states WHERE account_id=?", (account_id,)).fetchone()
            return dict(row) if row else None

    def apply(self, payload, raw, price_id):
        if not isinstance(payload, dict):
            raise HTTPException(400, "Invalid event")
        event_id = payload.get("event_id")
        if not isinstance(event_id, str) or not event_id.startswith(("evt_", "ntfsimevt_")) or len(event_id) > 100:
            raise HTTPException(400, "Invalid event ID")
        event_type = payload.get("event_type")
        if not isinstance(event_type, str):
            raise HTTPException(400, "Invalid event type")
        if event_type == "transaction.completed":
            return self._complete_checkout(payload, raw, price_id)
        if event_type not in EVENTS:
            return "ignored"
        try:
            when = timestamp(payload.get("occurred_at"))
            data = payload["data"]
            sub_id, customer_id, status = data["id"], data["customer_id"], data["status"]
            if status not in STATUSES:
                raise ValueError("Unsupported status")
            items = data["items"]
            if len(items) != 1 or items[0]["price"]["id"] != price_id or items[0]["quantity"] != 1:
                raise ValueError("Unexpected catalog item")
            change = data.get("scheduled_change")
            action = change.get("action") if isinstance(change, dict) else None
            if action not in (None, "cancel", "pause", "resume"):
                raise ValueError("Unexpected scheduled change")
            if not all(isinstance(v, str) for v in (sub_id, customer_id)):
                raise ValueError("Invalid IDs")
        except (KeyError, TypeError, ValueError, IndexError):
            raise HTTPException(400, "Invalid subscription event") from None
        digest = hashlib.sha256(raw).hexdigest()
        # Single SQLite transaction makes deduplication + state write atomic across processes.
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            duplicate = db.execute("SELECT digest FROM events WHERE event_id=?", (event_id,)).fetchone()
            if duplicate:
                if duplicate[0] != digest:
                    raise HTTPException(409, "Event ID conflict")
                return "duplicate"
            binding = db.execute("SELECT account_id FROM bindings WHERE subscription_id=? AND customer_id=?",
                                 (sub_id, customer_id)).fetchone()
            if not binding:
                # Non-2xx permits retry after trusted binding is established. No event is consumed.
                raise HTTPException(409, "Subscription binding required")
            account_id = binding[0]
            old = db.execute("SELECT occurred_at, subscription_id, provider_status, scheduled_action FROM states WHERE account_id=?", (account_id,)).fetchone()
            if old and when == old[0]:
                # Separate Paddle event types may describe the same state at the same instant.
                if old[1:] == (sub_id, status, action):
                    db.execute("INSERT INTO events VALUES (?, ?, ?)", (event_id, digest, "equivalent"))
                    return "equivalent"
                # Different state at the same timestamp still needs reconciliation.
                raise HTTPException(409, "Subscription reconciliation required")
            result = "stale" if old and when < old[0] else "applied"
            if result == "applied":
                db.execute("INSERT OR REPLACE INTO states VALUES (?, ?, ?, ?, ?, ?)",
                           (account_id, sub_id, status, STATUSES[status], when, action))
            db.execute("INSERT INTO events VALUES (?, ?, ?)", (event_id, digest, result))
        return result


def create_app(*, database, secret, price_id, environment="sandbox", clock=time.time):
    if environment != "sandbox" or not secret or not price_id.startswith("pri_"):
        raise ValueError("Explicit sandbox configuration required")
    app = FastAPI(title="Trade Paper AI isolated Sandbox webhook", docs_url=None, redoc_url=None)
    store = SandboxStore(database)
    app.state.sandbox_store = store

    @app.post("/webhooks/paddle")
    async def webhook(request: Request):
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > MAX_BODY:
                raise HTTPException(413, "Event too large")
        raw = bytes(body)
        verify(raw, request.headers.get("paddle-signature", ""), secret, clock())
        try:
            payload = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            raise HTTPException(400, "Invalid JSON") from None
        return {"environment": "sandbox", "result": store.apply(payload, raw, price_id)}

    return app


def from_environment():
    """Uvicorn factory; never falls back to production configuration."""
    import os
    if os.environ.get("PADDLE_TEST_ENVIRONMENT") != "sandbox":
        raise ValueError("Set PADDLE_TEST_ENVIRONMENT=sandbox explicitly")
    return create_app(database=os.environ["PADDLE_TEST_DATABASE"],
                      secret=os.environ["PADDLE_TEST_WEBHOOK_SECRET"],
                      price_id=os.environ["PADDLE_TEST_PRICE_ID"])
