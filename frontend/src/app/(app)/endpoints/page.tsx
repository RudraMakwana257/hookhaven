"use client";

import Link from "next/link";
import { useState, type FormEvent } from "react";
import { ActiveBadge, Button, EmptyState, ErrorNote, Field, Modal, PageHeader, QueryBoundary, SecretReveal, Table, inputClass, td } from "@/components/ui";
import { parseEventTypes, timeAgo } from "@/lib/format";
import { useCreateEndpoint, useEndpoints } from "@/lib/hooks";
import { useTick } from "@/lib/use-tick";

function CreateEndpointModal({ onClose, onCreated }: { onClose: () => void; onCreated: (secret: string) => void }) {
  const create = useCreateEndpoint();
  const [url, setUrl] = useState("");
  const [description, setDescription] = useState("");
  const [types, setTypes] = useState("");

  async function submit(event: FormEvent) {
    event.preventDefault();
    const created = await create.mutateAsync({ url: url.trim(), description: description.trim() || null, event_types: parseEventTypes(types) }).catch(() => null);
    if (created) onCreated(created.secret);
  }

  return (
    <Modal title="New endpoint" onClose={onClose}>
      <form onSubmit={submit}>
        {create.error && (
          <div className="mb-4">
            <ErrorNote>{create.error.message}</ErrorNote>
          </div>
        )}
        <Field label="Destination URL" hint="Must be a public HTTPS URL. Private and internal addresses are refused.">
          {(id) => <input id={id} required type="url" placeholder="https://example.com/webhooks" value={url} onChange={(e) => setUrl(e.target.value)} className={inputClass} />}
        </Field>
        <Field label="Description (optional)">
          {(id) => <input id={id} maxLength={200} value={description} onChange={(e) => setDescription(e.target.value)} className={inputClass} />}
        </Field>
        <Field label="Event types" hint="Comma separated, e.g. order.created, invoice.paid. Leave blank to receive everything (*).">
          {(id) => <input id={id} placeholder="*" value={types} onChange={(e) => setTypes(e.target.value)} className={inputClass} />}
        </Field>
        <div className="flex justify-end gap-2">
          <Button onClick={onClose}>Cancel</Button>
          <Button type="submit" variant="primary" loading={create.isPending}>
            Create endpoint
          </Button>
        </div>
      </form>
    </Modal>
  );
}

export default function EndpointsPage() {
  const endpoints = useEndpoints();
  const [creating, setCreating] = useState(false);
  const [secret, setSecret] = useState<string | null>(null);
  useTick();

  return (
    <>
      <PageHeader
        title="Endpoints"
        subtitle="Where Hookhaven delivers your events."
        actions={
          <Button variant="primary" onClick={() => setCreating(true)}>
            New endpoint
          </Button>
        }
      />
      <QueryBoundary query={endpoints}>
        {(rows) =>
          rows.length === 0 ? (
            <EmptyState title="No endpoints yet">Register the URL that should receive your webhooks.</EmptyState>
          ) : (
            <Table head={["URL", "Events", "Status", "Created"]}>
              {rows.map((endpoint) => (
                <tr key={endpoint.id}>
                  <td className={td}>
                    <Link href={`/endpoints/${endpoint.id}`} className="font-medium text-accent hover:underline">
                      {endpoint.url}
                    </Link>
                    {endpoint.description && <p className="text-xs text-muted">{endpoint.description}</p>}
                  </td>
                  <td className={`${td} font-mono text-xs`}>{endpoint.event_types.join(", ")}</td>
                  <td className={td}>
                    <ActiveBadge active={endpoint.is_active} />
                    {!endpoint.is_active && endpoint.disabled_reason && <p className="mt-1 text-xs text-muted">{endpoint.disabled_reason}</p>}
                  </td>
                  <td className={`${td} text-muted`}>{timeAgo(endpoint.created_at)}</td>
                </tr>
              ))}
            </Table>
          )
        }
      </QueryBoundary>

      {creating && (
        <CreateEndpointModal
          onClose={() => setCreating(false)}
          onCreated={(value) => {
            setCreating(false);
            setSecret(value);
          }}
        />
      )}
      {secret && <SecretReveal title="Signing secret" value={secret} onClose={() => setSecret(null)} />}
    </>
  );
}
