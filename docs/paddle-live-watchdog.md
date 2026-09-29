# Optional external scheduler watchdog

The same-instance scheduler can send a completion signal to a separately hosted
Healthchecks check. If the server, scheduler or network stops, missing signals can
trigger notifications at the provider. No account, check, integration, recipient
or ping URL is created by this code. No public route or dependency was added.

## Before enabling

Both scheduler and watchdog remain off by default. Configure the external check
and notification destination, verify an actual alert and recovery, and explicitly
authorize telemetry before enabling. The existing requirements for an existing
production ledger, private job directory, jobs and scheduler still apply. Never
create a dummy production ledger just to enable monitoring.

```
TRADE_PAPER_PADDLE_LIVE_WATCHDOG=0
TRADE_PAPER_PADDLE_LIVE_WATCHDOG_URL=https://hc-ping.com/<check-uuid>
```

Store the real URL as a secret. Only the exact HTTPS hc-ping.com UUID endpoint
is accepted; custom hosts, query strings, fragments, ports, slug URLs and automatic
check creation are rejected. Disabled mode does not validate the URL or send any
request. Enabled scheduler startup validates the URL offline before starting.

Suggested check name: `Trade Paper AI — billing job completion`. With the default
900-second start-to-start cycle, use a 15-minute period and a 15-minute grace
window; adjust when the configured interval changes. Monitoring is not active
until the provider check is enabled and has received its first real signal.

## Signals and limitations

- A completed job exit of zero sends success; warning, critical, timeout, child
  launch failure and a caught loop failure send failure. A provider check skipped
  by the job produces warning and therefore failure, not an unqualified success.
- Only an empty-body POST is sent. No customer records, identifiers, local paths,
  reasons or log output are transmitted. The provider necessarily sees the check
  identifier, originating server IP and timing. No start or log signal is sent.
- HTTP 200 alone is insufficient: unknown and rate-limited checks can return 200
  with a different body. The client requires the `OK` acknowledgment. Redirects
  are not followed, TLS verification is enabled, and there is no automatic retry.
- The network socket timeout is five seconds; the scheduler runs the sender in
  a child bounded to ten seconds, then uses its existing terminate/kill cleanup.
  The secret URL stays in environment/memory and is absent from process arguments,
  output, exceptions and logs. Standalone CLI use does not provide the scheduler's
  wall-clock subprocess bound.
- Send failure is logged with a sanitized status. It does not change the job's
  original outcome, stop backups, or prevent the separate operator-mail attempt.
  Provider/network failure may prevent immediate notification; missed-success
  detection is the independent fallback and itself depends on the provider.
- Shutdown interruption sends no success and stops further notification attempts.
  There is no automatic external-check pause during deployment or maintenance.
  If downtime exceeds the configured grace period, the check should alert.
- This is **job completion monitoring**, not a separate six-hour remote-backup
  freshness check. With offsite backups disabled, job success does not prove an
  external backup exists. With offsite enabled, its errors make the job critical.
  Receipt reuse still follows the offsite adapter's documented verification period.
- The existing child exit status determines the signal. This does not prove real
  payment health, successful customer charges or email delivery.

The implementation tests use a fake transport and synthetic local ledgers only.
They do not register Healthchecks or prove real alert delivery. Live account setup,
controlled failure/recovery and an operator receiving the notification remain
separate go-live requirements.

Official references: [Pinging API](https://healthchecks.io/docs/http_api/),
[check periods and grace](https://healthchecks.io/docs/configuring_checks/).
