# Social sign-in activation

Code is ready for provider configuration, but no real provider sign-in has been verified.
All four providers are disabled by default; unset IDs, secrets, origin or enable flags hide their buttons. No apps, keys, fees or permissions are created by deploying this code.

## Setup

Choose one canonical HTTPS origin in `TRADE_PAPER_OAUTH_ORIGIN`. Use that exact host (including www if present) in provider registration. Each callback is `<origin>/auth/<provider>/callback` for `google`, `kakao`, `naver`, `apple`. The UI starts authentication on that canonical host, keeping the host-only state cookie with its callback.

Set per-provider CLIENT_ID, CLIENT_SECRET and ENABLED=1 only after the registered app, consent branding, required review and privacy policy are ready. Secrets belong in the existing server secret store, never Git or chat. New account/permission/cost commitments require user approval. Do not replace the entire Render environment to add these keys.

- Google: web OAuth client, exact redirect URI; scopes openid and email. Authorization code with PKCE, nonce and RS256 verification of the ID token.
- Kakao: REST API key as CLIENT_ID; enable client secret and email consent; exact redirect URI. Profile email must be valid and verified. Only account_email is requested; no Kakao messaging access.
- NAVER: website login app, callback registration and email permission. User identity is response/id, never contact email. Follow the provider's production review process before enabling.
- Apple: web Services ID, associated primary App ID, domain/return URL and developer-signed client-secret JWT. Do not paste the .p8 private key into CLIENT_SECRET. The JWT expires; plan rotation before enabling. Uses form_post, a Secure SameSite=None binding cookie, and RS256 ID-token verification. Check existing Apple developer membership; do not purchase membership without approval. Configure private email relay if sending service mail to relay addresses.

## Before enabling public access

Use provider test users on a registered test deployment. Test fresh signup, returning user, cancellation, rejected email consent, expired state, browser back/retry, Safari Apple form_post and relay email. Confirm new accounts are Free/Trial and keep imported-document destinations. Verify provider button branding against the current provider guidelines before release. Test real users only with their consent; never finish purchases.

Matching email in an existing account is deliberately rejected. No silent account linking or workspace access is granted. Social-only identities cannot use email password recovery or become platform admins via an email allowlist. Existing password signup and password recovery remain available. Provider tokens are not persisted. Application access logs redact callback parameters; also verify upstream platform logging before production activation.

## Sources checked 2026-11-02

- https://developers.google.com/identity/openid-connect/openid-connect
- https://developers.kakao.com/docs/ko/kakaologin/rest-api
- https://developers.naver.com/docs/login/api/api.md
- https://developers.naver.com/docs/login/profile/profile.md
- https://developer.apple.com/documentation/signinwithapplerestapi/generate-and-validate-tokens
- https://developer.apple.com/documentation/signinwithapplerestapi/request-an-authorization-to-the-sign-in-with-apple-server.
- https://pyjwt.readthedocs.io/en/latest/api.html

## HS helper scope

The new product form offers four manually confirmed HS6 profiles (portable computers, porcelain tableware, other ceramic tableware, plastic tableware) with official source links. It is not a general classifier, tariff calculation or customs ruling. Unsupported items remain editable without an invented code. Saved same-account products can be selected to reuse their HS code and fields; changed values require overwrite confirmation. No external API receives the product name during suggestion lookup.
