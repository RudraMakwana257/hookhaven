# ADR 0003: Signing secrets are encrypted, not hashed

## Status
Accepted

## Context
Every delivery is signed with HMAC-SHA256 using a per-endpoint secret, so the worker needs the
plaintext secret. Unlike passwords or API keys, it cannot be stored as a one-way hash.

## Decision
- Secrets are generated server-side (`whsec_<256-bit random>`), shown to the user **once**
  (on creation and rotation), and stored Fernet-encrypted.
- `ENCRYPTION_KEYS` is a comma-separated list: the first key encrypts, all keys decrypt, so a
  key can be rotated without downtime or re-encrypting everything at once (`MultiFernet`).
- A database leak alone does not reveal signing secrets; the attacker also needs the app key.

## Consequences
- Losing every encryption key means endpoint secrets must be rotated by their owners.
- Rotation currently invalidates the old secret immediately. A grace period during which both
  secrets sign requests is a planned improvement.
