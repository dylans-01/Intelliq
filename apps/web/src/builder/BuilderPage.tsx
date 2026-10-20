import { useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, BarChart3, Eye, Redo2, SlidersHorizontal, Type, Undo2, Upload, Variable } from "lucide-react";
import { useCallback, useEffect, useMemo, useReducer, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api } from "@/api/client";
import { keys, useDashboard, useDataset, useDatasets } from "@/api/hooks";
import type { CalcValidation, DashboardDetail, DashboardSpec, SemanticField } from "@/api/types";
import { useAuth } from "@/auth/AuthProvider";
import { DashboardCanvas } from "@/dashboard/DashboardCanvas";
import { initialState, reducer } from "@/dashboard/runtime";
import { Button, Dialog, EmptyState, ErrorState, Field, IconButton, Input, PageLoader, Select, useToast } from "@/design-system";
import { FormulaEditor } from "@/pages/MetricsPage";
import { ColorRegistry } from "@/viz/colors";
import { BuilderCanvas } from "./BuilderCanvas";
import { FieldPanel } from "./FieldPanel";
import { FilterInspector, ParameterInspector, Section, TextInspector, WorksheetInspector } from "./Inspector";
import { addField, addFilter, addFormulaField, addParameter, addText, addWorksheet, applyLayout, emptySpec, removeItem, updateWorksheet } from "./specOps";
import { useHistory } from "./useHistory";
import "./builder.css";

export default function BuilderPage() {
  const { id } = useParams();
  const isNew = !id;
  const existing = useDashboard(id ?? "", true);
  const datasets = useDatasets();

  if (!isNew && existing.isLoading) return <PageLoader />;
  if (!isNew && existing.error) return <ErrorState body={(existing.error as Error).message} />;
  if (datasets.isLoading) return <PageLoader />;
  if (!datasets.data?.length)
    return <EmptyState title="No datasets available" body="An administrator needs to create a dataset before dashboards can be built." />;
  return <Builder detail={isNew ? null : existing.data!} defaultDataset={datasets.data[0].id} datasets={datasets.data.map((d) => ({ id: d.id, name: d.name }))} />;
}

function Builder({ detail, defaultDataset, datasets }: { detail: DashboardDetail | null; defaultDataset: string; datasets: { id: string; name: string }[] }) {
  const { can } = useAuth();
  const navigate = useNavigate();
  const toast = useToast();
  const qc = useQueryClient();
  const initial = useMemo(() => detail?.spec ?? emptySpec(), [detail]);
  const history = useHistory<DashboardSpec>(initial);
  const spec = history.value;
  const [savedSpec, setSavedSpec] = useState<DashboardSpec>(initial);
  const [selected, setSelected] = useState<string | null>(null);
  const [preview, setPreview] = useState(false);
  const [saving, setSaving] = useState(false);
  const [calcOpen, setCalcOpen] = useState(false);
  const [paramOpen, setParamOpen] = useState(false);
  const [filterOpen, setFilterOpen] = useState(false);
  const selectedItem = spec.layout.items.find((i) => i.id === selected) ?? null;
  const selectedWs = selectedItem?.kind === "worksheet" ? spec.worksheets.find((w) => w.id === selectedItem.ref) ?? null : null;
  const [activeDataset, setActiveDataset] = useState(spec.worksheets[0]?.dataset_id ?? defaultDataset);
  const datasetId = selectedWs?.dataset_id ?? activeDataset;
  const dataset = useDataset(datasetId);
  const dirty = JSON.stringify(spec) !== JSON.stringify(savedSpec);
  const colors = useMemo(() => new ColorRegistry(), []);
  const [view, dispatch] = useReducer(reducer(spec), spec, initialState);

  const drillLevels = useMemo(() => {
    const out: Record<string, string[]> = {};
    for (const w of spec.worksheets) {
      if (!w.drill_hierarchy || w.dataset_id !== dataset.data?.id) continue;
      const h = dataset.data.hierarchies.find((x) => x.id === w.drill_hierarchy);
      if (h) out[w.id] = h.levels;
    }
    return out;
  }, [spec.worksheets, dataset.data]);

  const { set: setHistory, undo, redo } = history;
  const setSpec = useCallback((fn: (s: DashboardSpec) => DashboardSpec, coalesce?: string) => setHistory(fn, coalesce), [setHistory]);

  // Undo / redo shortcuts and unsaved-change protection.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement;
      if (target.closest("input, textarea, select")) return;
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "z") {
        e.preventDefault();
        if (e.shiftKey) redo();
        else undo();
      }
    };
    const onUnload = (e: BeforeUnloadEvent) => {
      if (dirty) e.preventDefault();
    };
    window.addEventListener("keydown", onKey);
    window.addEventListener("beforeunload", onUnload);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("beforeunload", onUnload);
    };
  }, [undo, redo, dirty]);

  const save = async (): Promise<string | null> => {
    setSaving(true);
    try {
      let saved: DashboardDetail;
      if (detail) saved = await api.put<DashboardDetail>(`/api/dashboards/${detail.id}`, { spec, note: "Saved from editor" });
      else saved = await api.post<DashboardDetail>("/api/dashboards", { spec });
      setSavedSpec(spec);
      await qc.invalidateQueries({ queryKey: keys.dashboards });
      await qc.invalidateQueries({ queryKey: ["dashboard", saved.id] });
      toast(`Saved version ${saved.current_version}`, "good");
      if (!detail) navigate(`/dashboards/${saved.id}/edit`, { replace: true });
      return saved.id;
    } catch (e) {
      toast((e as Error).message, "critical");
      return null;
    } finally {
      setSaving(false);
    }
  };

  const publish = async () => {
    const savedId = dirty || !detail ? await save() : detail.id;
    if (!savedId) return;
    try {
      await api.post(`/api/dashboards/${savedId}/publish`, {});
      await qc.invalidateQueries({ queryKey: keys.dashboards });
      await qc.invalidateQueries({ queryKey: ["dashboard", savedId] });
      toast("Published", "good");
      navigate(`/dashboards/${savedId}`);
    } catch (e) {
      toast((e as Error).message, "critical");
    }
  };

  const close = () => {
    if (dirty && !window.confirm("Discard unsaved changes?")) return;
    navigate(detail ? `/dashboards/${detail.id}?draft=1` : "/dashboards");
  };

  const addChart = () => {
    const { spec: next, id } = addWorksheet(spec, datasetId);
    history.set(next);
    setSelected(`i_${id}`);
  };

  const onAddField = (f: SemanticField) => {
    if (!selectedWs) return;
    setSpec((s) => updateWorksheet(s, selectedWs.id, (w) => addField(w, f)));
  };

  const onAddFilter = (f: SemanticField) => {
    const { spec: next, id } = addFilter(spec, datasetId, f);
    history.set(next);
    setSelected(`i_${id}`);
  };

  const inspector = () => {
    if (selectedWs)
      return (
        <WorksheetInspector
          spec={spec}
          ws={selectedWs}
          dataset={dataset.data}
          update={(fn, coalesce) => setSpec((s) => updateWorksheet(s, selectedWs.id, fn), coalesce)}
          setSpec={(fn) => setSpec(fn)}
        />
      );
    if (selectedItem?.kind === "filter") {
      const f = spec.filters.find((x) => x.id === selectedItem.ref);
      return f ? <FilterInspector spec={spec} filter={f} dataset={dataset.data} setSpec={setSpec} /> : null;
    }
    if (selectedItem?.kind === "parameter") {
      const p = spec.parameters.find((x) => x.id === selectedItem.ref);
      return p ? <ParameterInspector parameter={p} setSpec={setSpec} /> : null;
    }
    if (selectedItem?.kind === "text")
      return (
        <TextInspector
          text={selectedItem.text ?? ""}
          onChange={(text) => setSpec((s) => ({ ...s, layout: { ...s.layout, items: s.layout.items.map((i) => (i.id === selectedItem.id ? { ...i, text } : i)) } }), `text-${selectedItem.id}`)}
        />
      );
    return (
      <>
        <Section title="Dashboard">
          <div className="stack" style={{ gap: 8 }}>
            <Field label="Description">
              {(fid) => <Input id={fid} size_="sm" value={spec.description ?? ""} onChange={(e) => setSpec((s) => ({ ...s, description: e.target.value }), "description")} />}
            </Field>
            <Field label="Data for new charts">
              {(fid) => <Select id={fid} size_="sm" value={activeDataset} onChange={(e) => setActiveDataset(e.target.value)} options={datasets.map((d) => ({ value: d.id, label: d.name }))} />}
            </Field>
          </div>
        </Section>
        <Section title="Interactions">
          {spec.actions.length === 0 ? (
            <p className="bld-hint">Select a chart and enable “Selecting marks filters other charts” to add cross-filtering.</p>
          ) : (
            spec.actions.map((a) => (
              <div key={a.id} className="bld-filter-row">
                <span>
                  {a.type === "filter" ? "Filter" : "Highlight"} from {spec.worksheets.find((w) => w.id === a.source)?.title || a.source}
                </span>
              </div>
            ))
          )}
        </Section>
        <p className="bld-hint" style={{ padding: "0 16px" }}>
          Select a tile to configure it. Drag tiles by their handle; resize from the bottom-right corner.
        </p>
      </>
    );
  };

  return (
    <div className="bld">
      <header className="bld-top">
        <IconButton label="Close editor" icon={<ArrowLeft />} onClick={close} />
        <input className="bld-title" aria-label="Dashboard title" value={spec.title} onChange={(e) => setSpec((s) => ({ ...s, title: e.target.value || "Untitled dashboard" }), "title")} />
        <span className="bld-status">{saving ? "Saving…" : dirty ? "Unsaved changes" : detail ? `Saved · v${detail.current_version}` : "Not saved"}</span>
        <div className="bld-top__group">
          <IconButton label="Undo (Ctrl+Z)" icon={<Undo2 />} disabled={!history.canUndo} onClick={history.undo} />
          <IconButton label="Redo (Ctrl+Shift+Z)" icon={<Redo2 />} disabled={!history.canRedo} onClick={history.redo} />
        </div>
        <div className="bld-top__group">
          <Button size="sm" icon={<BarChart3 />} onClick={addChart} disabled={preview}>
            Add chart
          </Button>
          <Button size="sm" icon={<SlidersHorizontal />} disabled={preview || !dataset.data} onClick={() => setFilterOpen(true)}>
            Filter
          </Button>
          <Button size="sm" icon={<Variable />} disabled={preview} onClick={() => setParamOpen(true)}>
            Parameter
          </Button>
          <Button
            size="sm"
            icon={<Type />}
            disabled={preview}
            onClick={() => {
              const { spec: next, id } = addText(spec);
              history.set(next);
              setSelected(id);
            }}
          >
            Text
          </Button>
        </div>
        <div className="bld-top__right">
          <Button size="sm" variant="ghost" icon={<Eye />} aria-pressed={preview} onClick={() => setPreview((p) => !p)}>
            {preview ? "Back to editing" : "Preview"}
          </Button>
          <Button size="sm" loading={saving} disabled={!dirty && !!detail} onClick={() => void save()}>
            Save
          </Button>
          {can("publish_dashboards") && (
            <Button size="sm" variant="primary" icon={<Upload />} onClick={() => void publish()}>
              Publish
            </Button>
          )}
        </div>
      </header>

      {preview ? (
        <main className="bld-preview">
          <h1 style={{ marginBottom: 16 }}>{spec.title}</h1>
          <DashboardCanvas spec={spec} draft preview state={view} dispatch={dispatch} drillLevels={drillLevels} />
        </main>
      ) : (
        <div className="bld-body">
          <aside className="bld-left" aria-label="Fields">
            <div className="bld-left__head">
              <Select size_="sm" aria-label="Dataset" value={datasetId} disabled={!!selectedWs} onChange={(e) => setActiveDataset(e.target.value)} options={datasets.map((d) => ({ value: d.id, label: d.name }))} />
              {!selectedWs && <p className="bld-hint">Select a chart to add fields to it.</p>}
            </div>
            {dataset.data ? (
              <FieldPanel dataset={dataset.data} onAdd={onAddField} onAddFilter={onAddFilter} onNewCalc={() => setCalcOpen(true)} canAdd={!!selectedWs} />
            ) : (
              <PageLoader />
            )}
          </aside>
          <main className="bld-center">
            <BuilderCanvas
              spec={spec}
              selected={selected}
              onSelect={setSelected}
              onLayout={(l) => {
                const next = applyLayout(spec, l);
                if (JSON.stringify(next.layout) !== JSON.stringify(spec.layout)) history.set(next, "layout");
              }}
              onRemove={(itemId) => {
                setSpec((s) => removeItem(s, itemId));
                if (selected === itemId) setSelected(null);
              }}
              state={view}
              dispatch={dispatch}
              colors={colors}
              drillLevels={drillLevels}
            />
          </main>
          <aside className="bld-right" aria-label="Inspector">
            {inspector()}
          </aside>
        </div>
      )}
      {calcOpen && selectedWs && dataset.data && (
        <AdhocCalcDialog
          datasetId={dataset.data.id}
          fields={dataset.data.fields}
          parameters={spec.parameters.map((p) => ({ name: p.name, data_type: p.data_type }))}
          onClose={() => setCalcOpen(false)}
          onAdd={(formula, label, role) => {
            setSpec((s) => updateWorksheet(s, selectedWs.id, (w) => addFormulaField(w, formula, label, role)));
            setCalcOpen(false);
          }}
        />
      )}
      {filterOpen && dataset.data && (
        <AddFilterDialog
          fields={dataset.data.fields.filter((f) => !f.hidden && f.level !== "agg" && f.level !== "table")}
          onClose={() => setFilterOpen(false)}
          onAdd={(f) => {
            onAddFilter(f);
            setFilterOpen(false);
          }}
        />
      )}
      {paramOpen && (
        <NewParameterDialog
          onClose={() => setParamOpen(false)}
          onCreate={(name, dataType) => {
            const { spec: next, id } = addParameter(spec, { name, data_type: dataType, default: null, allowed: { type: "all" }, control: "input" });
            history.set(next);
            setSelected(`i_${id}`);
            setParamOpen(false);
          }}
        />
      )}
    </div>
  );
}

function AdhocCalcDialog({
  datasetId,
  fields,
  parameters,
  onClose,
  onAdd,
}: {
  datasetId: string;
  fields: SemanticField[];
  parameters: { name: string; data_type: string }[];
  onClose: () => void;
  onAdd: (formula: string, label: string, role: "dimension" | "measure") => void;
}) {
  const [label, setLabel] = useState("");
  const [formula, setFormula] = useState("");
  const [check, setCheck] = useState<CalcValidation | null>(null);
  useEffect(() => {
    if (!formula.trim()) return setCheck(null);
    const t = window.setTimeout(async () => {
      try {
        setCheck(await api.post<CalcValidation>("/api/calc/validate", { dataset_id: datasetId, formula, parameters }));
      } catch (e) {
        setCheck({ ok: false, error: { code: "request", message: (e as Error).message, start: null, end: null } });
      }
    }, 300);
    return () => window.clearTimeout(t);
  }, [formula, datasetId, parameters]);
  return (
    <Dialog
      open
      onClose={onClose}
      title="New calculation"
      description="Used only in this chart. Ask an administrator to promote reusable logic to a certified metric."
      wide
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" disabled={!check?.ok || !label.trim()} onClick={() => onAdd(formula, label.trim(), check!.role!)}>
            Add to chart
          </Button>
        </>
      }
    >
      <div className="stack">
        <Field label="Name">{(id) => <Input id={id} value={label} onChange={(e) => setLabel(e.target.value)} placeholder="e.g. Margin %" />}</Field>
        <FormulaEditor value={formula} onChange={setFormula} check={check} fields={fields} />
      </div>
    </Dialog>
  );
}

function NewParameterDialog({ onClose, onCreate }: { onClose: () => void; onCreate: (name: string, dataType: "integer" | "real" | "string" | "date") => void }) {
  const [name, setName] = useState("");
  const [type, setType] = useState<"integer" | "real" | "string" | "date">("integer");
  return (
    <Dialog
      open
      onClose={onClose}
      title="New parameter"
      description="Parameters are viewer-controlled values that calculations reference as [Parameters].[Name]."
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" disabled={!name.trim()} onClick={() => onCreate(name.trim(), type)}>
            Create
          </Button>
        </>
      }
    >
      <div className="grid-2">
        <Field label="Name">{(id) => <Input id={id} value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Top N" />}</Field>
        <Field label="Type">
          {(id) => (
            <Select id={id} value={type} onChange={(e) => setType(e.target.value as typeof type)} options={[{ value: "integer", label: "Integer" }, { value: "real", label: "Decimal" }, { value: "string", label: "Text" }, { value: "date", label: "Date" }]} />
          )}
        </Field>
      </div>
    </Dialog>
  );
}

function AddFilterDialog({ fields, onClose, onAdd }: { fields: SemanticField[]; onClose: () => void; onAdd: (f: SemanticField) => void }) {
  const [fieldId, setFieldId] = useState(fields[0]?.id ?? "");
  const field = fields.find((f) => f.id === fieldId);
  return (
    <Dialog
      open
      onClose={onClose}
      title="Add dashboard filter"
      description="Interactive filters let viewers narrow every chart on the same dataset."
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" disabled={!field} onClick={() => field && onAdd(field)}>
            Add filter
          </Button>
        </>
      }
    >
      <Field label="Field">
        {(id) => <Select id={id} value={fieldId} onChange={(e) => setFieldId(e.target.value)} options={fields.map((f) => ({ value: f.id, label: f.name }))} />}
      </Field>
    </Dialog>
  );
}
