import { useEffect, useState } from "react";

export type ThemeChoice = "system" | "light" | "dark";
const KEY = "intelliq-theme";

function read(): ThemeChoice {
  try {
    const v = localStorage.getItem(KEY);
    return v === "light" || v === "dark" ? v : "system";
  } catch {
    return "system";
  }
}

function apply(choice: ThemeChoice) {
  const root = document.documentElement;
  if (choice === "system") root.removeAttribute("data-theme");
  else root.setAttribute("data-theme", choice);
}

/** The effective mode after resolving "system". */
export function resolvedMode(): "light" | "dark" {
  const attr = document.documentElement.getAttribute("data-theme");
  if (attr === "light" || attr === "dark") return attr;
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

export function useTheme() {
  const [choice, setChoice] = useState<ThemeChoice>(read);
  useEffect(() => {
    apply(choice);
    try {
      if (choice === "system") localStorage.removeItem(KEY);
      else localStorage.setItem(KEY, choice);
    } catch {
      /* storage unavailable */
    }
    window.dispatchEvent(new Event("intelliq-theme"));
  }, [choice]);
  return { choice, setChoice };
}

/** Re-render when the effective theme changes (toggle or OS setting). */
export function useResolvedMode(): "light" | "dark" {
  const [mode, setMode] = useState(resolvedMode);
  useEffect(() => {
    const update = () => setMode(resolvedMode());
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    mq.addEventListener("change", update);
    window.addEventListener("intelliq-theme", update);
    return () => {
      mq.removeEventListener("change", update);
      window.removeEventListener("intelliq-theme", update);
    };
  }, []);
  return mode;
}
