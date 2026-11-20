"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { createApiClient, type ApiClient } from "./api";
import type { TokenPair, User } from "./types";

export const API_URL = (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000").replace(/\/$/, "");

// Trade-off, stated plainly: the access token lives only in memory, but the refresh token has to
// survive reloads, so it sits in localStorage and is reachable by any XSS. The stricter design is
// an httpOnly-cookie BFF; see frontend/README.md.
const REFRESH_KEY = "hookhaven.refresh_token";

export type AuthState =
  | { status: "loading" }
  | { status: "anonymous" }
  | { status: "authenticated"; user: User };

interface AuthContextValue {
  state: AuthState;
  api: ApiClient;
  login: (email: string, password: string) => Promise<void>;
  register: (email: string, password: string) => Promise<void>;
  logout: () => void;
  getAccessToken: () => string | null;
  /** Single-flight: concurrent callers share one refresh request. */
  refreshAccessToken: () => Promise<string | null>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const accessToken = useRef<string | null>(null);
  const inFlight = useRef<Promise<string | null> | null>(null);
  const [state, setState] = useState<AuthState>({ status: "loading" });

  const store = useCallback((pair: TokenPair) => {
    accessToken.current = pair.access_token;
    localStorage.setItem(REFRESH_KEY, pair.refresh_token);
  }, []);

  const clear = useCallback(() => {
    accessToken.current = null;
    localStorage.removeItem(REFRESH_KEY);
    queryClient.clear(); // never show one user's cached data to the next
    setState({ status: "anonymous" });
  }, [queryClient]);

  const refreshAccessToken = useCallback((): Promise<string | null> => {
    if (!inFlight.current) {
      inFlight.current = (async () => {
        const refreshToken = localStorage.getItem(REFRESH_KEY);
        if (!refreshToken) return null;
        try {
          const response = await fetch(`${API_URL}/v1/auth/refresh`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ refresh_token: refreshToken }),
          });
          if (response.status === 401) {
            localStorage.removeItem(REFRESH_KEY); // definitively invalid
            return null;
          }
          if (!response.ok) return null; // transient (rate limit, 5xx): keep the token
          const pair = (await response.json()) as TokenPair;
          store(pair);
          return pair.access_token;
        } catch {
          return null; // network error: keep the token, the user may just be offline
        }
      })().finally(() => {
        inFlight.current = null;
      });
    }
    return inFlight.current;
  }, [store]);

  const api = useMemo(
    () =>
      createApiClient({
        baseUrl: API_URL,
        getAccessToken: () => accessToken.current,
        refreshAccessToken,
        onAuthLost: clear,
      }),
    [refreshAccessToken, clear],
  );

  // Restore a session on first load.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      const token = await refreshAccessToken();
      if (!token) return void (!cancelled && setState({ status: "anonymous" }));
      try {
        const user = await api.request<User>("/v1/auth/me");
        if (!cancelled) setState({ status: "authenticated", user });
      } catch {
        if (!cancelled) setState({ status: "anonymous" });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [api, refreshAccessToken]);

  const login = useCallback(
    async (email: string, password: string) => {
      const pair = await api.request<TokenPair>("/v1/auth/login", {
        method: "POST",
        json: { email, password },
        auth: false,
      });
      store(pair);
      const user = await api.request<User>("/v1/auth/me");
      setState({ status: "authenticated", user });
    },
    [api, store],
  );

  const register = useCallback(
    async (email: string, password: string) => {
      await api.request<User>("/v1/auth/register", { method: "POST", json: { email, password }, auth: false });
      await login(email, password);
    },
    [api, login],
  );

  const value = useMemo<AuthContextValue>(
    () => ({ state, api, login, register, logout: clear, getAccessToken: () => accessToken.current, refreshAccessToken }),
    [state, api, login, register, clear, refreshAccessToken],
  );
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const value = useContext(AuthContext);
  if (!value) throw new Error("useAuth must be used inside <AuthProvider>");
  return value;
}
