// @vitest-environment node
import { describe, expect, it } from "vitest";
import { describe as describeMessage, staleKeys } from "./realtime";
import type { RealtimeMessage } from "./types";

const updated = (over: Partial<Extract<RealtimeMessage, { type: "delivery.updated" }>> = {}): RealtimeMessage => ({
  type: "delivery.updated",
  delivery_id: "d1d1d1d1-0000-0000-0000-000000000000",
  event_id: "e1",
  endpoint_id: "p1",
  status: "succeeded",
  attempt_count: 1,
  next_attempt_at: null,
  last_status_code: 200,
  last_error: null,
  attempt: null,
  ...over,
});

describe("describe (activity feed lines)", () => {
  it("celebrates success and links to the delivery", () => {
    const line = describeMessage(updated());
    expect(line).toMatchObject({ tone: "ok", text: "Delivery d1d1d1d1 succeeded", href: "/deliveries/d1d1d1d1-0000-0000-0000-000000000000" });
  });

  it("explains retries with the HTTP status or the error", () => {
    expect(describeMessage(updated({ status: "pending", last_status_code: 503 })).text).toBe("Delivery d1d1d1d1: HTTP 503, retrying");
    expect(describeMessage(updated({ status: "pending", last_status_code: null, last_error: "request timed out" })).text).toBe(
      "Delivery d1d1d1d1: request timed out, retrying",
    );
  });

  it("flags dead letters and disabled endpoints as problems", () => {
    expect(describeMessage(updated({ status: "dead", attempt_count: 7 }))).toMatchObject({ tone: "bad", text: expect.stringContaining("7 attempts") });
    expect(describeMessage({ type: "endpoint.disabled", endpoint_id: "p1", reason: "10 consecutive deliveries failed" })).toMatchObject({
      tone: "bad",
      href: "/endpoints/p1",
    });
  });

  it("describes new events and replays", () => {
    expect(
      describeMessage({ type: "event.created", event_id: "e9", event_type: "order.created", created_at: "x", delivery_ids: ["a", "b"] }),
    ).toMatchObject({ tone: "info", text: "Event order.created received (2 deliveries)", href: "/events/e9" });
    expect(describeMessage({ type: "deliveries.replayed", endpoint_id: "p1", count: 3 }).text).toBe("3 dead deliveries replayed");
  });
});

describe("staleKeys (what a message invalidates)", () => {
  it("targets the specific delivery, event and endpoint plus the lists and stats", () => {
    const keys = staleKeys(updated());
    expect(keys).toContainEqual(["delivery", "d1d1d1d1-0000-0000-0000-000000000000"]);
    expect(keys).toContainEqual(["event", "e1"]);
    expect(keys).toContainEqual(["endpoint", "p1"]);
    expect(keys).toContainEqual(["stats"]);
    expect(keys).toContainEqual(["deliveries"]);
  });

  it("a disabled endpoint refreshes endpoints and stats, not delivery lists", () => {
    const keys = staleKeys({ type: "endpoint.disabled", endpoint_id: "p1", reason: "r" });
    expect(keys).toContainEqual(["endpoints"]);
    expect(keys).toContainEqual(["endpoint", "p1"]);
    expect(keys).not.toContainEqual(["deliveries"]);
  });
});
