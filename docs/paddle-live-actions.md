# Live checkout and cancellation service

This is a server service layer, not a public checkout release. The default-off
management adapter now connects status/cancellation (paddle-live-management.md).
Checkout is not exposed and production flags/credentials are unchanged. Do not
enable sales yet. Any purchase adapter must require the authenticated owner,
trusted origin and CSRF, and must never accept a price, account, transaction or
subscription identifier from browser input. Errors must not expose provider
payloads, credentials or private storage details.

## Provider boundary

`LiveClient` uses only `https://api.paddle.com`, modern Live-prefixed private API
keys, version 1, 15-second timeouts and a 256 KiB response limit. Redirects are
refused. Create calls send only the server price, quantity one and automatic
collection. No email or custom account metadata is sent. Retrieval and cancel
paths accept strictly validated Paddle identifiers. Tests use a fake transport;
no real API request or money movement was performed.

`configured_service('checkout')` requires LIVE_CHECKOUT, LIVE_WEBHOOK and
LIVE_ACCESS switches, a separate Live signing secret, key and price. All variable
names start with `TRADE_PAPER_PADDLE_`. The cancellation factory requires its own
LIVE_CANCEL switch, Live API key and ledger price; disabling sales/access must
not prevent canceling renewals. Only the cancellation factory is called by the
management adapter. The checkout factory still has no route.

## Durable checkout intent

SQLite `live_operations` is an additive schema-1 extension. Read-only code also
supports older ledgers; a writable open creates the new table transactionally.
A unique account/kind intent is committed before the first external request.
Parallel requests and a process restart therefore cannot create another draft.
Valid responses register the transaction and mark the intent ready in one commit.
An unbound ready transaction may be reused for 15 minutes only after a fresh
provider GET confirms the same expected price/quantity and draft/ready status.
Bound, expired, conflicting or uncertain operations require operator review.

No automatic retries, reservation expiry/deletion, or resubscription are allowed.
Timeout and local commit failure may mean Paddle already created a transaction.
Never clear an intent to retry without investigating the provider outcome. An
operation without a transaction ID is already provider-managed for local edits
and cannot inherit stale paid access from users.json.

## Cancellation and read-only recovery

The target subscription/customer comes only from the account's trusted ledger
binding. Persist intent before network, GET canonical state, and validate ownership
and the expected monthly Starter item. Already canceled/scheduled cancellation
can be acknowledged without POST. A first eligible active subscription with no
scheduled change can POST `effective_from=next_billing_period`. Other states,
stale periods, mismatched identities or conflicting changes require review.
The response must acknowledge cancellation on the current period end.

Retries may GET to confirm a timed-out cancellation but never repeat the POST.
If no cancellation is found, leave the intent reserved for operator review.
Acknowledgements update only the operation journal. Signed webhooks continue to
drive entitlements; an API acknowledgement does not overwrite subscription state
or users.json. Customers are not promised cancellation completion before it is
confirmed. A public adapter must surface unresolved requests as requiring help.

## Remaining release work

Authenticated checkout HTTP/UI adapter, explicit purchase terms, Live product/price amount
validation, private configuration, canonical entitlement reconciliation, recovery
tools for ambiguous checkout operations, refund/chargeback policy, SQLite-consistent
backup, monitoring and end-to-end launch verification remain required. Runtime
ledgers must be backed up consistently before operational use; local JSON/source
archives do not cover production SQLite data.

References:
- https://developer.paddle.com/api-reference/transactions/create-transaction/
- https://developer.paddle.com/api-reference/subscriptions/cancel-subscription/
