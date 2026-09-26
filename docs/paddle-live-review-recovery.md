# Live billing review recovery (default off)

This host-operator tool releases a conservative refund/dispute access hold only
when fresh Paddle reads establish resolved adjustment state and fully restored
Starter payments. It never refunds, cancels, retries a charge, creates ownership,
edits signed subscription snapshots, or changes users.json. There is no public
HTTP endpoint. Keep actual Live sales disabled pending the remaining release gates.

## Release criteria

- Existing server-established account/subscription/customer binding and signed
  active subscription snapshot must match the current subscription's access fields.
- All adjustment pages for that subscription must be available. Missing signed
  adjustment IDs, mismatched ownership, pending cases, approved refunds/credits,
  and unresolved disputes block release. Partial/tax refunds remain blocked too.
- Rejected/reversed adjustments qualify for further checks. An approved reversal
  must have a reversed original action for the same transaction.
- The initial checkout and every adjusted transaction must belong to this same
  account's subscription/customer and pass the existing strict completed-payment
  validator: agreed product/price/currency/tax, captured payment, no discount or
  credit, and adjusted totals restored to the original paid totals.
- Two fresh read rounds must agree, and local binding, signed snapshot, events and
  release coverage must remain unchanged. The entire review must finish within
  90 seconds; paid access must still be unexpired at commit.

Limits are intentionally conservative: 20 pages of up to 50 adjustments and at
most 50 transactions. Incomplete, malformed, unstable, oversized or unsupported
history stays on hold. Paging uses fixed Paddle API paths and validated cursors,
not a server-supplied next URL. Existing bounded transport refuses redirects.

## Trusted host workflow

Run from the backend repository on the trusted application host with the existing
Live database mounted. Configure the existing price/product/tax contract and Live
API key through the secret environment, never command-line arguments. The API key
needs subscription, transaction and adjustment read permissions; this tool issues
only GET requests. Set `TRADE_PAPER_PADDLE_LIVE_RECONCILE=1` explicitly. Preview
opens the existing database read-only and does not initialize or migrate it.

```sh
venv/bin/python -m app.paddle_live_reconcile \
  --account ACCOUNT_ID --operator billing-operator --case CASE_REFERENCE
```

Inspect the returned subscription, adjustment statuses, payment totals, covered
event count, access end and digest against the support case. Operator and case
references must be short opaque labels without personal details. They are audit
labels, not authentication: trusted host/OS access is the authorization boundary.

```sh
venv/bin/python -m app.paddle_live_reconcile \
  --account ACCOUNT_ID --operator billing-operator --case CASE_REFERENCE \
  --apply PREVIEW_DIGEST
```

Apply re-fetches all provider evidence. It does not trust a saved report. Any
changed evidence, account, operator or case invalidates the digest; obtain and
inspect a new preview. Repeating a successful apply finds no unresolved events;
it does not create another release. Failures exit nonzero without printing raw
provider responses or secrets. Do not delete evidence or insert coverage manually.

## Audit and access behavior

The additive schema-1 tables `live_review_releases` and `live_review_coverage`
record the operator/case/time, policy version, sanitized canonical evidence and
digest, and the exact signed event IDs resolved. Audit and coverage commit in
one SQLite transaction. Original adjustment rows and event receipts are retained.
Raw customer/card data, reasons and provider response bodies are not stored.

Readers suppress access if any review event lacks coverage. A new event ID—even
an older delayed event or a reversal—requires another review. Exact event-ID
replays stay deduplicated. Signed snapshots and the existing access flag still
govern access; release cannot extend a billing period, activate an inactive
subscription, override cancellation, or grant rights to another account. Old
ledgers without release tables remain readable and keep their existing holds.

Two read rounds are not a provider-side atomic snapshot. Provider changes after
the last read depend on signed notifications reaching the ledger. Reliable Live
notification delivery, reconciliation monitoring and exhausted-event recovery
are therefore still release gates. Production-consistent SQLite backup/restore,
ambiguous checkout/cancellation recovery, final partial-refund policy and actual
provider end-to-end verification remain unfinished. This tool has been tested
with isolated synthetic data; it was not run against a real customer or payment.

References:
- https://developer.paddle.com/api-reference/adjustments/list-adjustments/
- https://developer.paddle.com/api-reference/transactions/get-transaction/
