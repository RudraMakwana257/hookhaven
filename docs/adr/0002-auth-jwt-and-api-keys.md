# ADR 0002: Two credentials: JWT for people, API keys for machines

## Status
Accepted

## Decision
- **Dashboard users** authenticate with email + password (argon2id) and receive a short-lived
  JWT access token (15 min) and a refresh token (7 days). Tokens carry a `type` claim so a
  refresh token can never be used as an access token.
- **Programs** authenticate with `X-API-Key`. Keys look like `hh_<prefix>_<secret>`; only a
  SHA-256 hash is stored, the prefix is a lookup index, and the plaintext is shown once.
  Keys can be revoked individually.

## Why SHA-256 for API keys but argon2 for passwords
Passwords are low-entropy and need a slow hash. API keys are 256-bit random values, so a fast
hash is safe, and verification sits on the hot path of every API request.

## Security details
- Login does equal work for unknown emails (no user enumeration by timing) and returns the
  same error for both failure modes.
- Password hashing runs in a thread so it does not block the event loop.
- Production refuses to boot with a default/short `JWT_SECRET`.

## Known limitations (tracked)
- Refresh tokens are stateless: no rotation or server-side revocation yet.
- Login is not rate-limited until phase 7.
