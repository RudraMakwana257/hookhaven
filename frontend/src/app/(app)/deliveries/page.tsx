"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { Button, EmptyState, ErrorNote, PageHeader, Pagination, QueryBoundary, Spinner, StatusBadge, Table, inputClass, td } from "@/components/ui";
import { shortId, timeAgo, timeUntil } from "@/lib/format";
import { PAGE_SIZE, useDeliveries, useEndpoints, useReplayDelivery } from "@/lib/hooks";
import type { DeliveryStatus } from "@/lib/types";
import { useTick } from "@/lib/use-tick";

const TABS: { value: DeliveryStatus | undefined; label: string }[] = [
  { value: undefined, label: "All" },
  { value: "pending", label: "Pending" },
  { value: "succeeded", label: "Delivered" },
  { value: "dead", label: "Dead-letter queue" },
];

function parseStatus(value: string | null): DeliveryStatus | undefined {
  return value === "pending" || value === "succeeded" || value === "dead" ? value : undefined;
}

function DeliveriesView() {
  const router = useRouter();
  const params = useSearchParams();
  const status = parseStatus(params.get("status"));
  const endpointId = params.get("endpoint_id") ?? undefined;
  const [page, setPage] = useState(0);
  const deliveries = useDeliveries({ status, endpointId, page });
  const endpoints = useEndpoints();
  const replay = useReplayDelivery();
  const urlOf = new Map((endpoints.data ?? []).map((endpoint) => [endpoint.id, endpoint.url]));
  useTick();

  function setFilter(next: { status?: DeliveryStatus; endpointId?: string }) {
    const search = new URLSearchParams();
    if (next.status) search.set("status", next.status);
    if (next.endpointId) search.set("endpoint_id", next.endpointId);
    setPage(0);
    router.replace(search.size ? `/deliveries?${search}` : "/deliveries");
  }

  return (
    <>
      <PageHeader title="Deliveries" subtitle="One row per event and endpoint. Dead deliveries ran out of retries and can be replayed." />

      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div role="tablist" aria-label="Filter by status" className="flex flex-wrap gap-1">
          {TABS.map((tab) => (
            <button
              key={tab.label}
              role="tab"
              type="button"
              aria-selected={status === tab.value}
              onClick={() => setFilter({ status: tab.value, endpointId })}
              className={`rounded-md px-3 py-1.5 text-sm ${status === tab.value ? "bg-accent/10 font-medium text-accent" : "text-muted hover:bg-line/50"}`}
            >
              {tab.label}
            </button>
          ))}
        </div>
        <select
          aria-label="Filter by endpoint"
          value={endpointId ?? ""}
          onChange={(e) => setFilter({ status, endpointId: e.target.value || undefined })}
          className={`${inputClass} max-w-xs`}
        >
          <option value="">All endpoints</option>
          {(endpoints.data ?? []).map((endpoint) => (
            <option key={endpoint.id} value={endpoint.id}>
              {endpoint.url}
            </option>
          ))}
        </select>
      </div>

      {replay.error && <div className="mb-4"><ErrorNote>{replay.error.message}</ErrorNote></div>}

      <QueryBoundary query={deliveries}>
        {(rows) =>
          rows.length === 0 ? (
            <EmptyState title={status === "dead" ? "The dead-letter queue is empty" : "No deliveries match"}>
              {status === "dead" ? "Everything was delivered or is still being retried. 🎉" : "Try another filter."}
            </EmptyState>
          ) : (
            <>
              <Table head={["Delivery", "Endpoint", "Status", "Tries", "Last result", "When", ""]}>
                {rows.slice(0, PAGE_SIZE).map((d) => (
                  <tr key={d.id}>
                    <td className={td}>
                      <Link href={`/deliveries/${d.id}`} className="font-mono text-accent hover:underline">
                        {shortId(d.id)}
                      </Link>
                    </td>
                    <td className={`${td} max-w-[14rem] truncate`} title={urlOf.get(d.endpoint_id)}>
                      {urlOf.get(d.endpoint_id) ?? shortId(d.endpoint_id)}
                    </td>
                    <td className={td}>
                      <StatusBadge status={d.status} />
                      {d.status === "pending" && d.next_attempt_at && d.attempt_count > 0 && (
                        <p className="mt-1 text-xs text-muted">retry {timeUntil(d.next_attempt_at)}</p>
                      )}
                    </td>
                    <td className={td}>{d.attempt_count}</td>
                    <td className={`${td} text-muted`}>{d.last_status_code ? `HTTP ${d.last_status_code}` : (d.last_error ?? "-")}</td>
                    <td className={`${td} text-muted`}>{timeAgo(d.created_at)}</td>
                    <td className={td}>
                      {d.status === "dead" && (
                        <Button loading={replay.isPending && replay.variables === d.id} onClick={() => replay.mutate(d.id)}>
                          Replay
                        </Button>
                      )}
                    </td>
                  </tr>
                ))}
              </Table>
              <Pagination page={page} hasNext={rows.length > PAGE_SIZE} onChange={setPage} />
            </>
          )
        }
      </QueryBoundary>
    </>
  );
}

export default function DeliveriesPage() {
  // useSearchParams() needs a Suspense boundary for static rendering.
  return (
    <Suspense fallback={<div className="flex justify-center p-10"><Spinner /></div>}>
      <DeliveriesView />
    </Suspense>
  );
}
