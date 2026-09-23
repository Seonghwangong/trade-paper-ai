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
past_due, paused and canceled. Transaction events are ignored and cannot grant
access. Stale events cannot replace newer state; equal-timestamp changes return
409 pending canonical reconciliation. This remains a prototype, not a complete
production billing integration (refunds, provider cancellation, reconciliation,
checkout ownership creation, real entitlements and operational retention pending).

Validation: tests/test_paddle_sandbox.py. Uses signed synthetic requests plus the
real app middleware. Real Paddle delivery still needs environment configuration,
a trusted test binding and a notification destination.

Sources:
https://developer.paddle.com/webhooks/about/signature-verification/
https://developer.paddle.com/webhooks/subscriptions/subscription-activated/
