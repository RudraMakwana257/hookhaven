"use client";

import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useAuth } from "./auth";
import type {
  ApiKey,
  ApiKeyCreated,
  Delivery,
  DeliveryDetail,
  DeliveryStatus,
  Endpoint,
  EndpointWithSecret,
  EventDetail,
  EventListItem,
  Stats,
} from "./types";

export const PAGE_SIZE = 20;

/** Query keys: realtime invalidation relies on these prefixes. */
export const keys = {
  stats: ["stats"] as const,
  endpoints: ["endpoints"] as const,
  endpoint: (id: string) => ["endpoint", id] as const,
  events: ["events"] as const,
  event: (id: string) => ["event", id] as const,
  deliveries: ["deliveries"] as const,
  delivery: (id: string) => ["delivery", id] as const,
  apiKeys: ["api-keys"] as const,
};

function qs(params: Record<string, string | number | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) if (value !== undefined && value !== "") search.set(key, String(value));
  const text = search.toString();
  return text ? `?${text}` : "";
}

export function useStats() {
  const { api } = useAuth();
  return useQuery({ queryKey: keys.stats, queryFn: () => api.request<Stats>("/v1/stats"), refetchInterval: 60_000 });
}

export function useEndpoints() {
  const { api } = useAuth();
  return useQuery({
    queryKey: keys.endpoints,
    queryFn: () => api.request<Endpoint[]>(`/v1/endpoints${qs({ limit: 100 })}`),
  });
}

export function useEndpoint(id: string) {
  const { api } = useAuth();
  return useQuery({ queryKey: keys.endpoint(id), queryFn: () => api.request<Endpoint>(`/v1/endpoints/${id}`) });
}

/** One extra row is requested so the UI can tell whether a next page exists. */
export function useEvents(page: number, type: string) {
  const { api } = useAuth();
  return useQuery({
    queryKey: [...keys.events, { page, type }],
    queryFn: () =>
      api.request<EventListItem[]>(`/v1/events${qs({ limit: PAGE_SIZE + 1, offset: page * PAGE_SIZE, type })}`),
    placeholderData: keepPreviousData,
  });
}

export function useEvent(id: string) {
  const { api } = useAuth();
  return useQuery({ queryKey: keys.event(id), queryFn: () => api.request<EventDetail>(`/v1/events/${id}`) });
}

export interface DeliveryFilter {
  status?: DeliveryStatus;
  endpointId?: string;
  page: number;
}

export function useDeliveries(filter: DeliveryFilter, pageSize = PAGE_SIZE) {
  const { api } = useAuth();
  return useQuery({
    queryKey: [...keys.deliveries, filter, pageSize],
    queryFn: () =>
      api.request<Delivery[]>(
        `/v1/deliveries${qs({
          status: filter.status,
          endpoint_id: filter.endpointId,
          limit: pageSize + 1,
          offset: filter.page * pageSize,
        })}`,
      ),
    placeholderData: keepPreviousData,
  });
}

export function useDelivery(id: string) {
  const { api } = useAuth();
  return useQuery({ queryKey: keys.delivery(id), queryFn: () => api.request<DeliveryDetail>(`/v1/deliveries/${id}`) });
}

export function useApiKeys() {
  const { api } = useAuth();
  return useQuery({ queryKey: keys.apiKeys, queryFn: () => api.request<ApiKey[]>("/v1/api-keys") });
}

// ------------------------------------------------------------------ mutations

function useInvalidate() {
  const queryClient = useQueryClient();
  return (...queryKeys: readonly (readonly unknown[])[]) =>
    Promise.all(queryKeys.map((queryKey) => queryClient.invalidateQueries({ queryKey })));
}

export interface EndpointInput {
  url: string;
  description: string | null;
  event_types: string[];
}

export function useCreateEndpoint() {
  const { api } = useAuth();
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: (input: EndpointInput) => api.request<EndpointWithSecret>("/v1/endpoints", { method: "POST", json: input }),
    onSuccess: () => invalidate(keys.endpoints, keys.stats),
  });
}

export function useUpdateEndpoint(id: string) {
  const { api } = useAuth();
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: (changes: Partial<EndpointInput> & { is_active?: boolean }) =>
      api.request<Endpoint>(`/v1/endpoints/${id}`, { method: "PATCH", json: changes }),
    onSuccess: () => invalidate(keys.endpoints, keys.endpoint(id), keys.stats),
  });
}

export function useDeleteEndpoint(id: string) {
  const { api } = useAuth();
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: () => api.request<void>(`/v1/endpoints/${id}`, { method: "DELETE" }),
    onSuccess: () => invalidate(keys.endpoints, keys.stats, keys.deliveries, keys.events),
  });
}

export function useRotateSecret(id: string) {
  const { api } = useAuth();
  return useMutation({
    mutationFn: () => api.request<EndpointWithSecret>(`/v1/endpoints/${id}/rotate-secret`, { method: "POST" }),
  });
}

export function useReplayDelivery() {
  const { api } = useAuth();
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: (id: string) => api.request<Delivery>(`/v1/deliveries/${id}/replay`, { method: "POST" }),
    onSuccess: () => invalidate(keys.deliveries, keys.events, keys.stats),
  });
}

export function useReplayDead(endpointId: string) {
  const { api } = useAuth();
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: () => api.request<{ replayed: number }>(`/v1/endpoints/${endpointId}/replay-dead`, { method: "POST" }),
    onSuccess: () => invalidate(keys.deliveries, keys.events, keys.stats),
  });
}

export function useSendEvent() {
  const { api } = useAuth();
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: (input: { type: string; payload: Record<string, unknown>; idempotencyKey?: string }) =>
      api.request<EventDetail>("/v1/events", {
        method: "POST",
        json: { type: input.type, payload: input.payload },
        headers: input.idempotencyKey ? { "Idempotency-Key": input.idempotencyKey } : undefined,
      }),
    onSuccess: () => invalidate(keys.events, keys.stats, keys.deliveries),
  });
}

export function useCreateApiKey() {
  const { api } = useAuth();
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: (name: string) => api.request<ApiKeyCreated>("/v1/api-keys", { method: "POST", json: { name } }),
    onSuccess: () => invalidate(keys.apiKeys),
  });
}

export function useRevokeApiKey() {
  const { api } = useAuth();
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: (id: string) => api.request<void>(`/v1/api-keys/${id}`, { method: "DELETE" }),
    onSuccess: () => invalidate(keys.apiKeys),
  });
}
