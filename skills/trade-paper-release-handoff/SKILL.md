---
name: trade-paper-release-handoff
description: Hand off verified Trade Paper AI development and release results to the user's Rion marketing thread after a meaningful implementation, validation, deployment, rollback, or customer-visible fix. Also use for an explicit product-development handoff request. Does not publish marketing or send customer email.
---

# Product development → Rion marketing

The product owner requested automatic internal handoffs whenever Trade Paper AI
is developed or updated. Complete this handoff as the closing step of relevant
development work; do not wait for another per-update approval. This is a step
within existing work, not a new timer, background process or external campaign.

## Recipient and permission

Use the connected app's thread list/read/send tools. Resolve the existing
**리온 · 클라우드 홍보 보고** thread and verify its recent context identifies Rion
and Trade Paper AI. Prefer this cloud recipient so delivery does not depend on
the Mac being open. The local **마케팅 홍보** thread is an operational context,
not an automatic second recipient: avoid duplicate messages and reports.

The user's standing authorization covers product-result messages to Rion only.
Do not treat it as authorization to contact customers, publish posts, alter
marketing schedules, resume paused work, send emails, or message other threads.
If the recipient is unavailable or ambiguous, retain a pending brief in the
existing development records; do not invent an ID or create another thread.
Keep account/thread IDs and delivery receipts in private records, not GitHub.

## Evidence and brief

Read the actual change, relevant tests, current release evidence, and previous
handoff record. Separate **implemented**, **validated**, **deployed**, and
**available to customers**. A commit, PR, local test or successful build alone
does not prove production availability. Include only verified current facts.

Write plain Korean suitable for the marketing owner:

- What changed, with one short before/after example.
- Customer benefit; mark unmeasured time savings or expected effects as such.
- Release/availability status and the observation time.
- Relevant test result and supporting commit/PR/release link.
- A short usable marketing sentence and limits that must stay with the claim.
- Next missing release check, if any.

For unreleased changes, say **홍보 준비용 · 운영 미배포** prominently. Provide
planned-feature wording only; prohibit presenting it as currently available.
When the deployment later becomes verified, send one status update correcting
the availability. Do not invent pricing, performance, security certification,
AI accuracy, customer uptake or capabilities. Technical links are internal
evidence, not customer signup or purchasing links. Never include customer
documents, transaction data, credentials, private emails or personal context.

## Delivery and durable record

Deduplicate by product change/commit + release status + recipient. No-change
checks do not require another marketing brief. A rollback or changed limitation
does require a corrective update to prevent stale marketing claims.

Send the cohesive brief using the existing thread messaging tool, citing the
user's standing handoff instruction. Request that Rion retain it as a product
brief within existing authorized operations. The message itself must not start
a new campaign, override rest/stop requests, or duplicate the user's development
report. Do not require a reply or create a reply loop.

Record the brief, timestamp, recipient, change/commit, release status and tool
delivery result in the existing private outputs/development records. Distinguish
accepted for delivery from acknowledged/read. If the tool outcome is uncertain,
inspect the recipient before any retry; never repeatedly send the same brief.
Tell the user briefly whether the handoff was delivered or remains pending.
