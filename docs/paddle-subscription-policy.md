# Paddle subscription access and local-change guard

This is preparation for Live billing, not a completed Live payment integration.
The default-off Live runtime now uses the evaluator for authoritative access reads.
No production switches have been enabled and no Paddle API is called.

`app/paddle_subscription_policy.py:evaluate_snapshot` is a pure function for a
trusted, current Paddle subscription snapshot. Before calling it, the
adapter must authenticate webhook/API provenance, resolve ownership from a
server-created checkout binding, and enforce event ordering/deduplication.
Do not treat this function as a signature validator or pass browser success
callbacks, email matches or custom_data as ownership evidence.

The supported product is one automatically collected monthly Starter item at a
server-configured price ID. Subscription/customer/price IDs and integer quantity
must match the trusted binding. Malformed or mismatched snapshots are rejected.
The decision grants Starter access only for an active subscription during its
current billing period. Scheduled cancellation/pause caps access at its effective
time; it does not remove the remaining valid period on receipt of the schedule.
Past-due, paused, canceled and trialing states do not grant Starter access under
this initial policy (no Starter paid-plan trial or past-due grace is configured).
Unknown status fails validation. Future grace/trial offerings need explicit policy.
An expired active snapshot cannot renew itself: reconcile against Paddle first.
Refund/chargeback handling and suspension are separate launch requirements.

The existing local cancel, downgrade and administrator status routes now reject
records marked with a nonempty billing_provider or paddle_subscription_id with
409, before changing users, billing history or audit records. The subscription
page directs those accounts to billing support until the authenticated provider
portal/cancellation flow is implemented. Unmarked legacy/free accounts retain
existing behavior. Server-managed markers are honored for legacy safety; current Live ownership is
read from the ledger itself, including checkout reservations, so JSON markers are
not required for local-change protection. Do not use local
administrative status changes as a replacement for stopping provider renewal.

Implemented separately: durable Live ledger, signed HTTP adapter, opt-in access
projection. Remaining: reconciliation, provider cancellation/customer portal,
refund handling, private Live
configuration and full end-to-end verification. Sandbox remains isolated.

Reference: https://developer.paddle.com/api-reference/subscriptions/cancel-subscription/
Validation: tests/test_paddle_subscription_policy.py plus subscription/billing and
existing sandbox tests. Test snapshots are synthetic and do not contact Paddle.
