# Customer checkout release — October 25, 2026

`TRADE_PAPER_PADDLE_LIVE_PUBLIC_CHECKOUT=1` opens the existing monthly Starter
purchase flow to authenticated account Owners beyond the pilot allowlist. The
CHECKOUT, WEBHOOK, ACCESS, MANAGE and CANCEL flags must all also equal `1`.
Missing or other values preserve the allowlisted pilot and closed public copy.

The switch does not grant Starter, create a transaction, change catalog prices,
or charge a customer. Owner authorization, same-origin account-bound CSRF,
explicit recurring-billing consent, server-selected price/transaction, duplicate
reservation, signed paid evidence and management ownership remain enforced.
The existing purchase-details path redirects to the Paddle purchase page, with
language preserved; public Starter, pricing, subscription, FAQ, guides and
current payment availability disclosures follow the same switch. Refund rights
and historical release entries remain unchanged. Professional is not released.

Before enabling: verify current provider catalog/domain configuration, payout
receipt, operational ledger/backups, and application tests. The October24
record verifies the catalog and empty-ledger backup/restore; no real payment was
made and the user declined a test charge. Independent watchdog and operator
alerts remain disabled. Do not represent simulated tests as live reconciliation.
Review these limitations with the operator before broad release.

Rollback: set PUBLIC_CHECKOUT to `0` and redeploy. Preserve WEBHOOK, ACCESS,
MANAGE and CANCEL so existing customers retain paid access and renewal controls.
Disabling new sales does not cancel provider subscriptions or refund payments.
Do not alter the pilot account list or enable wildcard IDs.

After enabling: inspect logged-out Starter and authenticated Owner checkout in
English/Korean, verify the signed-in price/readiness without clicking consent or
starting a payment, and inspect public availability copy. First actual payment
must be completed by an external customer, then separately reconciled with
signed evidence and backups. No acquired-customer or revenue claim before that.
