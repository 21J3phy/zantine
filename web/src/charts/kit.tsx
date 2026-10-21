import type { ReactNode } from 'react';
import { LABS, modelOf, tint } from '../data/models';
import { CM } from '../lib/units';
import { fmt, type CmAxis } from './scale';

export interface ChartProps {
  /** Chart area in whole centimetres. */
  w: number;
  h: number;
  /** Active model ids, in fixed order. */
  models: string[];
  expanded?: boolean;
  /** Draw the Pareto frontier, on charts that support it. */
  pareto?: boolean;
}

export const FONT = 11;

export const textWidth = (s: string, size = FONT) => s.length * size * 0.56;

export function Svg({ w, h, label, children }: { w: number; h: number; label: string; children: ReactNode }) {
  return (
    <svg className="chart" width={w * CM} height={h * CM} viewBox={`0 0 ${w * CM} ${h * CM}`} role="img" aria-label={label}>
      {children}
    </svg>
  );
}

/**
 * Hairline gridlines on the labelled ticks: horizontal every `every` cm, counted up from the bottom as
 * YTicks counts; vertical ones (every `vEvery` cm from the left, as XTicks counts) only where asked.
 */
export function Grid({
  x,
  y,
  nx,
  ny,
  base,
  every = 1,
  h = true,
  v = false,
  vEvery = every,
}: {
  x: number;
  y: number;
  nx: number;
  ny: number;
  base?: 'left' | 'bottom';
  every?: number;
  h?: boolean;
  v?: boolean;
  vEvery?: number;
}) {
  const lines: ReactNode[] = [];
  if (v) for (let i = 0; i <= nx; i += vEvery) lines.push(<line key={`v${i}`} className="grid" x1={x + i * CM} x2={x + i * CM} y1={y} y2={y + ny * CM} />);
  if (h)
    for (let j = 0; j <= ny; j++)
      if ((ny - j) % every === 0) lines.push(<line key={`h${j}`} className="grid" x1={x} x2={x + nx * CM} y1={y + j * CM} y2={y + j * CM} />);
  return (
    <g>
      {lines}
      {base === 'left' && <line className="baseline" x1={x} x2={x} y1={y} y2={y + ny * CM} />}
      {base === 'bottom' && <line className="baseline" x1={x} x2={x + nx * CM} y1={y + ny * CM} y2={y + ny * CM} />}
    </g>
  );
}

/** Use only the decimals the labelled ticks need (0, 2, 4 rather than 0.0, 2.0). */
function defaultFormat(axis: CmAxis, every: number) {
  const labelled = axis.ticks.filter((_, i) => i % every === 0);
  let d = 0;
  while (d < axis.decimals && !labelled.every((t) => Math.abs(t * 10 ** d - Math.round(t * 10 ** d)) < 1e-6)) d++;
  return (v: number) => fmt(v, d);
}

export function XTicks({
  axis,
  x,
  y,
  format,
  every,
}: {
  axis: CmAxis;
  x: number;
  y: number;
  format?: (v: number) => string;
  every?: number;
}) {
  const k = every ?? axis.labelEvery;
  const f = format ?? defaultFormat(axis, k);
  return (
    <g className="tick">
      {axis.ticks.map((t, i) =>
        i % k === 0 ? (
          <text key={i} x={x + i * CM} y={y + 14} textAnchor="middle">
            {f(t)}
          </text>
        ) : null,
      )}
    </g>
  );
}

export function YTicks({
  axis,
  x,
  y,
  reverse,
  format,
  every,
}: {
  axis: CmAxis;
  /** Right edge of the labels. */
  x: number;
  /** Top of the plot. */
  y: number;
  reverse?: boolean;
  format?: (v: number) => string;
  every?: number;
}) {
  const k = every ?? 1;
  const f = format ?? defaultFormat(axis, k);
  return (
    <g className="tick">
      {axis.ticks.map((t, i) => {
        if (i % k !== 0) return null;
        const yy = reverse ? y + i * CM : y + (axis.n - i) * CM;
        return (
          <text key={i} x={x - 6} y={yy + 4} textAnchor="end">
            {f(t)}
          </text>
        );
      })}
    </g>
  );
}

export function AxisTitle({ x, y, children, anchor = 'start' }: { x: number; y: number; children: ReactNode; anchor?: 'start' | 'middle' | 'end' }) {
  return (
    <text className="axis-title" x={x} y={y} textAnchor={anchor}>
      {children}
    </text>
  );
}

/** Lab color dot (lightened for a Sibling) plus the model name. */
export function ModelName({ id, x, y, anchor = 'end', size = 12, text }: { id: string; x: number; y: number; anchor?: 'start' | 'end'; size?: number; text?: string }) {
  const m = modelOf(id);
  const color = LABS[m.lab].color;
  const dotX = anchor === 'end' ? x - 4 : x + 4;
  const textX = anchor === 'end' ? x - 14 : x + 14;
  return (
    <g className="model-name">
      <MarkDot x={dotX} y={y} r={4} color={color} hollow={m.tier === 'sibling'} />
      <text x={textX} y={y + 4} textAnchor={anchor} fontSize={size}>
        {text ?? m.short}
      </text>
    </g>
  );
}

export function MarkDot({ x, y, r, color, hollow, ring = true }: { x: number; y: number; r: number; color: string; hollow?: boolean; ring?: boolean }) {
  return (
    <g>
      {ring && <circle cx={x} cy={y} r={r + 2} className="ring" />}
      {hollow ? (
        <circle cx={x} cy={y} r={r - 1} fill={tint(color)} stroke={color} strokeWidth={2} />
      ) : (
        <circle cx={x} cy={y} r={r} fill={color} />
      )}
    </g>
  );
}

/** Horizontal bar from x0 with a 4px rounded data end, square at the baseline. */
export function hBarPath(x0: number, yc: number, len: number, thick: number) {
  const r = Math.min(4, Math.abs(len) / 2, thick / 2);
  const y0 = yc - thick / 2;
  const y1 = yc + thick / 2;
  if (len <= 0) return `M${x0},${y0}h0v${thick}h0z`;
  const x1 = x0 + len;
  return `M${x0},${y0}H${x1 - r}Q${x1},${y0} ${x1},${y0 + r}V${y1 - r}Q${x1},${y1} ${x1 - r},${y1}H${x0}Z`;
}

/** Vertical column rising from baseline y0 with a 4px rounded top. */
export function vBarPath(xc: number, y0: number, len: number, thick: number) {
  const r = Math.min(4, Math.abs(len) / 2, thick / 2);
  const x0 = xc - thick / 2;
  const x1 = xc + thick / 2;
  if (len <= 0) return `M${x0},${y0}h${thick}v0h${-thick}z`;
  const yt = y0 - len;
  return `M${x0},${y0}V${yt + r}Q${x0},${yt} ${x0 + r},${yt}H${x1 - r}Q${x1},${yt} ${x1},${yt + r}V${y0}Z`;
}

/** Map a pointer event to SVG user units, whatever the canvas zoom. */
export function localPoint(e: React.PointerEvent<SVGElement>) {
  const svg = e.currentTarget instanceof SVGSVGElement ? e.currentTarget : e.currentTarget.ownerSVGElement;
  if (!svg) return { x: 0, y: 0 };
  const rect = svg.getBoundingClientRect();
  const vb = svg.viewBox.baseVal;
  return {
    x: ((e.clientX - rect.left) / rect.width) * vb.width,
    y: ((e.clientY - rect.top) / rect.height) * vb.height,
  };
}

export interface Box {
  x: number;
  y: number;
  w: number;
  h: number;
}

const overlaps = (a: Box, b: Box) => a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;

/**
 * Place point labels without collisions: try right, left, above, below of each
 * point; skip a label only when every spot is taken (the tooltip and table
 * still carry it).
 */
export function placeLabels(
  pts: { id: string; x: number; y: number; text: string; r: number }[],
  bounds: Box,
  blockers: Box[] = [],
) {
  const placed: Box[] = [...blockers, ...pts.map((p) => ({ x: p.x - p.r - 2, y: p.y - p.r - 2, w: 2 * p.r + 4, h: 2 * p.r + 4 }))];
  const out: Record<string, { x: number; y: number; anchor: 'start' | 'end' | 'middle' } | null> = {};
  for (const p of pts) {
    const tw = textWidth(p.text, 11.5);
    const th = 14;
    const gap = p.r + 5;
    const spots: { box: Box; x: number; y: number; anchor: 'start' | 'end' | 'middle' }[] = [
      { box: { x: p.x + gap, y: p.y - th / 2, w: tw, h: th }, x: p.x + gap, y: p.y + 4, anchor: 'start' },
      { box: { x: p.x - gap - tw, y: p.y - th / 2, w: tw, h: th }, x: p.x - gap, y: p.y + 4, anchor: 'end' },
      { box: { x: p.x - tw / 2, y: p.y - gap - th, w: tw, h: th }, x: p.x, y: p.y - gap - 3, anchor: 'middle' },
      { box: { x: p.x - tw / 2, y: p.y + gap, w: tw, h: th }, x: p.x, y: p.y + gap + 11, anchor: 'middle' },
    ];
    const ok = spots.find(
      (s) =>
        s.box.x >= bounds.x &&
        s.box.y >= bounds.y &&
        s.box.x + s.box.w <= bounds.x + bounds.w &&
        s.box.y + s.box.h <= bounds.y + bounds.h &&
        !placed.some((b) => overlaps(b, s.box)),
    );
    if (ok) {
      placed.push(ok.box);
      out[p.id] = { x: ok.x, y: ok.y, anchor: ok.anchor };
    } else out[p.id] = null;
  }
  return out;
}

export function Legend({ items, x, y }: { items: { label: string; color: string; kind?: 'rect' | 'line' | 'dot' | 'ring' }[]; x: number; y: number }) {
  const starts = items.reduce<number[]>((acc, _, i) => [...acc, i === 0 ? x : acc[i - 1] + 18 + textWidth(items[i - 1].label, 11) + 14], []);
  return (
    <g className="legend">
      {items.map((it, i) => {
        const start = starts[i];
        return (
          <g key={it.label}>
            {it.kind === 'line' ? (
              <line x1={start} x2={start + 12} y1={y} y2={y} stroke={it.color} strokeWidth={2} strokeLinecap="round" />
            ) : it.kind === 'dot' ? (
              <circle cx={start + 5} cy={y} r={4} fill={it.color} />
            ) : it.kind === 'ring' ? (
              <circle cx={start + 5} cy={y} r={3.5} fill={tint(it.color, 0.7)} stroke={it.color} strokeWidth={2} />
            ) : (
              <rect x={start} y={y - 5} width={12} height={10} rx={2} fill={it.color} />
            )}
            <text x={start + 18} y={y + 4}>
              {it.label}
            </text>
          </g>
        );
      })}
    </g>
  );
}
