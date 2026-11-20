/** Typed fetch wrapper: bearer auth, one transparent token refresh, readable errors. */

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    message: string,
    public readonly retryAfterSeconds?: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export interface ClientOptions {
  baseUrl: string;
  getAccessToken: () => string | null;
  /** Resolves to a fresh access token, or null if the session cannot be renewed. */
  refreshAccessToken: () => Promise<string | null>;
  onAuthLost: () => void;
  fetchImpl?: typeof fetch;
}

export interface RequestOptions {
  method?: "GET" | "POST" | "PATCH" | "DELETE";
  json?: unknown;
  /** Defaults to true. Login/register send no credentials. */
  auth?: boolean;
  signal?: AbortSignal;
  headers?: Record<string, string>;
}

/** FastAPI sends `{detail: string}` or, for validation errors, `{detail: [{loc, msg}]}`. */
export function messageFromBody(body: unknown, status: number): string {
  if (body && typeof body === "object" && "detail" in body) {
    const detail = (body as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) {
      return detail
        .map((item: unknown) => {
          if (item && typeof item === "object" && "msg" in item) {
            const { msg, loc } = item as { msg: unknown; loc?: unknown };
            const field = Array.isArray(loc) ? loc.slice(1).join(".") : "";
            return field ? `${field}: ${String(msg)}` : String(msg);
          }
          return String(item);
        })
        .join("; ");
    }
  }
  return `Request failed (${status})`;
}

export function createApiClient(options: ClientOptions) {
  const doFetch = options.fetchImpl ?? fetch;

  async function send(path: string, opts: RequestOptions, token: string | null): Promise<Response> {
    const headers: Record<string, string> = { Accept: "application/json", ...opts.headers };
    if (opts.json !== undefined) headers["Content-Type"] = "application/json";
    if (token) headers.Authorization = `Bearer ${token}`;
    return doFetch(`${options.baseUrl}${path}`, {
      method: opts.method ?? "GET",
      headers,
      body: opts.json === undefined ? undefined : JSON.stringify(opts.json),
      signal: opts.signal,
      cache: "no-store",
    });
  }

  async function request<T>(path: string, opts: RequestOptions = {}): Promise<T> {
    const authenticated = opts.auth !== false;
    let response = await send(path, opts, authenticated ? options.getAccessToken() : null);

    if (response.status === 401 && authenticated) {
      const fresh = await options.refreshAccessToken();
      if (!fresh) {
        options.onAuthLost();
        throw new ApiError(401, "Your session has expired. Please sign in again.");
      }
      response = await send(path, opts, fresh); // retried exactly once
    }

    if (!response.ok) {
      const body: unknown = await response.json().catch(() => null);
      const retryAfter = Number(response.headers.get("Retry-After"));
      throw new ApiError(
        response.status,
        messageFromBody(body, response.status),
        Number.isFinite(retryAfter) && retryAfter > 0 ? retryAfter : undefined,
      );
    }
    if (response.status === 204) return undefined as T;
    return (await response.json()) as T;
  }

  return { request };
}

export type ApiClient = ReturnType<typeof createApiClient>;
