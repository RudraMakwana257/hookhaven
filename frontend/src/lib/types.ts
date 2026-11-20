export type DeliveryStatus = "pending" | "succeeded" | "dead";

export interface User {
  id: string;
  email: string;
  created_at: string;
}

export interface TokenPair {
  access_token: string;
  refresh_token: string;
  token_type: string;
}

export interface Endpoint {
  id: string;
  url: string;
  description: string | null;
  event_types: string[];
  is_active: boolean;
  consecutive_failures: number;
  disabled_at: string | null;
  disabled_reason: string | null;
  created_at: string;
  updated_at: string;
}

/** Returned once, on creation and on secret rotation. */
export interface EndpointWithSecret extends Endpoint {
  secret: string;
}

export interface Delivery {
  id: string;
  endpoint_id: string;
  status: DeliveryStatus;
  attempt_count: number;
  next_attempt_at: string | null;
  last_status_code: number | null;
  last_error: string | null;
  delivered_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface Attempt {
  attempt_number: number;
  started_at: string;
  duration_ms: number;
  success: boolean;
  status_code: number | null;
  error: string | null;
  response_snippet: string | null;
}

export interface DeliveryDetail extends Delivery {
  event_id: string;
  attempts: Attempt[];
}

export interface EventListItem {
  id: string;
  type: string;
  idempotency_key: string | null;
  created_at: string;
  delivery_counts: Record<DeliveryStatus, number>;
}

export interface EventDetail {
  id: string;
  type: string;
  idempotency_key: string | null;
  created_at: string;
  payload: Record<string, unknown>;
  deliveries: Delivery[];
}

export interface ApiKey {
  id: string;
  name: string;
  prefix: string;
  created_at: string;
  last_used_at: string | null;
  revoked_at: string | null;
}

/** Returned once, at creation. */
export interface ApiKeyCreated extends ApiKey {
  key: string;
}

export interface Stats {
  window_hours: number;
  events: number;
  deliveries: Record<DeliveryStatus, number>;
  dead_total: number;
  endpoints: { total: number; active: number; disabled: number };
}

// ---- realtime (Server-Sent Events) ----

export type RealtimeMessage =
  | { type: "event.created"; event_id: string; event_type: string; created_at: string; delivery_ids: string[] }
  | {
      type: "delivery.updated";
      delivery_id: string;
      event_id: string;
      endpoint_id: string;
      status: DeliveryStatus;
      attempt_count: number;
      next_attempt_at: string | null;
      last_status_code: number | null;
      last_error: string | null;
      attempt: { attempt_number: number; success: boolean; status_code: number | null; error: string | null; duration_ms: number } | null;
    }
  | { type: "endpoint.disabled"; endpoint_id: string; reason: string }
  | { type: "deliveries.replayed"; endpoint_id: string; count: number };

export type ConnectionStatus = "connecting" | "live" | "reconnecting" | "offline";
