# Access requires a matching completed payment period

An opt-in Live account receives Starter access only when all these conditions hold:

- The latest signed subscription snapshot belongs to its server-bound subscription
  and customer, is active, and the current time is inside its billing period.
- A validated signed initial completion or recurring completion covers **exactly**
  that period for that ownership. Compare normalized UTC instants for both boundaries,
  not overlap, payment date, newest payment, or historical maximum expiration.
- A scheduled cancellation/pause has not taken effect, and no refund/dispute review
  hold applies. Completion cannot release a hold or reactivate a canceled subscription.

No unpaid grace is granted. Monitoring's delivery grace delays an alert only; it
does not grant access. A future period/payment does not cover an unpaid current one.
The latest snapshot remains authoritative: the reader does not fall back to an older
active snapshot when a newer period arrives early or when the status changes.

## Ordering and initial evidence

Paddle can send the new-period subscription update before payment completion.
Subscription-first delivery therefore returns no paid access/no confirmed access end
until matching payment evidence arrives. Payment-first delivery waits for the matching
subscription snapshot. Both are ordinary acknowledged events; access is recomputed
from one consistent read transaction without writing entitlements into account JSON.

The additive `live_initial_periods` table records a validated initial completion's
transaction, event receipt and normalized billing period, atomically with ownership
and the main receipt. Different events cannot change the recorded period. Initial
`billing_period` may be null in provider data: ownership can still bind, but this
case grants no initial access. A malformed non-null period rolls back the event.
Periods must be positive, timezone-aware, and no longer than 32 days for this monthly
single-item offer. Recurring evidence uses the existing renewal tables and paid-term
digests. Raw payment/customer payloads remain unstored.

Legacy ledgers open read-only without migration. Missing initial evidence is **not**
backfilled from existing snapshots, binding timestamps or old event digests. Writable
opens only add an empty table. Replaying an already-consumed event ID remains a no-op;
an operator would need a separate verified recovery workflow for historical evidence.
No production migration/backfill has been performed. Existing correctly recorded
recurring evidence can still prove its own exact paid period.

## Consistent consumers and recovery

Runtime entitlements and the billing management screen use the same paid-period
decision within their read transaction. Management shows payment confirmation pending
and asks the customer not to pay again. Cancellation remains available even while
payment evidence is pending. The review-release CLI checks paid coverage again under
its final database transaction before preview/apply can succeed; review release cannot
bypass this gate.

Backup/restore includes initial evidence and validates period/ownership/receipt links.
Legacy backups remain accepted as historical evidence, not automatically entitled
state. Read-only diagnostics report `active_period_payment_unconfirmed` after delivery
grace if an active period lacks matching payment evidence. No replay, charge, refund,
schedule, external alert, or Live flag is enabled by this change.

Synthetic tests cover both arrival orders, null/legacy evidence, restarts, duplicates,
future/wrong/overlapping periods, timezone and exclusive-end boundaries, canceled/
past-due/paused/trial status, scheduled stops, review holds, initial write rollback,
management/runtime agreement, continued cancellation, monitoring and isolated restore.
Real provider lifecycle validation and safe historical replay/ambiguous-operation
recovery remain launch gates.

Sources:
- https://developer.paddle.com/webhooks/simulator/subscription-renewed/
- https://developer.paddle.com/webhooks/transactions/transaction-completed/
