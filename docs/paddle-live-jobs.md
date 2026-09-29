# Host billing backup and diagnostic job

`python -m app.paddle_live_jobs` supplies a default-off, one-shot job for a trusted
host scheduler. It does not install a schedule, send alerts, upload archives,
delete old backups, change payment flags, replay events or activate a restore.
Importing it starts no background task. An optional application-lifecycle
integration is described below; it is disabled unless explicitly configured.

## One cycle

Create a dedicated directory owned by the service user with mode 0700 on private
durable storage. Its parents must also be trusted. Use the same absolute ledger
path, price and directory every time. Inventory is bound to that path and price;
it does not prove provider lineage or authenticate against service-user tampering.

```sh
TRADE_PAPER_PADDLE_LIVE_JOBS=1 venv/bin/python -m app.paddle_live_jobs run \
  --ledger /durable-data/paddle_live.sqlite3 \
  --directory /private-backups/paddle-live-jobs --price-id LIVE_PRICE_ID
```

The job uses the existing verified SQLite online backup and read-only diagnostics.
It creates a new archive when none exists or the last snapshot is six hours old
(`--backup-hours` supports 1–24). Otherwise it verifies and reuses the recorded
archive. Unique filenames, mode 0600, checksums and manifest creation times are
recorded in the private inventory. Source JSON and the active ledger are unchanged.
Existing archives are never overwritten or deleted.

Local-only checks return a warning because provider coverage is not established.
For bounded read-only provider comparison, add `--with-provider`, explicitly enable
`TRADE_PAPER_PADDLE_LIVE_MONITOR=1` and configure the existing API key in the secret
environment. No secret is accepted in CLI arguments. Do not grant new permissions
or enable Live sales merely to run this job.

An advisory nonblocking OS lock prevents overlap within this directory on one host.
Separate directories/hosts are not coordinated. The platform must support POSIX
flock, atomic rename, hard links and directory fsync on durable local storage;
do not assume network filesystems provide these guarantees. A skipped overlapping
run returns a warning. Process exit/death releases the kernel lock.

A durable running receipt precedes work, and the final report is published
atomically. A crash during backup/diagnostics leaves an incomplete receipt.
A successful backup updates the inventory before diagnostics. A failed new backup
is critical even if an older copy is still fresh; the old pointer is preserved.
Discovery of a corrupt/missing archive remains critical for that cycle even if a
replacement succeeds. A later successful cycle may clear findings, so collect
each cycle's outcome in the scheduler's external log.

Storage failures return a failure exit. A published archive may remain unindexed
after an inventory-write failure; preserve it for inspection. Never select a file
by newest name/mtime or reset inventory automatically. If the initial receipt
cannot be written, only the scheduler's failed exit and subsequent freshness check
can expose that missed run.

## Independent missed-run check

```sh
venv/bin/python -m app.paddle_live_jobs check \
  --ledger /durable-data/paddle_live.sqlite3 \
  --directory /private-backups/paddle-live-jobs --price-id LIVE_PRICE_ID \
  --max-age-minutes 30
```

This reads the last receipt and probes the lock; it makes no provider calls and
requires no JOBS/MONITOR opt-in. It may create the private lock file. It does not
verify the current ledger/archive again. Never-run, interrupted, stale, invalid
inventory and clock rollback are critical. A currently locked run is a warning
until stale. Age uses the job's start, so a hung process cannot keep health current.
A finished receipt preserves the latest diagnostic/backup-attempt failures but
does not prove that files or provider state stayed unchanged afterward.

Both commands print sanitized JSON and exit 0 (checked conditions pass), 1 (warning)
or 2 (critical/configuration/storage failure). Normal output excludes customer rows,
account IDs, raw payloads, private paths and exception strings; the underlying
monitor may include bounded event IDs. Unexpected crashes also exit unsuccessfully
and leave an incomplete receipt when the initial write succeeded.

## Production wiring still required

1. Confirm the actual durable ledger, service user, private directory capacity and
   Live price on the application host. Local code/JSON ZIPs are not this backup.
2. Run a cycle and verify its archive/checksum. Prove isolated recovery using the
   [backup runbook](paddle-live-backup.md), never a production route.
3. Configure the opt-in same-instance scheduler below, for example every 15 minutes with the
   default overlapping 24-hour event window. Capture every nonzero exit, including
   pre-receipt failures and overlap skips. A separate worker without access to the
   app's durable ledger cannot perform this backup.
4. Invoke the freshness check independently and route outcomes to an agreed
   operator channel. Neither command sends email. Also configure an external host
   availability check: a dead host cannot alert about its own outage.
5. Configure approved encrypted off-host storage, preserve the checksum separately,
   verify the copied archive, agree retention and monitor capacity. This tool never
   deletes archives; disk usage grows until retention is explicitly managed.

No production schedule, alert recipient, off-host destination or retention deletion
was configured during implementation. Complete that wiring and a controlled
production drill before relying on this job for operations.

## Render deployment constraint (reviewed September 29, 2026)

Render cron jobs cannot mount or access persistent disks. A service's disk is
accessible only by its own running instance, not another worker, one-off job,
build command or pre-deploy command. Creating a separate scheduled service with
the same path string therefore does not back up this application's SQLite ledger.

For the current single-instance SQLite deployment, the backup cycle must execute
on the running application instance. The optional integration below provides
startup/restart/shutdown wiring. Its local subprocess tests do not establish
production scheduling, host recovery, capacity or off-host protection. Merely
deploying its code does not enable it.

After a verified online backup is published, copy it to an approved encrypted
off-host destination and compare its checksum there. The existing ZIP is not
itself encrypted. The destination, scoped transfer credentials, retention policy
and independent alert destination remain deployment decisions. Do not put the
archive behind a public application route to work around disk access restrictions.

The independent availability/missed-run observer must run outside the application
instance. A successful `/health` response alone does not prove backup freshness.
See the consolidated [release gates](paddle-live-release-gates.md).

Official sources:
- https://render.com/docs/cronjobs
- https://render.com/docs/disks

## Optional same-instance application scheduler

`app.paddle_live_scheduler` is connected to FastAPI startup and shutdown. The
default-off path starts no task or child process and touches no billing storage.
It never creates/migrates the ledger, changes payment flags, uploads an archive
or deletes a backup. A separately opted-in [operator email extension](paddle-live-alerts.md)
can report cycle failures/recovery. No public status/control route is added.

After the ledger and private directory have been deliberately prepared and the
release gates reviewed, the integration accepts these service environment values:

| Variable (prefix `TRADE_PAPER_PADDLE_LIVE_`) | Requirement/default |
|---|---|
| `SCHEDULER` | Exactly `1` to enable; absent/other values disable |
| `JOBS` | Must also be exactly `1` |
| `JOBS_DIRECTORY` | Existing absolute directory, service-owned mode 0700, trusted parents |
| `PRICE_ID` | Existing distinct Live price matching ledger metadata |
| `SCHEDULER_INTERVAL_SECONDS` | Default 900; integer 60–3600 |
| `SCHEDULER_TIMEOUT_SECONDS` | Default 120; integer 10–300, less than interval |
| `BACKUP_HOURS` | Default 6; integer 1–24 |
| `MONITOR` | Exactly `1` adds read-only provider comparison; otherwise local-only warning |
| `API_KEY` | Existing secret Live key required when MONITOR=1; never a command-line argument |
| `ALERTS` | Default off; exactly `1` enables the separate operator mail extension |
| `ALERT_RECIPIENT` | Explicit single operator address when ALERTS=1; no fallback recipient |
| `WATCHDOG` | Default off; exactly `1` enables the separate external completion signal |
| `WATCHDOG_URL` | Secret Healthchecks HTTPS UUID URL; see [watchdog setup](paddle-live-watchdog.md) |

The ledger is always `paddle_live.sqlite3` in the application's configured
`TRADE_PAPER_DATA_DIR`; a separate scheduler ledger path is not accepted. Enabled
startup validates an existing read-only ledger and refuses invalid settings,
an unsafe directory/lock, or an already-held scheduler lock with a sanitized
startup error. Do not enable against the currently absent production ledger.

The first cycle starts immediately in a subprocess using the app's Python
interpreter and environment. Subsequent cycles use a monotonic start-to-start
interval, run serially and do not replay missed intervals. Work cannot block the
HTTP event loop. A lifetime `scheduler.lock` coordinates enabled instances on
the same directory; the existing `job.lock` also prevents overlap with manual
cycles or a previous child that outlives an abruptly killed parent.

Timeout/shutdown sends terminate, waits up to five seconds, then kills and reaps
the child. A normal stop interrupts the interval wait immediately. A restart
uses the existing inventory: it rechecks/reuses fresh archives and reports a
previous running receipt as incomplete. Files interrupted during backup are not
automatically cleaned up; preserve orphan archives/temporary files for inspection.
SIGKILL of the parent cannot run shutdown cleanup, so an existing child may
outlive it. The next process still honors the job lock; a stuck orphan requires
operator action and must be detected through the independent stale-run check.

Logs expose only sanitized lifecycle/reason codes and numeric job exit status.
The detailed sanitized job report remains in private `state.json`; child stdout
and stderr (including unexpected tracebacks) are discarded. A launch failure is
logged as critical and retried on the next interval. An unexpected scheduler-loop
failure is logged as critical and stops scheduling until service restart. Neither
case makes `/health` prove backup health. Capture ERROR and WARNING results and
check freshness independently, including failures before a new receipt exists.
Adjust the missed-run threshold when changing interval/timeout; the default
15-minute interval, two-minute timeout and 30-minute threshold are a starting
configuration, not a recovery guarantee.

These settings are deployment instructions, not evidence that the production
flags are enabled. Off-host storage, alert routing, capacity monitoring, retention
and a controlled production drill remain separate release requirements.
