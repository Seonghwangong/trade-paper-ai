# Optional billing operator email

This is an opt-in extension to the [same-instance scheduler](paddle-live-jobs.md).
It uses the existing configured Resend/SMTP delivery adapter and sends only fixed
status/reason text and a UTC timestamp. It attaches no files and includes no
customer rows, account/provider IDs, ledger paths, event payloads or credentials.
An accepted mail API response is not proof of inbox delivery.

## Configuration (not enabled by installing code)

- `TRADE_PAPER_PADDLE_LIVE_ALERTS=1`: explicit mail opt-in. Any other value is a
  no-op without storage access or delivery. The scheduler itself remains gated by
  `SCHEDULER=1` and `JOBS=1` and requires an existing valid ledger.
- `TRADE_PAPER_PADDLE_LIVE_ALERT_RECIPIENT`: one explicitly selected operator
  address. Never default to a customer, reply-to address or browser input.
- `TRADE_PAPER_PADDLE_LIVE_JOBS_DIRECTORY`: the existing private absolute job
  directory. Own it as the service user with mode 0700 and trusted parent paths.
- The existing `TRADE_PAPER_EMAIL_*` settings must report Ready. For Resend this
  includes a verified sender-domain flag and a non-resend.dev sender. No new mail
  permissions or credential are created by this code.

An enabled invalid email configuration fails scheduler startup. When the scheduler
is disabled it ignores even invalid alert configuration and starts no job or mail.
Recipient changes invalidate the saved alert identity: preserve the old receipt
for audit and deliberately prepare a new receipt/directory rather than silently
carrying old notification state to a new recipient.

## Lifecycle and throttling

Scheduler outcomes map to `ok`, `warning`, or `critical`. Fixed reasons are job
exit, timeout, launch failure and loop failure. Nonzero billing-job exits remain
visible as warnings/errors even when mail delivery succeeds. Normal service
shutdown skips email; its incomplete job receipt remains available to the next
cycle or independent observer.

- Initial healthy cycles are quiet.
- First warning/failure is sent. Identical ongoing severity is reminded at most
  every six hours after provider acceptance. A critical escalation bypasses this
  suppression; lower severity during an incident is not immediate recovery.
- An `ok` cycle sends one recovery message only if an incident email was accepted.
  This is recovery of checked conditions, not permission to launch Live billing or
  proof of a payment, refund, off-host backup or end-to-end provider coverage.
- Failed/ambiguous sends are retried no sooner than 30 minutes, except critical
  escalation or the first recovery transition. Failed recovery attempts retain the
  incident marker so a later cycle can retry the recovery notice.

Each mail runs in a separate child with a 20-second deadline plus up to five
seconds for termination. It cannot block HTTP requests. The scheduling interval
also includes mail time: adjust external freshness thresholds for the configured
job timeout plus up to 25 seconds of mail cleanup, especially with short intervals.

An exclusive private `alert.lock` prevents concurrent sends in one directory.
`alert-state.json` is written and fsynced before sending, then atomically updated
after the mail adapter returns. Mode 0600 and recipient/directory identity checks
apply. It stores only status, fixed reason, attempt time, acceptance and whether an
incident was notified. Unexpected process death after API acceptance but before
the final write is ambiguous; a later retry can duplicate a message. This is not
exactly-once email delivery. Do not automatically delete/reset these receipts.

Storage corruption/permission failures, clock rollback or a changed recipient fail
closed and prevent sending. Scheduler logs capture generic mail child failures;
private paths, recipient, payload and exception strings are not printed.

## Preview and activation evidence

After reviewing the selected recipient and settings on the trusted host:

```sh
python -m app.paddle_live_alerts --status critical --reason timeout --preview
```

Preview can create the private lock file but sends no email and does not modify
the throttle receipt. It reports `would_send`, `suppressed`, `healthy_quiet`,
`disabled` or `busy`. A non-preview invocation with ALERTS=1 sends according to
the same rules; use it only after the operator's recipient/purpose are agreed.
The implementation tests use fake mail adapters, including a real child-process
preview with synthetic environment settings; they do not prove real delivery.

Before enabling production, send one clearly identified controlled test notice to
the agreed recipient and verify receipt, failure/recovery behavior and repeated
alert suppression. Do not treat an exit code alone as inbox confirmation.

## Still required outside this application

The service cannot email when its host/network is down. This extension also cannot
alert when its own directory is full/unreadable. An independently hosted observer
must detect availability/missed runs and alert via an independently working path.
Health HTTP 200 alone is insufficient for backup freshness. This email extension
does not configure that observer, encrypted external storage or retention. Those
remain release gates, and real sales must stay closed until the agreed gates pass.
