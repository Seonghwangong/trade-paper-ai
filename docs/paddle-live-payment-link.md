# Controlled payment-link landing page (default off)

Candidate default payment URL: `https://www.tradepaper.ai/subscription/paddle-payment`.
It is **not registered or enabled in production**. Do not register it until the
release gates below are satisfied. This is separate from the initial purchase
page, which continues to reject `_ptxn`.

## Existing transactions only

GET requires one well-formed `_ptxn`, an authenticated exact Owner and both
`TRADE_PAPER_PADDLE_LIVE_PAYMENT_LINK=1` and `..._MANAGE=1`. It reads the pinned
Live ledger without creating it. Query IDs never establish ownership. Cases:

1. **Initial purchase:** exact account reservation, ready/unexpired existing
   checkout operation, Free plan, pilot allowlist and every original checkout
   flag. Revalidate the catalog and fetched transaction. No creation endpoint is
   called, including on reopen. Already linked or uncertain purchases fail closed.
2. **Card update:** existing signed subscription/customer binding, PAYMENT_METHOD
   and WEBHOOK flags, valid separate Live token/secret. Provider transaction must
   match that binding and have origin `subscription_payment_method_change`,
   draft/ready status and zero calculated amounts. Fresh subscription must be
   active/automatic. Only matching Starter item/price and zero proration accepted.
3. **Overdue renewal:** the same binding/flags, `subscription_recurring` origin,
   past_due transaction and fresh past_due/automatic subscription. Require one
   full Starter monthly amount, KRW, exact price/tax/item, valid monthly period,
   consistent line/calculated totals and full unpaid balance. No discounts,
   credits, partial captures, prorated or adjusted totals. Other charges need
   support review. This path does not require new sales or access to be enabled.

All provider requests are GETs. Recheck local binding/review after network reads.
Refund/dispute review blocks opening. Missing/corrupt storage, incomplete provider
data, a different owner/transaction/subscription, or changed terms fail closed.

## Explicit review, no automatic checkout

Render a purpose-specific review page, without a client token. Remove the query
from browser history **before loading Paddle.js**, so `_ptxn` cannot trigger its
automatic open behavior. Show zero card-update amount, recurring purchase terms,
or actual overdue total as applicable. Require the review checkbox/button.

POST `/subscription/paddle-payment/open` checks trusted Origin, fetch metadata,
explicit confirmation and a 15-minute account/context-bound CSRF token before
provider access. Context includes transaction, purpose, amount and exact offer.
Repeat selection/ownership/amount/state checks and require unchanged context.
Only then return the public token and checked transaction ID; initialize/open
Paddle explicitly. No item array, new transaction, ownership write or access
grant occurs. Browser completion only displays a pending-confirmation message.
Use `no-store` and `no-referrer` throughout.

## Unfinished release gates

- PAYMENT_LINK stays off; default URL remains unset. Bare, malformed or expired
  links display an error. Users must log in and reopen their original link if
  their session expires. Verify the real Paddle/email/login sequence before
  registering this candidate URL.
- Actual Paddle.js and hosted checkout still need controlled provider QA. Local
  browser QA uses a synthetic widget; no real transaction/charge/card update was
  performed.
- The existing signed transaction handler treats completions as paid purchases
  or paid recurring renewals. A **zero-amount payment-method-change completion
  currently fails its paid-amount validation**. Add a dedicated authenticated,
  ownership-checked non-entitlement receipt path, plus backup/replay/monitor
  compatibility, before activating card-update links or the portal feature.
- Preserve the current sales/access gates until operational reconciliation,
  backups/monitoring and controlled financial tests are complete.

References reviewed 2026-09-27:
- https://developer.paddle.com/build/transactions/default-payment-link/
- https://developer.paddle.com/api-reference/subscriptions/get-subscription-update-payment-method-transaction/
- https://developer.paddle.com/build/subscriptions/update-payment-details/
