# Zero-value card-update receipts

The signed Live webhook recognizes `transaction.completed` with origin
`subscription_payment_method_change` separately from paid purchases and renewals.
This supports recording provider notifications; it does not enable card changes,
checkout, sales, access, management, or the default payment-link landing page.

## Contract and ownership

Require completed/automatic status, the exact pinned Starter catalog, KRW,
quantity one, no discount, zero totals/adjusted totals/line totals/payment amounts,
and absent or zero proration. The underlying recurring price remains KRW 29,000.
The landing page shares this validator for draft/ready transactions.

An existing signed subscription/customer/account binding is mandatory. An early
unbound event is rejected without consuming its event ID, so a later signed
redelivery can succeed. Reject transaction identity collisions with checkout or
renewal records and conflicting adjustment ownership, in either arrival order.

## Non-entitlement evidence

Store minimal identity and canonical catalog/zero-amount terms in
`live_payment_methods`, with per-event `live_payment_method_receipts` linked to
the authenticated event digest. No card details, customer email, custom data,
payment-method URL, or paid period is saved. Duplicate delivery is idempotent;
distinct event IDs for identical transaction terms link to the same record.

Results are `payment_method_recorded` or `payment_method_existing`. Neither
changes bindings, subscription snapshots, paid-period evidence, subscription JSON,
or refund/dispute review state. It cannot extend access or revive a canceled plan.

## Recovery and operations

Writable stores add both tables. Read-only legacy ledgers remain unchanged and
valid if they have no card-update receipts. Backup validation checks paired
tables, catalog/digest, binding, transaction collisions and complete receipt/event
relationships. Restore preserves duplicate handling without creating entitlements.

Replay validates ownership and zero-value terms and requests provider redelivery;
it never applies an unsigned API payload. Monitoring counts these as
`payment_method`, reports missing signed receipts and conflicting identities or
amounts. Existing operational feature flags remain required.

Local tests cover signed HTTP delivery, malformed/nonzero payloads, concurrency,
ownership conflicts, future-period access denial, refund/cancellation retention,
legacy migration, backup corruption/restore, monitoring and replay. No real card
change or payment was performed. Real Paddle end-to-end QA and the remaining
operational release gates must pass before enabling customer card-update links.
