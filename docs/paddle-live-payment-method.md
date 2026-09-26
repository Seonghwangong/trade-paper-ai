# Existing subscription payment-method portal (default off)

This feature prepares an authenticated handoff to Paddle's customer portal.
It does not activate sales, change cards, collect money, create transactions,
create portal sessions, or grant access. No live portal URL was requested in QA.

## Contract

`POST /subscription/paddle/payment-method` requires the existing authenticated
Owner session, `TRADE_PAPER_PADDLE_LIVE_MANAGE=1` and the separate default-off
`TRADE_PAPER_PADDLE_LIVE_PAYMENT_METHOD=1` switch. The UI appears only for a bound
active/past-due subscription without an unresolved billing review. Company setup
is not a prerequisite for managing existing payments. New-sales, checkout,
access and cancellation switches are independent.

The request requires a 15-minute account/purpose-bound `payment-method` CSRF
token, the configured HTTPS Origin, same-origin fetch metadata if present, and
`X-Billing-Confirm: open-payment-method-portal`. URL/body account, customer and
subscription identifiers are never used. GET cannot issue a portal link.

Read the subscription/customer pair from the signed Live ledger, using a
read-only connection. Check review evidence, then GET that subscription using
the existing Live API key. Require exact subscription/customer identity,
automatic collection, and active or past-due status. Recheck the binding/review
after the network read. Missing/corrupt storage fails closed without creating it.
API access requires only the existing `subscription.read` scope.

Only return Paddle's temporary `management_urls.update_payment_method` URL when
it has exact HTTPS origin `buyer-portal.paddle.com`, the exact bound subscription
path `/subscriptions/{id}/update-payment-method`, and one nonempty token query.
Reject userinfo, ports, alternate hosts/paths, duplicate/extra query fields,
fragments, whitespace and malformed responses. Provider failures are redacted.
Use `no-store` and `no-referrer`; never persist/log the URL or embed it in the
initial page/status response. Fetch a fresh URL for each deliberate button click.

The browser navigates only after the validated POST response. It explains that
an overdue balance must be reviewed in Paddle and opening the portal does not
confirm payment or activate Starter. On failure the action is disabled until
status refresh. Signed provider events remain the source of payment/access state.

## Release limits

The PAYMENT_METHOD and MANAGE flags remain off in production. No customer card,
portal session or charge was changed. This does not implement the separate
default payment-link landing page; the Paddle default payment link remains unset.
Do not point it at the initial purchase page. Before rollout, verify the real
portal sign-in/active/past-due behavior with controlled provider data, the default
link prerequisite, signed event reconciliation and current public disclosures.

ASGI tests cover Owner/account/CSRF boundaries, disabled flags, inactive/manual
subscriptions, URL allowlisting, stale review races, unavailable storage and
unchanged billing records. Native Chrome UI checks use a local synthetic HTTP
server and no Paddle calls: action visibility, request headers, failure disabling
and status-refresh recovery. They are not hosted-portal or real-payment tests.

References (reviewed 2026-09-27):
- https://developer.paddle.com/build/subscriptions/update-payment-details/
- https://developer.paddle.com/api-reference/subscriptions/get-subscription/
- https://developer.paddle.com/build/transactions/default-payment-link/
