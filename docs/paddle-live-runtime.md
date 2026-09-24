# Default-off Live webhook and account access

The production app registers POST `/webhooks/paddle-live`, but returns 404 until
`TRADE_PAPER_PADDLE_LIVE_WEBHOOK=1`. The route has its own signature authentication;
only this exact POST path bypasses browser session/company setup authentication.

Required private configuration, not currently enabled in production:
- `TRADE_PAPER_PADDLE_LIVE_PRICE_ID`: actual Live monthly Starter price ID.
- `TRADE_PAPER_PADDLE_LIVE_WEBHOOK_SECRET`: Live destination signing secret.
- `TRADE_PAPER_PADDLE_LIVE_WEBHOOK=1`: receive authenticated Live notifications.
- `TRADE_PAPER_PADDLE_LIVE_ACCESS=1`: separately enable paid access projection.

The runtime rejects reuse of a configured Sandbox secret/price. This cannot prove
an arbitrary key belongs to Live: the operator must configure the correct Live
destination. Never point this route at a Sandbox notification destination.
No Live checkout, keys, notification destination or prices are created by code.

## HTTP behavior

Raw streamed body is bounded to 262144 bytes before parsing. Signature validation
occurs before opening/creating the database. Synchronous verification/storage work
runs in the thread pool; database locks wait at most two seconds per connection.
There is no external API call or user JSON write in the request. The response is
sent after durable commit, not before storage. A persistent async queue is not
implemented; delivery failures must be retried and monitored.

- 404: switch off.
- 503: missing/mismatched configuration, unavailable/corrupt storage or DB error.
- 401: missing, invalid or expired signature.
- 413: oversized body; 400: malformed length/event or unsupported simulator ID.
- 409: checkout not registered, binding not yet present, or reconciliation needed.
- 200: committed application/binding, duplicate, stale/equivalent snapshot, or
  unsupported event ignored. The response reveals no account/provider identifiers.

## Authoritative access without users.json copying

With access enabled, an existing user whose server-bound snapshot has valid active
Starter coverage receives Starter/Active. Checkout completion alone grants no
paid access. Reserved/bound accounts with no valid coverage receive Free/Active,
subject to the existing five-document monthly allowance. This includes canceled,
paused, past-due, trialing and expired coverage; no Starter trial/grace is offered.
Unlinked accounts retain existing legacy behavior. A deleted/missing user record
cannot gain access from an orphan ledger entry.

Read-only SQLite connections validate metadata and never create/repair the DB.
Access-enabled configuration with a missing DB is unavailable, not free setup.
Missing/corrupt/mismatched required storage returns 503; document middleware stops
before creating the document rather than trusting stale paid JSON fields.
Provider-marked JSON without valid enabled coverage never grants paid access.

Existing Live reservations remain protected against local cancel/downgrade/admin
status edits even if access is disabled. Keep the price configuration and ledger
available when turning off access: removing the ledger/configuration can prevent
safe ownership decisions. Only a provider cancellation flow actually stops renewal.
Support links remain until the public cancellation flow is implemented. The
server-only checkout/cancel services are described in paddle-live-actions.md;
they have no public routes yet. Flags do not cancel charges.

Both flags remain off in production. Do not enable access for customers before
Live checkout, reconciliation, cancellation/portal, refunds, backup and operational
verification are completed. Existing paid-plan purchase notices still describe
checkout as unavailable; opening sales requires a coordinated release.

## Validation

Actual ASGI app tests verify default-off/missing config, session-free signed
webhooks, no DB creation by unsigned traffic, body limits, binding races and
retry, end-to-end signed event → ledger → account access, expiry/cancellation,
account isolation, immutable users/history, guard enforcement without JSON markers,
read-only DB behavior, missing/corrupt DB failure and safe middleware 503.

Sources:
https://developer.paddle.com/webhooks/about/signature-verification/
https://developer.paddle.com/webhooks/about/respond-to-webhooks/
