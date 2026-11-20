"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { Badge, Button, Card, ErrorNote, Notice, PageHeader, QueryBoundary, StatusBadge } from "@/components/ui";
import { formatDuration, shortId, timeAgo, timeUntil } from "@/lib/format";
import { useDelivery, useEndpoints, useReplayDelivery } from "@/lib/hooks";
import { useTick } from "@/lib/use-tick";

export default function DeliveryDetailPage() {
  const { id } = useParams<{ id: string }>();
  const delivery = useDelivery(id);
  const endpoints = useEndpoints();
  const replay = useReplayDelivery();
  const urlOf = new Map((endpoints.data ?? []).map((endpoint) => [endpoint.id, endpoint.url]));
  useTick();

  return (
    <QueryBoundary query={delivery}>
      {(d) => (
        <>
          <PageHeader
            title={`Delivery ${shortId(d.id)}`}
            subtitle={
              <>
                to <span className="break-all font-mono">{urlOf.get(d.endpoint_id) ?? shortId(d.endpoint_id)}</span> · event{" "}
                <Link href={`/events/${d.event_id}`} className="font-mono text-accent hover:underline">
                  {shortId(d.event_id)}
                </Link>
              </>
            }
            actions={
              d.status === "dead" && (
                <Button variant="primary" loading={replay.isPending} onClick={() => replay.mutate(d.id)}>
                  Replay delivery
                </Button>
              )
            }
          />
          {replay.error && <div className="mb-4"><ErrorNote>{replay.error.message}</ErrorNote></div>}
          {replay.isSuccess && <Notice tone="info">Queued for redelivery. This page updates as attempts happen.</Notice>}
          {d.status === "dead" && <Notice tone="bad">All retries were used up. Fix the receiver if needed, then replay.</Notice>}

          <Card className="mb-6">
            <dl className="grid grid-cols-2 gap-4 text-sm sm:grid-cols-4">
              <div>
                <dt className="text-muted">Status</dt>
                <dd className="mt-1"><StatusBadge status={d.status} /></dd>
              </div>
              <div>
                <dt className="text-muted">Attempts</dt>
                <dd className="mt-1">{d.attempt_count}</dd>
              </div>
              <div>
                <dt className="text-muted">{d.status === "succeeded" ? "Delivered" : "Next attempt"}</dt>
                <dd className="mt-1">
                  {d.status === "succeeded" && d.delivered_at
                    ? timeAgo(d.delivered_at)
                    : d.status === "pending" && d.next_attempt_at
                      ? timeUntil(d.next_attempt_at)
                      : "-"}
                </dd>
              </div>
              <div>
                <dt className="text-muted">Created</dt>
                <dd className="mt-1">{timeAgo(d.created_at)}</dd>
              </div>
            </dl>
          </Card>

          <h2 className="mb-3 font-medium">Attempts</h2>
          {d.attempts.length === 0 ? (
            <p className="text-sm text-muted">No attempt has been made yet. It is queued.</p>
          ) : (
            <ol className="space-y-3">
              {[...d.attempts].reverse().map((attempt) => (
                <li key={attempt.attempt_number}>
                  <Card>
                    <div className="flex flex-wrap items-center gap-2 text-sm">
                      <span className="font-medium">Attempt {attempt.attempt_number}</span>
                      <Badge tone={attempt.success ? "ok" : "bad"}>{attempt.success ? "Success" : "Failed"}</Badge>
                      {attempt.status_code && <Badge tone="muted">HTTP {attempt.status_code}</Badge>}
                      <span className="text-muted">
                        {formatDuration(attempt.duration_ms)} · {timeAgo(attempt.started_at)}
                      </span>
                    </div>
                    {attempt.error && <p className="mt-2 text-sm text-bad">{attempt.error}</p>}
                    {attempt.response_snippet && (
                      <pre className="mt-2 max-h-40 overflow-auto rounded-md border border-line bg-bg p-2 font-mono text-xs">{attempt.response_snippet}</pre>
                    )}
                  </Card>
                </li>
              ))}
            </ol>
          )}
        </>
      )}
    </QueryBoundary>
  );
}
