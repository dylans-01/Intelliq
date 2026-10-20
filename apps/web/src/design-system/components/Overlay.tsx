import { X } from "lucide-react";
import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type ReactNode,
  type RefObject,
} from "react";
import { createPortal } from "react-dom";
import { IconButton } from "./Button";

export function useDismiss(refs: RefObject<HTMLElement>[], onDismiss: () => void, active: boolean) {
  useEffect(() => {
    if (!active) return;
    const onDown = (e: MouseEvent) => {
      if (refs.some((r) => r.current?.contains(e.target as Node))) return;
      onDismiss();
    };
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onDismiss();
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [active, onDismiss, refs]);
}

/**
 * Anchored floating panel. Positions below (or above, if there is no room)
 * the trigger and flips horizontally to stay on screen.
 */
export function Popover({
  open,
  onClose,
  anchor,
  children,
  align = "start",
  width,
  label,
}: {
  open: boolean;
  onClose: () => void;
  anchor: RefObject<HTMLElement>;
  children: ReactNode;
  align?: "start" | "end";
  width?: number | "anchor";
  label?: string;
}) {
  const panel = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState<{ top: number; left: number; width?: number } | null>(null);
  const refs = useRef([anchor, panel]).current;
  useDismiss(refs, onClose, open);

  useLayoutEffect(() => {
    if (!open || !anchor.current) return;
    const place = () => {
      const r = anchor.current!.getBoundingClientRect();
      const w = width === "anchor" ? r.width : width;
      const ph = panel.current?.offsetHeight ?? 200;
      const pw = w ?? panel.current?.offsetWidth ?? 200;
      let top = r.bottom + 4;
      if (top + ph > window.innerHeight - 8 && r.top - ph - 4 > 8) top = r.top - ph - 4;
      let left = align === "end" ? r.right - pw : r.left;
      left = Math.max(8, Math.min(left, window.innerWidth - pw - 8));
      setPos({ top, left, width: w });
    };
    place();
    window.addEventListener("resize", place);
    window.addEventListener("scroll", place, true);
    return () => {
      window.removeEventListener("resize", place);
      window.removeEventListener("scroll", place, true);
    };
  }, [open, anchor, align, width]);

  if (!open) return null;
  return createPortal(
    <div
      ref={panel}
      className="iq-popover"
      role="dialog"
      aria-label={label}
      style={{ top: pos?.top ?? -9999, left: pos?.left ?? -9999, width: pos?.width }}
    >
      {children}
    </div>,
    document.body,
  );
}

export interface MenuItem {
  label: string;
  icon?: ReactNode;
  onSelect: () => void;
  danger?: boolean;
  disabled?: boolean;
}

export function Menu({
  trigger,
  items,
  align = "end",
  label = "Menu",
}: {
  trigger: (props: { ref: RefObject<HTMLButtonElement>; onClick: () => void; "aria-expanded": boolean }) => ReactNode;
  items: (MenuItem | "separator")[];
  align?: "start" | "end";
  label?: string;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLButtonElement>(null);
  const close = useCallback(() => setOpen(false), []);
  return (
    <>
      {trigger({ ref, onClick: () => setOpen((o) => !o), "aria-expanded": open })}
      <Popover open={open} onClose={close} anchor={ref} align={align} label={label}>
        <div role="menu">
          {items.map((item, i) =>
            item === "separator" ? (
              <div key={`sep-${i}`} className="iq-menu-sep" role="separator" />
            ) : (
              <button
                key={item.label}
                role="menuitem"
                className={`iq-menu-item${item.danger ? " iq-menu-item--danger" : ""}`}
                disabled={item.disabled}
                onClick={() => {
                  close();
                  item.onSelect();
                }}
              >
                {item.icon}
                {item.label}
              </button>
            ),
          )}
        </div>
      </Popover>
    </>
  );
}

/** Modal dialog with focus containment and Escape to close. */
export function Dialog({
  open,
  onClose,
  title,
  description,
  children,
  footer,
  wide,
}: {
  open: boolean;
  onClose: () => void;
  title: ReactNode;
  description?: ReactNode;
  children: ReactNode;
  footer?: ReactNode;
  wide?: boolean;
}) {
  const panel = useRef<HTMLDivElement>(null);
  // Held in a ref so an inline onClose doesn't re-run the effect (and steal focus) on every render.
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  useEffect(() => {
    if (!open) return;
    const onClose = () => closeRef.current();
    const previous = document.activeElement as HTMLElement | null;
    const focusable = () =>
      Array.from(
        panel.current?.querySelectorAll<HTMLElement>(
          'button:not([disabled]), [href], input:not([disabled]), select, textarea, [tabindex]:not([tabindex="-1"])',
        ) ?? [],
      );
    (focusable().find((el) => el.tagName !== "BUTTON") ?? focusable()[0])?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
      if (e.key === "Tab") {
        const els = focusable();
        if (!els.length) return;
        const first = els[0];
        const last = els[els.length - 1];
        if (e.shiftKey && document.activeElement === first) {
          e.preventDefault();
          last.focus();
        } else if (!e.shiftKey && document.activeElement === last) {
          e.preventDefault();
          first.focus();
        }
      }
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      previous?.focus();
    };
  }, [open]);

  if (!open) return null;
  return createPortal(
    <div className="iq-dialog-scrim" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div ref={panel} className={`iq-dialog${wide ? " iq-dialog--wide" : ""}`} role="dialog" aria-modal="true" aria-label={typeof title === "string" ? title : undefined}>
        <div className="iq-dialog__header">
          <div>
            <h2>{title}</h2>
            {description && <p>{description}</p>}
          </div>
          <IconButton label="Close" icon={<X />} size="sm" onClick={onClose} />
        </div>
        <div className="iq-dialog__body">{children}</div>
        {footer && <div className="iq-dialog__footer">{footer}</div>}
      </div>
    </div>,
    document.body,
  );
}
