import { useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, CheckCircle2, CircleDashed, ExternalLink, Wrench, XCircle } from "lucide-react";
import { useMemo, useState, type ReactNode } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api } from "@/api/client";
import { keys, useDataset, useDatasets, useImport } from "@/api/hooks";
import type { DashboardSpec, ImportPayload, ImportReportItem, ReportStatus } from "@/api/types";
import { useAuth } from "@/auth/AuthProvider";
import { DashboardCanvas, useDashboardRuntime } from "@/dashboard/DashboardCanvas";
import { Badge, Button, DataTable, EmptyState, ErrorState, PageLoader, Segmented, Select, Tabs, useToast } from "@/design-system";
import { bytes, relativeTime } from "@/lib/time";
import { PageHeader } from "@/shell/AppShell";

type Tab = "report" | "mapping" | "preview" | "inventory";

const STATUS: Record<ReportStatus, { label: string; tone: "good" | "accent" | "warning" | "critical"; icon: ReactNode; dot: string }> = {
  supported: { label: "Supported", tone: "good", icon: <CheckCircle2 />, dot: "good" },
  adjusted: { label: "Adjusted", tone: "accent", icon: <Wrench />, dot: "warning" },
  manual: { label: "Manual", tone: "warning", icon: <AlertTriangle />, dot: "serious" },
  unsupported: { label: "Unsupported", tone: "critical", icon: <XCircle />, dot: "critical" },
};

export default function ImportDetailPage() {
  const { id = "" } = useParams();
  const { data, isLoading, error } = useImport(id);
  if (isLoading) return <PageLoader />;
  if (error || !data) return <ErrorState body={(error as Error | null)?.message} />;
  return <ImportDetail record={data} />;
}

function ImportDetail({ record }: { record: ImportPayload }) {
  const { can } = useAuth();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const toast = useToast();
  const [tab, setTab] = useState<Tab>("report");
  const [applying, setApplying] = useState(false);
  const s = record.report.summary;
  const newFieldCount = Object.values(record.preview?.new_fields ?? {}).reduce((n, f) => n + f.length, 0);

  const apply = async () => {
    setApplying(true);
    try {
      const res = await api.post<ImportPayload>(`/api/imports/${record.id}/apply`);
      qc.setQueryData(keys.import(record.id), res);
      await qc.invalidateQueries({ queryKey: keys.dashboards });
      await qc.invalidateQueries({ queryKey: keys.imports });
      toast(`Created ${res.dashboard_ids.length} draft dashboard(s)`, "good");
    } catch (e) {
      toast((e as Error).message, "critical");
    } finally {
      setApplying(false);
    }
  };

  return (
    <div className="page page--wide">
      <PageHeader
        title={record.filename}
        meta={
          <>
            {record.status === "applied" ? <Badge tone="good">Applied</Badge> : <Badge>Analyzed</Badge>}
            <span>
              {bytes(record.size)} · Tableau format {record.inventory.version ?? "unknown"} · uploaded {relativeTime(record.created_at)}
            </span>
            <code title="SHA-256 of the uploaded file" style={{ fontSize: 11 }}>
              {record.sha256.slice(0, 12)}…
            </code>
          </>
        }
        actions={
          record.status === "applied" ? (
            record.dashboard_ids.map((did, i) => (
              <Button key={did} variant={i === 0 ? "primary" : "secondary"} icon={<ExternalLink />} onClick={() => navigate(`/dashboards/${did}`)}>
                Open dashboard{record.dashboard_ids.length > 1 ? ` ${i + 1}` : ""}
              </Button>
            ))
          ) : (
            <Button
              variant="primary"
              loading={applying}
              disabled={!record.preview?.dashboards.length || (newFieldCount > 0 && !can("manage_datasets"))}
              title={newFieldCount > 0 && !can("manage_datasets") ? "Adds calculated fields to a dataset; an administrator must apply it" : undefined}
              onClick={apply}
            >
              Create {record.preview?.dashboards.length ?? 0} draft dashboard{record.preview?.dashboards.length === 1 ? "" : "s"}
            </Button>
          )
        }
      />

      <div className="stat-row" style={{ marginBottom: 16 }}>
        <Stat label="Estimated fidelity" value={`${Math.round(s.fidelity * 100)}%`} />
        {(Object.keys(STATUS) as ReportStatus[]).map((k) => (
          <Stat key={k} label={STATUS[k].label} value={String(s[k] ?? 0)} dot={STATUS[k].dot} />
        ))}
      </div>
      <p className="muted" style={{ fontSize: 12, margin: "-4px 0 16px" }}>
        Fidelity is a structural estimate (supported + ½ adjusted, over all items). Numeric parity must be confirmed with the validation framework against
        reference outputs from Tableau.
      </p>

      <div className="iq-card">
        <div style={{ padding: "0 16px" }}>
          <Tabs<Tab>
            label="Import sections"
            value={tab}
            onChange={setTab}
            tabs={[
              { value: "report", label: "Compatibility report", count: record.report.items.length },
              { value: "mapping", label: "Mapping" },
              { value: "preview", label: "Preview" },
              { value: "inventory", label: "Workbook contents" },
            ]}
          />
        </div>
        {tab === "report" && <ReportTable items={record.report.items} />}
        {tab === "mapping" && <MappingEditor record={record} />}
        {tab === "preview" && <Preview record={record} />}
        {tab === "inventory" && <Inventory record={record} />}
      </div>
    </div>
  );
}

function Stat({ label, value, dot }: { label: string; value: string; dot?: string }) {
  return (
    <div className="stat">
      <div className="stat__label">{dot ? <span className="status-dot" data-tone={dot}>{label}</span> : label}</div>
      <div className="stat__value">{value}</div>
    </div>
  );
}

function ReportTable({ items }: { items: ImportReportItem[] }) {
  const [status, setStatus] = useState<"all" | ReportStatus>("all");
  const rows = useMemo(() => items.filter((i) => status === "all" || i.status === status), [items, status]);
  return (
    <div>
      <div style={{ padding: "12px 16px 4px" }}>
        <Segmented<"all" | ReportStatus>
          label="Filter by status"
          value={status}
          onChange={setStatus}
          options={[{ value: "all", label: "All" }, ...(Object.keys(STATUS) as ReportStatus[]).map((k) => ({ value: k, label: STATUS[k].label }))]}
        />
      </div>
      <DataTable<ImportReportItem>
        rows={rows}
        rowKey={(r, i) => `${r.kind}:${r.ref}:${i}`}
        searchable
        dense
        pageSize={50}
        initialSort={{ key: "status", dir: "desc" }}
        columns={[
          {
            key: "status",
            header: "Status",
            value: (r) => ["supported", "adjusted", "manual", "unsupported"].indexOf(r.status),
            width: 130,
            render: (r) => (
              <Badge tone={STATUS[r.status].tone} icon={STATUS[r.status].icon}>
                {STATUS[r.status].label}
              </Badge>
            ),
          },
          { key: "kind", header: "Item", value: (r) => r.kind, width: 110, render: (r) => <span className="muted">{r.kind.replace("_", " ")}</span> },
          { key: "ref", header: "Source", value: (r) => r.ref },
          {
            key: "message",
            header: "Detail",
            value: (r) => r.message,
            render: (r) => (
              <span title={r.translated ? `Tableau: ${r.formula}\nIntelliq: ${r.translated}` : r.formula} style={{ whiteSpace: "normal" }}>
                {r.message}
              </span>
            ),
          },
        ]}
      />
    </div>
  );
}

function MappingEditor({ record }: { record: ImportPayload }) {
  const qc = useQueryClient();
  const toast = useToast();
  const datasets = useDatasets();
  const [mapping, setMapping] = useState(record.mapping);
  const [saving, setSaving] = useState(false);
  const dirty = JSON.stringify(mapping) !== JSON.stringify(record.mapping);
  const readOnly = record.status === "applied";

  const save = async () => {
    setSaving(true);
    try {
      const res = await api.put<ImportPayload>(`/api/imports/${record.id}/mapping`, mapping);
      qc.setQueryData(keys.import(record.id), res);
      toast("Mapping updated and conversion re-run", "good");
    } catch (e) {
      toast((e as Error).message, "critical");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="iq-card__body stack">
      {record.inventory.datasources.map((ds) => {
        const target = mapping.datasources[ds.name] ?? "";
        return (
          <div key={ds.name} className="stack" style={{ gap: 10 }}>
            <div className="row" style={{ justifyContent: "space-between" }}>
              <div>
                <strong style={{ fontSize: 13 }}>{ds.caption}</strong>
                <div className="muted" style={{ fontSize: 12 }}>
                  {ds.connection?.class ?? "unknown"} {ds.connection?.server ? `· ${ds.connection.server}` : ""} {ds.connection?.dbname ? `· ${ds.connection.dbname}` : ""}
                  {ds.extract && " · packaged extract"}
                  {ds.custom_sql && " · custom SQL"}
                </div>
              </div>
              <div style={{ width: 300 }}>
                <Select
                  aria-label={`Target dataset for ${ds.caption}`}
                  value={target}
                  disabled={readOnly}
                  onChange={(e) =>
                    setMapping((m) => ({ ...m, datasources: { ...m.datasources, [ds.name]: e.target.value }, fields: { ...m.fields, [ds.name]: e.target.value === target ? m.fields[ds.name] ?? {} : {} } }))
                  }
                >
                  <option value="">Not mapped</option>
                  {datasets.data?.map((d) => (
                    <option key={d.id} value={d.id}>
                      {d.name}
                    </option>
                  ))}
                </Select>
              </div>
            </div>
            {target && <FieldMapping datasetId={target} sourceFields={ds.raw_fields} value={mapping.fields[ds.name] ?? {}} readOnly={readOnly} onChange={(f) => setMapping((m) => ({ ...m, fields: { ...m.fields, [ds.name]: f } }))} />}
          </div>
        );
      })}
      {!readOnly && (
        <div className="row" style={{ justifyContent: "flex-end" }}>
          <Button onClick={() => setMapping(record.mapping)} disabled={!dirty}>
            Discard changes
          </Button>
          <Button variant="primary" loading={saving} disabled={!dirty} onClick={save}>
            Save mapping and re-convert
          </Button>
        </div>
      )}
    </div>
  );
}

function FieldMapping({
  datasetId,
  sourceFields,
  value,
  onChange,
  readOnly,
}: {
  datasetId: string;
  sourceFields: { name: string; caption: string }[];
  value: Record<string, string>;
  onChange: (v: Record<string, string>) => void;
  readOnly: boolean;
}) {
  const { data } = useDataset(datasetId);
  const options = (data?.fields ?? []).filter((f) => !f.formula);
  return (
    <div className="iq-card" style={{ boxShadow: "none" }}>
      <DataTable
        rows={sourceFields}
        rowKey={(r) => r.name}
        dense
        pageSize={12}
        columns={[
          { key: "src", header: "Tableau field", value: (r) => r.caption },
          {
            key: "status",
            header: "",
            value: (r) => (value[r.name] ? 1 : 0),
            width: 40,
            render: (r) => (value[r.name] ? <CheckCircle2 aria-label="Mapped" style={{ width: 15, color: "var(--good)" }} /> : <CircleDashed aria-label="Unmapped" style={{ width: 15, color: "var(--ink-muted)" }} />),
          },
          {
            key: "target",
            header: "Intelliq field",
            value: (r) => value[r.name] ?? "",
            sortable: false,
            render: (r) => (
              <Select
                size_="sm"
                aria-label={`Map ${r.caption}`}
                disabled={readOnly}
                value={value[r.name] ?? ""}
                onChange={(e) => {
                  const next = { ...value };
                  if (e.target.value) next[r.name] = e.target.value;
                  else delete next[r.name];
                  onChange(next);
                }}
              >
                <option value="">Not mapped</option>
                {options.map((f) => (
                  <option key={f.id} value={f.id}>
                    {f.name}
                  </option>
                ))}
              </Select>
            ),
          },
        ]}
      />
    </div>
  );
}

function Preview({ record }: { record: ImportPayload }) {
  const dashboards = record.preview?.dashboards ?? [];
  const [index, setIndex] = useState(0);
  if (!dashboards.length) return <EmptyState title="Nothing to preview" body="Map the workbook's data sources to datasets to convert its dashboards." />;
  const spec = dashboards[Math.min(index, dashboards.length - 1)];
  const newFields = Object.values(record.preview?.new_fields ?? {}).flat();
  return (
    <div className="iq-card__body stack">
      {dashboards.length > 1 && (
        <Segmented label="Converted dashboards" value={String(index)} onChange={(v) => setIndex(Number(v))} options={dashboards.map((d, i) => ({ value: String(i), label: d.title }))} />
      )}
      {newFields.length > 0 && (
        <p className="muted" style={{ fontSize: 12 }}>
          Applying adds {newFields.length} calculated field(s) to the dataset: {newFields.map((f) => f.name).join(", ")}. The preview below already uses them.
        </p>
      )}
      <PreviewCanvas key={index} spec={spec} importId={record.id} />
    </div>
  );
}

function PreviewCanvas({ spec, importId }: { spec: DashboardSpec; importId: string }) {
  const [state, dispatch] = useDashboardRuntime(spec);
  return (
    <div style={{ background: "var(--page)", padding: 12, borderRadius: 8, border: "1px solid var(--border)" }}>
      <h2 style={{ marginBottom: 12 }}>{spec.title}</h2>
      <DashboardCanvas spec={spec} draft state={state} dispatch={dispatch} preview previewImportId={importId} />
    </div>
  );
}

function Inventory({ record }: { record: ImportPayload }) {
  const inv = record.inventory;
  return (
    <div className="iq-card__body">
      <dl className="kv">
        <dt>Data sources</dt>
        <dd>{inv.datasources.map((d) => d.caption).join(", ") || "—"}</dd>
        <dt>Worksheets</dt>
        <dd>{inv.worksheets.join(", ") || "—"}</dd>
        <dt>Dashboards</dt>
        <dd>{inv.dashboards.join(", ") || "—"}</dd>
        <dt>Parameters</dt>
        <dd>{inv.parameters.join(", ") || "—"}</dd>
        <dt>Actions</dt>
        <dd>{inv.actions.join(", ") || "—"}</dd>
        <dt>Packaged assets</dt>
        <dd>{inv.assets.length ? inv.assets.map((a) => `${a.path} (${a.kind}, ${bytes(a.size)})`).join("; ") : "None (.twb)"}</dd>
      </dl>
    </div>
  );
}
