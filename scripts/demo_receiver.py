#!/usr/bin/env python3
"""A tiny webhook receiver for demos and smoke tests. Standard library only.

It shows what a *real* receiver must do: verify the HMAC signature over the raw body, reject stale
timestamps, and dedupe on `Hookhaven-Delivery-Id` (delivery is at-least-once).

  POST /hook            the webhook target (verifies, records, answers 200 or 500 per mode)
  POST /_config         {"secret": "whsec_...", "mode": "ok" | "fail"}
  GET  /_received       what has been received so far
  POST /_reset          forget everything

Run:  python scripts/demo_receiver.py --port 9000
"""

import argparse
import hashlib
import hmac
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TOLERANCE_SECONDS = 300
state = {"secret": "", "mode": "ok", "received": [], "seen_deliveries": set()}
lock = threading.Lock()


def verify_signature(secret: str, header: str, body: bytes) -> bool:
    """Reference implementation (see docs/webhook-signatures.md)."""
    timestamp, candidates = None, []
    for part in header.split(","):
        key, _, value = part.strip().partition("=")
        if key == "t" and value.isdigit():
            timestamp = int(value)
        elif key == "v1" and value:
            candidates.append(value)
    if timestamp is None or not candidates or abs(time.time() - timestamp) > TOLERANCE_SECONDS:
        return False
    expected = hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256).hexdigest()
    return any(hmac.compare_digest(expected, c) for c in candidates)


class Handler(BaseHTTPRequestHandler):
    def _send(self, status: int, payload: object) -> None:
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _body(self) -> bytes:
        return self.rfile.read(int(self.headers.get("Content-Length", 0)))

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/_received":
            with lock:
                self._send(200, state["received"])
        else:
            self._send(404, {"detail": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        body = self._body()
        if self.path == "/_config":
            config = json.loads(body or b"{}")
            with lock:
                state["secret"] = config.get("secret", state["secret"])
                state["mode"] = config.get("mode", state["mode"])
            return self._send(200, {"mode": state["mode"]})
        if self.path == "/_reset":
            with lock:
                state["received"].clear()
                state["seen_deliveries"].clear()
            return self._send(200, {"ok": True})
        if self.path != "/hook":
            return self._send(404, {"detail": "not found"})

        delivery_id = self.headers.get("Hookhaven-Delivery-Id", "")
        valid = verify_signature(state["secret"], self.headers.get("Hookhaven-Signature", ""), body)
        with lock:
            duplicate = delivery_id in state["seen_deliveries"]
            status = 400 if not valid else (500 if state["mode"] == "fail" else 200)
            if status == 200:
                state["seen_deliveries"].add(delivery_id)
            state["received"].append(
                {
                    "delivery_id": delivery_id,
                    "event_id": self.headers.get("Hookhaven-Event-Id"),
                    "event_type": self.headers.get("Hookhaven-Event-Type"),
                    "attempt": self.headers.get("Hookhaven-Attempt"),
                    "signature_valid": valid,
                    "duplicate": duplicate,
                    "responded": status,
                    "body": json.loads(body) if valid and body else None,
                }
            )
        self._send(status, {"received": status == 200})

    def log_message(self, fmt: str, *args: object) -> None:  # quiet
        pass


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=9000)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()
    print(f"demo receiver listening on http://{args.host}:{args.port}/hook")
    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()
