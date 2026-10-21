import { CM } from '../lib/units';

// A centimetre axis: the plot spans a whole number of centimetres and every
// tick sits on a centimetre line, so chart gridlines coincide with the canvas
// grid. The value per centimetre is the smallest "nice" step that fits the data.

export const NICE: number[] = [];
for (let e = -4; e <= 6; e++) for (const m of [1, 2, 2.5, 5]) NICE.push(m * 10 ** e);

export interface CmAxis {
  d0: number;
  d1: number;
  step: number;
  /** Length in whole centimetres. */
  n: number;
  ticks: number[];
  /** Offset in px from the axis origin (d0). */
  pos: (v: number) => number;
  /** Show a tick label on every k-th centimetre. */
  labelEvery: number;
  decimals: number;
}

export interface AxisOptions {
  zero?: boolean;
  /** Fraction of the range kept free at each end. */
  pad?: number;
  /** Approximate label width in cm, used to thin labels. */
  labelCm?: number;
  /** Clamp the domain to these bounds where possible. */
  floor?: number;
  ceil?: number;
  /** Override the candidate values per centimetre (e.g. Rounds: 1, 2, 3, 5). */
  steps?: number[];
}

/** An axis whose domain is fixed, e.g. 0–100% over the plot width. */
export function fixedAxis(d0: number, d1: number, n: number, labelEvery = 1): CmAxis {
  const step = (d1 - d0) / n;
  return {
    d0,
    d1,
    step,
    n,
    ticks: Array.from({ length: n + 1 }, (_, i) => d0 + i * step),
    pos: (v: number) => ((v - d0) / step) * CM,
    labelEvery,
    decimals: 0,
  };
}

export function cmAxis(min: number, max: number, n: number, opts: AxisOptions = {}): CmAxis {
  let lo = min;
  let hi = max;
  if (opts.zero) {
    lo = Math.min(0, lo);
    hi = Math.max(0, hi);
  }
  const pad = (hi - lo) * (opts.pad ?? 0);
  if (!opts.zero || lo < 0) lo -= pad;
  hi += pad;
  if (opts.floor !== undefined) lo = Math.max(opts.floor, lo);
  if (opts.ceil !== undefined) hi = Math.min(opts.ceil, hi);
  if (hi <= lo) hi = lo + 1;

  const candidates = opts.steps ?? NICE;
  let step = candidates[candidates.length - 1];
  let d0 = Math.floor(lo / step) * step;
  for (const s of candidates) {
    const start = Math.floor(lo / s + 1e-9) * s;
    if (start + n * s >= hi - 1e-9) {
      step = s;
      d0 = start;
      break;
    }
  }
  // Centre the data in any spare centimetres, keeping zero and bounds aligned.
  const used = Math.ceil((hi - d0) / step - 1e-9);
  const spare = n - used;
  if (spare > 1 && !opts.zero) {
    let shift = Math.floor(spare / 2);
    if (opts.floor !== undefined) shift = Math.min(shift, Math.max(0, Math.round((d0 - opts.floor) / step)));
    d0 -= shift * step;
  }
  // Keep a bounded axis inside its bound (no 102%); the data still fits because it lies below the bound.
  if (opts.ceil !== undefined) {
    const over = Math.ceil((d0 + n * step - opts.ceil) / step - 1e-9);
    if (over > 0) d0 -= over * step;
  }
  const d1 = d0 + n * step;
  const ticks = Array.from({ length: n + 1 }, (_, i) => +(d0 + i * step).toFixed(10));
  const decimals = Math.max(0, -Math.floor(Math.log10(step) + 1e-9)) + (String(step / 10 ** Math.floor(Math.log10(step))).includes('.') ? 1 : 0);
  const labelCm = opts.labelCm ?? 0.9;
  const labelEvery = labelCm <= 0.85 ? 1 : labelCm <= 1.8 ? 2 : labelCm <= 4 ? 5 : 10;
  return {
    d0,
    d1,
    step,
    n,
    ticks,
    pos: (v: number) => ((v - d0) / step) * CM,
    labelEvery,
    decimals,
  };
}

/**
 * Steps whose every multiple the formatter shows exactly: a formatter that rounds to whole Rounds rules
 * out 0.5 and 2.5, one that rounds to whole percent rules out 2.5%. Without this a tick at 62.5% reads "63%".
 */
export function exactSteps(format: (v: number) => string) {
  const exact = (t: number) => {
    const e = 1e-9 * Math.max(1, Math.abs(t));
    return format(t) === format(t + e) && format(t) === format(t - e);
  };
  return NICE.filter((s) => [1, 2, 3, 4, 5].every((k) => exact(+(k * s).toFixed(10))));
}

/**
 * A linear axis fitted to the data, as Artificial Analysis scales its charts: the range runs from a round
 * tick below the data to one above it, with about one tick every 80 px, each shown exactly by `format`.
 */
export function fitLinear(min: number, max: number, px: number, opts: { format?: (v: number) => string; pad?: number; floor?: number; ceil?: number } = {}) {
  const span = max - min || Math.abs(max) || 1;
  let lo = min - span * (opts.pad ?? 0.08);
  let hi = max + span * (opts.pad ?? 0.08);
  if (opts.floor !== undefined) lo = Math.max(opts.floor, lo);
  if (opts.ceil !== undefined) hi = Math.min(opts.ceil, hi);
  const most = Math.min(8, Math.max(3, Math.round(px / 80)));
  const steps = opts.format ? exactSteps(opts.format) : NICE;
  const count = (s: number) => Math.ceil(hi / s - 1e-9) - Math.floor(lo / s + 1e-9);
  const step = steps.find((s) => count(s) <= most) ?? steps[steps.length - 1];
  const d0 = Math.floor(lo / step + 1e-9) * step;
  const d1 = Math.max(d0 + step, Math.ceil(hi / step - 1e-9) * step);
  const ticks = Array.from({ length: Math.round((d1 - d0) / step) + 1 }, (_, i) => +(d0 + i * step).toFixed(10));
  return { ticks, pos: (v: number) => ((v - d0) / (d1 - d0)) * px };
}

export function fmt(v: number, decimals: number) {
  const s = v.toFixed(decimals);
  return s === '-0' ? '0' : s.replace('-', '−');
}

export function pct(v: number, decimals = 0) {
  return `${fmt(v * 100, decimals)}%`;
}

/** Decimals needed to show a percentage axis whose step is `step` (as a fraction). */
export function pctDecimals(step: number) {
  return Math.max(0, -Math.floor(Math.log10(step * 100) + 1e-9));
}

/**
 * An axis that fills a plot of any height: round ticks (1, 2, 2.5 or 5 × 10ⁿ) at least `minGap` px
 * apart, from a tick at or below `min` to the first tick at or above `max`. Unlike a centimetre axis it
 * never leaves part of the plot empty to keep ticks on centimetre lines.
 */
export function fitAxis(min: number, max: number, px: number, opts: { minGap?: number; ceil?: number } = {}) {
  const minGap = opts.minGap ?? 30;
  const hi = opts.ceil !== undefined ? Math.min(opts.ceil, max) : max;
  const span = Math.max(hi - Math.min(0, min), 1e-9);
  let step = NICE[NICE.length - 1];
  for (const s of NICE) {
    const d0 = Math.floor(Math.min(0, min) / s + 1e-9) * s;
    const d1 = Math.ceil(hi / s - 1e-9) * s;
    if (((d1 - d0) / s) * minGap <= px || s >= span) {
      step = s;
      break;
    }
  }
  const d0 = Math.floor(Math.min(0, min) / step + 1e-9) * step;
  const d1 = Math.max(d0 + step, Math.ceil(hi / step - 1e-9) * step);
  const ticks = Array.from({ length: Math.round((d1 - d0) / step) + 1 }, (_, i) => +(d0 + i * step).toFixed(10));
  return { d0, d1, ticks, pos: (v: number) => ((v - d0) / (d1 - d0)) * px };
}
