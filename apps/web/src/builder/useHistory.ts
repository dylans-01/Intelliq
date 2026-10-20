import { useCallback, useState } from "react";

/** Value with undo/redo. Rapid edits sharing a ``coalesce`` key form one step. */
export function useHistory<T>(initial: T) {
  const [state, setState] = useState<{ past: T[]; present: T; future: T[]; lastKey: string | null }>({
    past: [],
    present: initial,
    future: [],
    lastKey: null,
  });

  const set = useCallback((next: T | ((prev: T) => T), coalesce?: string) => {
    setState((s) => {
      const value = typeof next === "function" ? (next as (p: T) => T)(s.present) : next;
      if (value === s.present) return s;
      const merge = coalesce !== undefined && coalesce === s.lastKey;
      return {
        past: merge ? s.past : [...s.past.slice(-99), s.present],
        present: value,
        future: [],
        lastKey: coalesce ?? null,
      };
    });
  }, []);

  const undo = useCallback(() => {
    setState((s) => (s.past.length ? { past: s.past.slice(0, -1), present: s.past[s.past.length - 1], future: [s.present, ...s.future], lastKey: null } : s));
  }, []);

  const redo = useCallback(() => {
    setState((s) => (s.future.length ? { past: [...s.past, s.present], present: s.future[0], future: s.future.slice(1), lastKey: null } : s));
  }, []);

  const reset = useCallback((value: T) => setState({ past: [], present: value, future: [], lastKey: null }), []);

  return { value: state.present, set, undo, redo, reset, canUndo: state.past.length > 0, canRedo: state.future.length > 0 };
}
