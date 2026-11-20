"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState, type FormEvent } from "react";
import { Button, ErrorNote, Field, inputClass } from "@/components/ui";
import { ApiError } from "@/lib/api";
import { API_URL, useAuth } from "@/lib/auth";
import { retryMessage } from "@/lib/format";

function describeError(error: unknown): string {
  if (error instanceof ApiError) return error.status === 429 ? retryMessage(error.retryAfterSeconds) : error.message;
  if (error instanceof TypeError) return `Cannot reach the API at ${API_URL}. Is it running?`;
  return "Something went wrong. Please try again.";
}

export default function LoginPage() {
  const { state, login, register } = useAuth();
  const router = useRouter();
  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  useEffect(() => {
    if (state.status === "authenticated") router.replace("/");
  }, [state.status, router]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    setPending(true);
    try {
      await (mode === "login" ? login(email, password) : register(email, password));
      router.replace("/");
    } catch (err) {
      setError(describeError(err));
    } finally {
      setPending(false);
    }
  }

  const registering = mode === "register";
  return (
    <main className="flex min-h-screen items-center justify-center p-4">
      <div className="w-full max-w-sm">
        <h1 className="mb-1 text-2xl font-semibold tracking-tight">Hookhaven</h1>
        <p className="mb-6 text-sm text-muted">Reliable webhook delivery: retries, dead-letter queue and replay.</p>
        <form onSubmit={submit} className="rounded-lg border border-line bg-surface p-5" aria-label={registering ? "Create account" : "Sign in"}>
          <h2 className="mb-4 font-medium">{registering ? "Create your account" : "Sign in"}</h2>
          {error && (
            <div className="mb-4">
              <ErrorNote>{error}</ErrorNote>
            </div>
          )}
          <Field label="Email">
            {(id) => (
              <input id={id} type="email" required autoComplete="email" value={email} onChange={(e) => setEmail(e.target.value)} className={inputClass} />
            )}
          </Field>
          <Field label="Password" hint={registering ? "At least 8 characters." : undefined}>
            {(id) => (
              <input
                id={id}
                type="password"
                required
                minLength={registering ? 8 : undefined}
                autoComplete={registering ? "new-password" : "current-password"}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className={inputClass}
              />
            )}
          </Field>
          <Button type="submit" variant="primary" loading={pending} className="w-full">
            {registering ? "Create account" : "Sign in"}
          </Button>
        </form>
        <p className="mt-4 text-center text-sm text-muted">
          {registering ? "Already have an account?" : "New here?"}{" "}
          <button
            type="button"
            className="font-medium text-accent underline"
            onClick={() => {
              setMode(registering ? "login" : "register");
              setError(null);
            }}
          >
            {registering ? "Sign in" : "Create an account"}
          </button>
        </p>
      </div>
    </main>
  );
}
