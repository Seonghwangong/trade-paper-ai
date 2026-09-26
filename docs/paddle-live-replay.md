# Controlled redelivery of a missing signed event

`python -m app.paddle_live_replay` is a default-off trusted-host tool for one selected
original Paddle notification. It does not import GET payloads as signed events, mint
signatures, disable timestamp checks, charge customers or expose a public operator API.
Paddle's replay endpoint creates a new notification for the **same event ID**; normal
webhook signature verification, ownership, deduplication, snapshot ordering and paid
period/review rules remain the only path that changes billing evidence.

## Preconditions

1. Diagnose the missing event and fix its original delivery/validation/ownership
   problem. Obtain the original notification ID from trusted provider delivery logs;
   this tool does not guess it from an event ID or create a lost checkout reservation.
2. Use a verified backup and a trusted application host with the existing durable
   ledger. Coordinate maintenance across all service instances: Live ACCESS and
   CHECKOUT must be off while WEBHOOK reception remains enabled. The CLI checks its
   environment; operators must ensure it represents the running deployment. It does
   not remotely turn off other processes or restore sales/access afterward.
3. Explicitly set `TRADE_PAPER_PADDLE_LIVE_REPLAY=1`. Configure the existing Live API
   key with `notification.read`, `notification.write` and `notification_setting.read`
   permissions through secret configuration, never command-line key arguments.
4. Set `TRADE_PAPER_PADDLE_LIVE_NOTIFICATION_SETTING_ID` to the approved original
   destination. The tool requires an active version-1 URL destination of exactly
   `https://www.tradepaper.ai/webhooks/paddle-live`, platform-only traffic, a matching
   event subscription and the same secret as the local Live webhook configuration.
   It does not create/change a destination or print/store its secret.

Only retained original notifications with status failed/delivered and origin event
are eligible. Pending automatic retries, simulation traffic, replay-of-replay,
unknown ownership, other accounts, unhandled events and events outside the bounded
90-day occurrence window are rejected. The provider may reject older/unavailable
notifications even within the tool's occurrence check; do not bypass retention.

## Preview, apply, inspect

Preview performs only GETs and a consistent read of local ownership/receipts. It does
not create tables or modify a legacy ledger. Use non-personal operator/case references.

```sh
venv/bin/python -m app.paddle_live_replay --account ACCOUNT_ID \
  --notification ORIGINAL_NOTIFICATION_ID --operator OPERATOR_REF --case CASE_REF
```

Review event ID/type/time, original notification and exact destination. Apply requires
the preview digest and refetches provider evidence; changed evidence/ownership fails.

```sh
venv/bin/python -m app.paddle_live_replay --account ACCOUNT_ID \
  --notification ORIGINAL_NOTIFICATION_ID --operator OPERATOR_REF --case CASE_REF \
  --apply PREVIEW_DIGEST
venv/bin/python -m app.paddle_live_replay --status EVENT_ID
```

Apply records an additive `live_replay_requests` reservation keyed by event ID before
its single POST to `/notifications/{id}/replay`. It accepts only a valid HTTP 202 with
a distinct new notification ID. API timeout, malformed response, process crash or
acknowledgment-write failure retain the guard. Concurrent calls and restarts never
repeat that event's POST automatically. Only IDs, references, times and a preview
digest are journaled; raw event payloads, addresses and secrets are not stored.

Results distinguish `awaiting_signed_delivery`, `outcome_unknown`, `already_requested`,
`already_received` and `received`. Provider acceptance alone is not delivery success.
`received` requires a local event receipt with matching occurrence/type result; it
can include a safely ignored stale snapshot and is **not** an access/launch approval.
The status operation is local/read-only and needs no provider key or maintenance flags
other than the tool opt-in. Monitor findings mark requests without a local receipt
after the operation age limit (default 15 minutes); backup/restore preserves guards.

## Limits and recovery gates

- A replay request followed by a timeout might already have succeeded. Inspect its
  original/replay delivery logs and local status. Do not delete the guard or change
  event IDs to force another attempt. A verified retry/reset workflow is not provided.
- Already-consumed events remain deduplicated. This tool cannot backfill missing
  initial period evidence into historical receipts or recover lost checkout ownership.
- Provider GETs and the replay POST are not atomic with remote settings or events;
  the bounded preflight reduces drift but cannot prevent a later remote change.
  A normal webhook can arrive after the local reservation; dedupe handles that race.
- Recover all relevant missing lifecycle events and reconcile canonical provider
  state before restoring access/sales. An isolated old active event cannot prove
  that a later cancellation or refund was not missed. Keeping access off during
  recovery is mandatory; this command never enables it.
- No bulk replay, automatic retry, notification selection, historical backfill,
  production scheduler or outbound operator alert is installed. Controlled provider
  lifecycle testing, ambiguous checkout/cancel recovery and deployment operations
  remain launch gates.

Implementation tests use synthetic providers and signed payloads only. No production
replay, API permission change, Live flag change, payment, refund or cancellation was
performed while adding this tool.

Sources:
- https://developer.paddle.com/api-reference/notifications/replay-notification/
- https://developer.paddle.com/api-reference/notifications/get-notification/
- https://developer.paddle.com/api-reference/notification-settings/get-notification-setting/
- https://developer.paddle.com/webhooks/about/how-webhooks-work/
