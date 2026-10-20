import { ArrowDown, ArrowUp, Plus, Settings2, Trash2, X } from "lucide-react";
import { useCallback, useRef, useState, type DragEvent, type ReactNode } from "react";
import type {
  Aggregation,
  DashboardFilter,
  DashboardSpec,
  DatasetDetail,
  DatePart,
  FilterOp,
  Parameter,
  QueryFilter,
  QuickTableCalcType,
  Scalar,
  TimeUnit,
  VizField,
  Worksheet,
} from "@/api/types";
import { Button, Checkbox, Field, IconButton, Input, Popover, Select, Textarea } from "@/design-system";
import { PLUGINS, plugin } from "@/viz/plugins/registry";
import { FIELD_MIME } from "./FieldPanel";
import { addAction, addField, changeType, removeField, updateField } from "./specOps";

const AGGS: { value: Aggregation; label: string }[] = [
  { value: "sum", label: "Sum" }, { value: "avg", label: "Average" }, { value: "min", label: "Minimum" }, { value: "max", label: "Maximum" },
  { value: "count", label: "Count" }, { value: "countd", label: "Count distinct" }, { value: "median", label: "Median" },
  { value: "stdev", label: "Std. deviation" }, { value: "var", label: "Variance" }, { value: "attr", label: "Attribute" },
];
const TIME_UNITS: TimeUnit[] = ["year", "quarter", "month", "week", "day"];
const DATE_PARTS: DatePart[] = ["year", "quarter", "month", "weekday", "day"];
const TABLE_CALCS: { value: QuickTableCalcType; label: string }[] = [
  { value: "running_total", label: "Running total" }, { value: "percent_of_total", label: "Percent of total" },
  { value: "difference", label: "Difference from previous" }, { value: "percent_difference", label: "% difference from previous" },
  { value: "rank", label: "Rank" }, { value: "rank_dense", label: "Dense rank" },
];

export function Section({ title, children, actions }: { title: string; children: ReactNode; actions?: ReactNode }) {
  return (
    <section className="bld-section">
      <div className="bld-section__head">
        <h3>{title}</h3>
        {actions}
      </div>
      {children}
    </section>
  );
}

// ---------------------------------------------------------------- worksheet
export function WorksheetInspector({
  spec,
  ws,
  dataset,
  update,
  setSpec,
}: {
  spec: DashboardSpec;
  ws: Worksheet;
  dataset: DatasetDetail | undefined;
  update: (fn: (w: Worksheet) => Worksheet, coalesce?: string) => void;
  setSpec: (fn: (s: DashboardSpec) => DashboardSpec) => void;
}) {
  const p = plugin(ws.viz.type);
  const fieldById = (id?: string | null) => dataset?.fields.find((f) => f.id === id);
  const label = (f: VizField) => f.use.label || fieldById(f.use.field)?.name || f.use.formula || f.key;
  const options = ws.viz.options ?? {};
  const setOpt = (patch: Record<string, unknown>) => update((w) => ({ ...w, viz: { ...w.viz, options: { ...w.viz.options, ...patch } } }));
  const sourced = spec.actions.filter((a) => a.source === ws.id);

  const onDrop = (channel: VizField["channel"]) => (e: DragEvent) => {
    e.preventDefault();
    const f = fieldById(e.dataTransfer.getData(FIELD_MIME));
    const spec = p.channels.find((c) => c.channel === channel);
    if (!f || !spec?.roles.includes(f.role)) return;
    update((w) => addField(w, f, channel));
  };

  return (
    <>
      <Section title="Title">
        <Input aria-label="Title" value={ws.title ?? ""} onChange={(e) => update((w) => ({ ...w, title: e.target.value }), `title-${ws.id}`)} />
        <Input aria-label="Subtitle" placeholder="Subtitle (optional)" value={ws.subtitle ?? ""} onChange={(e) => update((w) => ({ ...w, subtitle: e.target.value }), `subtitle-${ws.id}`)} style={{ marginTop: 6 }} />
      </Section>

      <Section title="Visualization">
        <div className="bld-types" role="radiogroup" aria-label="Chart type">
          {PLUGINS.map((pl) => (
            <button key={pl.type} role="radio" aria-checked={ws.viz.type === pl.type} className="bld-type" title={pl.description} onClick={() => update((w) => changeType(w, pl.type))}>
              {pl.label}
            </button>
          ))}
        </div>
      </Section>

      <Section title="Fields">
        {p.channels.map((c) => {
          const placed = ws.viz.fields.filter((f) => f.channel === c.channel);
          return (
            <div key={c.channel} className="bld-shelf" onDragOver={(e) => e.preventDefault()} onDrop={onDrop(c.channel)}>
              <div className="bld-shelf__label">
                {c.label}
                {c.required && placed.length === 0 && <span className="bld-shelf__req">required</span>}
              </div>
              <div className="bld-shelf__chips">
                {placed.map((f) => (
                  <FieldChip key={f.key} field={f} label={label(f)} ws={ws} dataType={fieldById(f.use.field)?.data_type} isAggregate={fieldById(f.use.field)?.level === "agg"} update={update} />
                ))}
                {placed.length === 0 && <span className="bld-shelf__empty">{c.hint ?? `Drop ${c.roles.join(" or ")}s here`}</span>}
              </div>
            </div>
          );
        })}
        {p.ready(ws.viz) && <p className="bld-hint">{p.ready(ws.viz)}</p>}
      </Section>

      <Section title="Options">
        <div className="stack" style={{ gap: 8 }}>
          {ws.viz.type === "bar" && (
            <Select size_="sm" aria-label="Orientation" value={options.orientation ?? "vertical"} onChange={(e) => setOpt({ orientation: e.target.value })} options={[{ value: "vertical", label: "Vertical bars" }, { value: "horizontal", label: "Horizontal bars" }]} />
          )}
          {(ws.viz.type === "bar" || ws.viz.type === "area") && <Checkbox label="Stack series" checked={!!options.stacked} onChange={(v) => setOpt({ stacked: v })} />}
          {ws.viz.type === "bar" && <Checkbox label="Show value labels" checked={!!options.show_labels} onChange={(v) => setOpt({ show_labels: v })} />}
          {(ws.viz.type === "line" || ws.viz.type === "area") && <Checkbox label="Smooth lines" checked={!!options.smooth} onChange={(v) => setOpt({ smooth: v })} />}
          {(ws.viz.type === "line" || ws.viz.type === "area") && <Checkbox label="Show points" checked={!!options.show_points} onChange={(v) => setOpt({ show_points: v })} />}
          {ws.viz.type === "pie" && <Checkbox label="Donut" checked={options.donut !== false} onChange={(v) => setOpt({ donut: v })} />}
          {["bar", "line", "area", "scatter", "pie"].includes(ws.viz.type) && <Checkbox label="Show legend" checked={options.show_legend !== false} onChange={(v) => setOpt({ show_legend: v })} />}
          <Field label="Row limit" hint="Leave empty for no limit">
            {(id) => (
              <Input
                id={id}
                size_="sm"
                type="number"
                min={1}
                value={ws.viz.limit ?? ""}
                onChange={(e) => update((w) => ({ ...w, viz: { ...w.viz, limit: e.target.value ? Math.max(1, Number(e.target.value)) : null } }), `limit-${ws.id}`)}
              />
            )}
          </Field>
          {dataset && dataset.hierarchies.length > 0 && (
            <Field label="Drill-down hierarchy" hint="Clicking a mark drills into the next level">
              {(id) => (
                <Select id={id} size_="sm" value={ws.drill_hierarchy ?? ""} onChange={(e) => update((w) => ({ ...w, drill_hierarchy: e.target.value || null }))}>
                  <option value="">None</option>
                  {dataset.hierarchies.map((h) => (
                    <option key={h.id} value={h.id}>
                      {h.name}
                    </option>
                  ))}
                </Select>
              )}
            </Field>
          )}
        </div>
      </Section>

      <Section title="Interactions">
        <div className="stack" style={{ gap: 8 }}>
          {(["filter", "highlight"] as const).map((type) => {
            const existing = sourced.find((a) => a.type === type);
            return (
              <Checkbox
                key={type}
                label={type === "filter" ? "Selecting marks filters other charts" : "Selecting marks highlights other charts"}
                checked={!!existing}
                onChange={(on) =>
                  setSpec((s) => (on ? addAction(s, type, ws.id) : { ...s, actions: s.actions.filter((a) => a !== existing) }))
                }
              />
            );
          })}
        </div>
      </Section>

      <WorksheetFilters ws={ws} dataset={dataset} update={update} />
    </>
  );
}

function FieldChip({
  field,
  label,
  ws,
  dataType,
  isAggregate,
  update,
}: {
  field: VizField;
  label: string;
  ws: Worksheet;
  dataType?: string;
  isAggregate?: boolean;
  update: (fn: (w: Worksheet) => Worksheet) => void;
}) {
  const [open, setOpen] = useState(false);
  const anchor = useRef<HTMLButtonElement>(null);
  const close = useCallback(() => setOpen(false), []);
  const u = field.use;
  const temporal = dataType === "date" || dataType === "datetime";
  const sort = ws.viz.sort?.find((s) => s.key === field.key);
  const prefix =
    field.role === "measure"
      ? u.table_calc
        ? "Δ "
        : u.aggregation && u.aggregation !== "none"
          ? `${AGGS.find((a) => a.value === u.aggregation)?.label.slice(0, 3).toUpperCase()}(`
          : ""
      : u.time_unit
        ? `${u.time_unit.toUpperCase()}(`
        : u.date_part
          ? `${u.date_part.toUpperCase()}(`
          : "";
  const suffix = prefix.endsWith("(") ? ")" : "";

  const setSort = (direction: "asc" | "desc" | null) =>
    update((w) => ({ ...w, viz: { ...w.viz, sort: direction ? [{ key: field.key, direction }] : (w.viz.sort ?? []).filter((s) => s.key !== field.key) } }));

  return (
    <>
      <span className={`bld-chip bld-chip--${field.role}`}>
        <button ref={anchor} className="bld-chip__main" onClick={() => setOpen((o) => !o)} aria-label={`Configure ${label}`} aria-expanded={open}>
          <span className="bld-chip__text">
            {prefix}
            {label}
            {suffix}
          </span>
          {sort && (sort.direction === "asc" ? <ArrowUp aria-label="sorted ascending" /> : <ArrowDown aria-label="sorted descending" />)}
          <Settings2 aria-hidden />
        </button>
        <button className="bld-chip__remove" aria-label={`Remove ${label}`} onClick={() => update((w) => removeField(w, field.key))}>
          <X />
        </button>
      </span>
      <Popover open={open} onClose={close} anchor={anchor} width={260} label={`${label} settings`}>
        <div className="stack" style={{ gap: 10, padding: 8 }}>
          {field.role === "measure" && !isAggregate && !u.formula && (
            <Field label="Aggregation">
              {(id) => (
                <Select id={id} size_="sm" value={u.aggregation && u.aggregation !== "none" ? u.aggregation : ""} onChange={(e) => update((w) => updateField(w, field.key, { aggregation: (e.target.value || null) as Aggregation | null }))}>
                  <option value="">Default</option>
                  {AGGS.map((a) => (
                    <option key={a.value} value={a.value}>
                      {a.label}
                    </option>
                  ))}
                </Select>
              )}
            </Field>
          )}
          {field.role === "dimension" && temporal && (
            <Field label="Date level">
              {(id) => (
                <Select
                  id={id}
                  size_="sm"
                  value={u.time_unit ? `t:${u.time_unit}` : u.date_part ? `p:${u.date_part}` : ""}
                  onChange={(e) => {
                    const [kind, v] = e.target.value.split(":");
                    update((w) => updateField(w, field.key, kind === "t" ? { time_unit: v as TimeUnit, date_part: null } : kind === "p" ? { date_part: v as DatePart, time_unit: null } : { time_unit: null, date_part: null }));
                  }}
                >
                  <option value="">Exact date</option>
                  <optgroup label="Continuous (truncate)">
                    {TIME_UNITS.map((t) => (
                      <option key={t} value={`t:${t}`}>
                        {t[0].toUpperCase() + t.slice(1)}
                      </option>
                    ))}
                  </optgroup>
                  <optgroup label="Discrete (date part)">
                    {DATE_PARTS.map((t) => (
                      <option key={t} value={`p:${t}`}>
                        {t[0].toUpperCase() + t.slice(1)} of
                      </option>
                    ))}
                  </optgroup>
                </Select>
              )}
            </Field>
          )}
          {field.role === "measure" && (
            <Field label="Quick table calculation">
              {(id) => (
                <Select
                  id={id}
                  size_="sm"
                  value={u.table_calc?.type ?? ""}
                  onChange={(e) => update((w) => updateField(w, field.key, { table_calc: e.target.value ? { type: e.target.value as QuickTableCalcType } : null }))}
                >
                  <option value="">None</option>
                  {TABLE_CALCS.map((t) => (
                    <option key={t.value} value={t.value}>
                      {t.label}
                    </option>
                  ))}
                </Select>
              )}
            </Field>
          )}
          <Field label="Display name">
            {(id) => <Input id={id} size_="sm" placeholder={label} value={u.label ?? ""} onChange={(e) => update((w) => updateField(w, field.key, { label: e.target.value || null }))} />}
          </Field>
          <div className="row" style={{ gap: 6 }}>
            <Button size="sm" aria-pressed={sort?.direction === "asc"} icon={<ArrowUp />} onClick={() => setSort(sort?.direction === "asc" ? null : "asc")}>
              Sort ascending
            </Button>
            <Button size="sm" aria-pressed={sort?.direction === "desc"} icon={<ArrowDown />} onClick={() => setSort(sort?.direction === "desc" ? null : "desc")}>
              Descending
            </Button>
          </div>
        </div>
      </Popover>
    </>
  );
}

const OPS: { value: FilterOp; label: string }[] = [
  { value: "in", label: "is one of" }, { value: "not_in", label: "is not one of" }, { value: "contains", label: "contains" },
  { value: "gte", label: "≥" }, { value: "lte", label: "≤" }, { value: "between", label: "between" },
];

function WorksheetFilters({ ws, dataset, update }: { ws: Worksheet; dataset: DatasetDetail | undefined; update: (fn: (w: Worksheet) => Worksheet) => void }) {
  const [adding, setAdding] = useState(false);
  const [draft, setDraft] = useState<{ field: string; op: FilterOp; values: string; stage: "dimension" | "context" }>({ field: "", op: "in", values: "", stage: "dimension" });
  const name = (id?: string | null) => dataset?.fields.find((f) => f.id === id)?.name ?? id ?? "";
  const filters = ws.filters ?? [];

  const describe = (f: QueryFilter) => {
    const op = OPS.find((o) => o.value === f.op)?.label ?? f.op;
    return `${name(f.target?.field)} ${op} ${(f.values ?? []).map(String).join(", ")}${f.stage === "context" ? " (context)" : ""}`;
  };

  const save = () => {
    const field = dataset?.fields.find((f) => f.id === draft.field);
    if (!field) return;
    const parsed: Scalar[] = draft.values.split(",").map((v) => v.trim()).filter(Boolean).map((v) => (field.data_type === "integer" || field.data_type === "real" ? Number(v) : v));
    const values = draft.op === "between" ? [parsed[0] ?? null, parsed[1] ?? null] : parsed;
    const stage = field.level === "agg" ? "measure" : draft.stage;
    const filter: QueryFilter = { target: { field: field.id }, op: draft.op, values, stage };
    update((w) => ({ ...w, filters: [...(w.filters ?? []), { filter }] }));
    setAdding(false);
    setDraft({ field: "", op: "in", values: "", stage: "dimension" });
  };

  return (
    <Section title="Worksheet filters" actions={<IconButton size="sm" label="Add worksheet filter" icon={<Plus />} onClick={() => setAdding(true)} />}>
      <p className="bld-hint" style={{ marginTop: 0 }}>Always applied to this chart; viewers can't remove them.</p>
      {filters.map((f, i) => (
        <div key={i} className="bld-filter-row">
          <span>{describe(f.filter)}</span>
          <IconButton size="sm" label="Remove filter" icon={<Trash2 />} onClick={() => update((w) => ({ ...w, filters: (w.filters ?? []).filter((_, j) => j !== i) }))} />
        </div>
      ))}
      {adding && (
        <div className="stack" style={{ gap: 6, marginTop: 6 }}>
          <Select size_="sm" aria-label="Filter field" value={draft.field} onChange={(e) => setDraft((d) => ({ ...d, field: e.target.value }))}>
            <option value="">Choose a field…</option>
            {dataset?.fields.map((f) => (
              <option key={f.id} value={f.id}>
                {f.name}
              </option>
            ))}
          </Select>
          <Select size_="sm" aria-label="Operator" value={draft.op} onChange={(e) => setDraft((d) => ({ ...d, op: e.target.value as FilterOp }))} options={OPS} />
          <Input size_="sm" aria-label="Values" placeholder={draft.op === "between" ? "min, max" : "Comma-separated values"} value={draft.values} onChange={(e) => setDraft((d) => ({ ...d, values: e.target.value }))} />
          <Checkbox label="Context filter (applies before FIXED and Top N)" checked={draft.stage === "context"} onChange={(v) => setDraft((d) => ({ ...d, stage: v ? "context" : "dimension" }))} />
          <div className="row" style={{ justifyContent: "flex-end" }}>
            <Button size="sm" onClick={() => setAdding(false)}>
              Cancel
            </Button>
            <Button size="sm" variant="primary" disabled={!draft.field} onClick={save}>
              Add
            </Button>
          </div>
        </div>
      )}
    </Section>
  );
}

// ---------------------------------------------------------------- filter
export function FilterInspector({ spec, filter, dataset, setSpec }: { spec: DashboardSpec; filter: DashboardFilter; dataset: DatasetDetail | undefined; setSpec: (fn: (s: DashboardSpec) => DashboardSpec, coalesce?: string) => void }) {
  const patch = (p: Partial<DashboardFilter>, coalesce?: string) => setSpec((s) => ({ ...s, filters: s.filters.map((f) => (f.id === filter.id ? { ...f, ...p } : f)) }), coalesce);
  const sameDataset = spec.worksheets.filter((w) => w.dataset_id === filter.dataset_id);
  const appliesAll = filter.applies_to === "all" || filter.applies_to === undefined;
  return (
    <>
      <Section title="Filter">
        <div className="stack" style={{ gap: 8 }}>
          <Field label="Label">{(id) => <Input id={id} size_="sm" value={filter.label ?? ""} onChange={(e) => patch({ label: e.target.value }, `flabel-${filter.id}`)} />}</Field>
          <Field label="Field">{(id) => <Input id={id} size_="sm" readOnly value={dataset?.fields.find((f) => f.id === filter.field)?.name ?? filter.field} />}</Field>
          <Field label="Control">
            {(id) => (
              <Select
                id={id}
                size_="sm"
                value={filter.control}
                onChange={(e) => patch({ control: e.target.value as DashboardFilter["control"], default: [] })}
                options={[
                  { value: "multi_select", label: "Multiple values (dropdown)" },
                  { value: "single_select", label: "Single value (dropdown)" },
                  { value: "date_range", label: "Date range" },
                  { value: "range", label: "Numeric range" },
                  { value: "search", label: "Search (contains)" },
                ]}
              />
            )}
          </Field>
          <Checkbox label="Context filter (applies before FIXED and Top N)" checked={filter.stage === "context"} onChange={(v) => patch({ stage: v ? "context" : "dimension" })} />
        </div>
      </Section>
      <Section title="Applies to">
        <div className="stack" style={{ gap: 6 }}>
          <Checkbox label="All charts using this dataset" checked={appliesAll} onChange={(v) => patch({ applies_to: v ? "all" : sameDataset.map((w) => w.id) })} />
          {!appliesAll &&
            sameDataset.map((w) => (
              <Checkbox
                key={w.id}
                label={w.title || w.id}
                checked={(filter.applies_to as string[]).includes(w.id)}
                onChange={(v) => patch({ applies_to: v ? [...(filter.applies_to as string[]), w.id] : (filter.applies_to as string[]).filter((x) => x !== w.id) })}
              />
            ))}
        </div>
      </Section>
    </>
  );
}

// ---------------------------------------------------------------- parameter
export function ParameterInspector({ parameter, setSpec }: { parameter: Parameter; setSpec: (fn: (s: DashboardSpec) => DashboardSpec, coalesce?: string) => void }) {
  const patch = (p: Partial<Parameter>, coalesce?: string) => setSpec((s) => ({ ...s, parameters: s.parameters.map((x) => (x.id === parameter.id ? { ...x, ...p } : x)) }), coalesce);
  const allowed = parameter.allowed ?? { type: "all" };
  const numeric = parameter.data_type === "integer" || parameter.data_type === "real";
  return (
    <Section title="Parameter">
      <div className="stack" style={{ gap: 8 }}>
        <Field label="Name" hint="Referenced in formulas as [Parameters].[Name]">
          {(id) => <Input id={id} size_="sm" value={parameter.name} onChange={(e) => patch({ name: e.target.value }, `pname-${parameter.id}`)} />}
        </Field>
        <Field label="Type">
          {(id) => (
            <Select id={id} size_="sm" value={parameter.data_type} onChange={(e) => patch({ data_type: e.target.value as Parameter["data_type"], default: null })} options={["integer", "real", "string", "date", "boolean"].map((t) => ({ value: t, label: t }))} />
          )}
        </Field>
        <Field label="Default value">
          {(id) => (
            <Input
              id={id}
              size_="sm"
              type={numeric ? "number" : parameter.data_type === "date" ? "date" : "text"}
              value={parameter.default === null || parameter.default === undefined ? "" : String(parameter.default)}
              onChange={(e) => patch({ default: e.target.value === "" ? null : numeric ? Number(e.target.value) : e.target.value }, `pdef-${parameter.id}`)}
            />
          )}
        </Field>
        <Field label="Allowed values">
          {(id) => (
            <Select
              id={id}
              size_="sm"
              value={allowed.type}
              onChange={(e) => patch({ allowed: { ...allowed, type: e.target.value as "all" | "list" | "range" }, control: e.target.value === "list" ? "dropdown" : e.target.value === "range" ? "slider" : "input" })}
              options={[{ value: "all", label: "Any value" }, { value: "list", label: "List" }, ...(numeric ? [{ value: "range", label: "Range" }] : [])]}
            />
          )}
        </Field>
        {allowed.type === "list" && (
          <Field label="List values" hint="Comma-separated">
            {(id) => (
              <Input
                id={id}
                size_="sm"
                value={(allowed.values ?? []).join(", ")}
                onChange={(e) => patch({ allowed: { ...allowed, values: e.target.value.split(",").map((v) => v.trim()).filter(Boolean).map((v) => (numeric ? Number(v) : v)) } }, `plist-${parameter.id}`)}
              />
            )}
          </Field>
        )}
        {allowed.type === "range" && (
          <div className="grid-2">
            <Field label="Min">{(id) => <Input id={id} size_="sm" type="number" value={allowed.min ?? ""} onChange={(e) => patch({ allowed: { ...allowed, min: e.target.value === "" ? null : Number(e.target.value) } }, `pmin-${parameter.id}`)} />}</Field>
            <Field label="Max">{(id) => <Input id={id} size_="sm" type="number" value={allowed.max ?? ""} onChange={(e) => patch({ allowed: { ...allowed, max: e.target.value === "" ? null : Number(e.target.value) } }, `pmax-${parameter.id}`)} />}</Field>
          </div>
        )}
      </div>
    </Section>
  );
}

export function TextInspector({ text, onChange }: { text: string; onChange: (v: string) => void }) {
  return (
    <Section title="Text">
      <Textarea aria-label="Text" rows={6} value={text} onChange={(e) => onChange(e.target.value)} />
    </Section>
  );
}
