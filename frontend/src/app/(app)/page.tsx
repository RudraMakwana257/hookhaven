"use client";

import Link from "next/link";
import { Badge, Card, EmptyState, LinkButton, Notice, PageHeader, QueryBoundary, StatusBadge } from "@/components/ui";
import { shortId, timeAgo } from "@/lib/format";
import { useEvents, useStats } from "@/lib/hooks";
import { useRealtime, type ActivityItem } from "@/lib/realtime";
import { useTick } from "@/lib/use-tick";
import type { Stats } from "@/lib/types";

function StatCard({ label, value, hint, href, tone }: { label: string; value: number | string; hint?: string; href?: string; tone?: "bad" }) {
  const body = (
    <Card className={href ? "transition hover:border-accent" : ""}>
      <p className="text-xs uppercase tracking-wide text-muted">{label}</p>
      <p className={`mt-1 text-2xl font-semibold ${tone === "bad" ? "text-bad" : ""}`}>{value}</p>
      {hint && <p className="mt-1 text-xs text-muted">{hint}</p>}
    </Card>
  );
  return href ? <Link href={href}>{body}</Link> : body;
}

function Overview({ stats }: { stats: Stats }) {
  const { deliveries, endpoints } = stats;
  const total = deliveries.pending + deliveries.succeeded + deliveries.dead;
  const rate = total ? `${Math.round((deliveries.succeeded / total) * 100)}% delivered` : "No deliveries yet";
  return (
    <>
      {stats.dead_total > 0 && (
        <Notice tone="bad">
          {stats.dead_total} {stats.dead_total === 1 ? "delivery is" : "deliveries are"} dead-lettered.{" "}
          <Link href="/deliveries?status=dead" className="font-medium underline">
            Review and replay
          </Link>
        </Notice>
      )}
      {endpoints.disabled > 0 && (
        <Notice tone="warn">
          {endpoints.disabled} {endpoints.disabled === 1 ? "endpoint is" : "endpoints are"} disabled.{" "}
          <Link href="/endpoints" className="font-medium underline">
            Check endpoints
          </Link>
        </Notice>
      )}
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
        <StatCard label={`Events (${stats.window_hours}h)`} value={stats.events} href="/events" />
        <StatCard label="Delivered" value={deliveries.succeeded} hint={rate} />
        <StatCard label="Pending" value={deliveries.pending} href="/deliveries?status=pending" />
        <StatCard label="Dead-lettered" value={stats.dead_total} hint="all time" href="/deliveries?status=dead" tone={stats.dead_total > 0 ? "bad" : undefined} />
        <StatCard label="Endpoints" value={endpoints.active} hint={endpoints.disabled ? `${endpoints.disabled} disabled` : "all active"} href="/endpoints" />
      </div>
    </>
  );
}

function ActivityFeed({ items }: { items: ActivityItem[] }) {
  useTick();
  const dot = { ok: "bg-ok", warn: "bg-warn", bad: "bg-bad", info: "bg-accent" } as const;
  if (items.length === 0) return <p className="text-sm text-muted">Waiting for activity. Send a test event to see it appear here instantly.</p>;
  return (
    <ul className="space-y-2">
      {items.map((item) => (
        <li key={item.id} className="flex items-start gap-2 text-sm">
          <span className={`mt-1.5 h-2 w-2 shrink-0 rounded-full ${dot[item.tone]}`} aria-hidden />
          <span className="min-w-0 flex-1">
            {item.href ? (
              <Link href={item.href} className="hover:underline">
                {item.text}
              </Link>
            ) : (
              item.text
            )}
          </span>
          <span className="shrink-0 text-xs text-muted">{timeAgo(item.at.toISOString())}</span>
        </li>
      ))}
    </ul>
  );
}

export default function OverviewPage() {
  const stats = useStats();
  const recent = useEvents(0, "");
  const { activity } = useRealtime();
  useTick();

  return (
    <>
      <PageHeader title="Overview" subtitle="Delivery health at a glance. Updates live." actions={<LinkButton href="/events">Send a test event</LinkButton>} />
      <QueryBoundary query={stats}>
        {(data) =>
          data.endpoints.total === 0 ? (
            <EmptyState title="Welcome to Hookhaven">
              <ol className="mx-auto mt-2 max-w-sm list-decimal space-y-1 pl-5 text-left">
                <li>
                  <Link href="/endpoints" className="text-accent underline">
                    Register an endpoint
                  </Link>{" "}
                  (where your webhooks should go)
                </li>
                <li>
                  <Link href="/events" className="text-accent underline">
                    Send a test event
                  </Link>{" "}
                  or call the API with a key from{" "}
                  <Link href="/settings" className="text-accent underline">
                    API keys
                  </Link>
                </li>
                <li>Watch it get delivered, retried and replayed here</li>
              </ol>
            </EmptyState>
          ) : (
            <Overview stats={data} />
          )
        }
      </QueryBoundary>

      <div className="mt-6 grid gap-4 lg:grid-cols-2">
        <Card>
          <h2 className="mb-3 font-medium">Live activity</h2>
          <ActivityFeed items={activity} />
        </Card>
        <Card>
          <div className="mb-3 flex items-center justify-between">
            <h2 className="font-medium">Recent events</h2>
            <Link href="/events" className="text-sm text-accent hover:underline">
              View all
            </Link>
          </div>
          <QueryBoundary query={recent}>
            {(events) =>
              events.length === 0 ? (
                <p className="text-sm text-muted">No events yet.</p>
              ) : (
                <ul className="divide-y divide-line">
                  {events.slice(0, 6).map((event) => (
                    <li key={event.id} className="flex items-center justify-between gap-2 py-2 text-sm">
                      <Link href={`/events/${event.id}`} className="min-w-0 truncate hover:underline">
                        <span className="font-mono">{event.type}</span>{" "}
                        <span className="text-muted">{shortId(event.id)}</span>
                      </Link>
                      <span className="flex shrink-0 items-center gap-2">
                        {event.delivery_counts.dead > 0 && <StatusBadge status="dead" />}
                        {event.delivery_counts.pending > 0 && <StatusBadge status="pending" />}
                        {event.delivery_counts.succeeded > 0 && event.delivery_counts.pending + event.delivery_counts.dead === 0 && <StatusBadge status="succeeded" />}
                        {Object.values(event.delivery_counts).every((n) => n === 0) && <Badge tone="muted">No endpoint</Badge>}
                        <span className="text-xs text-muted">{timeAgo(event.created_at)}</span>
                      </span>
                    </li>
                  ))}
                </ul>
              )
            }
          </QueryBoundary>
        </Card>
      </div>
    </>
  );
}
