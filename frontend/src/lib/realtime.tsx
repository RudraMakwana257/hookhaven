"use client";

import { useQueryClient } from "@tanstack/react-query";
import { createContext, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { API_URL, useAuth } from "./auth";
import { shortId } from "./format";
import { keys } from "./hooks";
import { connectStream } from "./sse";
import type { ConnectionStatus, RealtimeMessage } from "./types";

export interface ActivityItem {
  id: number;
  at: Date;
  tone: "ok" | "warn" | "bad" | "info";
  text: string;
  href?: string;
}

interface RealtimeValue {
  status: ConnectionStatus;
  activity: ActivityItem[];
}

const RealtimeContext = createContext<RealtimeValue>({ status: "offline", activity: [] });
const MAX_ACTIVITY = 20;

/** Describes a message as a line for the activity feed. */
export function describe(message: RealtimeMessage): Omit<ActivityItem, "id" | "at"> {
  switch (message.type) {
    case "event.created":
      return {
        tone: "info",
        text: `Event ${message.event_type} received (${message.delivery_ids.length} deliveries)`,
        href: `/events/${message.event_id}`,
      };
    case "delivery.updated": {
      const href = `/deliveries/${message.delivery_id}`;
      const id = shortId(message.delivery_id);
      if (message.status === "succeeded") return { tone: "ok", text: `Delivery ${id} succeeded`, href };
      if (message.status === "dead") return { tone: "bad", text: `Delivery ${id} dead-lettered after ${message.attempt_count} attempts`, href };
      const why = message.last_status_code ? `HTTP ${message.last_status_code}` : (message.last_error ?? "pending");
      return { tone: "warn", text: `Delivery ${id}: ${why}, retrying`, href };
    }
    case "endpoint.disabled":
      return { tone: "bad", text: `Endpoint disabled: ${message.reason}`, href: `/endpoints/${message.endpoint_id}` };
    case "deliveries.replayed":
      return { tone: "info", text: `${message.count} dead deliveries replayed`, href: `/endpoints/${message.endpoint_id}` };
  }
}

/** Which cached queries a message makes stale. */
export function staleKeys(message: RealtimeMessage): (readonly unknown[])[] {
  switch (message.type) {
    case "event.created":
      return [keys.stats, keys.events, keys.deliveries];
    case "delivery.updated":
      return [
        keys.stats,
        keys.events,
        keys.deliveries,
        keys.delivery(message.delivery_id),
        keys.event(message.event_id),
        keys.endpoint(message.endpoint_id),
      ];
    case "endpoint.disabled":
      return [keys.stats, keys.endpoints, keys.endpoint(message.endpoint_id)];
    case "deliveries.replayed":
      return [keys.stats, keys.events, keys.deliveries, keys.endpoint(message.endpoint_id)];
  }
}

const KNOWN = new Set(["event.created", "delivery.updated", "endpoint.disabled", "deliveries.replayed"]);

export function RealtimeProvider({ children }: { children: ReactNode }) {
  const { state, getAccessToken, refreshAccessToken } = useAuth();
  const queryClient = useQueryClient();
  const [status, setStatus] = useState<ConnectionStatus>("connecting");
  const [activity, setActivity] = useState<ActivityItem[]>([]);
  const counter = useRef(0);
  const authenticated = state.status === "authenticated";

  useEffect(() => {
    if (!authenticated) return;

    // A burst of updates (a worker draining a backlog) must not become a burst of refetches:
    // collect keys and flush once.
    const pending = new Map<string, readonly unknown[]>();
    let timer: ReturnType<typeof setTimeout> | null = null;
    const schedule = (queryKeys: (readonly unknown[])[]) => {
      for (const key of queryKeys) pending.set(JSON.stringify(key), key);
      timer ??= setTimeout(() => {
        timer = null;
        const flushed = [...pending.values()];
        pending.clear();
        for (const queryKey of flushed) void queryClient.invalidateQueries({ queryKey });
      }, 300);
    };

    const stop = connectStream({
      url: `${API_URL}/v1/stream`,
      getToken: async (force) => (force ? refreshAccessToken() : (getAccessToken() ?? refreshAccessToken())),
      onStatus: setStatus,
      onMessage: ({ event, data }) => {
        if (event === "ready") {
          // Messages are lossy hints: after (re)connecting, treat everything as possibly stale.
          schedule([keys.stats, keys.events, keys.deliveries, keys.endpoints]);
          return;
        }
        if (!KNOWN.has(event) || typeof data !== "object" || data === null) return;
        const message = data as RealtimeMessage;
        schedule(staleKeys(message));
        const item = describe(message);
        setActivity((items) => [{ ...item, id: counter.current++, at: new Date() }, ...items].slice(0, MAX_ACTIVITY));
      },
    });
    return () => {
      stop();
      if (timer) clearTimeout(timer);
    };
  }, [authenticated, queryClient, getAccessToken, refreshAccessToken]);

  const value = useMemo(() => ({ status: authenticated ? status : "offline", activity }), [authenticated, status, activity]);
  return <RealtimeContext.Provider value={value}>{children}</RealtimeContext.Provider>;
}

export function useRealtime(): RealtimeValue {
  return useContext(RealtimeContext);
}
