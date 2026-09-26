# Paddle Live SQLite backup and restore staging

`python -m app.paddle_live_backup` provides host-only `backup`, `verify` and
`restore` commands. It does not call Paddle, schedule jobs, upload artifacts,
change payment flags or replace the active database. The older code/local-JSON
recovery ZIP and account-scoped JSON backups do **not** contain this ledger.

## What a backup contains

SQLite's online backup API creates a consistent snapshot, including committed
WAL data while other connections are active. Uncommitted changes are excluded.
The copy is converted to standalone rollback-journal mode; do not copy an active
`.sqlite3` file alone or manually combine a main database with unrelated WAL files.

The ZIP contains only `ledger.sqlite3` and `manifest.json`. It preserves all
supported Live tables: ownership reservations, event deduplication, subscription
snapshots, ambiguous checkout/cancel operation guards, adjustment evidence and
audited review coverage. Authentication/API keys and unrelated JSON are excluded.

Before publication, the tool independently extracts and validates the ZIP:

- SQLite integrity, known schema/columns/primary keys and ownership uniqueness;
- exact Live environment/schema/price metadata;
- binding/snapshot/adjustment/event relationships and pending operation guards;
- review coverage ownership and canonical audit evidence digest;
- ZIP CRC, database size/hash and manifest table counts.

Checks do not authenticate the original webhook signatures again (raw payloads
are intentionally not stored), prove that Paddle's current state matches an old
snapshot, or prove operator audit history is externally untampered. Preserve the
archive SHA256 independently in the trusted backup log. SHA256 is an integrity
check, not encryption or a digital signature.

Current limits: 512 MiB uncompressed database, 64 KiB manifest, 60 seconds for the
online copy and a bounded SQLite validation phase. Unknown schemas, partial
extensions, corrupt evidence and incomplete artifacts fail closed. Existing
supported schema-1 ledgers can be copied without migrating the source.

## Create and verify on the trusted application host

Use the actual durable data directory and configured Live price. Create the
private backup destination directory first. The filename must be new.

```sh
venv/bin/python -m app.paddle_live_backup --price-id LIVE_PRICE_ID backup \
  --source /durable-data/paddle_live.sqlite3 \
  --output /private-backups/paddle-live-TIMESTAMP.zip
```

The result includes the archive checksum, creation time, metadata and table
counts, but no customer rows. Save this receipt in the backup inventory and verify
the off-host copy with the recorded hash:

```sh
venv/bin/python -m app.paddle_live_backup --price-id LIVE_PRICE_ID verify \
  --archive /private-backups/paddle-live-TIMESTAMP.zip --sha256 RECORDED_SHA256
```

Outputs use mode 0600, staging directories mode 0700. Publication is exclusive
and does not overwrite an existing archive or symlink. No files leave the host.
The ZIP contains private billing identifiers and is not encrypted: use approved
encrypted storage and transfer when off-host retention is configured. Retention,
backup scheduling, off-host replication and failure/staleness alerts are still
deployment tasks; creating this tool alone does not provide those protections.

The separate [read-only diagnostic CLI](paddle-live-monitor.md) verifies a selected
archive and reports age/corruption alongside billing findings. It does not schedule
backups, verify off-host retention or deliver alerts automatically.

## Restore drill (isolated, never automatic activation)

Restore requires the independently recorded SHA256 and a directory that does not
already exist. No application database or configuration is changed.

```sh
venv/bin/python -m app.paddle_live_backup --price-id LIVE_PRICE_ID restore \
  --archive /private-backups/paddle-live-TIMESTAMP.zip --sha256 RECORDED_SHA256 \
  --output-dir /private-restore/drill-TIMESTAMP
```

This produces `paddle_live.sqlite3` and `RESTORE_NOT_ACTIVATED.json` in the new
directory. The marker records that provider reconciliation is still required.
Inspect account ownership, held/released access, expiration and pending operations
using read-only application code. Do not open checkout/cancellation routes against
a drill database and do not point the live server at it. The marker is an operator
receipt, not an application-enforced lock; it does not make activation safe.

## Actual incident recovery gates

1. Put payment/access workflows into maintenance and stop **all** processes that
   can write this ledger, including webhooks, CLI operations and worker instances.
   Disabling sales alone does not stop cancellation/webhook writers. Preserve
   the current database and its matching sidecars for diagnosis; never replace
   or delete files while connections remain open.
2. Stage and verify the selected trusted backup. Determine its recovery point.
   A local code/JSON ZIP cannot substitute for the Live database backup.
3. Reconcile activity after that point with provider records and signed event
   replay: checkout/ownership, cancellations, refunds/disputes, coverage releases
   and unconfirmed operations. A lost reservation can otherwise allow another
   charge; an old active snapshot can otherwise restore revoked access. Do not
   blindly clear or retry pending operations. The general post-backup replay and
   ambiguous-operation recovery workflow is still a release blocker.
4. Only after reconciliation and a controlled drill should an authorized operator
   promote a staged database with every writer stopped, preserving the previous
   files for rollback. This tool deliberately provides no promotion command.
5. Reopen with flags disabled, check health/ownership/holds, then resume notification
   processing and validate end-to-end behavior before enabling sales/access.

The implementation was tested with isolated synthetic data, including concurrent
signed events and WAL/uncommitted transactions. No production ledger was exported,
replaced, activated or financially reconciled during development.

Sources:
- https://www.sqlite.org/backup.html
- https://docs.python.org/3.9/library/sqlite3.html#sqlite3.Connection.backup
