import { useState, type FormEvent } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { post, type User } from "../api";
import { Button, ErrorNote, Field, Input, PageHeader, Panel } from "../components/ui";

function Shell({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex min-h-screen items-center justify-center px-4 py-10">
      <div className="w-full max-w-sm">
        <div className="mb-8 flex flex-col items-center gap-3 text-center">
          <img src="/favicon.svg" alt="" width="44" height="44" />
          <div className="text-[20px] font-semibold tracking-[-0.01em]">AS-Stats</div>
        </div>
        {children}
      </div>
    </div>
  );
}

export function Login({ onLogin }: { onLogin: (u: User) => void }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      onLogin(await post<User>("/auth/login", { username, password }));
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Shell>
      <form onSubmit={submit} className="rounded-xl border border-rule bg-surface p-6 shadow-sm">
        <h1 className="mb-5 text-[17px] font-semibold">Log in</h1>
        <div className="grid gap-4">
          <Field label="Username">
            <Input value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="username" autoFocus required />
          </Field>
          <Field label="Password">
            <Input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" required />
          </Field>
          {error ? <ErrorNote error={error} /> : null}
          <Button type="submit" variant="primary" disabled={busy}>{busy ? "Logging in" : "Log in"}</Button>
        </div>
      </form>
    </Shell>
  );
}

function PasswordForm({ onDone, submitLabel }: { onDone: (u: User) => void; submitLabel: string }) {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [repeat, setRepeat] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (next !== repeat) {
      setError(new Error("The new passwords do not match."));
      return;
    }
    setBusy(true);
    setError(null);
    try {
      onDone(await post<User>("/auth/password", { current_password: current, new_password: next }));
      setCurrent("");
      setNext("");
      setRepeat("");
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  };
  return (
    <form onSubmit={submit} className="grid gap-4">
      <Field label="Current password">
        <Input type="password" value={current} onChange={(e) => setCurrent(e.target.value)} autoComplete="current-password" required />
      </Field>
      <Field label="New password" hint="At least 8 characters.">
        <Input type="password" value={next} onChange={(e) => setNext(e.target.value)} autoComplete="new-password" minLength={8} required />
      </Field>
      <Field label="New password again">
        <Input type="password" value={repeat} onChange={(e) => setRepeat(e.target.value)} autoComplete="new-password" minLength={8} required />
      </Field>
      {error ? <ErrorNote error={error} /> : null}
      <Button type="submit" variant="primary" disabled={busy}>{submitLabel}</Button>
    </form>
  );
}

export function ForcePasswordChange({ user, onDone }: { user: User; onDone: (u: User) => void }) {
  return (
    <Shell>
      <div className="rounded-xl border border-rule bg-surface p-6 shadow-sm">
        <h1 className="text-[17px] font-semibold">Choose your own password</h1>
        <p className="mt-1 mb-5 text-muted">{user.username}, your password was set by someone else. Replace it before you continue.</p>
        <PasswordForm onDone={onDone} submitLabel="Set password and continue" />
      </div>
    </Shell>
  );
}

export function Account() {
  const qc = useQueryClient();
  const [done, setDone] = useState(false);
  return (
    <>
      <PageHeader title="Account" />
      <Panel title="Change password" className="max-w-md">
        <PasswordForm
          submitLabel="Change password"
          onDone={(u) => {
            qc.setQueryData(["me"], u);
            setDone(true);
          }}
        />
        {done && <p className="mt-3 text-ok">Password changed. Your other sessions were logged out.</p>}
      </Panel>
    </>
  );
}
