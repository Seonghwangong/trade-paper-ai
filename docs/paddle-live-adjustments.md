# Live adjustment handling (pre-release)

The default-off Live webhook now handles `adjustment.created` and
`adjustment.updated`. Both event types must be enabled on the **Live** notification
destination before paid access is released. This code does not configure that
destination, send refunds, cancel subscriptions, or activate Live sales.

## Access policy

| Signed status | Local behavior |
| --- | --- |
| `pending_approval`, `rejected` | Record evidence; do not create a new access hold. |
| `approved`, `reversed` | Record evidence and require billing review. |

Supported actions are refunds, credits, chargebacks, chargeback warnings, and
their documented reversal actions. Full, partial, and unspecified adjustment
scope all require review when approved/reversed. This intentionally includes
tax-only refunds: this first release does not infer remaining paid service from
an adjustment amount. It is an operational hold, not a final refund policy.

Review suppresses Starter access and its displayed end date while retaining the
provider's subscription status. Free-plan limits apply. Billing support and
renewal cancellation remain available; checkout cannot create a second purchase.
A review hold does **not** stop Paddle renewals. The customer or operator still
needs the existing cancellation workflow when cancellation is appropriate.

## Provenance, ordering, and storage

The existing raw-body Live signature check runs before storage access. An
adjustment must name an already-bound subscription and its exact customer.
Initial transaction reservations and previously observed adjustment identities
must not conflict. A signed adjustment can identify a renewal transaction through
that existing subscription/customer pair; it never creates account ownership.
Email and `custom_data` are not used. Unbound evidence returns HTTP 409 without
consuming the event, allowing retry after checkout binding.

`live_adjustment_events` is an additive schema-1 table, created on writable open.
The old ledger remains readable before migration. Minimal identifiers, action,
status, type, currency, total, event time and review flag are stored; reasons,
customer details, card data and full payloads are not. Evidence and the event
receipt commit atomically. Event IDs deduplicate repeated deliveries.

Review is monotonic unless an explicit audited operator recovery covers its exact
event IDs. Late pending/rejected evidence, subscription updates, later completed
checkouts and reversal evidence cannot restore access on their own.
This avoids restoring access when delivery order differs from occurrence order.
Do not delete evidence rows to resolve a case.

## Remaining release gates

The default-off host operator workflow in [paddle-live-review-recovery.md](paddle-live-review-recovery.md)
now compares current provider subscription, transaction and adjustment state and
records exact-event review coverage. It only supports resolved cases with fully
restored payments; approved partial/tax refunds stay blocked. Alerting/replay of
exhausted or unbound notifications and production SQLite backup/restore remain
unfinished. Until those are ready and the actual provider lifecycle is verified,
keep Live activation flags off. Tests use synthetic events and isolated DBs.

## Completed-transaction tax correction

For a tax-inclusive KRW 29,000 offer, a Korean 10% VAT example is subtotal
26,364 plus tax 2,636 equals total 29,000. The previous validator incorrectly
required subtotal 29,000 too. It now checks subtotal + tax = total, and pins the
inclusive total or exclusive subtotal to the agreed price. Line and adjusted
totals must remain consistent; discounts/credits are still rejected.

Sources:
- https://developer.paddle.com/webhooks/adjustments/adjustment-created/
- https://developer.paddle.com/webhooks/adjustments/adjustment-updated/
- https://developer.paddle.com/webhooks/transactions/transaction-completed/
- https://developer.paddle.com/changelog/2025/tax-exclusive-refunds/
