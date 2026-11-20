// @vitest-environment node
import { describe, expect, it, vi } from "vitest";
import { backoffMs, connectStream, parseSSE } from "./sse";
import type { ConnectionStatus } from "./types";

describe("parseSSE", () => {
  it("parses complete frames and leaves the remainder", () => {
    const { events, rest } = parseSSE('event: ready\ndata: {}\n\nevent: delivery.updated\ndata: {"a":1}\n\nevent: par');
    expect(events).toEqual([
      { event: "ready", data: "{}" },
      { event: "delivery.updated", data: '{"a":1}' },
    ]);
    expect(rest).toBe("event: par");
  });

  it("copes with frames split at arbitrary chunk boundaries", () => {
    const full = 'event: x\ndata: {"n":1}\n\n';
    let buffer = "";
    const seen: string[] = [];
    for (const ch of full) {
      buffer += ch;
      const parsed = parseSSE(buffer);
      buffer = parsed.rest;
      seen.push(...parsed.events.map((e) => e.data));
    }
    expect(seen).toEqual(['{"n":1}']);
  });

  it("handles CRLF line endings", () => {
    expect(parseSSE("event: a\r\ndata: 1\r\n\r\n").events).toEqual([{ event: "a", data: "1" }]);
  });

  it("ignores comment-only heartbeat frames", () => {
    expect(parseSSE(": keep-alive\n\n").events).toEqual([]);
  });

  it("joins multi-line data and defaults the event name", () => {
    expect(parseSSE("data: line1\ndata: line2\n\n").events).toEqual([{ event: "message", data: "line1\nline2" }]);
  });
});

describe("backoffMs", () => {
  it("grows exponentially, is capped, and is jittered", () => {
    expect(backoffMs(1, () => 1)).toBe(1000);
    expect(backoffMs(1, () => 0)).toBe(500);
    expect(backoffMs(3, () => 1)).toBe(4000);
    expect(backoffMs(50, () => 1)).toBe(30_000);
    expect(backoffMs(50, () => 0)).toBe(15_000);
  });
});

// ------------------------------------------------------------------ connectStream

const enc = new TextEncoder();

/** A streaming Response. `hang` keeps it open until the caller aborts. */
function streamResponse(chunks: string[], opts: { hang?: boolean; signal?: AbortSignal; status?: number } = {}) {
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(enc.encode(chunk));
      if (!opts.hang) controller.close();
      opts.signal?.addEventListener("abort", () =>
        controller.error(new DOMException("aborted", "AbortError")),
      );
    },
  });
  return new Response(body, { status: opts.status ?? 200 });
}

const instantSleep = vi.fn(async () => {});

function harness() {
  const statuses: ConnectionStatus[] = [];
  const messages: { event: string; data: unknown }[] = [];
  const getToken = vi.fn(async (force: boolean) => (force ? "fresh" : "first"));
  return { statuses, messages, getToken };
}

async function until(predicate: () => boolean) {
  for (let i = 0; i < 200 && !predicate(); i++) await new Promise((r) => setTimeout(r, 5));
  expect(predicate()).toBe(true);
}

describe("connectStream", () => {
  it("goes live, forwards messages, then reconnects with a fresh token after the stream ends", async () => {
    const h = harness();
    const fetchImpl = vi
      .fn<typeof fetch>()
      .mockImplementationOnce(async () =>
        streamResponse([
          "event: ready\ndata: {}\n\n",
          'event: delivery.updated\ndata: {"status":"dead"}\n\n',
          'event: reconnect\ndata: {"reason":"max_lifetime"}\n\n',
        ]),
      )
      .mockImplementation(async (_url, init) => streamResponse(["event: ready\ndata: {}\n\n"], { hang: true, signal: init?.signal as AbortSignal }));

    const stop = connectStream({
      url: "http://api/v1/stream",
      getToken: h.getToken,
      onMessage: (m) => h.messages.push(m),
      onStatus: (s) => h.statuses.push(s),
      fetchImpl,
      sleep: instantSleep,
    });
    await until(() => fetchImpl.mock.calls.length >= 2 && h.statuses.filter((s) => s === "live").length >= 2);
    stop();

    expect(h.messages.map((m) => m.event)).toContain("delivery.updated");
    expect(h.messages.find((m) => m.event === "delivery.updated")?.data).toEqual({ status: "dead" });
    expect(h.getToken.mock.calls.map((c) => c[0])).toEqual([false, true]); // refreshed on reconnect
    const headers = (fetchImpl.mock.calls[1]?.[1] as RequestInit).headers as Record<string, string>;
    expect(headers.Authorization).toBe("Bearer fresh");
    expect(headers.Accept).toBe("text/event-stream");
    expect(h.statuses).toContain("reconnecting");
  });

  it("refreshes the token and retries when the server answers 401", async () => {
    const h = harness();
    const fetchImpl = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(new Response("{}", { status: 401 }))
      .mockImplementation(async (_u, init) => streamResponse(["event: ready\ndata: {}\n\n"], { hang: true, signal: init?.signal as AbortSignal }));

    const stop = connectStream({
      url: "http://api/v1/stream",
      getToken: h.getToken,
      onMessage: () => {},
      onStatus: (s) => h.statuses.push(s),
      fetchImpl,
      sleep: instantSleep,
    });
    await until(() => h.statuses.includes("live"));
    stop();
    expect(h.getToken.mock.calls.map((c) => c[0])).toEqual([false, true]);
  });

  it("honours Retry-After when the per-user stream cap is hit", async () => {
    const h = harness();
    const sleep = vi.fn<(ms: number, signal: AbortSignal) => Promise<void>>(async () => {});
    const fetchImpl = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(new Response("{}", { status: 429, headers: { "Retry-After": "5" } }))
      .mockImplementation(async (_u, init) => streamResponse(["event: ready\ndata: {}\n\n"], { hang: true, signal: init?.signal as AbortSignal }));

    const stop = connectStream({ url: "u", getToken: h.getToken, onMessage: () => {}, onStatus: (s) => h.statuses.push(s), fetchImpl, sleep });
    await until(() => h.statuses.includes("live"));
    stop();
    expect(sleep.mock.calls[0]?.[0]).toBe(5000);
  });

  it("stops quietly when there is no session", async () => {
    const h = harness();
    const fetchImpl = vi.fn<typeof fetch>();
    connectStream({ url: "u", getToken: async () => null, onMessage: () => {}, onStatus: (s) => h.statuses.push(s), fetchImpl, sleep: instantSleep });
    await until(() => h.statuses.includes("offline"));
    expect(fetchImpl).not.toHaveBeenCalled();
  });

  it("skips malformed frames without dropping the connection", async () => {
    const h = harness();
    const fetchImpl = vi
      .fn<typeof fetch>()
      .mockImplementation(async (_u, init) =>
        streamResponse(["event: ready\ndata: {}\n\n", "event: bad\ndata: {not json\n\n", 'event: good\ndata: {"ok":true}\n\n'], { hang: true, signal: init?.signal as AbortSignal }),
      );
    const stop = connectStream({ url: "u", getToken: h.getToken, onMessage: (m) => h.messages.push(m), onStatus: () => {}, fetchImpl, sleep: instantSleep });
    await until(() => h.messages.some((m) => m.event === "good"));
    stop();
    expect(h.messages.map((m) => m.event)).toEqual(["ready", "good"]);
  });

  it("backs off between failed attempts and stops when told to", async () => {
    const h = harness();
    const sleeps: number[] = [];
    const fetchImpl = vi.fn<typeof fetch>().mockRejectedValue(new TypeError("network down"));
    const stop = connectStream({
      url: "u",
      getToken: h.getToken,
      onMessage: () => {},
      onStatus: (s) => h.statuses.push(s),
      fetchImpl,
      sleep: async (ms) => {
        sleeps.push(ms);
        await new Promise((r) => setTimeout(r, 1));
      },
      random: () => 1,
    });
    await until(() => sleeps.length >= 3);
    stop();
    expect(sleeps.slice(0, 3)).toEqual([1000, 2000, 4000]);
  });
});
