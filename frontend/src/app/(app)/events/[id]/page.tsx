"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { Badge, Card, Json, Notice, PageHeader, QueryBoundary, StatusBadge, Table, td } from "@/components/ui";
import { shortId, timeAgo } from "@/lib/format";
import { useEndpoints, useEvent } from "@/lib/hooks";
import { useTick } from "@/lib/use-tick";

export default function EventDetailPage() {
  const { id } = useParams<{ id: string }>();
  const event = useEvent(id);
  const endpoints = useEndpoints();
  const urlOf = new Map((endpoints.data ?? []).map((endpoint) => [endpoint.id, endpoint.url]));
  useTick();

  return (
    <QueryBoundary query={event}>
      {(data) => (
        <>
          <PageHeader
            title={data.type}
            subtitle={
              <>
                <span className="font-mono">{shortId(data.id)}</span> · received {timeAgo(data.created_at)}
                {data.idempotency_key && (
                  <>
                    {" "}
                    · idempotency key <span className="font-mono">{data.idempotency_key}</span>
                  </>
                )}
              </>
            }
          />
          <div className="grid gap-4 lg:grid-cols-2">
            <Card>
              <h2 className="mb-3 font-medium">Payload</h2>
              <Json value={data.payload} />
            </Card>
            <Card>
              <h2 className="mb-3 font-medium">Deliveries</h2>
              {data.deliveries.length === 0 ? (
                <Notice tone="info">
                  No active endpoint is subscribed to <span className="font-mono">{data.type}</span>, so nothing was sent. The event itself is stored.
                </Notice>
              ) : (
                <Table head={["Endpoint", "Status", "Tries"]}>
                  {data.deliveries.map((delivery) => (
                    <tr key={delivery.id}>
                      <td className={`${td} break-all`}>
                        <Link href={`/deliveries/${delivery.id}`} className="text-accent hover:underline">
                          {urlOf.get(delivery.endpoint_id) ?? shortId(delivery.endpoint_id)}
                        </Link>
                        {delivery.last_error && <p className="text-xs text-muted">{delivery.last_error}</p>}
                      </td>
                      <td className={td}>
                        <StatusBadge status={delivery.status} />
                      </td>
                      <td className={td}>{delivery.attempt_count || <Badge tone="muted">queued</Badge>}</td>
                    </tr>
                  ))}
                </Table>
              )}
            </Card>
          </div>
        </>
      )}
    </QueryBoundary>
  );
}
