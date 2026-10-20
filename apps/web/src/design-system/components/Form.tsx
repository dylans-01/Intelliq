import clsx from "clsx";
import { Search } from "lucide-react";
import {
  forwardRef,
  useId,
  type InputHTMLAttributes,
  type ReactNode,
  type SelectHTMLAttributes,
  type TextareaHTMLAttributes,
} from "react";

export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement> & { size_?: "sm" | "md" }>(
  function Input({ className, size_ = "md", ...rest }, ref) {
    return <input ref={ref} className={clsx("iq-input", size_ === "sm" && "iq-input--sm", className)} {...rest} />;
  },
);

export function SearchInput(props: InputHTMLAttributes<HTMLInputElement> & { size_?: "sm" | "md" }) {
  return (
    <div className="iq-input-group">
      <Search aria-hidden />
      <Input type="search" {...props} />
    </div>
  );
}

export const Select = forwardRef<
  HTMLSelectElement,
  SelectHTMLAttributes<HTMLSelectElement> & { size_?: "sm" | "md"; options?: { value: string; label: string }[] }
>(function Select({ className, size_ = "md", options, children, ...rest }, ref) {
  return (
    <select ref={ref} className={clsx("iq-select", size_ === "sm" && "iq-select--sm", className)} {...rest}>
      {options?.map((o) => (
        <option key={o.value} value={o.value}>
          {o.label}
        </option>
      ))}
      {children}
    </select>
  );
});

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaHTMLAttributes<HTMLTextAreaElement> & { code?: boolean }>(
  function Textarea({ className, code, ...rest }, ref) {
    return <textarea ref={ref} className={clsx("iq-textarea", code && "iq-textarea--code", className)} {...rest} />;
  },
);

/** Label + control + hint/error. The child receives the generated id via render prop. */
export function Field({
  label,
  hint,
  error,
  children,
  className,
}: {
  label: ReactNode;
  hint?: ReactNode;
  error?: ReactNode;
  children: (id: string) => ReactNode;
  className?: string;
}) {
  const id = useId();
  return (
    <div className={clsx("iq-field", className)}>
      <label className="iq-field__label" htmlFor={id}>
        {label}
      </label>
      {children(id)}
      {error ? (
        <span className="iq-field__error" role="alert">
          {error}
        </span>
      ) : hint ? (
        <span className="iq-field__hint">{hint}</span>
      ) : null}
    </div>
  );
}

export function Checkbox({
  checked,
  onChange,
  label,
  disabled,
}: {
  checked: boolean;
  onChange: (v: boolean) => void;
  label: ReactNode;
  disabled?: boolean;
}) {
  return (
    <label className="iq-check">
      <input type="checkbox" checked={checked} disabled={disabled} onChange={(e) => onChange(e.target.checked)} />
      <span>{label}</span>
    </label>
  );
}

export function Switch({ checked, onChange, label }: { checked: boolean; onChange: (v: boolean) => void; label: string }) {
  return (
    <button type="button" role="switch" aria-checked={checked} aria-label={label} className="iq-switch" onClick={() => onChange(!checked)} />
  );
}
