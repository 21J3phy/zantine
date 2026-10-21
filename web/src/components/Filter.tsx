import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from 'react';
import { ENTRIES, type Effort } from '../data/models';

/**
 * What each figure shows. Every figure has its own legend, so hiding a model or an effort level
 * applies to that figure only; its card and its full-screen view share the state.
 */
interface FilterApi {
  /** Active entry ids (model × effort) for one figure, in fixed order. */
  active: (fig: string) => string[];
  isActive: (fig: string, model: string) => boolean;
  toggle: (fig: string, model: string) => void;
  isEffortActive: (fig: string, effort: Effort) => boolean;
  toggleEffort: (fig: string, effort: Effort) => void;
  /** True when the figure hides anything. */
  filtered: (fig: string) => boolean;
  reset: (fig: string) => void;
  /** Per-figure Pareto line, shown until it is turned off. */
  paretoOn: (fig: string) => boolean;
  togglePareto: (fig: string) => void;
}

const FilterContext = createContext<FilterApi | null>(null);

interface Hidden {
  models: Set<string>;
  efforts: Set<Effort>;
}

const NONE: Hidden = { models: new Set(), efforts: new Set() };

/** Toggle one key in a set. */
const flip = <T,>(prev: Set<T>, key: T) => {
  const next = new Set(prev);
  if (!next.delete(key)) next.add(key);
  return next;
};

/** A figure must keep at least one entry: a model can be left with only effort levels it lacks. */
const anyVisible = (h: Hidden) => ENTRIES.some((e) => !h.models.has(e.model.id) && !h.efforts.has(e.effort));

/** Apply a change to one figure's hidden set, unless it would leave the figure empty. */
const update = (prev: Record<string, Hidden>, fig: string, change: (h: Hidden) => Hidden) => {
  const next = change(prev[fig] ?? NONE);
  return anyVisible(next) ? { ...prev, [fig]: next } : prev;
};

export function FilterProvider({ children }: { children: ReactNode }) {
  const [hidden, setHidden] = useState<Record<string, Hidden>>({});
  const [paretoOff, setParetoOff] = useState<Set<string>>(new Set());
  const toggle = useCallback(
    (fig: string, id: string) =>
      setHidden((prev) => update(prev, fig, (h) => ({ ...h, models: flip(h.models, id) }))),
    [],
  );
  const toggleEffort = useCallback(
    (fig: string, e: Effort) =>
      setHidden((prev) => update(prev, fig, (h) => ({ ...h, efforts: flip(h.efforts, e) }))),
    [],
  );
  const reset = useCallback(
    (fig: string) =>
      setHidden((prev) => {
        const next = { ...prev };
        delete next[fig];
        return next;
      }),
    [],
  );
  const togglePareto = useCallback(
    (fig: string) =>
      setParetoOff((prev) => {
        const next = new Set(prev);
        if (next.has(fig)) next.delete(fig);
        else next.add(fig);
        return next;
      }),
    [],
  );
  const api = useMemo<FilterApi>(() => {
    const get = (fig: string) => hidden[fig] ?? NONE;
    return {
      active: (fig) => {
        const h = get(fig);
        return ENTRIES.filter((e) => !h.models.has(e.model.id) && !h.efforts.has(e.effort)).map((e) => e.id);
      },
      isActive: (fig, id) => !get(fig).models.has(id),
      toggle,
      isEffortActive: (fig, e) => !get(fig).efforts.has(e),
      toggleEffort,
      filtered: (fig) => get(fig).models.size + get(fig).efforts.size > 0,
      reset,
      paretoOn: (fig) => !paretoOff.has(fig),
      togglePareto,
    };
  }, [hidden, toggle, toggleEffort, reset, paretoOff, togglePareto]);
  return <FilterContext.Provider value={api}>{children}</FilterContext.Provider>;
}

export function useFilter() {
  const ctx = useContext(FilterContext);
  if (!ctx) throw new Error('useFilter outside FilterProvider');
  return ctx;
}
