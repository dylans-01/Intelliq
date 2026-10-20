import { useState, type FormEvent } from "react";
import { Navigate, useLocation, useNavigate } from "react-router-dom";
import { ApiError } from "@/api/client";
import { useAuth } from "@/auth/AuthProvider";
import { Button, Field, Input, PageLoader } from "@/design-system";

export function LoginPage() {
  const { me, loading, login } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const from = (location.state as { from?: string } | null)?.from ?? "/dashboards";

  if (loading) return <PageLoader />;
  if (me) return <Navigate to={from} replace />;

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await login(email, password);
      navigate(from, { replace: true });
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not sign in");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="login">
      <form className="login__card" onSubmit={submit} noValidate>
        <div className="login__brand">
          <svg viewBox="0 0 32 32" width="28" height="28" aria-hidden>
            <rect width="32" height="32" rx="8" fill="var(--accent)" />
            <path d="M9 22V14M16 22V9M23 22v-5" stroke="var(--accent-ink)" strokeWidth="3" strokeLinecap="round" />
          </svg>
          <span>Intelliq</span>
        </div>
        <div>
          <h1>Sign in</h1>
          <p className="muted" style={{ marginTop: 4, fontSize: 13 }}>
            Use your workspace account.
          </p>
        </div>
        <Field label="Email">
          {(id) => <Input id={id} type="email" autoComplete="username" required value={email} onChange={(e) => setEmail(e.target.value)} autoFocus />}
        </Field>
        <Field label="Password" error={error}>
          {(id) => (
            <Input
              id={id}
              type="password"
              autoComplete="current-password"
              required
              value={password}
              aria-invalid={!!error}
              onChange={(e) => setPassword(e.target.value)}
            />
          )}
        </Field>
        <Button type="submit" variant="primary" loading={busy} disabled={!email || !password}>
          Sign in
        </Button>
      </form>
    </div>
  );
}
