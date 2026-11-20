/** Server-Sent Events over `fetch`.
 *
 * `EventSource` cannot send an Authorization header, and putting a token in the URL leaks it into
 * logs. So we read the stream ourselves. Messages are *hints* (Redis pub/sub is lossy): the app
 * refetches whenever the connection (re)establishes.
 */
import type { ConnectionStatus } from "./types";

export interface SSEEvent {
  event: string;
  data: string;
}

/** Split a text buffer into complete SSE events plus the unconsumed remainder. */
export function parseSSE(buffer: string): { events: SSEEvent[]; rest: string } {
  const normalized = buffer.replace(/\r\n/g, "\n");
  const frames = normalized.split("\n\n");
  const rest = frames.pop() ?? ""; // the last piece may be an incomplete frame
  const events: SSEEvent[] = [];
  for (const frame of frames) {
    let event = "message";
    const data: string[] = [];
    let sawField = false;
    for (const line of frame.split("\n")) {
      if (line.startsWith(":")) continue; // comment / heartbeat
      const colon = line.indexOf(":");
      const field = colon === -1 ? line : line.slice(0, colon);
      const value = colon === -1 ? "" : line.slice(colon + 1).replace(/^ /, "");
      if (field === "event") {
        event = value;
        sawField = true;
      } else if (field === "data") {
        data.push(value);
        sawField = true;
      }
    }
    if (sawField) events.push({ event, data: data.join("\n") });
  }
  return { events, rest };
}

export interface StreamOptions {
  url: string;
  /** `forceRefresh` is true after any disconnect so a reconnect never reuses an expired token. */
  getToken: (forceRefresh: boolean) => Promise<string | null>;
  onMessage: (message: { event: string; data: unknown }) => void;
  onStatus: (status: ConnectionStatus) => void;
  fetchImpl?: typeof fetch;
  sleep?: (ms: number, signal: AbortSignal) => Promise<void>;
  random?: () => number;
}

export function abortableSleep(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    if (signal.aborted) return resolve();
    const timer = setTimeout(done, ms);
    function done() {
      clearTimeout(timer);
      signal.removeEventListener("abort", done);
      resolve();
    }
    signal.addEventListener("abort", done);
  });
}

export function backoffMs(attempt: number, random: () => number = Math.random): number {
  const capped = Math.min(30_000, 1_000 * 2 ** Math.max(0, attempt - 1));
  return capped * (0.5 + random() / 2); // jitter so a server restart does not cause a stampede
}

/** Keeps a stream open, reconnecting with backoff. Returns a function that stops it. */
export function connectStream(options: StreamOptions): () => void {
  const controller = new AbortController();
  const { signal } = controller;
  const doFetch = options.fetchImpl ?? fetch;
  const sleep = options.sleep ?? abortableSleep;
  const random = options.random ?? Math.random;

  async function run() {
    let attempt = 0;
    let forceRefresh = false;
    while (!signal.aborted) {
      options.onStatus(attempt === 0 ? "connecting" : "reconnecting");
      let wasLive = false;
      try {
        const token = await options.getToken(forceRefresh);
        if (!token) return options.onStatus("offline"); // signed out: nothing to stream
        const response = await doFetch(options.url, {
          headers: { Authorization: `Bearer ${token}`, Accept: "text/event-stream" },
          cache: "no-store",
          signal,
        });
        if (response.status === 401) {
          forceRefresh = true;
        } else if (response.status === 429) {
          const wait = Number(response.headers.get("Retry-After")) || 5;
          await sleep(wait * 1000, signal);
          continue;
        } else if (!response.ok || !response.body) {
          throw new Error(`stream failed with ${response.status}`);
        } else {
          const reader = response.body.getReader();
          const decoder = new TextDecoder();
          let buffer = "";
          for (;;) {
            const { done, value } = await reader.read();
            if (done) break;
            buffer += decoder.decode(value, { stream: true });
            const parsed = parseSSE(buffer);
            buffer = parsed.rest;
            for (const evt of parsed.events) {
              if (evt.event === "ready") {
                wasLive = true;
                attempt = 0;
                options.onStatus("live");
              }
              let data: unknown = null;
              try {
                data = evt.data ? JSON.parse(evt.data) : null;
              } catch {
                continue; // ignore a malformed frame rather than dropping the connection
              }
              options.onMessage({ event: evt.event, data });
            }
          }
          forceRefresh = true; // ended (lifetime cap / network): come back with a fresh token
        }
      } catch {
        if (signal.aborted) return;
        forceRefresh = true;
      }
      if (signal.aborted) return;
      // A stream that was healthy and then ended is the server asking us to reconnect: do it fast.
      attempt = wasLive ? 0 : attempt + 1;
      options.onStatus("reconnecting");
      await sleep(wasLive ? 250 : backoffMs(attempt, random), signal);
    }
  }

  void run();
  return () => controller.abort();
}
