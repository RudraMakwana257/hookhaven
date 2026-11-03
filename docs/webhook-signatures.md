# Verifying Hookhaven webhooks

Every delivery carries:

| Header | Meaning |
|---|---|
| `Hookhaven-Signature` | `t=<unix seconds>,v1=<hex HMAC-SHA256>` |
| `Hookhaven-Delivery-Id` | Stable across retries. **Use this to dedupe.** |
| `Hookhaven-Event-Id` / `Hookhaven-Event-Type` | The event being delivered |
| `Hookhaven-Attempt` | 1 for the first try, 2 for the first retry, ... |

The signature is `HMAC_SHA256(secret, "<t>." + raw_body)` where `secret` is the `whsec_...` value
returned when you created (or rotated) the endpoint.

Always verify against the **raw request bytes**, not a re-serialised JSON object.

## Python
```python
from app.core.signing import verify_signature  # or copy the ~15 lines

ok = verify_signature(secret, request.headers["Hookhaven-Signature"], raw_body)
if not ok:
    return Response(status_code=400)
```

## Node.js
```js
import { createHmac, timingSafeEqual } from "node:crypto";

export function verify(secret, header, rawBody, toleranceSec = 300) {
  const parts = Object.fromEntries(header.split(",").map((p) => p.trim().split("=")));
  const t = Number(parts.t);
  if (!t || Math.abs(Date.now() / 1000 - t) > toleranceSec) return false;
  const expected = createHmac("sha256", secret).update(`${t}.`).update(rawBody).digest("hex");
  const given = Buffer.from(parts.v1 ?? "", "hex");
  const want = Buffer.from(expected, "hex");
  return given.length === want.length && timingSafeEqual(given, want);
}
```

## Checklist for receivers
1. Verify the signature (constant-time compare) and reject timestamps older than ~5 minutes.
2. Respond `2xx` quickly; do slow work asynchronously. Anything else is retried.
3. Be idempotent: delivery is at-least-once, so store `Hookhaven-Delivery-Id` and skip repeats.
