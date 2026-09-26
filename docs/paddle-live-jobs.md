# Host billing backup and diagnostic job

`python -m app.paddle_live_jobs` supplies a default-off, one-shot job for a trusted
host scheduler. It does not install a schedule, send alerts, upload archives,
delete old backups, change payment flags, replay events or activate a restore.
Importing it starts no background task. This alone is not production automation.

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
3. Configure an approved host scheduler, for example every 15 minutes with the
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
