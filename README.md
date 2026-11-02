# Hookhaven

Webhook delivery and event reliability platform. Register endpoints, send events,
and Hookhaven delivers them with signed requests, automatic retries (exponential
backoff), a dead-letter queue and manual replay.

**Status:** Phase 1 (foundation) complete. See the roadmap below.

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

## Design
See [`docs/adr`](docs/adr) for architecture decisions.

## Roadmap
- [x] 1. Scaffold, config, structured logging, health/readiness, Docker, CI
- [ ] 2. Models + migrations, JWT auth, API keys
- [ ] 3. Endpoint CRUD + SSRF protection
- [ ] 4. Event ingestion + idempotency
- [ ] 5. Worker: signed delivery + attempt log
- [ ] 6. Retry/backoff, DLQ, replay, lease recovery
- [ ] 7. Rate limiting, metrics
- [ ] 8. SSE + Next.js dashboard
- [ ] 9. Docs and deployment
