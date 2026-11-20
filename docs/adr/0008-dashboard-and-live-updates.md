# ADR 0008: Dashboard and live updates

## Status
Accepted

## Live updates: Server-Sent Events over Redis pub/sub
Browsers need to see deliveries change state without polling. Options: polling, WebSockets, SSE.

**SSE** fits: traffic is one-directional (server to browser), it is plain HTTP (works through proxies,
no upgrade handshake), and the protocol is trivial to test. WebSockets add a bidirectional channel we
do not need.

```
worker / API --publish--> Redis channel hookhaven:rt:<user_id> --subscribe--> API stream --SSE--> browser
```
- One channel per user: a stream can only ever receive its owner's messages (tested).
- **Messages are hints.** Pub/sub is fire-and-forget; a disconnected client misses messages. The client
  refetches on every (re)connect, and Postgres remains the source of truth. This keeps the
  realtime path trivially simple, with no replay buffer or per-client offsets.
- Publishing is best-effort: a Redis hiccup never fails a delivery or an API request (tested).
- Frames: `ready` (client then refetches), `event.created`, `delivery.updated`, `endpoint.disabled`,
  `deliveries.replayed`, `reconnect`, plus `: keep-alive` comments so proxies do not idle us out.

## Authentication of a long-lived connection
- `EventSource` cannot set headers, and tokens in URLs leak into logs, so the client reads the stream
  with `fetch` and an `Authorization` header.
- A stream is authenticated once, at connect time. Therefore the server **caps its lifetime to the
  credential's remaining validity** (and to `SSE_MAX_LIFETIME_SECONDS`), sends `reconnect`, and closes;
  the client refreshes its token and reconnects. A revoked or expired credential cannot keep a stream alive.
- At most `SSE_MAX_STREAMS_PER_USER` (default 5) streams per user per API process: each holds a Redis
  connection, so this protects Redis from one tenant opening thousands of tabs. The cap is per replica,
  not global; that is enough for its purpose (resource protection, not billing).

## CORS
The dashboard is on another origin (Vercel vs the API host). `CORS_ALLOWED_ORIGINS` is an explicit
allowlist; production refuses `*`. Credentials mode is off: authentication is by bearer token, so there is
no ambient cookie authority for a malicious site to abuse. The CORS middleware is outermost so it answers
preflights before auth and rate limiting see them and decorates error responses too (otherwise browsers
hide 401/429 bodies from the app). Rate-limit, `Retry-After` and request-id headers are exposed.

## Dashboard-driven API additions
- `GET /v1/stats`: last-24h counts plus the all-time dead-letter total (a failure from last week still
  needs attention). It is aggregate SQL scoped to the user; for very large tenants it should become a
  cached or pre-aggregated figure.
- `GET /v1/events` includes per-event delivery counts (one grouped query per page, not one per row).

## Consequences
- Live updates degrade gracefully: if SSE is down the dashboard still works, just without push (queries
  still refetch on focus and on a 60 s stats interval).
- No exactly-once or ordered realtime stream. That is a deliberate non-goal.
- Multiple API replicas work without sticky sessions: any replica can serve any stream.
