"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, type ReactNode } from "react";
import { Spinner } from "@/components/ui";
import { useAuth } from "@/lib/auth";
import { RealtimeProvider, useRealtime } from "@/lib/realtime";
import type { ConnectionStatus } from "@/lib/types";

const NAV = [
  { href: "/", label: "Overview" },
  { href: "/endpoints", label: "Endpoints" },
  { href: "/events", label: "Events" },
  { href: "/deliveries", label: "Deliveries" },
  { href: "/settings", label: "API keys" },
] as const;

const LIVE: Record<ConnectionStatus, { dot: string; label: string }> = {
  live: { dot: "bg-ok", label: "Live" },
  connecting: { dot: "bg-warn animate-pulse", label: "Connecting" },
  reconnecting: { dot: "bg-warn animate-pulse", label: "Reconnecting" },
  offline: { dot: "bg-muted", label: "Offline" },
};

export function LiveIndicator() {
  const { status } = useRealtime();
  const meta = LIVE[status];
  return (
    <span className="inline-flex items-center gap-2 text-xs text-muted" title="Live updates over Server-Sent Events">
      <span className={`h-2 w-2 rounded-full ${meta.dot}`} aria-hidden />
      {meta.label}
    </span>
  );
}

function isActive(pathname: string, href: string): boolean {
  return href === "/" ? pathname === "/" : pathname === href || pathname.startsWith(`${href}/`);
}

/** Auth guard + navigation for everything behind the login. */
export function AppShell({ children }: { children: ReactNode }) {
  const { state, logout } = useAuth();
  const router = useRouter();
  const pathname = usePathname();

  useEffect(() => {
    if (state.status === "anonymous") router.replace("/login");
  }, [state.status, router]);

  if (state.status !== "authenticated") {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <Spinner />
      </div>
    );
  }

  return (
    <RealtimeProvider>
      <div className="min-h-screen md:flex">
        <aside className="border-b border-line bg-surface md:min-h-screen md:w-56 md:border-b-0 md:border-r">
          <div className="flex items-center justify-between px-4 py-3 md:block">
            <Link href="/" className="text-lg font-semibold tracking-tight">
              Hookhaven
            </Link>
            <div className="md:mt-1">
              <LiveIndicator />
            </div>
          </div>
          <nav aria-label="Main" className="flex gap-1 overflow-x-auto px-2 pb-2 md:flex-col md:pb-0">
            {NAV.map(({ href, label }) => (
              <Link
                key={href}
                href={href}
                aria-current={isActive(pathname, href) ? "page" : undefined}
                className={`whitespace-nowrap rounded-md px-3 py-2 text-sm ${
                  isActive(pathname, href) ? "bg-accent/10 font-medium text-accent" : "text-muted hover:bg-line/50"
                }`}
              >
                {label}
              </Link>
            ))}
          </nav>
          <div className="hidden px-4 py-4 text-xs text-muted md:block">
            <p className="truncate" title={state.user.email}>
              {state.user.email}
            </p>
            <button type="button" onClick={logout} className="mt-1 underline hover:text-fg">
              Sign out
            </button>
          </div>
        </aside>
        <main className="mx-auto w-full max-w-5xl flex-1 p-4 md:p-8">{children}</main>
      </div>
    </RealtimeProvider>
  );
}
