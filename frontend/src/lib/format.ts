export function shortId(id: string): string {
  return id.slice(0, 8);
}

export function timeAgo(iso: string | null | undefined, now: Date = new Date()): string {
  if (!iso) return "never";
  const then = new Date(iso.endsWith("Z") || /[+-]\d\d:?\d\d$/.test(iso) ? iso : `${iso}Z`);
  const seconds = Math.round((now.getTime() - then.getTime()) / 1000);
  if (seconds < 0) return timeUntil(iso, now);
  if (seconds < 5) return "just now";
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.floor(hours / 24)}d ago`;
}

export function timeUntil(iso: string, now: Date = new Date()): string {
  const then = new Date(iso.endsWith("Z") || /[+-]\d\d:?\d\d$/.test(iso) ? iso : `${iso}Z`);
  const seconds = Math.round((then.getTime() - now.getTime()) / 1000);
  if (seconds <= 0) return "now";
  if (seconds < 60) return `in ${seconds}s`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `in ${minutes}m`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `in ${hours}h`;
  return `in ${Math.round(hours / 24)}d`;
}

export function formatDuration(ms: number): string {
  return ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(1)} s`;
}

/** "order.created, invoice.paid" -> ["order.created", "invoice.paid"]; blank -> ["*"]. */
export function parseEventTypes(input: string): string[] {
  const parts = input
    .split(/[,\s]+/)
    .map((part) => part.trim())
    .filter(Boolean);
  return parts.length === 0 ? ["*"] : Array.from(new Set(parts));
}

export type JsonObjectResult = { ok: true; value: Record<string, unknown> } | { ok: false; error: string };

export function parseJsonObject(text: string): JsonObjectResult {
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch (error) {
    return { ok: false, error: error instanceof Error ? error.message : "Invalid JSON" };
  }
  if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
    return { ok: false, error: "Payload must be a JSON object, e.g. {\"id\": 1}" };
  }
  return { ok: true, value: parsed as Record<string, unknown> };
}

export function retryMessage(seconds: number | undefined): string {
  return seconds ? `Too many requests. Try again in ${seconds}s.` : "Too many requests. Try again shortly.";
}
