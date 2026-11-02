# Hookhaven

Webhook delivery and event reliability platform. Register endpoints, send events,
and Hookhaven delivers them with signed requests, automatic retries (exponential
backoff), a dead-letter queue and manual replay.

**Status:** Phases 1-3 (foundation, auth, endpoints + SSRF protection) complete. See the roadmap below.

## Stack
FastAPI · PostgreSQL · Redis · Python workers · Next.js · Docker

## Quick start
```bash
cp .env.example .env
make up            # api on http://localhost:8000/docs
```

Local development:
```bash
make install
make check         # ruff, mypy (strict), pytest
```

## API so far
| Method | Path | Auth |
|---|---|---|
| POST | `/v1/auth/register` | none |
| POST | `/v1/auth/login`, `/v1/auth/refresh` | none / refresh token |
| GET | `/v1/auth/me` | Bearer JWT |
| POST/GET | `/v1/api-keys` | Bearer JWT |
| DELETE | `/v1/api-keys/{id}` (revoke) | Bearer JWT |
| POST/GET | `/v1/endpoints` | Bearer JWT **or** `X-API-Key` |
| GET/PATCH/DELETE | `/v1/endpoints/{id}` | Bearer JWT or `X-API-Key` |
| POST | `/v1/endpoints/{id}/rotate-secret` | Bearer JWT or `X-API-Key` |

Interactive docs at `/docs`.

## Design
- Architecture decisions: [`docs/adr`](docs/adr)
- SSRF threat model and controls: [`docs/security/ssrf.md`](docs/security/ssrf.md)

## Roadmap
- [x] 1. Scaffold, config, structured logging, health/readiness, Docker, CI
- [x] 2. Models + migrations, JWT auth, API keys
- [x] 3. Endpoint CRUD + SSRF protection
- [ ] 4. Event ingestion + idempotency
- [ ] 5. Worker: signed delivery + attempt log
- [ ] 6. Retry/backoff, DLQ, replay, lease recovery
- [ ] 7. Rate limiting, metrics
- [ ] 8. SSE + Next.js dashboard
- [ ] 9. Docs and deployment
