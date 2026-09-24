# Owner billing management (default off)

`TRADE_PAPER_PADDLE_LIVE_MANAGE=1` exposes three session-authenticated routes:

- GET `/subscription/paddle`: account-scoped management page with CSRF token.
- GET `/subscription/paddle/status`: read-only subscription/cancellation summary.
- POST `/subscription/paddle/cancel`: verified period-end cancellation request.

The switch is unchanged/off in production. The separate allowlisted checkout
pilot and catalog validation are documented in paddle-live-checkout.md. Public
sales rollout, reconciliation, refund/chargeback handling and consistent SQLite
backup remain release gates.

Only an exact Owner role may read or mutate billing through these routes.
Authentication middleware still requires a valid login; company setup is not a
prerequisite for billing management. Other roles, including tenant Admin and
Viewer, cannot use these routes. My Subscription links to the management page
only for an enabled, provider-reserved Owner; others retain support guidance.

Cancellation validates the configured HTTPS origin (not Host/forwarded headers),
same-origin fetch metadata when present, a purpose/account-bound signed token
valid for 15 minutes, and an explicit confirmation header. Browser/query/body IDs
are never used. The request must have an existing readable account ledger record
before the service opens writable storage. The separate LIVE_CANCEL switch and
Live private API configuration remain required. Disabling paid access/sales does
not disable cancellation if management/cancel are configured.

GET reads use one SQLite transaction and never call Paddle or create the DB.
Statuses omit account, subscription, transaction, customer, price IDs and secrets.
Missing/corrupt required storage is a generic 503, not an empty success. All
adapter responses use no-store/no-referrer/frame-denial headers. Auth middleware
may first redirect signed-out users to login.

The UI distinguishes a request with uncertain outcome, API acknowledgement
awaiting a subscription event, signed scheduled cancellation, and actual canceled
status. Acknowledgement alone never revokes access or falsely displays a signed
state. Cancellation is disabled until the customer checks the confirmation box;
submission disables duplicate clicks. Ambiguous requests can be checked through
the same service, which only GETs and never repeats an uncertain POST. Support is
always visible. Polling is bounded to twelve five-second checks, then the customer
may refresh manually. No refund is implied by cancellation.

Validation includes actual ASGI route/middleware tests for role restrictions,
login, setup bypass, CSRF expiry/tampering/origin and cross-account substitution,
safe errors, account isolation, disabled switches, ambiguous operation recovery,
unchanged entitlement data and flag-independent cancellation. A local Chrome
preview with only synthetic data verified the checkbox/button interaction,
acknowledgement, subsequent confirmed scheduled cancellation and retained access.
No real subscription was modified in testing.
