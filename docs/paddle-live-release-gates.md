# Paddle Live release gates

Consolidated September 29, 2026. This is an execution plan, not proof that current
runtime settings have just been read. Operator evidence comes from September 28
and the September 29 public-sample deploy. Never infer a Live payment from Sandbox
results, a browser completion callback or a provider `active` snapshot alone.

## Evidence already completed

| Evidence | Scope and limit |
|---|---|
| Server-created checkout, signed notifications and account binding | Actual Sandbox flow; not Live billing |
| Zero-value card change | Actual Sandbox provider response and deployed app UI; paid period unchanged |
| Overdue recovery | Actual Sandbox failed payment and recovery, app confirmation and delivered notifications |
| Live API key connection and catalog GET validation | September 28: correct management ID, 6 read / 2 write permissions; no Live financial write |
| Host backup and isolated restore | September 28: empty drill ledger on `/var/data`, not a real financial ledger |
| Local recovery ZIP | September 29: source, 46 local JSON files and deliverables restored; not a Render database backup |

The last runtime billing inspection found only WEBHOOK enabled; ACCESS, CHECKOUT,
MANAGE, CANCEL, PAYMENT_LINK, PAYMENT_METHOD, JOBS and MONITOR were disabled. The
Live ledger did not exist. The API-key expiry displayed October 28, 2026, 12:51pm;
the provider UI timezone was not independently confirmed. Recheck validity before
any later release; do not paste the key into this runbook.

## Ordered remaining work

### 1. Finalize the operational deployment

- Pin the actual service user, Live ledger path and price. The candidate ledger
  path is `/var/data/paddle_live.sqlite3`; do not create it just to make a status
  check green.
- Run the existing [backup job](paddle-live-jobs.md) on the same service instance
  as the disk. Render Cron/one-off jobs cannot read this disk.
- The default-off application scheduler now supplies controlled startup/restart/
  shutdown wiring, with local subprocess tests for timeout, overlap and recovery.
  Configure and drill it deliberately before relying on it. Defaults: one
  diagnostic cycle every 15 minutes, backup every six hours and a separately
  configured 30-minute missed-run threshold. These are not active production
  jobs or a promised recovery objective.
- Select an encrypted off-host destination and independently observed alert
  channel. Verify a copied archive, interruption/staleness detection and isolated
  restore. Require adequate disk capacity and an explicit retention policy.
- The default-off [operator mail extension](paddle-live-alerts.md) can report
  local cycle failures/recovery using the existing email adapter. Its recipient
  must be explicit and actual delivery verified. It cannot detect a dead host or
  replace independent missed-run monitoring; those connections remain open.
- Treat an empty-ledger drill as scaffolding only. After the controlled Live flow,
  repeat backup/restore against actual recorded evidence before public rollout.

### 2. Prepare an explicitly bounded Live pilot

- Confirm the exact owner account and canonical allowlist ID, remaining Free
  status, approved domain, distinct Live secrets/token and current catalog.
- Deliberately initialize/migrate the pinned ledger with a pre-change snapshot
  and rollback plan; do not copy Sandbox ownership or local development data.
- Review the required flag combinations in [checkout](paddle-live-checkout.md)
  and [payment links](paddle-live-payment-link.md). Keep public pricing closed.
- Obtain the user's concrete go-ahead for the account, real charge amount and
  intended cancellation/refund handling before the financial test. Do not infer
  permission for a real charge from a prior Sandbox recovery approval.

### 3. Connect the default payment URL and test recovery navigation

Candidate: `https://www.tradepaper.ai/subscription/paddle-payment`.
Do not use `/subscription/paddle-buy` as the provider's default URL.

Paddle requires an approved website containing Paddle.js. The link is also used
for payment-method updates and recovery emails. Its ownership, login/reopen,
query removal and explicit-review flow must work for the pilot before customers
are directed there. The last dashboard inspection found the Live URL blank;
this runbook neither registers it nor switches features on.

### 4. Complete payout setup and controlled financial verification

The September 28 payout inspection found account type unset and representative /
Payoneer email fields blank. The owner must supply and complete the actual payout
details; never guess banking or identity information from a company profile.

For the approved pilot, verify server transaction creation, the actual checkout,
signed payment and subscription evidence, exact paid period, access, cancellation
and the agreed refund/recovery cases. Reconcile after-state with the provider.
Do not retry an ambiguous financial write by creating another transaction.

### 5. Open customer sales only after evidence is complete

Coordinate public price/renewal/tax/cancellation disclosures with the enabled
features. Record the bounded pilot outcome and operational recovery evidence.
Opening sales and disabling sales are separate from cancelling provider renewals.
Disabling an app flag does not undo charges or cancel existing subscriptions.

## Completion record required for each step

Record timestamp, deployed commit, exact action, sanitized result, source of
provider evidence and remaining limitations. Do not include credentials, card
details or raw customer payloads. A draft, implemented tool or green unit test is
not an executed production operation.

## Sources

- https://render.com/docs/cronjobs
- https://render.com/docs/disks
- https://developer.paddle.com/build/transactions/default-payment-link/
- [Runtime and access behavior](paddle-live-runtime.md)
- [Backup and restore procedure](paddle-live-backup.md)
