"use client";

import { useState, type FormEvent } from "react";
import {
  Badge,
  Button,
  Card,
  ConfirmModal,
  CopyButton,
  EmptyState,
  ErrorNote,
  Field,
  Modal,
  PageHeader,
  QueryBoundary,
  SecretReveal,
  Table,
  inputClass,
  td,
} from "@/components/ui";
import { API_URL } from "@/lib/auth";
import { timeAgo } from "@/lib/format";
import { useApiKeys, useCreateApiKey, useRevokeApiKey } from "@/lib/hooks";
import type { ApiKey } from "@/lib/types";
import { useTick } from "@/lib/use-tick";

function CreateKeyModal({ onClose, onCreated }: { onClose: () => void; onCreated: (key: string) => void }) {
  const create = useCreateApiKey();
  const [name, setName] = useState("");

  async function submit(event: FormEvent) {
    event.preventDefault();
    const created = await create.mutateAsync(name.trim()).catch(() => null);
    if (created) onCreated(created.key);
  }

  return (
    <Modal title="New API key" onClose={onClose}>
      <form onSubmit={submit}>
        {create.error && (
          <div className="mb-4">
            <ErrorNote>{create.error.message}</ErrorNote>
          </div>
        )}
        <Field label="Name" hint="Something that tells you where it is used, e.g. production-backend.">
          {(id) => <input id={id} required maxLength={100} value={name} onChange={(e) => setName(e.target.value)} className={inputClass} />}
        </Field>
        <div className="flex justify-end gap-2">
          <Button onClick={onClose}>Cancel</Button>
          <Button type="submit" variant="primary" loading={create.isPending}>
            Create key
          </Button>
        </div>
      </form>
    </Modal>
  );
}

const CURL = `curl -X POST ${API_URL}/v1/events \\
  -H "X-API-Key: $HOOKHAVEN_API_KEY" \\
  -H "Idempotency-Key: order-1042-created" \\
  -H "Content-Type: application/json" \\
  -d '{"type": "order.created", "payload": {"order_id": 1042}}'`;

export default function SettingsPage() {
  const keys = useApiKeys();
  const revoke = useRevokeApiKey();
  const [creating, setCreating] = useState(false);
  const [newKey, setNewKey] = useState<string | null>(null);
  const [revoking, setRevoking] = useState<ApiKey | null>(null);
  useTick();

  return (
    <>
      <PageHeader
        title="API keys"
        subtitle="Your servers use these to send events."
        actions={
          <Button variant="primary" onClick={() => setCreating(true)}>
            New API key
          </Button>
        }
      />
      <QueryBoundary query={keys}>
        {(rows) =>
          rows.length === 0 ? (
            <EmptyState title="No API keys yet">Create one to send events from your own backend.</EmptyState>
          ) : (
            <Table head={["Name", "Key", "Created", "Last used", ""]}>
              {rows.map((key) => (
                <tr key={key.id}>
                  <td className={td}>{key.name}</td>
                  <td className={`${td} font-mono text-xs`}>hh_{key.prefix}_…</td>
                  <td className={`${td} text-muted`}>{timeAgo(key.created_at)}</td>
                  <td className={`${td} text-muted`}>{timeAgo(key.last_used_at)}</td>
                  <td className={td}>
                    {key.revoked_at ? <Badge tone="bad">Revoked</Badge> : <Button onClick={() => setRevoking(key)}>Revoke</Button>}
                  </td>
                </tr>
              ))}
            </Table>
          )
        }
      </QueryBoundary>

      <Card className="mt-8">
        <div className="mb-2 flex items-center justify-between">
          <h2 className="font-medium">Send an event from your backend</h2>
          <CopyButton text={CURL} />
        </div>
        <pre className="overflow-x-auto rounded-md border border-line bg-bg p-3 font-mono text-xs">{CURL}</pre>
        <p className="mt-2 text-xs text-muted">
          Retrying with the same <span className="font-mono">Idempotency-Key</span> is safe: you get the original event back, never a duplicate.
        </p>
      </Card>

      {creating && (
        <CreateKeyModal
          onClose={() => setCreating(false)}
          onCreated={(key) => {
            setCreating(false);
            setNewKey(key);
          }}
        />
      )}
      {newKey && <SecretReveal title="Your new API key" value={newKey} onClose={() => setNewKey(null)} />}
      {revoking && (
        <ConfirmModal
          title={`Revoke “${revoking.name}”?`}
          message="Anything still using this key will start receiving 401 errors. This cannot be undone."
          confirmLabel="Revoke key"
          loading={revoke.isPending}
          error={revoke.error?.message}
          onClose={() => setRevoking(null)}
          onConfirm={async () => {
            const done = await revoke.mutateAsync(revoking.id).then(() => true, () => false);
            if (done) setRevoking(null);
          }}
        />
      )}
    </>
  );
}
