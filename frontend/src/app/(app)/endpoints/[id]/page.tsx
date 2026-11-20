"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";
import {
  ActiveBadge,
  Button,
  Card,
  ConfirmModal,
  ErrorNote,
  Field,
  Modal,
  Notice,
  PageHeader,
  QueryBoundary,
  SecretReveal,
  StatusBadge,
  Table,
  inputClass,
  td,
} from "@/components/ui";
import { parseEventTypes, shortId, timeAgo } from "@/lib/format";
import { useDeleteEndpoint, useDeliveries, useEndpoint, useReplayDead, useRotateSecret, useUpdateEndpoint } from "@/lib/hooks";
import type { Endpoint } from "@/lib/types";
import { useTick } from "@/lib/use-tick";

function EditModal({ endpoint, onClose }: { endpoint: Endpoint; onClose: () => void }) {
  const update = useUpdateEndpoint(endpoint.id);
  const [url, setUrl] = useState(endpoint.url);
  const [description, setDescription] = useState(endpoint.description ?? "");
  const [types, setTypes] = useState(endpoint.event_types.join(", "));

  async function submit(event: FormEvent) {
    event.preventDefault();
    const saved = await update
      .mutateAsync({ url: url.trim(), description: description.trim() || null, event_types: parseEventTypes(types) })
      .catch(() => null);
    if (saved) onClose();
  }

  return (
    <Modal title="Edit endpoint" onClose={onClose}>
      <form onSubmit={submit}>
        {update.error && (
          <div className="mb-4">
            <ErrorNote>{update.error.message}</ErrorNote>
          </div>
        )}
        <Field label="Destination URL" hint="A changed URL is checked again against the same safety rules.">
          {(id) => <input id={id} required type="url" value={url} onChange={(e) => setUrl(e.target.value)} className={inputClass} />}
        </Field>
        <Field label="Description">
          {(id) => <input id={id} maxLength={200} value={description} onChange={(e) => setDescription(e.target.value)} className={inputClass} />}
        </Field>
        <Field label="Event types" hint="Comma separated; blank means everything (*).">
          {(id) => <input id={id} value={types} onChange={(e) => setTypes(e.target.value)} className={inputClass} />}
        </Field>
        <div className="flex justify-end gap-2">
          <Button onClick={onClose}>Cancel</Button>
          <Button type="submit" variant="primary" loading={update.isPending}>
            Save
          </Button>
        </div>
      </form>
    </Modal>
  );
}

function Detail({ endpoint }: { endpoint: Endpoint }) {
  const router = useRouter();
  const update = useUpdateEndpoint(endpoint.id);
  const rotate = useRotateSecret(endpoint.id);
  const replayDead = useReplayDead(endpoint.id);
  const remove = useDeleteEndpoint(endpoint.id);
  const dead = useDeliveries({ status: "dead", endpointId: endpoint.id, page: 0 }, 100);
  const recent = useDeliveries({ endpointId: endpoint.id, page: 0 }, 10);
  const [editing, setEditing] = useState(false);
  const [confirm, setConfirm] = useState<"rotate" | "delete" | null>(null);
  const [secret, setSecret] = useState<string | null>(null);
  const [replayed, setReplayed] = useState<number | null>(null);
  useTick();

  const deadCount = dead.data ? Math.min(dead.data.length, 100) : 0;
  const deadLabel = dead.data && dead.data.length > 100 ? "100+" : String(deadCount);

  return (
    <>
      <PageHeader
        title="Endpoint"
        subtitle={<span className="break-all font-mono">{endpoint.url}</span>}
        actions={
          <>
            <Button onClick={() => setEditing(true)}>Edit</Button>
            <Button loading={update.isPending} onClick={() => update.mutate({ is_active: !endpoint.is_active })}>
              {endpoint.is_active ? "Disable" : "Enable"}
            </Button>
          </>
        }
      />

      {!endpoint.is_active && (
        <Notice tone="bad">
          <p className="font-medium">This endpoint is disabled{endpoint.disabled_reason ? `: ${endpoint.disabled_reason}` : ""}.</p>
          <p className="mt-1">
            Fix the receiver, then enable the endpoint and replay the dead deliveries. New events are not sent to it meanwhile.
          </p>
        </Notice>
      )}
      {update.error && <div className="mb-4"><ErrorNote>{update.error.message}</ErrorNote></div>}

      <div className="grid gap-4 md:grid-cols-2">
        <Card>
          <h2 className="mb-3 font-medium">Details</h2>
          <dl className="grid grid-cols-[8rem_1fr] gap-y-2 text-sm">
            <dt className="text-muted">Status</dt>
            <dd><ActiveBadge active={endpoint.is_active} /></dd>
            <dt className="text-muted">Subscribed to</dt>
            <dd className="font-mono text-xs">{endpoint.event_types.join(", ")}</dd>
            <dt className="text-muted">Description</dt>
            <dd>{endpoint.description ?? "None"}</dd>
            <dt className="text-muted">Failure streak</dt>
            <dd>{endpoint.consecutive_failures} dead deliveries in a row</dd>
            <dt className="text-muted">Created</dt>
            <dd>{timeAgo(endpoint.created_at)}</dd>
          </dl>
        </Card>

        <Card>
          <h2 className="mb-3 font-medium">Actions</h2>
          <div className="space-y-3 text-sm">
            <div>
              <Button
                loading={replayDead.isPending}
                disabled={!endpoint.is_active || deadCount === 0}
                onClick={async () => {
                  const result = await replayDead.mutateAsync().catch(() => null);
                  if (result) setReplayed(result.replayed);
                }}
              >
                Replay {deadLabel} dead {deadCount === 1 ? "delivery" : "deliveries"}
              </Button>
              {replayed !== null && <p className="mt-1 text-ok">Queued {replayed} for redelivery.</p>}
              {replayDead.error && <p className="mt-1 text-bad">{replayDead.error.message}</p>}
              {!endpoint.is_active && deadCount > 0 && <p className="mt-1 text-xs text-muted">Enable the endpoint first.</p>}
            </div>
            <div>
              <Button onClick={() => setConfirm("rotate")}>Rotate signing secret</Button>
              <p className="mt-1 text-xs text-muted">The old secret stops working immediately.</p>
            </div>
            <div>
              <Button variant="danger" onClick={() => setConfirm("delete")}>
                Delete endpoint
              </Button>
            </div>
          </div>
        </Card>
      </div>

      <h2 className="mb-3 mt-8 font-medium">Recent deliveries</h2>
      <QueryBoundary query={recent}>
        {(rows) =>
          rows.length === 0 ? (
            <p className="text-sm text-muted">No deliveries to this endpoint yet.</p>
          ) : (
            <Table head={["Delivery", "Status", "Attempts", "Last result", "When"]}>
              {rows.slice(0, 10).map((d) => (
                <tr key={d.id}>
                  <td className={td}>
                    <Link href={`/deliveries/${d.id}`} className="font-mono text-accent hover:underline">
                      {shortId(d.id)}
                    </Link>
                  </td>
                  <td className={td}><StatusBadge status={d.status} /></td>
                  <td className={td}>{d.attempt_count}</td>
                  <td className={`${td} text-muted`}>{d.last_status_code ? `HTTP ${d.last_status_code}` : (d.last_error ?? "-")}</td>
                  <td className={`${td} text-muted`}>{timeAgo(d.created_at)}</td>
                </tr>
              ))}
            </Table>
          )
        }
      </QueryBoundary>

      {editing && <EditModal endpoint={endpoint} onClose={() => setEditing(false)} />}
      {confirm === "rotate" && (
        <ConfirmModal
          title="Rotate signing secret?"
          message="Your receiver will reject deliveries signed with the new secret until you update it there. The old secret stops working immediately."
          confirmLabel="Rotate secret"
          loading={rotate.isPending}
          error={rotate.error?.message}
          onClose={() => setConfirm(null)}
          onConfirm={async () => {
            const result = await rotate.mutateAsync().catch(() => null);
            if (result) {
              setConfirm(null);
              setSecret(result.secret);
            }
          }}
        />
      )}
      {confirm === "delete" && (
        <ConfirmModal
          title="Delete this endpoint?"
          message="Its delivery history is deleted with it. Events already accepted for this endpoint will not be delivered."
          confirmLabel="Delete endpoint"
          loading={remove.isPending}
          error={remove.error?.message}
          onClose={() => setConfirm(null)}
          onConfirm={async () => {
            const done = await remove.mutateAsync().then(() => true, () => false);
            if (done) router.replace("/endpoints");
          }}
        />
      )}
      {secret && <SecretReveal title="New signing secret" value={secret} onClose={() => setSecret(null)} />}
    </>
  );
}

export default function EndpointDetailPage() {
  const { id } = useParams<{ id: string }>();
  const endpoint = useEndpoint(id);
  return <QueryBoundary query={endpoint}>{(data) => <Detail endpoint={data} />}</QueryBoundary>;
}
