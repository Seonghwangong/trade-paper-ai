# Isolated Paddle Live ledger

`PaddleLiveStore` is a tested storage foundation, not an active payment flow.
The default-off `/webhooks/paddle-live` adapter now uses this ledger. The opt-in
runtime derives account access using read-only SQLite connections. It has no user
JSON writer or outgoing Paddle API call. Importing it creates no database.
See paddle-live-runtime.md for configuration and response behavior.

Construct it explicitly with a separate path, environment="live" and the trusted
Live monthly Starter price ID. Metadata pins schema/environment/price. An existing
unmarked database (including the sandbox DB) or a mismatched configuration is
rejected. The host-only tool in [paddle-live-backup.md](paddle-live-backup.md)
creates consistent SQLite backups and stages verified restores into new private
directories. The local JSON recovery export does not include this ledger.

1. After a successful server-created Live checkout transaction, register its ID
   for the authenticated server account before exposing the transaction to the
   browser. This method is not an HTTP API. It is intentionally one initial
   checkout per account. Creating provider transactions, durable request-attempt
   reservations, timeout reconciliation and resubscription are still required.
2. Deliver bounded raw bytes and the signature to `apply_signed_event`, using
   only the private Live notification destination secret. Signature verification
   precedes all ledger changes. Secrets cannot identify their own environment;
   the future adapter/operator must not mix sandbox credentials or destinations.
3. A valid completed transaction binds subscription/customer to its registered
   account. Browser custom_data and email do not establish ownership. Completion
   alone never grants access. Unknown transaction IDs do not consume the event.
4. Supported subscription events require the matching trusted binding and the
   existing strict monthly Starter policy. An early unbound event conflicts and
   remains retryable. The HTTP adapter returns a retryable 409.
5. Deduplication by event_id, binding/snapshot updates and event registration are
   inside a single BEGIN IMMEDIATE transaction. Duplicate notifications cannot
   apply twice. Older occurred_at timestamps cannot replace current snapshots.
   Equal timestamps with identical policy fields are equivalent; conflicts need
   canonical provider reconciliation and do not consume the conflicting event.
6. `access_for_account` reevaluates expiry at read time. It returns None before a
   trusted snapshot exists. No callback automatically writes users.json. Only
   required policy fields are stored, excluding customer email/custom_data.

Errors: malformed data/configuration raises ValueError; unauthenticated or expired
signatures raise HTTPException(401) from the existing verifier; BillingConflict
requires retry/reconciliation. The public adapter maps errors without
exposing raw payloads or secrets. Unhandled DB failures must be retried, not acked.

Remaining before launch: Live checkout reservations/API client, private Live
configuration, canonical reconciliation, provider cancellation/portal, refunds and
chargebacks, durable backup/restore, monitoring and end-to-end verification. Existing
sandbox and application accounts are unchanged. Do not point a Live notification
destination at the sandbox route or treat this module as a completed integration.

Tests cover signed completion→binding→subscription→access, early events, replay,
out-of-order delivery, equivalent/conflicting timestamps, parallel deliveries,
SQL-trigger-injected atomic rollback, account conflicts, invalid price/quantity,
signature failures, expiry, reopen persistence and sandbox DB isolation.

Sources:
- https://developer.paddle.com/webhooks/about/how-webhooks-work/
- https://developer.paddle.com/webhooks/about/signature-verification/
- https://developer.paddle.com/api-reference/subscriptions/cancel-subscription/
