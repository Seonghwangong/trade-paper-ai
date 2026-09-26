# Audited recovery of uncertain checkout and cancellation operations

The default-off host CLI uses provider GETs only. It never creates a transaction,
repeats a cancellation, clears a pending guard, overwrites signed events or grants
access. It confirms a selected outcome with two matching reads and an explicit
preview digest, then updates the operation and audit journal atomically.

## Correlation before checkout creation

New server checkout attempts commit a random 256-bit token in
`live_checkout_correlations` in the same transaction as the pending operation,
before calling Paddle. The token is sent as `custom_data.trade_paper_intent` without
an account ID, email or personal data. The response must echo the token before its
transaction is registered. A missing/malformed echo retains the pending guard.
Concurrent calls or a restart still cannot create another transaction.

The token is a correlation value, not an API credential, signature or independent
authorization. Recovery takes an operator-selected transaction ID from trusted
provider records and compares the canonical Live API transaction against the
preexisting local intent. Browser metadata never selects the local account or
directly establishes a binding. Do not copy tokens to other provider entities.

For an unregistered candidate, the exact token and original operation timestamp
must agree, origin must be `api`, and provider creation must fall within -5/+60
seconds of that intent and not in the future. Changed/removed metadata fails closed.
Older unknown operations without a token cannot be inferred from price, time, email
or account metadata. If the exact transaction was already registered by the server
and is also the operation's recorded target, that existing reservation can instead
establish the link. No search/listing or automatic candidate discovery is performed.

## Allowed outcomes

- **Checkout draft/ready:** exact current monthly catalog terms, automatic collection,
  no subscription, no discount and no payment attempts. Reuse the same transaction
  with a fresh 15-minute local window. Normal checkout reuse performs another GET;
  it cannot create a second transaction or reuse one that has become paid.
- **Checkout completed:** strict paid totals/capture/catalog checks pass. Register
  the transaction as `awaiting_completion`; do not bind ownership or enable paid
  access from the GET. Recover its missing signed completion/subscription events
  using the [controlled replay procedure](paddle-live-replay.md).
- **Cancellation:** use only the existing bound subscription/customer and pending
  operation target. Accept a canceled subscription or an active subscription with
  cancellation scheduled exactly at its current period end, still in the future.
  Missing/different schedules, customer mismatch and changing state leave the guard.
  Acknowledgment alone never changes signed subscription state or access.

Paid-but-not-completed, past-due, canceled checkout transactions, ambiguous metadata,
another account's transaction and recurring transactions are not silently adopted.
The absence of a provider result never proves the first request failed, so it never
authorizes another POST. Already confirmed operations are not reopened by this CLI.

## Trusted-host procedure

Coordinate maintenance across every running instance: keep Live ACCESS and CHECKOUT
off. Set `TRADE_PAPER_PADDLE_LIVE_OPERATION_RECOVERY=1` only for the trusted host
workflow, with the existing Live key/catalog configuration. Provider permissions
needed for this CLI are transaction/subscription reads. It does not add permissions
or remotely change deployment flags. Webhook reception may continue independently.

```sh
venv/bin/python -m app.paddle_live_operation_recovery \
  --account ACCOUNT_ID --kind checkout --transaction TRUSTED_TRANSACTION_ID \
  --operator OPERATOR_REF --case CASE_REF
venv/bin/python -m app.paddle_live_operation_recovery \
  --account ACCOUNT_ID --kind checkout --transaction TRUSTED_TRANSACTION_ID \
  --operator OPERATOR_REF --case CASE_REF --apply PREVIEW_DIGEST
```

For cancellation use `--kind cancel` and omit `--transaction`; its target must come
from the local operation. Use non-personal operator/case references. Preview opens
the existing ledger read-only. Apply refetches twice, verifies the digest and local
state again under the write lock, and rejects a preflight lasting over 60 seconds.
The pending operation must still match; a concurrent webhook/operator update forces
a new review. Provider reads cannot atomically freeze remote state, so later changes
remain subject to ordinary checkout GET checks and signed webhook processing.

`live_operation_recoveries` keeps the exact original local operation, minimal
provider result, provider-payload hash, correlation hash, operator/case/time and
canonical evidence digest. Raw provider data and the correlation token are omitted
from output/audit; the original token remains in its dedicated local table. Audit,
registration and operation acknowledgment either all commit or all roll back.
Backup/restore preserves these tables; diagnostics flag overdue completed-checkout
requests still awaiting signed binding as `checkout_confirmation_overdue`.

The CLI never enables sales/access or resumes a checkout for the customer. Inspect
signed receipts, paid-period coverage and cancellation/refund/dispute state before
restoring access/sales. Remaining launch work includes historical consumed-event
evidence restoration, verified uncertain-replay reset, operational backup/monitor
scheduling and controlled end-to-end provider lifecycle validation. Legacy unknown
requests without correlation require separate trusted evidence and remain blocked.

All implementation validation used synthetic providers and isolated ledgers. No
production transaction, cancellation, recovery, permission or Live flag was changed.

Sources:
- https://developer.paddle.com/api-reference/transactions/create-transaction/
- https://developer.paddle.com/api-reference/transactions/get-transaction/
- https://developer.paddle.com/build/transactions/custom-data/
- https://developer.paddle.com/api-reference/subscriptions/get-subscription/
