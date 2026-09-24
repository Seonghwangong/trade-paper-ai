# Allowlisted Live checkout pilot (default off)

This change prepares checkout; it does not activate sales. Production credentials,
catalog, flags and allowlists were not changed. The public pricing/purchase
preparation notices remain closed. No real Paddle API call/payment was used in QA.

## Routes and authorization

- GET `/subscription/paddle-buy`: validate catalog and show purchase terms.
- POST `/subscription/paddle-buy`: create/reuse the account's reserved transaction.
- GET `/subscription/paddle-buy/status`: read-only server confirmation summary.

Require an authenticated exact Owner role and account membership in
`TRADE_PAPER_PADDLE_LIVE_PILOT_ACCOUNTS` (comma-separated, empty denies everyone).
Existing paid accounts cannot start another purchase. Price, product, account and
subscription IDs in browser requests are ignored. The page returns only a public
client token; the POST returns only that account's registered transaction ID.
Private keys and signing secrets never enter HTML/JSON responses.

All LIVE_CHECKOUT, LIVE_WEBHOOK, LIVE_ACCESS, LIVE_MANAGE and LIVE_CANCEL switches
must be 1. Names start with `TRADE_PAPER_PADDLE_`. Require distinct Live signing
secret/price, a modern Live API key, live client token and configured HTTPS public
origin. A correctly pinned Live SQLite ledger must already exist and be readable;
missing storage fails closed rather than creating it on page/status requests.
Initialize/migrate it deliberately as part of controlled deployment readiness.

## Price contract

Require `TRADE_PAPER_PADDLE_LIVE_PRODUCT_ID` and explicit
`TRADE_PAPER_PADDLE_LIVE_TAX_MODE` (`internal` inclusive or `external` exclusive).
Do not infer tax behavior from provider account defaults. The page discloses the
matching behavior and asks the customer to review the final total at checkout.
The fixed contract matches the existing app plan: KRW 29,000 per month, quantity
one, no trial. KRW uses zero decimal minor units. Currency changes require code
and public pricing review; an environment variable alone cannot change the price.

GET the Live price with its included product before showing checkout and again
before creating/reusing a transaction. Require exact price/product IDs, active
standard catalog entities, amount/currency, monthly frequency one, no trial,
no country price overrides, explicit matching tax mode and quantity min/max one.
Preflight failures create neither an operation intent nor a transaction.

Creation sends KRW explicitly. Validate returned/retrieved transaction currency,
absence of discount/previous subscription, quantity and embedded price terms
before releasing its ID. A mismatch after POST retains the uncertain reservation
for operator review. Do not automatically create a replacement transaction.

## UI and confirmation

Show price, currency, tax mode, monthly renewal, no trial, cancellation guidance,
and existing terms/privacy/refund links. Require a confirmation checkbox. POST
requires a signed account/purpose/offer-bound 15-minute token, trusted Origin,
same-origin fetch metadata when present and explicit confirmation header. A tax
or offer configuration change invalidates the page's prior consent token.

Paddle.js uses the public Live token and only a server-created transaction ID.
The normal discount-entry option is hidden. This is UX, not a security boundary.
Never grant access from a browser callback. It triggers bounded polling of signed
server state. The UI distinguishes ready, pending, operator review, linked and
confirmed access; it disables duplicate clicks and reopening while the overlay
is open. Existing durable intent rules still prevent duplicate provider writes.

## Release gates and validation limits

This pilot remains off. Before opening sales, complete canonical financial/state
reconciliation (including changes after the checkout opens), refund/chargeback
policy, durable SQLite backup/recovery, monitoring, catalog/credential provisioning
and a controlled provider end-to-end test. Verify public terms/tax disclosures and
replace closed purchase notices in the same sales release. No general public
pricing link points at the pilot yet. Cancellation remains independently available
when sales are disabled, if its own management/cancel flags are configured.

ASGI tests cover bad catalog before mutation, changed transaction terms after
POST, default-off/roles/allowlist, CSRF cross-account/cross-purpose and changed
offer terms, existing paid account protection, expiry, one transaction on repeated
requests and signed completion → subscription → access without users.json writes.
Local native Chrome QA used a stub payment widget and synthetic ledger/API only:
consent/button interaction, transaction request and confirmed access rendering.
This does not verify the real Paddle-hosted checkout or real payment methods.

References:
- https://developer.paddle.com/api-reference/prices/get-price/
- https://developer.paddle.com/api-reference/transactions/create-transaction/
- https://developer.paddle.com/paddle-js/methods/paddle-initialize/
- https://developer.paddle.com/sdks/components/foundation/
