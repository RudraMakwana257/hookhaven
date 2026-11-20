# Hookhaven dashboard

Next.js 15 (App Router) · TypeScript (strict) · Tailwind CSS 3 · TanStack Query.

```bash
cp .env.example .env.local        # NEXT_PUBLIC_API_URL=http://localhost:8000
npm install
npm run dev                       # http://localhost:3000
```
The API must list the dashboard's origin in `CORS_ALLOWED_ORIGINS` (e.g. `http://localhost:3000`).

| Script | What it does |
|---|---|
| `npm test` | Vitest: API client, SSE client, formatters, components, login flow |
| `npm run typecheck` | `tsc --noEmit`, strict + `noUncheckedIndexedAccess` |
| `npm run lint` | ESLint (`next/core-web-vitals` + `next/typescript`) |
| `npm run build` | Production build |

## Pages
Overview (stats, live activity feed) · Endpoints (create, edit, enable/disable, rotate secret, replay dead) ·
Events (send a test event, filter, detail with payload and deliveries) · Deliveries (status tabs, the
dead-letter queue, replay) · Delivery detail (attempt timeline with status codes and response snippets) ·
API keys (create-once reveal, revoke, curl example).

## How it is put together
```
src/lib/api.ts       typed fetch wrapper: bearer auth, ONE transparent refresh on 401, readable errors
src/lib/auth.tsx     session: access token in memory, single-flight refresh, clears the cache on sign-out
src/lib/sse.ts       Server-Sent Events over fetch: parser, reconnect with backoff + jitter, token refresh
src/lib/realtime.tsx stream -> batched TanStack Query invalidation + the activity feed
src/lib/hooks.ts     one hook per resource/mutation; query keys are what realtime invalidates
src/components/      accessible primitives (dialog with focus return, one-time secret reveal, ...)
```

## Decisions worth knowing
- **Live updates use SSE over `fetch`, not `EventSource`.** `EventSource` cannot send an
  `Authorization` header, and a token in the URL ends up in access logs and browser history.
- **Messages are hints, not data.** Redis pub/sub is lossy, so every (re)connect marks the lists stale
  and refetches; bursts of updates are collapsed into one refetch per query (300 ms window).
- **The server closes each stream before its access token expires** and tells the client to reconnect;
  the client then refreshes the token first. A stream never outlives its credential.
- **Secrets are shown once** (signing secret, API key) in a dialog that says so; the API cannot
  return them again.
- **Token storage trade-off.** The access token lives only in memory. The refresh token must survive
  reloads, so it is in `localStorage`, reachable by any XSS. The stricter design is a backend-for-frontend
  that keeps both in httpOnly cookies; that is the first thing to change if this ever holds sensitive tenants.
  The CSP and `no-store` headers on the API reduce, but do not remove, this exposure.
- **Sign-out is client-side.** Refresh tokens are stateless on the API (no server-side revocation yet).

## Deploying (Vercel)
Set `NEXT_PUBLIC_API_URL` to the public API URL, and add the Vercel domain to the API's
`CORS_ALLOWED_ORIGINS`. Nothing else is required.
