import { AlertCircle, CheckCircle2, Info } from "lucide-react";
import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";

type ToastTone = "good" | "critical" | "info";
interface ToastItem {
  id: number;
  tone: ToastTone;
  message: ReactNode;
}

const ToastContext = createContext<(message: ReactNode, tone?: ToastTone) => void>(() => {});

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);
  const push = useCallback((message: ReactNode, tone: ToastTone = "info") => {
    const id = Date.now() + Math.random();
    setItems((xs) => [...xs.slice(-3), { id, tone, message }]);
    window.setTimeout(() => setItems((xs) => xs.filter((x) => x.id !== id)), tone === "critical" ? 7000 : 4000);
  }, []);
  const value = useMemo(() => push, [push]);
  return (
    <ToastContext.Provider value={value}>
      {children}
      <div className="iq-toasts" aria-live="polite">
        {items.map((t) => (
          <div key={t.id} className={`iq-toast iq-toast--${t.tone}`} role={t.tone === "critical" ? "alert" : "status"}>
            {t.tone === "good" ? <CheckCircle2 /> : t.tone === "critical" ? <AlertCircle /> : <Info />}
            <div>{t.message}</div>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

export const useToast = () => useContext(ToastContext);
