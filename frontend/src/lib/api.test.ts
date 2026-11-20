// @vitest-environment node
import { describe, expect, it, vi } from "vitest";
import { ApiError, createApiClient, messageFromBody } from "./api";

function json(body: unknown, init: ResponseInit = {}): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json", ...(init.headers ?? {}) },
    ...init,
  });
}

function setup(responses: Response[], opts: { refresh?: string | null } = {}) {
  const fetchImpl = vi.fn<typeof fetch>();
  for (const response of responses) fetchImpl.mockResolvedValueOnce(response);
  let token: string | null = "old-token";
  const refreshAccessToken = vi.fn(async () => {
    token = opts.refresh === undefined ? "new-token" : opts.refresh;
    return token;
  });
  const onAuthLost = vi.fn();
  const client = createApiClient({
    baseUrl: "http://api.test",
    getAccessToken: () => token,
    refreshAccessToken,
    onAuthLost,
    fetchImpl,
  });
  return { client, fetchImpl, refreshAccessToken, onAuthLost };
}

const authHeader = (call: unknown[]) =>
  ((call[1] as RequestInit).headers as Record<string, string>).Authorization;

describe("requests", () => {
  it("sends the bearer token and parses JSON", async () => {
    const { client, fetchImpl } = setup([json({ ok: true })]);
    await expect(client.request("/v1/stats")).resolves.toEqual({ ok: true });
    expect(fetchImpl.mock.calls[0]?.[0]).toBe("http://api.test/v1/stats");
    expect(authHeader(fetchImpl.mock.calls[0] ?? [])).toBe("Bearer old-token");
  });

  it("serialises a JSON body and sets the content type", async () => {
    const { client, fetchImpl } = setup([json({ id: 1 }, { status: 201 })]);
    await client.request("/v1/events", { method: "POST", json: { type: "a.b" }, headers: { "Idempotency-Key": "k" } });
    const init = fetchImpl.mock.calls[0]?.[1] as RequestInit;
    expect(init.method).toBe("POST");
    expect(init.body).toBe('{"type":"a.b"}');
    expect((init.headers as Record<string, string>)["Content-Type"]).toBe("application/json");
    expect((init.headers as Record<string, string>)["Idempotency-Key"]).toBe("k");
  });

  it("returns undefined for 204", async () => {
    const { client } = setup([new Response(null, { status: 204 })]);
    await expect(client.request("/v1/api-keys/x", { method: "DELETE" })).resolves.toBeUndefined();
  });

  it("does not send credentials when auth is false", async () => {
    const { client, fetchImpl } = setup([json({ access_token: "a" })]);
    await client.request("/v1/auth/login", { method: "POST", json: {}, auth: false });
    expect(authHeader(fetchImpl.mock.calls[0] ?? [])).toBeUndefined();
  });
});

describe("token refresh", () => {
  it("refreshes once on 401 and retries with the new token", async () => {
    const { client, fetchImpl, refreshAccessToken } = setup([
      json({ detail: "Invalid or expired token" }, { status: 401 }),
      json({ ok: true }),
    ]);
    await expect(client.request("/v1/stats")).resolves.toEqual({ ok: true });
    expect(refreshAccessToken).toHaveBeenCalledTimes(1);
    expect(fetchImpl).toHaveBeenCalledTimes(2);
    expect(authHeader(fetchImpl.mock.calls[1] ?? [])).toBe("Bearer new-token");
  });

  it("signs the user out when the session cannot be renewed", async () => {
    const { client, onAuthLost } = setup([json({ detail: "expired" }, { status: 401 })], { refresh: null });
    await expect(client.request("/v1/stats")).rejects.toMatchObject({ status: 401 });
    expect(onAuthLost).toHaveBeenCalledTimes(1);
  });

  it("never loops: a second 401 after refreshing is an error", async () => {
    const { client, fetchImpl, refreshAccessToken } = setup([
      json({ detail: "nope" }, { status: 401 }),
      json({ detail: "still nope" }, { status: 401 }),
    ]);
    await expect(client.request("/v1/stats")).rejects.toMatchObject({ status: 401, message: "still nope" });
    expect(refreshAccessToken).toHaveBeenCalledTimes(1);
    expect(fetchImpl).toHaveBeenCalledTimes(2);
  });

  it("a wrong password (auth:false) is reported as such, without a refresh attempt", async () => {
    const { client, refreshAccessToken, onAuthLost } = setup([
      json({ detail: "Invalid email or password" }, { status: 401 }),
    ]);
    await expect(
      client.request("/v1/auth/login", { method: "POST", json: {}, auth: false }),
    ).rejects.toMatchObject({ message: "Invalid email or password" });
    expect(refreshAccessToken).not.toHaveBeenCalled();
    expect(onAuthLost).not.toHaveBeenCalled();
  });
});

describe("errors", () => {
  it("surfaces rate limits with the retry hint", async () => {
    const { client } = setup([
      json({ detail: "Rate limit exceeded" }, { status: 429, headers: { "Retry-After": "7" } }),
    ]);
    const error = await client.request("/v1/stats").catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 429, retryAfterSeconds: 7, message: "Rate limit exceeded" });
  });

  it("falls back to a generic message when the body is not JSON", async () => {
    const { client } = setup([new Response("<html>bad gateway</html>", { status: 502 })]);
    await expect(client.request("/v1/stats")).rejects.toMatchObject({ message: "Request failed (502)" });
  });
});

describe("messageFromBody", () => {
  it("uses string details as-is", () => {
    expect(messageFromBody({ detail: "Endpoint limit reached" }, 409)).toBe("Endpoint limit reached");
  });
  it("flattens FastAPI validation errors and drops the 'body' prefix", () => {
    const body = {
      detail: [
        { loc: ["body", "email"], msg: "value is not a valid email address", type: "value_error" },
        { loc: ["body", "password"], msg: "String should have at least 8 characters", type: "x" },
      ],
    };
    expect(messageFromBody(body, 422)).toBe(
      "email: value is not a valid email address; password: String should have at least 8 characters",
    );
  });
  it("handles unknown shapes", () => {
    expect(messageFromBody(null, 500)).toBe("Request failed (500)");
    expect(messageFromBody({ detail: 5 }, 500)).toBe("Request failed (500)");
  });
});
