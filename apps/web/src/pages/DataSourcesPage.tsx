import { useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, Database, Plus, Table2, XCircle } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "@/api/client";
import { keys, useConnections, useConnectorTypes, useDatasets } from "@/api/hooks";
import type { ConnectionInfo, ConnectorType } from "@/api/types";
import { Badge, Button, DataTable, Dialog, EmptyState, ErrorState, Field, Input, PageLoader, SearchInput, Select, useToast } from "@/design-system";
import { relativeTime } from "@/lib/time";
import { PageHeader } from "@/shell/AppShell";

interface TableInfo {
  schema: string | null;
  name: string;
  kind: string;
}
interface ColumnInfo {
  name: string;
  data_type: string;
  native_type: string;
  nullable: boolean;
}

export default function DataSourcesPage() {
  const { data, isLoading, error } = useConnections();
  const types = useConnectorTypes();
  const datasets = useDatasets();
  const [adding, setAdding] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);

  useEffect(() => {
    if (!selected && data?.length) setSelected(data[0].id);
  }, [data, selected]);

  if (isLoading) return <PageLoader />;
  if (error) return <ErrorState body={(error as Error).message} />;
  const typeLabel = (k: string) => types.data?.find((t) => t.kind === k)?.label ?? k;

  return (
    <div className="page page--wide">
      <PageHeader
        title="Data sources"
        description="Connections are used only by the server. Credentials are encrypted at rest and never returned to the browser."
        actions={
          <Button variant="primary" icon={<Plus />} onClick={() => setAdding(true)}>
            Add connection
          </Button>
        }
      />
      {data!.length === 0 ? (
        <div className="iq-card">
          <EmptyState icon={<Database />} title="No connections" body="Add a connection to a warehouse to start modelling datasets." />
        </div>
      ) : (
        <div className="stack">
          <div className="iq-card">
            <DataTable<ConnectionInfo>
              rows={data!}
              rowKey={(c) => c.id}
              onRowClick={(c) => setSelected(c.id)}
              isSelected={(c) => c.id === selected}
              columns={[
                {
                  key: "name",
                  header: "Name",
                  value: (c) => c.name,
                  render: (c) => (
                    <span className="cell-title">
                      <Database aria-hidden />
                      {c.name}
                    </span>
                  ),
                },
                { key: "kind", header: "Type", value: (c) => typeLabel(c.kind), width: 200 },
                {
                  key: "datasets",
                  header: "Datasets",
                  value: (c) => datasets.data?.filter((d) => d.connection_id === c.id).length ?? 0,
                  numeric: true,
                  width: 100,
                },
                { key: "secret", header: "Credentials", value: (c) => (c.has_secret ? "Stored" : "None"), width: 120, render: (c) => <span className="muted">{c.has_secret ? "Stored (encrypted)" : "None"}</span> },
                { key: "updated", header: "Updated", value: (c) => c.updated_at, render: (c) => <span className="muted">{relativeTime(c.updated_at)}</span>, width: 120 },
                { key: "test", header: "", value: () => "", sortable: false, width: 150, render: (c) => <TestButton id={c.id} /> },
              ]}
            />
          </div>
          {selected && <SchemaBrowser key={selected} connection={data!.find((c) => c.id === selected)!} />}
        </div>
      )}
      {adding && types.data && <AddConnectionDialog types={types.data} onClose={() => setAdding(false)} />}
    </div>
  );
}

function TestButton({ id }: { id: string }) {
  const [state, setState] = useState<{ ok: boolean; message: string } | null>(null);
  const [busy, setBusy] = useState(false);
  return (
    <span className="row" style={{ gap: 6, flexWrap: "nowrap" }} onClick={(e) => e.stopPropagation()}>
      <Button
        size="sm"
        loading={busy}
        onClick={async () => {
          setBusy(true);
          try {
            setState(await api.post<{ ok: boolean; message: string }>(`/api/connections/${id}/test`));
          } catch (e) {
            setState({ ok: false, message: (e as Error).message });
          } finally {
            setBusy(false);
          }
        }}
      >
        Test
      </Button>
      {state &&
        (state.ok ? (
          <CheckCircle2 aria-label="Connected" style={{ width: 16, color: "var(--good)" }} />
        ) : (
          <span title={state.message} style={{ display: "inline-flex" }}>
            <XCircle aria-label={`Failed: ${state.message}`} style={{ width: 16, color: "var(--critical)" }} />
          </span>
        ))}
    </span>
  );
}

function SchemaBrowser({ connection }: { connection: ConnectionInfo }) {
  const [table, setTable] = useState<TableInfo | null>(null);
  const [q, setQ] = useState("");
  const [creating, setCreating] = useState(false);
  const tables = useQuery({ queryKey: ["tables", connection.id], queryFn: () => api.get<TableInfo[]>(`/api/connections/${connection.id}/tables`) });
  const columns = useQuery({
    queryKey: ["columns", connection.id, table?.schema, table?.name],
    queryFn: () =>
      api.get<ColumnInfo[]>(`/api/connections/${connection.id}/columns?table=${encodeURIComponent(table!.name)}${table!.schema ? `&schema=${encodeURIComponent(table!.schema)}` : ""}`),
    enabled: !!table,
  });
  const shown = (tables.data ?? []).filter((t) => `${t.schema}.${t.name}`.toLowerCase().includes(q.toLowerCase()));

  return (
    <div className="iq-card">
      <div className="iq-card__header">
        <h2>Schema · {connection.name}</h2>
        {table && (
          <Button icon={<Plus />} onClick={() => setCreating(true)} disabled={!columns.data?.length}>
            Create dataset from {table.name}
          </Button>
        )}
      </div>
      <div className="split" style={{ gridTemplateColumns: "300px minmax(0,1fr)", gap: 0 }}>
        <div style={{ borderRight: "1px solid var(--divider)", minHeight: 280 }}>
          <div style={{ padding: 8 }}>
            <SearchInput size_="sm" placeholder="Filter tables" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Filter tables" />
          </div>
          {tables.isLoading && <PageLoader />}
          {tables.error && <ErrorState compact body={(tables.error as Error).message} />}
          <div style={{ padding: 4, maxHeight: 420, overflow: "auto" }}>
            {shown.map((t) => (
              <button key={`${t.schema}.${t.name}`} className="split__item" aria-current={table?.name === t.name && table?.schema === t.schema} onClick={() => setTable(t)}>
                <strong className="row" style={{ gap: 6 }}>
                  <Table2 style={{ width: 14, color: "var(--ink-3)" }} aria-hidden />
                  {t.name}
                </strong>
                <span>
                  {t.schema ?? "default"} · {t.kind}
                </span>
              </button>
            ))}
          </div>
        </div>
        <div style={{ minWidth: 0 }}>
          {!table ? (
            <EmptyState compact title="Select a table" body="Browse columns and types discovered from the connection." />
          ) : columns.isLoading ? (
            <PageLoader />
          ) : (
            <DataTable<ColumnInfo>
              rows={columns.data ?? []}
              rowKey={(c) => c.name}
              dense
              columns={[
                { key: "name", header: "Column", value: (c) => c.name, render: (c) => <code>{c.name}</code> },
                { key: "type", header: "Intelliq type", value: (c) => c.data_type, width: 140 },
                { key: "native", header: "Database type", value: (c) => c.native_type, width: 180, render: (c) => <span className="muted">{c.native_type}</span> },
                { key: "null", header: "Nullable", value: (c) => (c.nullable ? "yes" : "no"), width: 100 },
              ]}
            />
          )}
        </div>
      </div>
      {creating && table && columns.data && <CreateDatasetDialog connection={connection} table={table} columns={columns.data} onClose={() => setCreating(false)} />}
    </div>
  );
}

const NUMERIC = new Set(["integer", "real"]);

function CreateDatasetDialog({ connection, table, columns, onClose }: { connection: ConnectionInfo; table: TableInfo; columns: ColumnInfo[]; onClose: () => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const navigate = useNavigate();
  const [name, setName] = useState(table.name.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase()));
  const id = useMemo(() => name.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_|_$/g, "").replace(/^(\d)/, "d_$1") || "dataset", [name]);
  const [busy, setBusy] = useState(false);
  const fields = columns.map((c) => {
    const fid = c.name.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_|_$/g, "").replace(/^(\d)/, "f_$1") || "field";
    const label = c.name.replace(/_/g, " ").replace(/\b\w/g, (ch) => ch.toUpperCase());
    const measure = NUMERIC.has(c.data_type) && !/(^|_)(id|key|code|zip|postal)$/i.test(c.name);
    return { id: fid, name: label, role: measure ? "measure" : "dimension", data_type: c.data_type, column: `t0.${c.name}` };
  });

  const create = async () => {
    setBusy(true);
    try {
      await api.post("/api/datasets", {
        model: { id, name, connection_id: connection.id, base: { schema: table.schema, name: table.name }, base_alias: "t0", fields },
      });
      await qc.invalidateQueries({ queryKey: keys.datasets });
      toast(`Dataset ${name} created`, "good");
      navigate(`/metrics/${id}`);
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
      title="Create dataset"
      description={`Model ${table.schema ? `${table.schema}.` : ""}${table.name} as a governed dataset. Roles can be refined afterwards.`}
      wide
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" loading={busy} onClick={create} disabled={!name.trim()}>
            Create dataset
          </Button>
        </>
      }
    >
      <div className="stack">
        <div className="grid-2">
          <Field label="Name">{(fid) => <Input id={fid} value={name} onChange={(e) => setName(e.target.value)} />}</Field>
          <Field label="Identifier" hint="Used in dashboard specifications">
            {(fid) => <Input id={fid} value={id} readOnly />}
          </Field>
        </div>
        <div className="iq-card" style={{ boxShadow: "none" }}>
          <DataTable
            rows={fields}
            rowKey={(f) => f.id}
            dense
            pageSize={12}
            columns={[
              { key: "name", header: "Field", value: (f) => f.name },
              { key: "role", header: "Role", value: (f) => f.role, render: (f) => <Badge tone={f.role === "measure" ? "accent" : "neutral"}>{f.role}</Badge>, width: 120 },
              { key: "type", header: "Type", value: (f) => f.data_type, width: 100 },
              { key: "col", header: "Column", value: (f) => f.column, render: (f) => <code>{f.column}</code> },
            ]}
          />
        </div>
      </div>
    </Dialog>
  );
}

function AddConnectionDialog({ types, onClose }: { types: ConnectorType[]; onClose: () => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [kind, setKind] = useState(types[0]?.kind ?? "duckdb");
  const [name, setName] = useState("");
  const [values, setValues] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const type = types.find((t) => t.kind === kind)!;

  const submit = async () => {
    setBusy(true);
    try {
      const config: Record<string, string> = {};
      const secrets: Record<string, string> = {};
      for (const f of type.fields) {
        const v = values[f.name];
        if (!v) continue;
        (f.secret ? secrets : config)[f.name] = v;
      }
      await api.post("/api/connections", { name, kind, config, secrets: Object.keys(secrets).length ? secrets : undefined });
      await qc.invalidateQueries({ queryKey: keys.connections });
      toast("Connection added", "good");
      onClose();
    } catch (e) {
      toast((e as Error).message, "critical");
    } finally {
      setBusy(false);
    }
  };

  const missing = type.fields.some((f) => f.required && !values[f.name]);
  return (
    <Dialog
      open
      onClose={onClose}
      title="Add connection"
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" loading={busy} disabled={!name.trim() || missing} onClick={submit}>
            Add connection
          </Button>
        </>
      }
    >
      <div className="stack">
        <Field label="Type" hint={type.verified ? "Covered by the automated connector contract suite." : "Adapter implemented; not yet verified against a live service in automated tests."}>
          {(id) => (
            <Select
              id={id}
              value={kind}
              onChange={(e) => {
                setKind(e.target.value);
                setValues({});
              }}
              options={types.map((t) => ({ value: t.kind, label: `${t.label}${t.verified ? "" : " (unverified)"}` }))}
            />
          )}
        </Field>
        <Field label="Name">{(id) => <Input id={id} value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Finance warehouse" />}</Field>
        {type.fields.map((f) => (
          <Field key={f.name} label={`${f.label}${f.required ? "" : " (optional)"}`} hint={f.secret ? "Stored encrypted; never shown again." : undefined}>
            {(id) => (
              <Input
                id={id}
                type={f.secret ? "password" : "text"}
                autoComplete={f.secret ? "new-password" : "off"}
                value={values[f.name] ?? ""}
                onChange={(e) => setValues((v) => ({ ...v, [f.name]: e.target.value }))}
              />
            )}
          </Field>
        ))}
      </div>
    </Dialog>
  );
}
