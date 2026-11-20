"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";
import {
  Badge,
  Button,
  EmptyState,
  ErrorNote,
  Field,
  Modal,
  PageHeader,
  Pagination,
  QueryBoundary,
  StatusBadge,
  Table,
  inputClass,
  td,
} from "@/components/ui";
import { parseJsonObject, shortId, timeAgo } from "@/lib/format";
import { PAGE_SIZE, useEvents, useSendEvent } from "@/lib/hooks";
import { useTick } from "@/lib/use-tick";

const SAMPLE_PAYLOAD = '{\n  "order_id": 1042,\n  "amount": 1999,\n  "currency": "usd"\n}';

function SendEventModal({ onClose }: { onClose: () => void }) {
  const router = useRouter();
  const send = useSendEvent();
  const [type, setType] = useState("order.created");
  const [payload, setPayload] = useState(SAMPLE_PAYLOAD);
  const [key, setKey] = useState("");
  const [localError, setLocalError] = useState<string | null>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    const parsed = parseJsonObject(payload);
    if (!parsed.ok) return setLocalError(parsed.error);
    setLocalError(null);
    const created = await send
      .mutateAsync({ type: type.trim(), payload: parsed.value, idempotencyKey: key.trim() || undefined })
      .catch(() => null);
    if (created) router.push(`/events/${created.id}`);
  }

  const error = localError ?? send.error?.message;
  return (
    <Modal title="Send a test event" onClose={onClose}>
      <form onSubmit={submit}>
        {error && (
          <div className="mb-4">
            <ErrorNote>{error}</ErrorNote>
          </div>
        )}
        <Field label="Event type" hint="Lowercase, dot separated. Endpoints subscribed to this type (or *) receive it.">
          {(id) => <input id={id} required value={type} onChange={(e) => setType(e.target.value)} className={`${inputClass} font-mono`} />}
        </Field>
        <Field label="Payload (JSON object)">
          {(id) => <textarea id={id} rows={6} value={payload} onChange={(e) => setPayload(e.target.value)} spellCheck={false} className={`${inputClass} font-mono`} />}
        </Field>
        <Field label="Idempotency key (optional)" hint="Sending the same key twice returns the first event instead of creating a duplicate.">
          {(id) => <input id={id} value={key} onChange={(e) => setKey(e.target.value)} className={`${inputClass} font-mono`} />}
        </Field>
        <div className="flex justify-end gap-2">
          <Button onClick={onClose}>Cancel</Button>
          <Button type="submit" variant="primary" loading={send.isPending}>
            Send event
          </Button>
        </div>
      </form>
    </Modal>
  );
}

export default function EventsPage() {
  const [page, setPage] = useState(0);
  const [draft, setDraft] = useState("");
  const [applied, setApplied] = useState("");
  const [sending, setSending] = useState(false);
  const events = useEvents(page, applied);
  useTick();

  return (
    <>
      <PageHeader
        title="Events"
        subtitle="Everything sent to Hookhaven, newest first."
        actions={
          <Button variant="primary" onClick={() => setSending(true)}>
            Send test event
          </Button>
        }
      />
      <form
        className="mb-4 flex gap-2"
        onSubmit={(event) => {
          event.preventDefault();
          setApplied(draft.trim());
          setPage(0);
        }}
      >
        <input aria-label="Filter by event type" placeholder="Filter by type, e.g. order.created" value={draft} onChange={(e) => setDraft(e.target.value)} className={`${inputClass} max-w-xs font-mono`} />
        <Button type="submit">Filter</Button>
        {applied && (
          <Button
            variant="ghost"
            onClick={() => {
              setDraft("");
              setApplied("");
              setPage(0);
            }}
          >
            Clear
          </Button>
        )}
      </form>

      <QueryBoundary query={events}>
        {(rows) =>
          rows.length === 0 ? (
            <EmptyState title={applied ? "No events of that type" : "No events yet"}>
              {applied ? "Try a different type." : "Send a test event, or call POST /v1/events with an API key."}
            </EmptyState>
          ) : (
            <>
              <Table head={["Type", "Event", "Deliveries", "Received"]}>
                {rows.slice(0, PAGE_SIZE).map((event) => {
                  const { pending, succeeded, dead } = event.delivery_counts;
                  return (
                    <tr key={event.id}>
                      <td className={`${td} font-mono`}>
                        <Link href={`/events/${event.id}`} className="text-accent hover:underline">
                          {event.type}
                        </Link>
                      </td>
                      <td className={`${td} font-mono text-xs text-muted`}>{shortId(event.id)}</td>
                      <td className={td}>
                        <span className="flex flex-wrap gap-1">
                          {succeeded > 0 && <span className="inline-flex items-center gap-1"><StatusBadge status="succeeded" />{succeeded > 1 && `×${succeeded}`}</span>}
                          {pending > 0 && <span className="inline-flex items-center gap-1"><StatusBadge status="pending" />{pending > 1 && `×${pending}`}</span>}
                          {dead > 0 && <span className="inline-flex items-center gap-1"><StatusBadge status="dead" />{dead > 1 && `×${dead}`}</span>}
                          {pending + succeeded + dead === 0 && <Badge tone="muted">No endpoint subscribed</Badge>}
                        </span>
                      </td>
                      <td className={`${td} text-muted`}>{timeAgo(event.created_at)}</td>
                    </tr>
                  );
                })}
              </Table>
              <Pagination page={page} hasNext={rows.length > PAGE_SIZE} onChange={setPage} />
            </>
          )
        }
      </QueryBoundary>
      {sending && <SendEventModal onClose={() => setSending(false)} />}
    </>
  );
}
