import { Check, ChevronDown } from "lucide-react";
import { useCallback, useMemo, useRef, useState, type ReactNode } from "react";
import { SearchInput } from "./Form";
import { Popover } from "./Overlay";
import { Spinner } from "./Feedback";

export interface Option {
  value: string;
  label: string;
  hint?: ReactNode;
}

/**
 * Searchable single or multi select. Options may be loaded lazily
 * (``onOpen``) — e.g. distinct filter values fetched on first open.
 */
export function Combobox({
  options,
  value,
  onChange,
  multiple = false,
  placeholder = "Select…",
  loading,
  onOpen,
  label,
  allLabel,
  footer,
}: {
  options: Option[];
  value: string[];
  onChange: (v: string[]) => void;
  multiple?: boolean;
  placeholder?: string;
  loading?: boolean;
  onOpen?: () => void;
  label: string;
  allLabel?: string;
  footer?: ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const anchor = useRef<HTMLButtonElement>(null);
  const close = useCallback(() => setOpen(false), []);

  const shown = useMemo(() => {
    const q = query.trim().toLowerCase();
    return q ? options.filter((o) => o.label.toLowerCase().includes(q)) : options;
  }, [options, query]);

  const selectedLabels = value.map((v) => options.find((o) => o.value === v)?.label ?? v);
  const display =
    value.length === 0 ? allLabel ?? placeholder : value.length <= 2 ? selectedLabels.join(", ") : `${value.length} selected`;

  const toggle = (v: string) => {
    if (!multiple) {
      onChange(value[0] === v ? [] : [v]);
      close();
      return;
    }
    onChange(value.includes(v) ? value.filter((x) => x !== v) : [...value, v]);
  };

  return (
    <>
      <button
        ref={anchor}
        type="button"
        className="iq-combo-trigger"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label={label}
        onClick={() => {
          if (!open) onOpen?.();
          setOpen((o) => !o);
          setQuery("");
          setActive(0);
        }}
      >
        <span className={`iq-combo-trigger__value${value.length === 0 && !allLabel ? " is-placeholder" : ""}`}>{display}</span>
        <ChevronDown aria-hidden />
      </button>
      <Popover open={open} onClose={close} anchor={anchor} width="anchor" label={label}>
        <div style={{ padding: 4, minWidth: 200 }}>
          {options.length > 8 && (
            <SearchInput
              size_="sm"
              autoFocus
              placeholder="Search"
              value={query}
              aria-label={`Search ${label}`}
              onChange={(e) => {
                setQuery(e.target.value);
                setActive(0);
              }}
              onKeyDown={(e) => {
                if (e.key === "ArrowDown") setActive((a) => Math.min(a + 1, shown.length - 1));
                if (e.key === "ArrowUp") setActive((a) => Math.max(a - 1, 0));
                if (e.key === "Enter" && shown[active]) toggle(shown[active].value);
              }}
            />
          )}
          <div role="listbox" aria-multiselectable={multiple} style={{ marginTop: options.length > 8 ? 4 : 0 }}>
            {multiple && value.length > 0 && (
              <button className="iq-option" onClick={() => onChange([])}>
                <span className="iq-option__check" />
                <span className="iq-option__label muted">Clear selection</span>
              </button>
            )}
            {loading && (
              <div className="iq-state iq-state--compact">
                <Spinner />
              </div>
            )}
            {!loading && shown.length === 0 && <div className="iq-state iq-state--compact">No matches</div>}
            {shown.slice(0, 500).map((o, i) => (
              <button
                key={o.value}
                role="option"
                aria-selected={value.includes(o.value)}
                data-active={i === active ? "true" : undefined}
                className="iq-option"
                onClick={() => toggle(o.value)}
                onMouseEnter={() => setActive(i)}
              >
                <span className="iq-option__check">{value.includes(o.value) && <Check />}</span>
                <span className="iq-option__label">{o.label}</span>
                {o.hint && <span className="muted" style={{ fontSize: 12 }}>{o.hint}</span>}
              </button>
            ))}
          </div>
          {footer}
        </div>
      </Popover>
    </>
  );
}
