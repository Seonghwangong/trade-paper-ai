# Toss billing registration review

This opt-in flow opens the real Toss Payments test card-registration SDK and verifies the returned authorization on the server. It does not charge a card, persist billing keys, activate Starter, or schedule recurring payments. It is not a complete production subscription system.

## Configuration

Set these values through the deployment environment, never in source control:

- `TRADE_PAPER_TOSS_TEST_BILLING=true`
- `TRADE_PAPER_TOSS_CLIENT_KEY`: API individual-integration test client key for MID `bill_otradwfih`
- `TRADE_PAPER_TOSS_SECRET_KEY`: matching test secret key for that MID
- `TRADE_PAPER_PUBLIC_BASE_URL=https://www.tradepaper.ai`

Live keys are rejected. Missing or invalid configuration retains the existing purchase-preparation page without a registration button. Keep the stable production session secret configured. Disable the feature by removing its flag.

## Review flow

1. Sign in with a dedicated reviewer account and complete company setup.
2. Open `/starter` and follow purchase details to `/subscription/checkout?plan=Starter`.
3. Acknowledge that registration is a test and open the test card-registration window.
4. On return, the server validates a 15-minute, account-bound signed state, an HttpOnly browser cookie, and the customer key before exchanging the one-use authorization with Toss.
5. Verified registration, failure, and cancellation pages do not expose authorization keys, billing keys, or card data. No payment or subscription data is written.

Capture actual site screens for the requested PPT only after the deployed flow is verified. Include business details, refund terms, sign-in path, product/price, and the actual billing registration window. Never substitute a mock SDK screenshot for a live review capture. The dedicated reviewer account, final renewal/cancellation terms, and business-registration comparison still require completion before final submission. On September 22, the Toss onboarding team replied that this review requires no separate mail-order registration number display/documents, and that the completed materials may be sent in the same email thread without a fixed submission deadline. This is the reviewer’s request, not a general legal exemption.

## Deployment/logging

Payment providers send authorization values in callback query strings. Configure hosting/access logs to redact query strings on `/subscription/billing-test/*`; do not export these URLs to analytics. Callback responses use `no-store` and `no-referrer`, and remove the query string from browser history. An expired login can require restarting the review flow.

## References

- https://docs.tosspayments.com/guides/v2/billing/integration
- https://docs.tosspayments.com/sdk/v2/js/payment#paymentrequestbillingauth

## Verification

Run `PYTHONDONTWRITEBYTECODE=1 venv/bin/python -m pytest -q`. Billing review tests cover disabled/live configuration, account isolation, cookie/state expiry, customer mismatch, provider errors, secret-free HTML, and SDK invocation in Chromium/WebKit. Browser SDK tests use a stub; they do not establish that Toss has approved the merchant or that a real card registration succeeded.
