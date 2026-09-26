# Read-only Live billing diagnostics

`python -m app.paddle_live_monitor` checks the existing ledger, a selected verified
backup, and optionally Paddle's retained event stream. It never replays events,
modifies access, retries a checkout/cancellation, refunds, sends notifications,
creates a ledger or exposes a public route. Scheduling and alert delivery are not
configured by this change.

The [host job runner](paddle-live-jobs.md) now combines verified backup inventory
with these diagnostics and an independent missed-run receipt check. Deployment
scheduling and outbound alert wiring still require explicit host configuration.

## Run on the trusted host

Start with local diagnostics and the archive/hash from the trusted backup inventory:

```sh
venv/bin/python -m app.paddle_live_monitor \
  --ledger /durable-data/paddle_live.sqlite3 --price-id LIVE_PRICE_ID \
  --backup /private-backups/paddle-live-TIMESTAMP.zip \
  --backup-sha256 RECORDED_SHA256
```

Local-only mode reports `provider_check_skipped` as a warning. It cannot establish
whether a webhook is missing. To compare provider events, explicitly set
`TRADE_PAPER_PADDLE_LIVE_MONITOR=1`, configure the existing Live API key through the
secret environment with `notification.read` permission, and add `--with-provider`.
No key is accepted as a command-line argument and no provider state is mutated.

```sh
venv/bin/python -m app.paddle_live_monitor \
  --ledger /durable-data/paddle_live.sqlite3 --price-id LIVE_PRICE_ID \
  --backup /private-backups/paddle-live-TIMESTAMP.zip \
  --backup-sha256 RECORDED_SHA256 --with-provider
```

The JSON result contains codes, counts, timestamps and at most 20 missing/conflicting
event IDs per issue. It excludes customer/account rows, subscription/transaction
identifiers, raw payloads, reasons, paths and credentials. Exit status is 0 for
checked conditions passing, 1 for warnings, 2 for critical findings or configuration
errors. Exit 0 is **not** a Live-launch readiness decision or a proof of full history.

## Scope and thresholds

- Default event window: last 24 hours, excluding the newest 300 seconds for delivery
  grace. `--lookback-hours` supports 1–2160; `--grace-seconds` 1–3600 and must be
  shorter than the window. Paddle retains events for 90 days. Use overlapping
  windows when scheduling; outages beyond retention require another recovery source.
- Only handled transaction-completed, subscription and adjustment event types are
  requested. Each response is validated and all pages must complete: 20 events/page,
  maximum 50 pages/1000 events and a 60-second scan deadline. Malformed, duplicate,
  out-of-window, truncated, timed-out or unavailable results cannot report complete.
  Pagination constructs fixed Paddle API paths and validated cursors; it never
  follows the returned next URL.
- Initial completed transactions are tracked only when their transaction IDs were
  registered by the server. Recurring completions require an existing subscription
  binding and are compared against both event and transaction receipt mappings.
  Subscription events require an existing binding;
  adjustments are tracked by a known subscription or registered transaction.
  Unknown events for this price or ambiguous ownership produce a warning. Explicit
  other-price entities are counted as unrelated. Emails/custom_data never establish
  ownership. No normal monthly inactivity warning is inferred from event silence.
- Registered events are compared with receipt IDs, occurrence times and expected
  result categories. Comparison reads the ledger **after** the provider scan, in a
  consistent local read transaction, reducing false positives from concurrent delivery.
  A webhook that arrives after that read may clear the finding on the next run.
- Unconfirmed operations older than 900 seconds are critical; provider-acknowledged
  cancellations lacking matching signed state are warnings after that same age.
  `--operation-age-seconds` supports 60–86400. Missing snapshots, review holds and
  active snapshots whose period/scheduled stop ended beyond grace are also reported.
- The specified archive is fully verified with the independent SHA256 and Live
  price. Age is taken from its verified manifest, **not** filesystem mtime. Default
  maximum age is 24 hours (`--backup-age-hours`, 1–720). Future dates beyond grace,
  corruption or missing files are critical; no archive/hash configuration is a warning.
  Choose the correct ledger's archive from a trusted inventory. This check does not
  establish backup lineage, test an off-host copy or prove post-backup changes are
  recoverable. It also cannot detect a failed backup attempt that left a still-fresh
  previous archive; connect backup job exit status to the scheduler's alerts separately.

## Findings and response

| Code | Meaning / next action |
| --- | --- |
| `ledger_unavailable_or_invalid` | Check storage, schema/price configuration and the separate backup verifier. Do not recreate an empty production ledger. |
| `provider_check_skipped`, `provider_check_incomplete` | Event coverage was not established. Check opt-in/key permission, provider health, time window and scan limits. |
| `provider_events_missing_locally` | Tracked provider events older than grace lack local receipts. Inspect delivery logs and ownership/order conflicts; recover through trusted signed replay. |
| `provider_receipt_conflict` | Receipt time/type differs from canonical evidence. Escalate for investigation; do not overwrite receipts. |
| `provider_events_without_local_ownership` | Price/ownership scope is unresolved. Match against trusted checkout records; never bind from email. |
| `unsupported_subscription_completion` | A bound subscription has an unregistered completion with an origin other than `subscription_recurring`. Investigate unsupported one-time charges or subscription changes; do not bind from metadata. |
| `ambiguous_operations_overdue` | Checkout/cancel outcome is still uncertain. Query provider state; do not repeat a financial POST. |
| `checkout_confirmation_overdue` | Recovery registered a completed transaction, but its signed ownership binding is still missing. Inspect signed delivery/replay; do not ask the customer to pay again. |
| `notification_replay_unconfirmed` | A recorded replay request still lacks its signed local receipt after the operation age limit. Inspect provider delivery logs/local replay status; never automatically repeat an uncertain POST. |
| `cancellation_confirmation_overdue`, `subscription_confirmation_overdue`, `bound_subscriptions_without_snapshot` | Expected signed subscription state is absent/stale. Investigate delivery and canonical provider status. |
| `billing_reviews_pending` | Existing adjustment evidence requires the audited recovery workflow, not an automatic release. |
| `active_period_payment_unconfirmed` | An active period has no matching completed initial/recurring payment evidence after delivery grace. Access is withheld immediately. Check signed deliveries and exact period ownership; do not create another payment. |
| `ledger_clock_ahead`, `backup_clock_ahead` | Check host/provider timestamps before trusting age-based checks. |
| `verified_backup_not_configured`, `backup_unavailable_or_invalid`, `backup_too_old` | Fix backup inventory/configuration or create and verify a new snapshot; preserve existing evidence. |

No production monitoring scan, scheduler, external alert or real financial operation
was run during implementation. Synthetic tests exercise the complete CLI and read-only
comparisons. Standard recurring completions now have [signed payment evidence](paddle-live-renewals.md).
Access now requires exact paid-period evidence as well as the signed active snapshot.
The [controlled replay CLI](paddle-live-replay.md) can request a selected missing
notification through normal signed ingress during access/sales maintenance.
The [operation recovery CLI](paddle-live-operation-recovery.md) can acknowledge proven
checkout/cancel outcomes using GETs only, with a preserved audit and no second POST.
Before launch, resolve historical/uncorrelated evidence recovery, production backup scheduling/retention
and controlled provider lifecycle testing. Monitoring alone does not resolve these conditions.

Sources:
- https://developer.paddle.com/api-reference/events/list-events/
- https://developer.paddle.com/api-reference/about/pagination/
