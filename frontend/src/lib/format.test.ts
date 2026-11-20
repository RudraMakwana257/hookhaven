// @vitest-environment node
import { describe, expect, it } from "vitest";
import { formatDuration, parseEventTypes, parseJsonObject, shortId, timeAgo, timeUntil } from "./format";

const NOW = new Date("2026-11-04T12:00:00Z");

describe("timeAgo", () => {
  it.each([
    ["2026-11-04T11:59:58Z", "just now"],
    ["2026-11-04T11:59:30Z", "30s ago"],
    ["2026-11-04T11:55:00Z", "5m ago"],
    ["2026-11-04T09:00:00Z", "3h ago"],
    ["2026-11-01T12:00:00Z", "3d ago"],
  ])("%s -> %s", (iso, expected) => {
    expect(timeAgo(iso, NOW)).toBe(expected);
  });

  it("treats timestamps without a zone as UTC (the API may omit it)", () => {
    expect(timeAgo("2026-11-04T11:55:00", NOW)).toBe("5m ago");
    expect(timeAgo("2026-11-04T11:55:00+00:00", NOW)).toBe("5m ago");
  });

  it("handles missing values and future times", () => {
    expect(timeAgo(null, NOW)).toBe("never");
    expect(timeAgo(undefined, NOW)).toBe("never");
    expect(timeAgo("2026-11-04T12:00:30Z", NOW)).toBe("in 30s");
  });
});

describe("timeUntil", () => {
  it.each([
    ["2026-11-04T11:59:00Z", "now"],
    ["2026-11-04T12:00:10Z", "in 10s"],
    ["2026-11-04T12:10:00Z", "in 10m"],
    ["2026-11-04T18:00:00Z", "in 6h"],
    ["2026-11-06T12:00:00Z", "in 2d"],
  ])("%s -> %s", (iso, expected) => {
    expect(timeUntil(iso, NOW)).toBe(expected);
  });
});

describe("misc formatting", () => {
  it("shortens ids and durations", () => {
    expect(shortId("1f3a9c2e-aaaa-bbbb-cccc-1234567890ab")).toBe("1f3a9c2e");
    expect(formatDuration(42)).toBe("42 ms");
    expect(formatDuration(1500)).toBe("1.5 s");
  });
});

describe("parseEventTypes", () => {
  it("splits on commas and whitespace and de-duplicates", () => {
    expect(parseEventTypes("order.created, invoice.paid  order.created")).toEqual([
      "order.created",
      "invoice.paid",
    ]);
  });
  it("defaults to the wildcard when blank", () => {
    expect(parseEventTypes("")).toEqual(["*"]);
    expect(parseEventTypes("  , ")).toEqual(["*"]);
  });
});

describe("parseJsonObject", () => {
  it("accepts objects", () => {
    expect(parseJsonObject('{"a": 1}')).toEqual({ ok: true, value: { a: 1 } });
  });
  it.each(["[1,2]", "42", '"text"', "null"])("rejects non-objects: %s", (text) => {
    const result = parseJsonObject(text);
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.error).toMatch(/JSON object/);
  });
  it("reports syntax errors", () => {
    const result = parseJsonObject("{oops");
    expect(result.ok).toBe(false);
  });
});
