"""HMAC-SHA256 request signing (Stripe-style).

Header:  Hookhaven-Signature: t=<unix timestamp>,v1=<hex hmac>
Signed:  "<timestamp>.<raw request body>"

Including the timestamp in the MAC lets receivers reject replayed requests: an attacker who
captures a valid request cannot change `t`, and an old `t` is refused by `verify_signature`.
Several `v1=` entries may be present (useful during secret rotation); any match is accepted.
"""

import hashlib
import hmac
import time

SIGNATURE_HEADER = "Hookhaven-Signature"
DEFAULT_TOLERANCE_SECONDS = 300


def compute_signature(secret: str, timestamp: int, body: bytes) -> str:
    message = f"{timestamp}.".encode() + body
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def build_signature_header(secret: str, timestamp: int, body: bytes) -> str:
    return f"t={timestamp},v1={compute_signature(secret, timestamp, body)}"


def verify_signature(
    secret: str,
    header: str,
    body: bytes,
    *,
    tolerance_seconds: int = DEFAULT_TOLERANCE_SECONDS,
    now: float | None = None,
) -> bool:
    """What a receiver should run. Constant-time compare; rejects stale timestamps."""
    timestamp: int | None = None
    candidates: list[str] = []
    for part in header.split(","):
        key, _, value = part.strip().partition("=")
        if key == "t":
            try:
                timestamp = int(value)
            except ValueError:
                return False
        elif key == "v1" and value:
            candidates.append(value)
    if timestamp is None or not candidates:
        return False
    if abs((time.time() if now is None else now) - timestamp) > tolerance_seconds:
        return False
    expected = compute_signature(secret, timestamp, body)
    return any(hmac.compare_digest(expected, candidate) for candidate in candidates)
