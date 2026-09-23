# Paddle Sandbox webhook

POST /webhooks/paddle-sandbox is disabled by default (404). Browser sessions do
not authenticate this endpoint; a valid Paddle-Signature is required whenever
enabled. Missing config returns 503 and invalid signatures return 401.

Configure only a Paddle SANDBOX notification destination with:
- TRADE_PAPER_PADDLE_SANDBOX_ENABLED=1
- TRADE_PAPER_PADDLE_SANDBOX_WEBHOOK_SECRET=<sandbox destination secret>
- TRADE_PAPER_PADDLE_SANDBOX_PRICE_ID=pri_01m36saxd6z4e2kgsdff4mw2py

Paddle webhook secrets do not encode the environment. The operator must select
the sandbox destination secret; this module never changes production entitlements
even if an incorrect secret is supplied. No secret values belong in Git.

State is stored in DATA_DIR/paddle_sandbox.sqlite3, entirely separate from
users.json. SQLite atomically stores the deduplication ledger and shadow state.
This data is experimental and is not part of the user-facing JSON backup export.

A trusted local binding of provider subscription ID, customer ID and a test
account label must be established via SandboxStore.bind() before accepting a
subscription event. Verify those IDs against the Sandbox dashboard first; use
sandbox: labels and never trust browser custom_data or email for ownership.
There is no HTTP binding API. An unbound event returns 409 so Paddle can retry.

Subscribe to subscription.created, activated, updated, resumed, trialing,
past_due, paused and canceled. Unregistered transaction events are ignored and cannot grant
access. Registered checkout completions can establish a sandbox binding only;
they never grant access or update subscription state. Stale events cannot replace newer state; equal-timestamp changes return
409 pending canonical reconciliation. This remains a prototype, not a complete
production billing integration (refunds, provider cancellation, reconciliation,
checkout ownership creation, real entitlements and operational retention pending).

Validation: tests/test_paddle_sandbox.py. Uses signed synthetic requests plus the
real app middleware. Real Sandbox delivery was verified on 2026-09-23: signed transaction simulation
returned 200, and a trusted test subscription pause/resume updated shadow state.
The server-created checkout path described below is locally tested, not yet
connected to an authenticated checkout endpoint.

Sources:
https://developer.paddle.com/webhooks/about/signature-verification/
https://developer.paddle.com/webhooks/subscriptions/subscription-activated/

## Server-created checkout ownership foundation

`SandboxStore.register_checkout(transaction_id, account_id, price_id)` is a
trusted server-only method with no HTTP endpoint. It must be called after the
Sandbox API creates a transaction for the authenticated session, and before
returning the transaction to the browser. Use a `sandbox:` account namespace.
Never register browser-submitted transaction IDs or infer ownership from email,
custom_data, or a checkout success callback.

A signed platform `transaction.completed` for that exact stored transaction,
expected price, quantity one, and automatic collection binds its subscription
and customer to the stored account atomically. Simulator events cannot create
bindings. Conflicting ownership and altered replays are rejected. The transaction
completion does not activate a subscription; signed subscription events still
drive shadow state. Early unbound subscription deliveries return 409 and can be
retried after completion establishes the binding.

The schema is intentionally limited to one checkout per test account. Retry,
expiration and replacement policy must be implemented before exposing checkout
creation to users. A private Sandbox API key and authenticated server-side
transaction creation endpoint are still required. No production access is granted.

Provider reference: https://developer.paddle.com/webhooks/transactions/transaction-completed/
