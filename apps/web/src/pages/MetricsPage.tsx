import { useQuery, useQueryClient } from "@tanstack/react-query";
import { BadgeCheck, Calculator, Columns3, Plus, Sigma } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api } from "@/api/client";
import { keys, useDataset, useDatasets } from "@/api/hooks";
import type { CalcValidation, DatasetDetail, SemanticField } from "@/api/types";
import { useAuth } from "@/auth/AuthProvider";
import {
  Badge,
  Button,
  DataTable,
  Dialog,
  EmptyState,
  ErrorState,
  Field,
  Input,
  PageLoader,
  Select,
  Tabs,
  Textarea,
  useToast,
} from "@/design-system";
import { relativeTime } from "@/lib/time";
import { PageHeader } from "@/shell/AppShell";

type Tab = "metrics" | "fields" | "hierarchies" | "security" | "versions";

export default function MetricsPage() {
  const { datasetId } = useParams();
  const navigate = useNavigate();
  const { data, isLoading, error } = useDatasets();

  useEffect(() => {
    if (!datasetId && data?.length) navigate(`/metrics/${data[0].id}`, { replace: true });
  }, [datasetId, data, navigate]);

  if (isLoading) return <PageLoader />;
  if (error) return <ErrorState body={(error as Error).message} />;
  if (!data?.length)
    return (
      <div className="page">
        <PageHeader title="Metrics" description="Certified business definitions shared by every dashboard." />
        <div className="iq-card">
          <EmptyState icon={<Sigma />} title="No datasets yet" body="An administrator creates datasets on top of data source connections." />
        </div>
      </div>
    );

  return (
    <div className="page page--wide">
      <PageHeader title="Metrics" description="One canonical definition per metric, reused by every dashboard and validated on every change." />
      <div className="split">
        <nav className="iq-card split__list" aria-label="Datasets">
          {data.map((d) => (
            <button key={d.id} className="split__item" aria-current={d.id === datasetId} onClick={() => navigate(`/metrics/${d.id}`)}>
              <strong>{d.name}</strong>
              <span>
                {d.field_count} fields · v{d.version}
              </span>
            </button>
          ))}
        </nav>
        {datasetId && <DatasetPanel id={datasetId} />}
      </div>
    </div>
  );
}

function DatasetPanel({ id }: { id: string }) {
  const { can } = useAuth();
  const { data, isLoading, error } = useDataset(id);
  const [tab, setTab] = useState<Tab>("metrics");
  const [selected, setSelected] = useState<SemanticField | null>(null);
  const [adding, setAdding] = useState(false);
  const versions = useQuery({
    queryKey: ["dataset-versions", id],
    queryFn: () => api.get<{ version: number; note: string; created_at: string }[]>(`/api/datasets/${id}/versions`),
    enabled: tab === "versions",
  });

  if (isLoading) return <PageLoader />;
  if (error || !data) return <ErrorState body={(error as Error | null)?.message} />;

  const metrics = data.fields.filter((f) => f.role === "measure");
  const rls = (data.model.row_level_security as { field: string; user_attribute: string; exempt_roles: string[] }[] | undefined) ?? [];

  return (
    <div className="stack">
      <div className="iq-card">
        <div className="iq-card__header">
          <div>
            <h2>{data.name}</h2>
            <div className="muted" style={{ fontSize: 12, marginTop: 2 }}>
              {data.description || "No description"} · version {data.version}
            </div>
          </div>
          {can("manage_datasets") && (
            <Button icon={<Plus />} onClick={() => setAdding(true)}>
              Calculated field
            </Button>
          )}
        </div>
        {data.problems.length > 0 && (
          <div style={{ padding: "10px 16px", background: "var(--critical-soft)", color: "var(--critical-ink)", fontSize: 13 }} role="alert">
            {data.problems.length} field(s) fail validation: {data.problems.map((p) => p.message).join("; ")}
          </div>
        )}
        <div style={{ padding: "0 16px" }}>
          <Tabs<Tab>
            label="Dataset sections"
            value={tab}
            onChange={setTab}
            tabs={[
              { value: "metrics", label: "Measures", count: metrics.length },
              { value: "fields", label: "All fields", count: data.fields.length },
              { value: "hierarchies", label: "Hierarchies", count: data.hierarchies.length },
              { value: "security", label: "Row-level security", count: rls.length },
              { value: "versions", label: "Versions" },
            ]}
          />
        </div>
        {(tab === "metrics" || tab === "fields") && (
          <FieldTable fields={tab === "metrics" ? metrics : data.fields} onSelect={setSelected} />
        )}
        {tab === "hierarchies" && (
          <div className="iq-card__body">
            {data.hierarchies.length === 0 ? (
              <EmptyState compact title="No hierarchies" body="Hierarchies enable drill-down from coarse to fine levels." />
            ) : (
              <div className="stack">
                {data.hierarchies.map((h) => (
                  <div key={h.id}>
                    <strong style={{ fontSize: 13 }}>{h.name}</strong>
                    <div className="row" style={{ marginTop: 6 }}>
                      {h.levels.map((l, i) => (
                        <span key={l} className="row" style={{ gap: 6 }}>
                          {i > 0 && <span className="muted">›</span>}
                          <Badge>{data.fields.find((f) => f.id === l)?.name ?? l}</Badge>
                        </span>
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
        {tab === "security" && (
          <div className="iq-card__body">
            {rls.length === 0 ? (
              <EmptyState compact title="No row-level security" body="Every user who can open a dashboard on this dataset sees all rows." />
            ) : (
              <div className="stack">
                {rls.map((r) => (
                  <p key={r.field} style={{ fontSize: 13 }}>
                    Users see only rows where <strong>{data.fields.find((f) => f.id === r.field)?.name ?? r.field}</strong> matches their{" "}
                    <code>{r.user_attribute}</code> attribute. Exempt roles: {r.exempt_roles.join(", ") || "none"}. Users without the attribute see no rows.
                  </p>
                ))}
              </div>
            )}
          </div>
        )}
        {tab === "versions" && (
          <DataTable
            rows={versions.data ?? []}
            rowKey={(r) => String(r.version)}
            dense
            columns={[
              { key: "v", header: "Version", value: (r) => r.version, numeric: true, width: 90 },
              { key: "note", header: "Change", value: (r) => r.note },
              { key: "at", header: "When", value: (r) => r.created_at, render: (r) => relativeTime(r.created_at), width: 140 },
            ]}
          />
        )}
      </div>
      {selected && <FieldDialog field={selected} dataset={data} onClose={() => setSelected(null)} />}
      {adding && <CalcFieldDialog dataset={data} onClose={() => setAdding(false)} />}
    </div>
  );
}

function FieldTable({ fields, onSelect }: { fields: SemanticField[]; onSelect: (f: SemanticField) => void }) {
  return (
    <DataTable<SemanticField>
      rows={fields}
      rowKey={(f) => f.id}
      searchable={fields.length > 8}
      onRowClick={onSelect}
      dense
      columns={[
        {
          key: "name",
          header: "Name",
          value: (f) => f.name,
          render: (f) => (
            <span className="cell-title">
              {f.formula ? <Calculator aria-hidden /> : <Columns3 aria-hidden />}
              {f.name}
              {f.certified && <Badge tone="good" icon={<BadgeCheck />}>Certified</Badge>}
            </span>
          ),
        },
        { key: "role", header: "Role", value: (f) => f.role, width: 110, render: (f) => <span className="muted">{f.role}</span> },
        { key: "type", header: "Type", value: (f) => f.data_type, width: 100, render: (f) => <span className="muted">{f.data_type}</span> },
        {
          key: "def",
          header: "Definition",
          value: (f) => f.formula ?? f.column ?? "",
          render: (f) => <code style={{ fontSize: 12 }}>{f.formula ?? f.column}</code>,
        },
        { key: "used", header: "Used by", value: (f) => f.used_by?.length ?? 0, numeric: true, width: 90 },
      ]}
    />
  );
}

function FieldDialog({ field, dataset, onClose }: { field: SemanticField; dataset: DatasetDetail; onClose: () => void }) {
  const name = (id: string) => dataset.fields.find((f) => f.id === id)?.name ?? id;
  return (
    <Dialog open onClose={onClose} title={field.name} description={field.description || undefined} wide>
      <dl className="kv">
        <dt>Identifier</dt>
        <dd>
          <code>{field.id}</code>
        </dd>
        <dt>Role / type</dt>
        <dd>
          {field.role} · {field.data_type} · {field.level === "agg" ? "aggregate" : field.level === "table" ? "table calculation" : "row level"}
        </dd>
        {field.default_aggregation && (
          <>
            <dt>Default aggregation</dt>
            <dd>{field.default_aggregation}</dd>
          </>
        )}
        <dt>Definition</dt>
        <dd>
          <code className="formula">{field.formula ?? field.column}</code>
        </dd>
        <dt>Depends on</dt>
        <dd>{field.depends_on?.length ? field.depends_on.map(name).join(", ") : "—"}</dd>
        <dt>Used by</dt>
        <dd>{field.used_by?.length ? field.used_by.map(name).join(", ") : "—"}</dd>
        <dt>Physical columns</dt>
        <dd>{field.columns?.length ? field.columns.map((c) => <code key={c} style={{ marginRight: 8 }}>{c}</code>) : "—"}</dd>
      </dl>
    </Dialog>
  );
}

function CalcFieldDialog({ dataset, onClose }: { dataset: DatasetDetail; onClose: () => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [name, setName] = useState("");
  const [formula, setFormula] = useState("");
  const [description, setDescription] = useState("");
  const [certified, setCertified] = useState("no");
  const [check, setCheck] = useState<CalcValidation | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!formula.trim()) {
      setCheck(null);
      return;
    }
    const t = window.setTimeout(async () => {
      try {
        setCheck(await api.post<CalcValidation>("/api/calc/validate", { dataset_id: dataset.id, formula }));
      } catch (e) {
        setCheck({ ok: false, error: { code: "request", message: (e as Error).message, start: null, end: null } });
      }
    }, 300);
    return () => window.clearTimeout(t);
  }, [formula, dataset.id]);

  const id = useMemo(() => name.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_|_$/g, "").replace(/^(\d)/, "f_$1"), [name]);
  const duplicate = dataset.fields.some((f) => f.id === id || f.name.toLowerCase() === name.trim().toLowerCase());

  const save = async () => {
    if (!check?.ok) return;
    setSaving(true);
    try {
      const field: Partial<SemanticField> = {
        id,
        name: name.trim(),
        role: check.role,
        data_type: (check.data_type === "null" ? "real" : check.data_type) as SemanticField["data_type"],
        formula,
        description,
        certified: certified === "yes",
      };
      const model = { ...dataset.model, fields: [...dataset.model.fields, field] };
      await api.put(`/api/datasets/${dataset.id}`, { model, note: `Added calculated field ${name.trim()}` });
      await qc.invalidateQueries({ queryKey: keys.dataset(dataset.id) });
      await qc.invalidateQueries({ queryKey: keys.datasets });
      toast(`${name.trim()} added`, "good");
      onClose();
    } catch (e) {
      toast((e as Error).message, "critical");
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog
      open
      onClose={onClose}
      title="New calculated field"
      description="Formulas use Tableau-compatible syntax and are validated against this dataset."
      wide
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" loading={saving} disabled={!check?.ok || !name.trim() || duplicate} onClick={save}>
            Save as new version
          </Button>
        </>
      }
    >
      <div className="stack">
        <div className="grid-2">
          <Field label="Name" error={duplicate ? "A field with this name already exists" : undefined}>
            {(fid) => <Input id={fid} value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Average order value" />}
          </Field>
          <Field label="Certified metric" hint="Certified metrics are marked as the canonical definition.">
            {(fid) => (
              <Select id={fid} value={certified} onChange={(e) => setCertified(e.target.value)} options={[{ value: "no", label: "No" }, { value: "yes", label: "Yes" }]} />
            )}
          </Field>
        </div>
        <FormulaEditor value={formula} onChange={setFormula} check={check} fields={dataset.fields} />
        <Field label="Description">{(fid) => <Input id={fid} value={description} onChange={(e) => setDescription(e.target.value)} />}</Field>
      </div>
    </Dialog>
  );
}

/** Formula textarea with validation feedback and click-to-insert field names. */
export function FormulaEditor({
  value,
  onChange,
  check,
  fields,
}: {
  value: string;
  onChange: (v: string) => void;
  check: CalcValidation | null;
  fields: SemanticField[];
}) {
  const err = check && !check.ok ? check.error : null;
  return (
    <div className="stack" style={{ gap: 8 }}>
      <Field
        label="Formula"
        error={err ? `${err.message}${err.start !== null ? ` (at character ${err.start + 1})` : ""}` : undefined}
        hint={check?.ok ? `Valid · ${check.data_type} · ${check.level === "agg" ? "aggregate (measure)" : check.level === "table" ? "table calculation" : "row level"}` : "Example: SUM([Profit]) / SUM([Sales])"}
      >
        {(fid) => <Textarea id={fid} code rows={4} value={value} onChange={(e) => onChange(e.target.value)} aria-invalid={!!err} spellCheck={false} />}
      </Field>
      <div className="row" style={{ gap: 6 }}>
        {fields.slice(0, 24).map((f) => (
          <button key={f.id} type="button" className="iq-badge" style={{ cursor: "pointer" }} onClick={() => onChange(`${value}${value && !value.endsWith(" ") ? " " : ""}[${f.name}]`)}>
            {f.name}
          </button>
        ))}
      </div>
    </div>
  );
}
