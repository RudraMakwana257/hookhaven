"use client";

import Link from "next/link";
import { useEffect, useId, useRef, useState, type ButtonHTMLAttributes, type ReactNode } from "react";
import type { UseQueryResult } from "@tanstack/react-query";
import type { DeliveryStatus } from "@/lib/types";

export const inputClass =
  "w-full rounded-md border border-line bg-surface px-3 py-2 text-sm outline-none placeholder:text-muted/70 focus:border-accent focus:ring-1 focus:ring-accent";

// ------------------------------------------------------------------ buttons

const buttonStyles = {
  primary: "bg-accent text-white hover:opacity-90",
  secondary: "border border-line bg-surface hover:bg-line/50",
  danger: "bg-bad text-white hover:opacity-90",
  ghost: "hover:bg-line/50",
} as const;

export function Button({
  variant = "secondary",
  loading = false,
  disabled,
  className = "",
  children,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: keyof typeof buttonStyles; loading?: boolean }) {
  return (
    <button
      type="button"
      disabled={disabled || loading}
      className={`inline-flex items-center justify-center gap-2 rounded-md px-3 py-2 text-sm font-medium transition disabled:cursor-not-allowed disabled:opacity-50 ${buttonStyles[variant]} ${className}`}
      {...props}
    >
      {loading && <Spinner small />}
      {children}
    </button>
  );
}

export function LinkButton({ href, children }: { href: string; children: ReactNode }) {
  return (
    <Link href={href} className={`inline-flex items-center rounded-md px-3 py-2 text-sm font-medium ${buttonStyles.secondary}`}>
      {children}
    </Link>
  );
}

// ------------------------------------------------------------------ layout bits

export function Card({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <section className={`rounded-lg border border-line bg-surface p-4 ${className}`}>{children}</section>;
}

export function PageHeader({ title, subtitle, actions }: { title: string; subtitle?: ReactNode; actions?: ReactNode }) {
  return (
    <header className="mb-6 flex flex-wrap items-start justify-between gap-3">
      <div>
        <h1 className="text-xl font-semibold">{title}</h1>
        {subtitle && <p className="mt-1 text-sm text-muted">{subtitle}</p>}
      </div>
      {actions && <div className="flex flex-wrap gap-2">{actions}</div>}
    </header>
  );
}

export function Spinner({ small = false }: { small?: boolean }) {
  const size = small ? "h-4 w-4" : "h-6 w-6";
  return (
    <span
      role="status"
      aria-label="Loading"
      className={`inline-block ${size} animate-spin rounded-full border-2 border-current border-t-transparent opacity-60`}
    />
  );
}

export function EmptyState({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="rounded-lg border border-dashed border-line p-8 text-center">
      <p className="font-medium">{title}</p>
      {children && <div className="mt-2 text-sm text-muted">{children}</div>}
    </div>
  );
}

export function ErrorNote({ children }: { children: ReactNode }) {
  return (
    <p role="alert" className="rounded-md border border-bad/40 bg-bad/10 px-3 py-2 text-sm text-bad">
      {children}
    </p>
  );
}

export function Notice({ tone, children }: { tone: "warn" | "bad" | "info"; children: ReactNode }) {
  const styles = { warn: "border-warn/40 bg-warn/10 text-warn", bad: "border-bad/40 bg-bad/10 text-bad", info: "border-line bg-bg text-fg" };
  return <div className={`mb-4 rounded-md border px-3 py-2 text-sm ${styles[tone]}`}>{children}</div>;
}

/** Renders loading / error / data for a query, so pages only describe the happy path. */
export function QueryBoundary<T>({ query, children }: { query: UseQueryResult<T>; children: (data: T) => ReactNode }) {
  if (query.isPending) return <div className="flex justify-center p-10"><Spinner /></div>;
  if (query.isError) return <ErrorNote>{query.error.message}</ErrorNote>;
  return <>{children(query.data)}</>;
}

// ------------------------------------------------------------------ badges

const tones = {
  ok: "bg-ok/15 text-ok",
  warn: "bg-warn/15 text-warn",
  bad: "bg-bad/15 text-bad",
  muted: "bg-line/60 text-muted",
} as const;

export function Badge({ tone, children }: { tone: keyof typeof tones; children: ReactNode }) {
  return <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${tones[tone]}`}>{children}</span>;
}

const statusMeta: Record<DeliveryStatus, { label: string; tone: keyof typeof tones }> = {
  pending: { label: "Pending", tone: "warn" },
  succeeded: { label: "Delivered", tone: "ok" },
  dead: { label: "Dead", tone: "bad" },
};

export function StatusBadge({ status }: { status: DeliveryStatus }) {
  const meta = statusMeta[status];
  return <Badge tone={meta.tone}>{meta.label}</Badge>;
}

export function ActiveBadge({ active }: { active: boolean }) {
  return active ? <Badge tone="ok">Active</Badge> : <Badge tone="bad">Disabled</Badge>;
}

// ------------------------------------------------------------------ forms

export function Field({ label, hint, children }: { label: string; hint?: string; children: (id: string) => ReactNode }) {
  const id = useId();
  return (
    <div className="mb-4">
      <label htmlFor={id} className="mb-1 block text-sm font-medium">
        {label}
      </label>
      {children(id)}
      {hint && <p className="mt-1 text-xs text-muted">{hint}</p>}
    </div>
  );
}

// ------------------------------------------------------------------ modal

export function Modal({ title, onClose, children }: { title: string; onClose: () => void; children: ReactNode }) {
  const titleId = useId();
  const panel = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const previouslyFocused = document.activeElement as HTMLElement | null;
    panel.current?.focus();
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      previouslyFocused?.focus?.();
    };
  }, [onClose]);

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/50 p-4 sm:pt-24"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div
        ref={panel}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
        className="w-full max-w-lg rounded-lg border border-line bg-surface p-5 shadow-xl outline-none"
      >
        <div className="mb-4 flex items-center justify-between">
          <h2 id={titleId} className="text-lg font-semibold">
            {title}
          </h2>
          <button type="button" aria-label="Close" onClick={onClose} className="rounded p-1 text-muted hover:bg-line/50">
            ✕
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}

export function ConfirmModal({
  title,
  message,
  confirmLabel,
  onConfirm,
  onClose,
  loading,
  error,
}: {
  title: string;
  message: ReactNode;
  confirmLabel: string;
  onConfirm: () => void;
  onClose: () => void;
  loading?: boolean;
  error?: string;
}) {
  return (
    <Modal title={title} onClose={onClose}>
      <div className="mb-4 text-sm text-muted">{message}</div>
      {error && <div className="mb-3"><ErrorNote>{error}</ErrorNote></div>}
      <div className="flex justify-end gap-2">
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="danger" loading={loading} onClick={onConfirm}>
          {confirmLabel}
        </Button>
      </div>
    </Modal>
  );
}

// ------------------------------------------------------------------ secrets & copying

export function CopyButton({ text, label = "Copy" }: { text: string; label?: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <Button
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(text);
          setCopied(true);
          setTimeout(() => setCopied(false), 1500);
        } catch {
          /* clipboard unavailable (insecure context): the value is still selectable */
        }
      }}
    >
      {copied ? "Copied" : label}
    </Button>
  );
}

/** A credential that the API will never show again. */
export function SecretReveal({ title, value, onClose }: { title: string; value: string; onClose: () => void }) {
  return (
    <Modal title={title} onClose={onClose}>
      <Notice tone="warn">Copy this now. For your security it is shown only once and cannot be retrieved later.</Notice>
      <code className="mb-4 block break-all rounded-md border border-line bg-bg p-3 font-mono text-sm select-all">{value}</code>
      <div className="flex justify-end gap-2">
        <CopyButton text={value} />
        <Button variant="primary" onClick={onClose}>
          I have saved it
        </Button>
      </div>
    </Modal>
  );
}

// ------------------------------------------------------------------ tables & paging

export function Table({ head, children }: { head: string[]; children: ReactNode }) {
  return (
    <div className="overflow-x-auto rounded-lg border border-line bg-surface">
      <table className="w-full text-left text-sm">
        <thead className="border-b border-line text-xs uppercase tracking-wide text-muted">
          <tr>
            {head.map((label) => (
              <th key={label} scope="col" className="px-3 py-2 font-medium">
                {label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-line">{children}</tbody>
      </table>
    </div>
  );
}

export const td = "px-3 py-2 align-top";

export function Pagination({ page, hasNext, onChange }: { page: number; hasNext: boolean; onChange: (page: number) => void }) {
  if (page === 0 && !hasNext) return null;
  return (
    <nav aria-label="Pagination" className="mt-3 flex items-center justify-between text-sm">
      <Button disabled={page === 0} onClick={() => onChange(page - 1)}>
        ← Newer
      </Button>
      <span className="text-muted">Page {page + 1}</span>
      <Button disabled={!hasNext} onClick={() => onChange(page + 1)}>
        Older →
      </Button>
    </nav>
  );
}

export function Json({ value }: { value: unknown }) {
  return (
    <pre className="max-h-96 overflow-auto rounded-md border border-line bg-bg p-3 font-mono text-xs">
      {JSON.stringify(value, null, 2)}
    </pre>
  );
}
