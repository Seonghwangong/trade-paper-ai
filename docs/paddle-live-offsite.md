# Opt-in external B2 backups

`TRADE_PAPER_PADDLE_LIVE_OFFSITE` defaults off. The existing serialized billing
job can mirror its verified local SQLite ZIP to a private B2 bucket. This code
does not create a bucket/key, activate billing, delete files, modify bucket
settings, or restore over a running ledger. No new dependency or public route.

## Before enabling

Keep the scheduler, jobs and offsite flags off until an existing production
ledger, private local job directory, authorized destination and scoped credentials
are available. Never initialize an empty production ledger to pass readiness.

Provide these values through the server's secret/environment management:

```
TRADE_PAPER_PADDLE_LIVE_OFFSITE=0
TRADE_PAPER_PADDLE_LIVE_OFFSITE_B2_BUCKET_ID=<dedicated bucket id>
TRADE_PAPER_PADDLE_LIVE_OFFSITE_B2_BUCKET_NAME=<private bucket name>
TRADE_PAPER_PADDLE_LIVE_OFFSITE_B2_PREFIX=ledger/
TRADE_PAPER_PADDLE_LIVE_OFFSITE_B2_KEY_ID=<scoped key id>
TRADE_PAPER_PADDLE_LIVE_OFFSITE_B2_APPLICATION_KEY=<secret>
```

The key must be restricted to exactly this single bucket and file prefix, with a
future expiration. Required capabilities are `readFiles,writeFiles`. The only
optional capabilities accepted are `listBuckets,readBuckets,listFiles,readBucketEncryption`.
In particular, `deleteFiles`, any bucket settings write, key management and
`listAllBucketNames` are rejected before file transfer. The web console's broad
**Read and Write preset is not a production credential for this adapter**.
Use the provider's granular key-creation API/CLI with an appropriately authorized
owner/bootstrap credential. Do not add a master key to the application server.
Key creation itself requires the owner's authorization; this module cannot issue keys.

The existing scheduler performs offline configuration validation at startup.
Actual scope/expiration verification happens at the first transfer. Configuration
does not prove a usable key. A changed credential/destination invalidates reuse
of the prior verification receipt. Keep a key-expiration/rotation procedure;
this module does not rotate credentials or send independent expiry reminders.

## Transfer and failure behavior

- The local archive must be private, owned, regular, nonsymlink, at most **16 MiB**,
  and match its independent SHA256 and validated SQLite ZIP format. Larger archives
  cause a critical job result; they are not truncated or silently skipped. Revisit
  this deliberate worker-memory bound before growth beyond it.
- Object names are the configured prefix plus the ZIP SHA256 and `.zip`.
- First read the object. Only HTTP 404 with JSON `status: 404, code: not_found`
  from the authenticated download-by-name endpoint permits a new upload. Generic
  errors and an uncertain prior upload remain blocked. Save a pending receipt durably **before** POST; upload once, then read
  back. No transparent POST retry or overwrite of mismatching existing bytes.
- Verify upload receipt, SSE-B2/AES256 encryption, downloaded bytes, object
  version, SHA256, ZIP/SQLite integrity and denial of unauthenticated downloads.
  TLS verification remains on, endpoints are limited to Backblaze domains, and
  HTTP redirects are not followed. Credentials stay in headers/process memory.
- A pending upload after timeout/process death is recovered by readback. If the
  object is still absent, report critical and do not blindly submit another
  version. Investigate the provider state and the private receipt. Do not erase
  a pending receipt to force retry without resolving the first request.
- Reuse an unchanged archive's verified receipt for the configured backup
  interval (default six hours), provided the key has not expired. Reuse preserves
  the original verification timestamp and is reported as `reused`, not a fresh
  remote check. Remote removal/settings changes between checks may therefore go
  undetected until the next verification. New archives always need verification.
- Any opt-in configuration/transfer failure makes the job **critical** via
  `offsite_backup_failed`. The existing scheduler/optional operator email path
  receives that severity. A disabled offsite job is explicitly `disabled`;
  ordinary local health does not prove external protection.
- Local and remote older copies are retained. There is no automatic cleanup,
  retention policy, billing upgrade, or remote restore activation.

State is held inside the existing private `state.json` under the job lock.
Configuration identity is a digest, and reports exclude secrets, object names and
customer data. A malformed receipt fails closed. Child timeouts/interruption are
still bounded by the existing scheduler; a killed process leaves durable pending
evidence. Local disk failure can prevent receipt writes, and complete host failure
cannot alert from this same process. The separately configured
[external job watchdog](paddle-live-watchdog.md) can detect missed scheduler
signals; it is not enabled or provisioned by the offsite adapter.

## Verification before production

Automated tests cover real SQLite ZIP round trips using a fake B2 transport,
capability/prefix/destination mismatches, encryption/privacy/corruption failures,
ambiguous uploads, process interruption, durable-state failure, cache validity,
secret-safe diagnostics, and scheduler opt-in validation. They send no customer
data and make no live provider requests. The separate September 29 synthetic
manual B2 drill is evidence for the storage account; it does not activate or
certify this new adapter against a production-scoped key.

The subsequent September 29 scoped-key drill exercised `mirror` and `B2Client`
with only `readFiles,writeFiles` against the `ledger/` prefix. A 2,629-byte
synthetic ZIP passed encrypted/private round-trip and isolated all-table row
comparison, pending recovery without another POST, and cache reuse without
network requests. It exposed the documented download-by-name `not_found` code;
the earlier fake provider incorrectly used `file_not_present`. The correction
also rejects mismatched status/code, malformed JSON and unrelated errors before
requesting an upload URL. This drill did not enable production jobs or billing.

Official references: [authorize](https://www.backblaze.com/apidocs/b2-authorize-account),
[upload URL](https://www.backblaze.com/apidocs/b2-get-upload-url),
[upload](https://www.backblaze.com/apidocs/b2-upload-file),
[download](https://www.backblaze.com/apidocs/b2-download-file-by-name),
[key capabilities](https://www.backblaze.com/docs/cloud-storage-application-keys).
