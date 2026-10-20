import { useEffect, useMemo, useState } from "react";
import { useFieldValues, useFilterValues } from "@/api/hooks";
import type { DashboardFilter, Parameter, Scalar } from "@/api/types";
import { Combobox, Input, Select } from "@/design-system";

function valueLabel(v: Scalar): string {
  return v === null ? "Null" : String(v);
}

/** Dashboard filter control. Viewer choices only ever narrow results. */
export function FilterControl({
  filter,
  dashboardId,
  draft,
  values,
  onChange,
}: {
  filter: DashboardFilter;
  dashboardId?: string;
  draft: boolean;
  values: Scalar[];
  onChange: (values: Scalar[]) => void;
}) {
  const label = filter.label || filter.field;
  const [opened, setOpened] = useState(false);
  const wantsValues = opened && (filter.control === "multi_select" || filter.control === "single_select");
  // Saved dashboards enumerate through the dashboard-scoped endpoint (viewer-safe);
  // the builder (no dashboard id yet) uses the author-only dataset endpoint.
  const scoped = useFilterValues(dashboardId, filter.id, draft, wantsValues && !!dashboardId);
  const authoring = useFieldValues(filter.dataset_id, filter.field, wantsValues && !dashboardId);
  const domain = dashboardId ? scoped : authoring;

  if (filter.control === "multi_select" || filter.control === "single_select") {
    const options = (domain.data?.values ?? values).map((v) => ({ value: JSON.stringify(v), label: valueLabel(v) }));
    return (
      <ControlFrame label={label}>
        <Combobox
          label={label}
          multiple={filter.control === "multi_select"}
          options={options}
          loading={domain.isLoading}
          value={values.map((v) => JSON.stringify(v))}
          allLabel="All"
          onOpen={() => setOpened(true)}
          onChange={(next) => onChange(next.map((s) => JSON.parse(s) as Scalar))}
          footer={domain.data?.truncated ? <div className="muted" style={{ fontSize: 11, padding: "6px 8px" }}>Showing the first 1,000 values</div> : undefined}
        />
      </ControlFrame>
    );
  }
  if (filter.control === "date_range" || filter.control === "range") {
    const type = filter.control === "date_range" ? "date" : "number";
    const [lo, hi] = [values[0] ?? null, values[1] ?? null];
    const set = (i: 0 | 1, raw: string) => {
      const v: Scalar = raw === "" ? null : type === "number" ? Number(raw) : raw;
      const next: Scalar[] = [lo, hi];
      next[i] = v;
      onChange(next[0] === null && next[1] === null ? [] : next);
    };
    return (
      <ControlFrame label={label}>
        <div className="dash-range">
          <Input type={type} size_="sm" aria-label={`${label} from`} value={lo === null ? "" : String(lo)} onChange={(e) => set(0, e.target.value)} />
          <span className="muted">–</span>
          <Input type={type} size_="sm" aria-label={`${label} to`} value={hi === null ? "" : String(hi)} onChange={(e) => set(1, e.target.value)} />
        </div>
      </ControlFrame>
    );
  }
  if (filter.control === "search") {
    return (
      <ControlFrame label={label}>
        <DebouncedInput value={values[0] === undefined || values[0] === null ? "" : String(values[0])} label={label} onChange={(v) => onChange(v ? [v] : [])} />
      </ControlFrame>
    );
  }
  return (
    <ControlFrame label={label}>
      <span className="muted" style={{ fontSize: 12 }}>
        Relative date filters are set by the author
      </span>
    </ControlFrame>
  );
}

function DebouncedInput({ value, onChange, label }: { value: string; onChange: (v: string) => void; label: string }) {
  const [text, setText] = useState(value);
  useEffect(() => setText(value), [value]);
  useEffect(() => {
    if (text === value) return;
    const t = window.setTimeout(() => onChange(text), 350);
    return () => window.clearTimeout(t);
  }, [text, value, onChange]);
  return <Input type="search" size_="sm" placeholder="Contains…" aria-label={label} value={text} onChange={(e) => setText(e.target.value)} />;
}

export function ParameterControl({ parameter, value, onChange }: { parameter: Parameter; value: Scalar; onChange: (v: Scalar) => void }) {
  const allowed = parameter.allowed ?? { type: "all" };
  const numeric = parameter.data_type === "integer" || parameter.data_type === "real";
  const options = useMemo(() => (allowed.values ?? []).map((v) => ({ value: JSON.stringify(v), label: valueLabel(v) })), [allowed.values]);

  if (allowed.type === "list") {
    return (
      <ControlFrame label={parameter.name}>
        <Select size_="sm" aria-label={parameter.name} value={JSON.stringify(value)} options={options} onChange={(e) => onChange(JSON.parse(e.target.value))} />
      </ControlFrame>
    );
  }
  if (allowed.type === "range" && numeric && allowed.min != null && allowed.max != null) {
    return (
      <ControlFrame label={`${parameter.name}: ${value ?? "–"}`}>
        <input
          className="dash-slider"
          type="range"
          aria-label={parameter.name}
          min={allowed.min}
          max={allowed.max}
          step={allowed.step ?? (parameter.data_type === "integer" ? 1 : "any")}
          value={Number(value ?? allowed.min)}
          onChange={(e) => onChange(Number(e.target.value))}
        />
      </ControlFrame>
    );
  }
  return (
    <ControlFrame label={parameter.name}>
      <Input
        size_="sm"
        aria-label={parameter.name}
        type={numeric ? "number" : parameter.data_type === "date" ? "date" : "text"}
        value={value === null || value === undefined ? "" : String(value)}
        onChange={(e) => onChange(e.target.value === "" ? null : numeric ? Number(e.target.value) : e.target.value)}
      />
    </ControlFrame>
  );
}

function ControlFrame({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="dash-control">
      <div className="dash-control__label">{label}</div>
      {children}
    </div>
  );
}
