#!/usr/bin/env python3
"""End-to-end smoke test against a RUNNING stack (API + worker + Redis + database).

Walks the whole product: register -> endpoint -> event -> signed delivery -> live SSE updates ->
failure -> dead letter -> fix receiver -> replay -> metrics -> CORS.

  API_URL       default http://localhost:8000
  RECEIVER_URL  control URL of scripts/demo_receiver.py as seen from here  (default http://localhost:9000)
  DESTINATION   the URL the *worker* should call (default RECEIVER_URL + /hook)

The worker must run with DELIVERY_MAX_ATTEMPTS=1 (so a failure dead-letters immediately) and, when
the receiver is on localhost, ALLOW_PRIVATE_DESTINATIONS=true.
"""

import json
import os
import sys
import threading
import time
import uuid

import httpx

API = os.environ.get("API_URL", "http://localhost:8000").rstrip("/")
RECEIVER = os.environ.get("RECEIVER_URL", "http://localhost:9000").rstrip("/")
DESTINATION = os.environ.get("DESTINATION", f"{RECEIVER}/hook")
ORIGIN = os.environ.get("DASHBOARD_ORIGIN", "http://localhost:3000")

failures: list[str] = []


def check(condition: bool, message: str) -> None:
    print(("  ok   " if condition else "  FAIL ") + message)
    if not condition:
        failures.append(message)


def wait_for(predicate, timeout: float = 15.0, what: str = "condition"):
    deadline = time.time() + timeout
    while time.time() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.2)
    check(False, f"timed out waiting for {what}")
    return None


class Stream(threading.Thread):
    """Reads /v1/stream like the dashboard does (fetch + Authorization header)."""

    def __init__(self, token: str) -> None:
        super().__init__(daemon=True)
        self.token = token
        self.events: list[tuple[str, dict]] = []
        self.ready = threading.Event()
        self.stop = threading.Event()

    def run(self) -> None:
        headers = {"Authorization": f"Bearer {self.token}", "Accept": "text/event-stream"}
        with httpx.stream("GET", f"{API}/v1/stream", headers=headers, timeout=None) as response:
            name = None
            for line in response.iter_lines():
                if self.stop.is_set():
                    return
                if line.startswith("event:"):
                    name = line[6:].strip()
                elif line.startswith("data:") and name:
                    data = json.loads(line[5:].strip() or "{}")
                    self.events.append((name, data))
                    if name == "ready":
                        self.ready.set()

    def has(self, name: str, **match) -> bool:
        return any(n == name and all(d.get(k) == v for k, v in match.items()) for n, d in self.events)


def main() -> int:
    http = httpx.Client(base_url=API, timeout=15)
    email = f"smoke-{uuid.uuid4().hex[:8]}@example.com"
    password = "correct-horse-battery-1"

    print("1. health, readiness and CORS")
    check(http.get("/health").json() == {"status": "ok"}, "liveness")
    ready = http.get("/ready")
    check(ready.status_code == 200, f"readiness (database + redis): {ready.json()}")
    preflight = http.options(
        "/v1/events",
        headers={"Origin": ORIGIN, "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "authorization,idempotency-key"},
    )
    check(preflight.headers.get("access-control-allow-origin") == ORIGIN, f"CORS preflight allows {ORIGIN}")

    print("2. account, login, endpoint")
    check(http.post("/v1/auth/register", json={"email": email, "password": password}).status_code == 201, "register")
    tokens = http.post("/v1/auth/login", json={"email": email, "password": password}).json()
    auth = {"Authorization": f"Bearer {tokens['access_token']}"}
    refreshed = http.post("/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    check(refreshed.status_code == 200, "token refresh")
    endpoint = http.post("/v1/endpoints", json={"url": DESTINATION, "event_types": ["order.created"]}, headers=auth)
    check(endpoint.status_code == 201, f"endpoint created ({endpoint.status_code}) {endpoint.text[:120] if endpoint.status_code != 201 else ''}")
    endpoint = endpoint.json()
    secret = endpoint["secret"]
    httpx.post(f"{RECEIVER}/_reset", timeout=5)
    httpx.post(f"{RECEIVER}/_config", json={"secret": secret, "mode": "ok"}, timeout=5)
    # These rules hold even with ALLOW_PRIVATE_DESTINATIONS (the local-dev switch):
    for bad in ("ftp://example.com/hook", "https://user:secret@example.com/hook"):
        check(http.post("/v1/endpoints", json={"url": bad}, headers=auth).status_code == 422, f"refused: {bad}")
    # The private-network rule is what the dev switch relaxes, so only assert it in strict mode.
    probe = http.post("/v1/endpoints", json={"url": "http://169.254.169.254/latest/meta-data/"}, headers=auth)
    if probe.status_code == 201:
        http.delete(f"/v1/endpoints/{probe.json()['id']}", headers=auth)  # do not leave it receiving events
        print("  note private destinations are allowed here (ALLOW_PRIVATE_DESTINATIONS=true): metadata check skipped")
    else:
        check(probe.status_code == 422, "cloud metadata address is refused (SSRF protection)")

    print("3. live stream")
    stream = Stream(tokens["access_token"])
    stream.start()
    check(stream.ready.wait(10), "SSE stream is ready")

    print("4. event -> signed delivery -> live updates")
    key = f"smoke-{uuid.uuid4().hex[:6]}"
    body = {"type": "order.created", "payload": {"order_id": 1042, "amount": 1999}}
    first = http.post("/v1/events", json=body, headers={**auth, "Idempotency-Key": key})
    check(first.status_code == 202, "event accepted (202)")
    event = first.json()
    again = http.post("/v1/events", json=body, headers={**auth, "Idempotency-Key": key})
    check(again.json()["id"] == event["id"] and again.headers.get("idempotent-replayed") == "true", "idempotent retry returns the original event")

    delivery_id = event["deliveries"][0]["id"]
    received = wait_for(lambda: httpx.get(f"{RECEIVER}/_received", timeout=5).json(), what="the receiver to get the webhook")
    if received:
        hit = received[0]
        check(hit["signature_valid"], "HMAC signature verified by the receiver")
        check(hit["delivery_id"] == delivery_id and hit["event_type"] == "order.created", "headers carry delivery and event ids")
        check(hit["body"]["data"] == body["payload"], "payload arrived intact")
        check(len(received) == 1, "delivered exactly once")
    done = wait_for(lambda: (d := http.get(f"/v1/deliveries/{delivery_id}", headers=auth).json())["status"] == "succeeded" and d, what="delivery to be recorded as succeeded")
    if done:
        check(done["attempts"][0]["status_code"] == 200, "attempt log records the 200")
    check(bool(wait_for(lambda: stream.has("event.created", event_id=event["id"]), 5, "event.created over SSE")), "SSE pushed event.created")
    check(bool(wait_for(lambda: stream.has("delivery.updated", delivery_id=delivery_id, status="succeeded"), 5, "delivery.updated over SSE")), "SSE pushed delivery.updated (succeeded)")

    print("5. receiver breaks -> dead letter")
    httpx.post(f"{RECEIVER}/_config", json={"mode": "fail"}, timeout=5)
    dead_event = http.post("/v1/events", json={"type": "order.created", "payload": {"order_id": 1043}}, headers=auth).json()
    dead_id = dead_event["deliveries"][0]["id"]
    dead = wait_for(lambda: (d := http.get(f"/v1/deliveries/{dead_id}", headers=auth).json())["status"] == "dead" and d, what="delivery to dead-letter")
    if dead:
        check(dead["attempts"][0]["status_code"] == 500, "failure recorded with the receiver's 500")
    check(bool(wait_for(lambda: stream.has("delivery.updated", delivery_id=dead_id, status="dead"), 5, "dead update over SSE")), "SSE pushed the dead-letter transition")
    dlq = http.get("/v1/deliveries?status=dead", headers=auth).json()
    check(dead_id in [d["id"] for d in dlq], "it is listed in the dead-letter queue")
    stats = http.get("/v1/stats", headers=auth).json()
    check(stats["dead_total"] >= 1 and stats["deliveries"]["succeeded"] >= 1, f"stats reflect it ({stats['deliveries']}, dead_total={stats['dead_total']})")

    print("6. receiver fixed -> replay")
    httpx.post(f"{RECEIVER}/_config", json={"mode": "ok"}, timeout=5)
    replay = http.post(f"/v1/deliveries/{dead_id}/replay", headers=auth)
    check(replay.status_code == 202, "replay accepted")
    healed = wait_for(lambda: (d := http.get(f"/v1/deliveries/{dead_id}", headers=auth).json())["status"] == "succeeded" and d, what="replayed delivery to succeed")
    if healed:
        check([a["attempt_number"] for a in healed["attempts"]] == [1, 2], "attempt history preserved, numbering continues")
    again_hits = [r for r in httpx.get(f"{RECEIVER}/_received", timeout=5).json() if r["delivery_id"] == dead_id]
    check(len({r["delivery_id"] for r in again_hits}) == 1 and again_hits[-1]["responded"] == 200, "receiver saw the same delivery id on replay")

    print("7. rate limiting and metrics")
    limited = [http.get("/v1/endpoints", headers=auth) for _ in range(5)]
    check(all("ratelimit-remaining" in r.headers for r in limited), "responses carry RateLimit headers")
    metrics = http.get("/metrics")
    check(metrics.status_code == 200 and "hookhaven_http_requests_total" in metrics.text, "API metrics exposed")
    check('route="/v1/endpoints/{endpoint_id}"' in metrics.text or 'route="/v1/endpoints"' in metrics.text, "metrics use route templates")
    check("hookhaven_queue_depth" in metrics.text, "queue depth gauge exposed")

    worker_metrics = os.environ.get("WORKER_METRICS_URL", "http://localhost:9100/metrics")
    try:
        text = httpx.get(worker_metrics, timeout=5).text
        check('hookhaven_delivery_attempts_total{outcome="success"}' in text, "worker metrics: successful attempts counted")
        check('hookhaven_delivery_attempts_total{outcome="http_5xx"}' in text, "worker metrics: the 500 was classified http_5xx")
        check("hookhaven_deliveries_finalized_total" in text, "worker metrics: delivery outcomes counted")
    except httpx.HTTPError:
        print(f"  note worker metrics not reachable at {worker_metrics}: skipped")

    stream.stop.set()
    print()
    if failures:
        print(f"{len(failures)} check(s) FAILED")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
