# Synthetic Live billing lifecycle drill

Run `venv/bin/python -m pytest -q tests/test_paddle_live_lifecycle.py` from the repository.

This is an in-process ASGI integration drill, not a Paddle sandbox/live acceptance test. Checkout, signed webhooks, owner status and cancellation use the actual app routes. The provider is an in-memory fake; unexpected `LiveClient` transport calls fail. Environment flags and authentication are test fixtures. SQLite ledgers, backup archives and restored copies exist only under pytest's temporary directory. No browser or external payment service is contacted.

The eight combinations cover normal/lost checkout responses, subscription-before/payment-before initial delivery, and period-before/payment-before renewal delivery. Each combination continues through the same full journey:

1. Create one checkout through the owner route. An uncertain response survives restore and blocks a second creation. If the provider completed it, correlated operator recovery registers it but does not grant access.
2. Send correctly signed events through the webhook route. Early subscription delivery retries; only completed payment plus matching subscription state unlocks access. Duplicate delivery changes neither ownership nor payment count.
3. At the next period boundary, require both the signed new period and its own captured recurring payment, regardless of arrival order. Check owner status and the application subscription projection agree.
4. Deliver a renewal chargeback. Hold access across restoration and payment redelivery. Preview/apply operator review against two provider reads of the reversed dispute and fully restored payments; retain the release audit and exact event coverage across restore.
5. Simulate a provider-accepted cancellation whose response is lost. Restore its pending guard, reconcile the existing schedule without a second cancellation POST, and wait for a signed subscription update. Access ends at the boundary even without the final canceled notification; a late older active event cannot reactivate it.

Every journey makes exactly one provider checkout creation and one cancellation. Five restores are exercised normally, six for an uncertain checkout. Backups verify the combined renewal, dispute, review-release, checkout-correlation and operation-recovery schema. The restore receipt stays explicitly not activated; only the isolated test runtime is switched. Local user, billing-history and usage JSON bytes remain unchanged. The unrelated account receives no subscription access.

## Interpretation and remaining release gates

Passing this drill demonstrates integration of the implemented local rules. It does not establish provider API compatibility, actual delivery/replay reliability, browser checkout operation, production backup scheduling or disaster-recovery readiness. Separate browser tests and controlled provider lifecycle acceptance remain required. Legacy historical evidence recovery, production retention/off-host copies/alerts and operational enablement also remain unresolved. Keep Live sales/access configuration unchanged until those gates are explicitly satisfied.
