import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus, UserRound } from "lucide-react";
import { useState } from "react";
import { api } from "@/api/client";
import { keys, useAudit, useUsers } from "@/api/hooks";
import type { AuditEntry, ConnectorType, UserRow } from "@/api/types";
import { useAuth } from "@/auth/AuthProvider";
import { Badge, Button, DataTable, Dialog, ErrorState, Field, Input, PageLoader, SearchInput, Select, Switch, Tabs, useToast } from "@/design-system";
import { relativeTime } from "@/lib/time";
import { PageHeader } from "@/shell/AppShell";

type Tab = "users" | "audit" | "system";
const ROLE_OPTIONS = [
  { value: "viewer", label: "Viewer — published dashboards" },
  { value: "author", label: "Author — build, import and publish" },
  { value: "admin", label: "Admin — data, users and security" },
];

export default function AdminPage() {
  const { can } = useAuth();
  const [tab, setTab] = useState<Tab>("users");
  return (
    <div className="page">
      <PageHeader title="Administration" description="Workspace members, roles, row-level security attributes and the audit trail." />
      <div className="iq-card">
        <div style={{ padding: "0 16px" }}>
          <Tabs<Tab>
            label="Administration sections"
            value={tab}
            onChange={setTab}
            tabs={[
              { value: "users", label: "Users" },
              ...(can("view_audit") ? [{ value: "audit" as Tab, label: "Audit log" }] : []),
              ...(can("manage_connections") ? [{ value: "system" as Tab, label: "System" }] : []),
            ]}
          />
        </div>
        {tab === "users" && <Users />}
        {tab === "audit" && <Audit />}
        {tab === "system" && <System />}
      </div>
    </div>
  );
}

function formatAttributes(a: Record<string, unknown>): string {
  return Object.entries(a)
    .map(([k, v]) => `${k}=${Array.isArray(v) ? v.join("|") : String(v)}`)
    .join(", ");
}

function parseAttributes(text: string): Record<string, string[]> {
  const out: Record<string, string[]> = {};
  for (const part of text.split(",").map((s) => s.trim()).filter(Boolean)) {
    const [k, ...rest] = part.split("=");
    const key = k.trim();
    if (!/^[a-z][a-z0-9_]{0,62}$/.test(key)) throw new Error(`Invalid attribute name "${key}"`);
    out[key] = rest.join("=").split("|").map((v) => v.trim()).filter(Boolean);
  }
  return out;
}

function Users() {
  const { me } = useAuth();
  const { data, isLoading, error } = useUsers();
  const qc = useQueryClient();
  const toast = useToast();
  const [adding, setAdding] = useState(false);
  const [editing, setEditing] = useState<UserRow | null>(null);

  const patch = async (u: UserRow, body: Record<string, unknown>, message: string) => {
    try {
      await api.patch(`/api/admin/users/${u.id}`, body);
      await qc.invalidateQueries({ queryKey: keys.users });
      toast(message, "good");
    } catch (e) {
      toast((e as Error).message, "critical");
    }
  };

  if (isLoading) return <PageLoader />;
  if (error) return <ErrorState body={(error as Error).message} />;
  return (
    <>
      <div className="row" style={{ justifyContent: "flex-end", padding: "12px 16px 4px" }}>
        <Button variant="primary" icon={<Plus />} onClick={() => setAdding(true)}>
          Add user
        </Button>
      </div>
      <DataTable<UserRow>
        rows={data ?? []}
        rowKey={(u) => u.id}
        searchable
        columns={[
          {
            key: "name",
            header: "User",
            value: (u) => `${u.name} ${u.email}`,
            render: (u) => (
              <span className="cell-title">
                <UserRound aria-hidden />
                <span>
                  {u.name} <span className="muted" style={{ fontWeight: 400 }}>{u.email}</span>
                </span>
                {u.id === me?.user.id && <Badge>You</Badge>}
              </span>
            ),
          },
          {
            key: "role",
            header: "Role",
            value: (u) => u.role,
            width: 180,
            render: (u) => (
              <Select size_="sm" aria-label={`Role for ${u.name}`} value={u.role} onChange={(e) => void patch(u, { role: e.target.value }, "Role updated")}>
                <option value="viewer">Viewer</option>
                <option value="author">Author</option>
                <option value="admin">Admin</option>
              </Select>
            ),
          },
          {
            key: "attrs",
            header: "Security attributes",
            value: (u) => formatAttributes(u.attributes),
            render: (u) => (
              <button className="iq-btn iq-btn--ghost iq-btn--sm" onClick={() => setEditing(u)}>
                {Object.keys(u.attributes).length ? formatAttributes(u.attributes) : <span className="muted">None</span>}
              </button>
            ),
          },
          { key: "last", header: "Last sign-in", value: (u) => u.last_login_at ?? "", width: 130, render: (u) => <span className="muted">{relativeTime(u.last_login_at)}</span> },
          {
            key: "active",
            header: "Active",
            value: (u) => (u.is_active ? 1 : 0),
            width: 80,
            render: (u) => <Switch label={`${u.name} active`} checked={u.is_active} onChange={(v) => void patch(u, { is_active: v }, v ? "User reactivated" : "User deactivated and signed out")} />,
          },
        ]}
      />
      {adding && <AddUserDialog onClose={() => setAdding(false)} />}
      {editing && (
        <AttributesDialog
          user={editing}
          onClose={() => setEditing(null)}
          onSave={async (attributes) => {
            await patch(editing, { attributes }, "Attributes updated");
            setEditing(null);
          }}
        />
      )}
    </>
  );
}

function AttributesDialog({ user, onClose, onSave }: { user: UserRow; onClose: () => void; onSave: (a: Record<string, string[]>) => Promise<void> }) {
  const [text, setText] = useState(formatAttributes(user.attributes));
  const [error, setError] = useState<string | null>(null);
  return (
    <Dialog
      open
      onClose={onClose}
      title={`Security attributes · ${user.name}`}
      description="Row-level security rules match dataset fields against these values. A user without a required attribute sees no rows."
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button
            variant="primary"
            onClick={() => {
              try {
                void onSave(parseAttributes(text));
              } catch (e) {
                setError((e as Error).message);
              }
            }}
          >
            Save
          </Button>
        </>
      }
    >
      <Field label="Attributes" hint="Format: region=West|Central, department=Sales" error={error}>
        {(id) => <Input id={id} value={text} onChange={(e) => setText(e.target.value)} placeholder="region=West" />}
      </Field>
    </Dialog>
  );
}

function AddUserDialog({ onClose }: { onClose: () => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [form, setForm] = useState({ email: "", name: "", password: "", role: "viewer", attributes: "" });
  const [busy, setBusy] = useState(false);
  const set = (k: keyof typeof form) => (e: { target: { value: string } }) => setForm((f) => ({ ...f, [k]: e.target.value }));
  const submit = async () => {
    setBusy(true);
    try {
      await api.post("/api/admin/users", { ...form, attributes: parseAttributes(form.attributes) });
      await qc.invalidateQueries({ queryKey: keys.users });
      toast(`${form.name} added`, "good");
      onClose();
    } catch (e) {
      toast((e as Error).message, "critical");
    } finally {
      setBusy(false);
    }
  };
  return (
    <Dialog
      open
      onClose={onClose}
      title="Add user"
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" loading={busy} disabled={!form.email || !form.name || form.password.length < 10} onClick={submit}>
            Add user
          </Button>
        </>
      }
    >
      <div className="stack">
        <div className="grid-2">
          <Field label="Name">{(id) => <Input id={id} value={form.name} onChange={set("name")} />}</Field>
          <Field label="Email">{(id) => <Input id={id} type="email" value={form.email} onChange={set("email")} />}</Field>
        </div>
        <Field label="Initial password" hint="At least 10 characters. Ask the user to change it after signing in.">
          {(id) => <Input id={id} type="password" autoComplete="new-password" value={form.password} onChange={set("password")} />}
        </Field>
        <Field label="Role">{(id) => <Select id={id} value={form.role} onChange={set("role")} options={ROLE_OPTIONS} />}</Field>
        <Field label="Security attributes (optional)" hint="Format: region=West|Central">
          {(id) => <Input id={id} value={form.attributes} onChange={set("attributes")} />}
        </Field>
      </div>
    </Dialog>
  );
}

function Audit() {
  const [action, setAction] = useState("");
  const { data, isLoading, error } = useAudit(action || undefined);
  if (error) return <ErrorState body={(error as Error).message} />;
  return (
    <>
      <div style={{ padding: "12px 16px 4px", width: 320 }}>
        <SearchInput size_="sm" placeholder="Filter by action prefix, e.g. dashboard." value={action} onChange={(e) => setAction(e.target.value)} aria-label="Filter audit log by action" />
      </div>
      {isLoading ? (
        <PageLoader />
      ) : (
        <DataTable<AuditEntry>
          rows={data ?? []}
          rowKey={(e) => String(e.id)}
          dense
          pageSize={50}
          columns={[
            { key: "ts", header: "When", value: (e) => e.ts, width: 120, render: (e) => <span className="muted" title={e.ts}>{relativeTime(e.ts)}</span> },
            { key: "actor", header: "Actor", value: (e) => e.actor_email ?? "system", width: 200 },
            { key: "action", header: "Action", value: (e) => e.action, width: 180, render: (e) => <code style={{ fontSize: 12 }}>{e.action}</code> },
            { key: "target", header: "Target", value: (e) => `${e.target_type ?? ""} ${e.target_id ?? ""}`, render: (e) => <span className="muted">{e.target_type ? `${e.target_type} ${e.target_id ?? ""}` : "—"}</span> },
            { key: "detail", header: "Detail", value: (e) => JSON.stringify(e.detail), render: (e) => <code style={{ fontSize: 11 }}>{JSON.stringify(e.detail)}</code> },
            { key: "ip", header: "IP", value: (e) => e.ip ?? "", width: 110 },
          ]}
        />
      )}
    </>
  );
}

function System() {
  const { data, isLoading } = useQuery({
    queryKey: ["system"],
    queryFn: () => api.get<{ cache: Record<string, number>; connector_types: ConnectorType[] }>("/api/admin/system"),
  });
  if (isLoading || !data) return <PageLoader />;
  return (
    <div className="iq-card__body stack">
      <div className="stat-row">
        <div className="stat">
          <div className="stat__label">Cached results</div>
          <div className="stat__value">{data.cache.entries}</div>
        </div>
        <div className="stat">
          <div className="stat__label">Cache hit rate</div>
          <div className="stat__value">{data.cache.hits + data.cache.misses ? `${Math.round((100 * data.cache.hits) / (data.cache.hits + data.cache.misses))}%` : "—"}</div>
        </div>
      </div>
      <div>
        <h3 style={{ marginBottom: 8 }}>Connectors</h3>
        <DataTable
          rows={data.connector_types}
          rowKey={(t) => t.kind}
          dense
          columns={[
            { key: "label", header: "Connector", value: (t) => t.label },
            { key: "dialect", header: "SQL dialect", value: (t) => t.dialect, width: 140 },
            {
              key: "verified",
              header: "Verification",
              value: (t) => (t.verified ? 1 : 0),
              width: 260,
              render: (t) => (t.verified ? <Badge tone="good">Contract-tested</Badge> : <Badge tone="warning">SQL generation tested; live service unverified</Badge>),
            },
          ]}
        />
      </div>
    </div>
  );
}
