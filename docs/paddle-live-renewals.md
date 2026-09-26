# Signed recurring payment evidence

The Live webhook recognizes `transaction.completed` with origin
`subscription_recurring` for an already bound subscription/customer. It records
one payment per transaction and a receipt for every distinct authenticated event.
It does not create a checkout, bind another account, request a charge or extend access.

## Acceptance and atomicity

The existing signature, delivery freshness and event identity checks run first.
The existing completion validator requires the configured Live price/product,
monthly single-item quantity-one automatic collection, exact currency and tax-aware
amounts, one completed capture, and no discounts, credits or proration.

Renewals additionally require an existing exact subscription/customer binding,
a timezone-aware positive billing period of at most 32 days, and a transaction ID
unused by any initial checkout reservation. Known adjustment ownership must agree.
Unknown ownership and changed transaction identity or paid terms fail without
consuming the event, allowing an out-of-order delivery to retry after ownership exists.
Nonrecurring unregistered completions retain the existing unregistered/retry behavior.

`live_renewals` stores the transaction, subscription, customer, account and canonical
minimal paid terms with a digest. `live_renewal_receipts` maps authenticated event IDs
to transactions. Raw addresses, invoices and customer metadata are not saved.
Both tables and the main event receipt commit in the same SQLite transaction.
Concurrent duplicate events produce one record; a distinct event for unchanged
transaction terms produces `renewal_existing`. A changed period or paid identity
for an existing transaction is a conflict, not a replacement.

The schema-1 extension is additive on writable opens. Read-only diagnostics and
backup inspection accept legacy ledgers without adding tables. Backup verification
requires a complete extension, matching ownership/digests, exactly one originating
payment receipt and no orphan mappings. Read-only monitoring compares known recurring
provider events to the local event and exact transaction mapping. It never replays them.

## Access and remaining launch gates

Completion evidence does not write subscription snapshots, remove refund/dispute
holds, reactivate a canceled subscription or change account JSON. Access now requires
both the signed active snapshot and [matching completed payment period](paddle-live-paid-access.md),
with review holds and scheduled stop boundaries still enforced.

Paddle can emit a renewed `subscription.updated` before payment completes. The read
policy denies access to that period until its payment receipt arrives; receipt-first
delivery also waits for the matching active snapshot. No unpaid grace period is
granted. Completion alone cannot extend an expired snapshot. Controlled provider
lifecycle testing remains required before enabling Live access.

One-time renewal add-ons, prorations, manual collection and changed catalog terms
remain unsupported and must not be silently accepted. General signed-event recovery,
ambiguous-operation recovery and operational backup/alert scheduling also remain
separate launch gates. Live payment/access flags stay disabled.

Validation uses synthetic signed events: concurrency, changed ownership/terms,
rollback, out-of-order binding, cancellation and dispute preservation, actual HTTP
dispatch, isolated backup/restore and provider-monitor receipt matching. No production
renewal, charge, refund or replay was performed for this change.

Sources:
- https://developer.paddle.com/webhooks/transactions/transaction-completed/
- https://developer.paddle.com/webhooks/simulator/subscription-renewed/
- https://developer.paddle.com/changelog/2023/subscription-charge-transaction-origin/
