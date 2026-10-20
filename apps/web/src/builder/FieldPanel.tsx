import { Calculator, Calendar, Hash, Plus, ToggleLeft, Type } from "lucide-react";
import { useMemo, useState } from "react";
import type { DatasetDetail, SemanticField } from "@/api/types";
import { IconButton, SearchInput } from "@/design-system";

function TypeIcon({ f }: { f: SemanticField }) {
  if (f.formula) return <Calculator aria-hidden />;
  if (f.data_type === "date" || f.data_type === "datetime") return <Calendar aria-hidden />;
  if (f.data_type === "boolean") return <ToggleLeft aria-hidden />;
  if (f.data_type === "string") return <Type aria-hidden />;
  return <Hash aria-hidden />;
}

export const FIELD_MIME = "application/x-intelliq-field";

/** Searchable list of a dataset's fields. Click "+" to add to the selected chart, or drag onto a shelf. */
export function FieldPanel({
  dataset,
  onAdd,
  onAddFilter,
  onNewCalc,
  canAdd,
}: {
  dataset: DatasetDetail;
  onAdd: (f: SemanticField) => void;
  onAddFilter: (f: SemanticField) => void;
  onNewCalc: () => void;
  canAdd: boolean;
}) {
  const [q, setQ] = useState("");
  const groups = useMemo(() => {
    const visible = dataset.fields.filter((f) => !f.hidden && f.name.toLowerCase().includes(q.trim().toLowerCase()));
    return [
      { title: "Dimensions", fields: visible.filter((f) => f.role === "dimension") },
      { title: "Measures", fields: visible.filter((f) => f.role === "measure") },
    ];
  }, [dataset, q]);

  return (
    <div className="bld-fields">
      <div className="bld-fields__search">
        <SearchInput size_="sm" placeholder="Search fields" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search fields" />
        <IconButton size="sm" variant="secondary" label="New calculation" icon={<Calculator />} onClick={onNewCalc} disabled={!canAdd} />
      </div>
      <div className="bld-fields__list">
        {groups.map((g) => (
          <div key={g.title}>
            <div className="bld-fields__group">{g.title}</div>
            {g.fields.map((f) => (
              <div
                key={f.id}
                className={`bld-field bld-field--${f.role}`}
                draggable
                onDragStart={(e) => {
                  e.dataTransfer.setData(FIELD_MIME, f.id);
                  e.dataTransfer.effectAllowed = "copy";
                }}
                title={f.description || f.formula || f.column || f.name}
              >
                <TypeIcon f={f} />
                <span className="bld-field__name">{f.name}</span>
                <span className="bld-field__actions">
                  <button type="button" onClick={() => onAddFilter(f)} title={`Add ${f.name} as a dashboard filter`} aria-label={`Add ${f.name} as a dashboard filter`}>
                    Filter
                  </button>
                  <button type="button" onClick={() => onAdd(f)} disabled={!canAdd} title={`Add ${f.name} to the selected chart`} aria-label={`Add ${f.name} to the selected chart`}>
                    <Plus />
                  </button>
                </span>
              </div>
            ))}
          </div>
        ))}
      </div>
    </div>
  );
}
