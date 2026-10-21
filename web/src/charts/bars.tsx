import type { ReactNode } from 'react';
import { useTip } from '../components/Tooltip';
import { LABS, MODEL_BY_ID } from '../data/models';
import { CM } from '../lib/units';
import { AxisTitle, Legend, ModelName, Svg, Watermark, XTicks, hBarPath, localPoint, textWidth, type ChartProps } from './kit';
import { cmAxis, fitAxis, type AxisOptions } from './scale';

export interface BarRow {
  id: string;
  v: number;
  lo?: number;
  hi?: number;
}

interface BarCIProps extends ChartProps {
  label: string;
  rows: BarRow[];
  /** Value label beside each bar. */
  format: (v: number) => string;
  tickFormat?: (v: number) => string;
  axis?: AxisOptions;
  sort?: 'desc' | 'asc' | 'none';
  refLine?: { v: number; label: string };
  xTitle: string;
  simulated?: boolean;
  note?: string;
}

/** Horizontal bars with 95% interval whiskers, one row per model. */
export function BarCI(p: BarCIProps) {
  const tip = useTip();
  const L = p.expanded ? 4 : 3;
  const R = 1;
  const nx = p.w - L - R;
  const rows = p.rows.filter((r) => p.models.includes(r.id));
  if (p.sort === 'desc') rows.sort((a, b) => b.v - a.v);
  if (p.sort === 'asc') rows.sort((a, b) => a.v - b.v);
  const ny = p.h - 1;
  const pitch = Math.max(1, Math.floor(ny / Math.max(rows.length, 1)));
  const top = Math.max(0, ny - rows.length * pitch);
  const lo = Math.min(...rows.map((r) => r.lo ?? r.v), p.refLine?.v ?? Infinity);
  const hi = Math.max(...rows.map((r) => r.hi ?? r.v), p.refLine?.v ?? -Infinity);
  const axis = cmAxis(lo, hi, nx, { zero: true, labelCm: 1, ...p.axis });
  const x0 = L * CM;
  const y0 = top * CM;
  const thick = Math.min(24, pitch * CM * 0.42);
  const base = axis.pos(0);

  const onMove = (e: React.PointerEvent<SVGRectElement>) => {
    const pt = localPoint(e);
    const i = Math.floor((pt.y - y0) / (pitch * CM));
    const r = rows[i];
    if (!r) return tip.hide();
    const m = MODEL_BY_ID[r.id];
    tip.show({
      x: e.clientX,
      y: e.clientY,
      title: m.name,
      rows: [
        { value: p.format(r.v), label: p.xTitle, color: LABS[m.lab].color, hollow: m.tier === 'sibling' },
        ...(r.lo !== undefined && r.hi !== undefined ? [{ value: `${p.format(r.lo)} – ${p.format(r.hi)}`, label: '95% interval' }] : []),
      ],
      note: p.note,
    });
  };

  return (
    <Svg w={p.w} h={p.h} label={p.label}>
      {axis.ticks.map((t) => (
        <line key={t} className="grid" x1={x0 + axis.pos(t)} x2={x0 + axis.pos(t)} y1={y0} y2={y0 + rows.length * pitch * CM} />
      ))}
      {p.simulated && <Watermark x={x0} y={y0} w={nx * CM} h={rows.length * pitch * CM} />}
      {p.refLine && (
        <g>
          <line className="refline" x1={x0 + axis.pos(p.refLine.v)} x2={x0 + axis.pos(p.refLine.v)} y1={y0 - 4} y2={y0 + rows.length * pitch * CM} />
          <text className="ref-label" x={x0 + axis.pos(p.refLine.v) + 5} y={y0 + rows.length * pitch * CM - 6}>
            {p.refLine.label}
          </text>
        </g>
      )}
      {rows.map((r, i) => {
        const m = MODEL_BY_ID[r.id];
        const color = LABS[m.lab].color;
        const yc = y0 + (i + 0.5) * pitch * CM;
        const len = axis.pos(r.v) - base;
        const hiX = x0 + axis.pos(r.hi ?? r.v);
        return (
          <g key={r.id}>
            <ModelName id={r.id} x={x0 - 8} y={yc} />
            <path
              d={hBarPath(x0 + base, yc, len, thick)}
              fill={color}
              fillOpacity={m.tier === 'sibling' ? 0.3 : 1}
              stroke={m.tier === 'sibling' ? color : 'none'}
              strokeWidth={1.5}
            />
            {r.lo !== undefined && r.hi !== undefined && (
              <g className="whisker">
                <line x1={x0 + axis.pos(r.lo)} x2={hiX} y1={yc} y2={yc} />
                <line x1={x0 + axis.pos(r.lo)} x2={x0 + axis.pos(r.lo)} y1={yc - 5} y2={yc + 5} />
                <line x1={hiX} x2={hiX} y1={yc - 5} y2={yc + 5} />
              </g>
            )}
            <text className="value" x={Math.max(hiX, x0 + base + len) + 6} y={yc + 4}>
              {p.format(r.v)}
            </text>
          </g>
        );
      })}
      <XTicks axis={axis} x={x0} y={y0 + rows.length * pitch * CM} format={p.tickFormat} />
      <AxisTitle x={x0 + nx * CM} y={y0 + rows.length * pitch * CM + 31} anchor="end">
        {p.xTitle}
      </AxisTitle>
      <rect className="hit" x={0} y={y0} width={p.w * CM} height={rows.length * pitch * CM} onPointerMove={onMove} onPointerLeave={tip.hide} />
    </Svg>
  );
}

interface RangeRow {
  id: string;
  lo: number;
  hi: number;
  /** True when the low end is a real estimate rather than equal to the high end. */
  hasRange: boolean;
}

/** Low–high range per model, e.g. cost with and without prompt caching. */
export function RangeBars(p: ChartProps & { label: string; rows: RangeRow[]; format: (v: number) => string; xTitle: string; note?: string }) {
  const tip = useTip();
  const L = p.expanded ? 4 : 3;
  const nx = p.w - L - 1;
  const rows = p.rows.filter((r) => p.models.includes(r.id)).sort((a, b) => b.hi - a.hi);
  const ny = p.h - 1;
  const pitch = Math.max(1, Math.floor(ny / Math.max(rows.length, 1)));
  const top = Math.max(0, ny - rows.length * pitch);
  const axis = cmAxis(0, Math.max(...rows.map((r) => r.hi)), nx, { zero: true, labelCm: 1 });
  const x0 = L * CM;
  const y0 = top * CM;

  const onMove = (e: React.PointerEvent<SVGRectElement>) => {
    const pt = localPoint(e);
    const r = rows[Math.floor((pt.y - y0) / (pitch * CM))];
    if (!r) return tip.hide();
    const m = MODEL_BY_ID[r.id];
    tip.show({
      x: e.clientX,
      y: e.clientY,
      title: m.name,
      rows: [
        { value: p.format(r.hi), label: 'No caching', color: LABS[m.lab].color },
        { value: r.hasRange ? p.format(r.lo) : 'not published', label: '90% of input cached' },
      ],
      note: p.note,
    });
  };

  return (
    <Svg w={p.w} h={p.h} label={p.label}>
      {axis.ticks.map((t) => (
        <line key={t} className="grid" x1={x0 + axis.pos(t)} x2={x0 + axis.pos(t)} y1={y0} y2={y0 + rows.length * pitch * CM} />
      ))}
      {rows.map((r, i) => {
        const m = MODEL_BY_ID[r.id];
        const color = LABS[m.lab].color;
        const yc = y0 + (i + 0.5) * pitch * CM;
        const xl = x0 + axis.pos(r.lo);
        const xh = x0 + axis.pos(r.hi);
        return (
          <g key={r.id}>
            <ModelName id={r.id} x={x0 - 8} y={yc} />
            {r.hasRange && <line x1={xl} x2={xh} y1={yc} y2={yc} stroke={color} strokeWidth={6} strokeLinecap="round" strokeOpacity={0.55} />}
            {r.hasRange && <circle cx={xl} cy={yc} r={4} fill="var(--panel)" stroke={color} strokeWidth={2} />}
            <circle cx={xh} cy={yc} r={5} fill={color} />
            <text className="value" x={xh + 9} y={yc + 4}>
              {r.hasRange ? `${p.format(r.lo)}–${p.format(r.hi)}` : p.format(r.hi)}
            </text>
          </g>
        );
      })}
      <XTicks axis={axis} x={x0} y={y0 + rows.length * pitch * CM} format={(v) => `$${v}`} />
      <AxisTitle x={x0 + nx * CM} y={y0 + rows.length * pitch * CM + 31} anchor="end">
        {p.xTitle}
      </AxisTitle>
      <rect className="hit" x={0} y={y0} width={p.w * CM} height={rows.length * pitch * CM} onPointerMove={onMove} onPointerLeave={tip.hide} />
    </Svg>
  );
}

/** White or navy text, whichever reads on the fill. */
function inkOn(hex: string) {
  const c = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255).map((v) => (v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4));
  const lum = 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2];
  return lum < 0.3 ? '#ffffff' : '#0b2a55';
}

export interface Segment {
  key: string;
  label: string;
  color: string;
}

interface StackRow {
  id: string;
  /** Row label; a model id renders the model name. */
  label?: string;
  values: Record<string, number>;
  n?: string;
}

/** 100% stacked horizontal bars with a 2px surface gap between segments. */
export function Stacked(p: ChartProps & { label: string; rows: StackRow[]; segments: Segment[]; isModel?: boolean; simulated?: boolean; xTitle: string; note?: string }) {
  const tip = useTip();
  const L = p.isModel ? (p.expanded ? 4 : 3) : p.expanded ? 5 : 4;
  // The bars span the full width; rows share the full height.
  const nx = p.w - L - 1;
  const rows = p.isModel ? p.rows.filter((r) => p.models.includes(r.id)) : p.rows;
  // Legend row on top; the tick row only when there is room for it (segment labels, tooltip and table carry the values).
  const showAxis = rows.length + 2 <= p.h;
  const ny = p.h - (showAxis ? 2 : 1);
  const pitch = Math.min(2.5, Math.max(1, ny / Math.max(rows.length, 1)));
  const x0 = L * CM;
  const y0 = CM;
  const axis = fitAxis(0, 1, nx * CM, { minGap: 46 });
  const thick = Math.min(24, pitch * CM * 0.46);

  const onMove = (e: React.PointerEvent<SVGRectElement>) => {
    const pt = localPoint(e);
    const r = rows[Math.floor((pt.y - y0) / (pitch * CM))];
    if (!r) return tip.hide();
    tip.show({
      x: e.clientX,
      y: e.clientY,
      title: p.isModel ? MODEL_BY_ID[r.id].name : (r.label ?? r.id),
      rows: p.segments.map((s) => ({ value: `${Math.round(r.values[s.key] * 100)}%`, label: s.label, color: s.color })),
      note: r.n ?? p.note,
    });
  };

  const fits = (text: string, width: number) => textWidth(text, 11) + 10 <= width;

  return (
    <Svg w={p.w} h={p.h} label={p.label}>
      <Legend items={p.segments.map((s) => ({ label: s.label, color: s.color }))} x={x0} y={CM / 2} />
      {axis.ticks.map((t) => (
        <line key={t} className="grid" x1={x0 + axis.pos(t)} x2={x0 + axis.pos(t)} y1={y0} y2={y0 + rows.length * pitch * CM} />
      ))}
      {rows.map((r, i) => {
        const yc = y0 + (i + 0.5) * pitch * CM;
        let acc = 0;
        const segs: ReactNode[] = [];
        for (const s of p.segments) {
          const v = r.values[s.key] ?? 0;
          if (v <= 0) continue;
          const xs = x0 + axis.pos(acc);
          const xe = x0 + axis.pos(acc + v);
          acc += v;
          const first = xs <= x0 + 0.5;
          const last = acc >= 0.9999;
          const gapL = first ? 0 : 1;
          const gapR = last ? 0 : 1;
          const width = Math.max(0, xe - xs - gapL - gapR);
          const text = `${Math.round(v * 100)}%`;
          segs.push(
            <g key={s.key}>
              <rect x={xs + gapL} y={yc - thick / 2} width={width} height={thick} rx={last ? 3 : 0} fill={s.color} />
              {last && <rect x={xs + gapL} y={yc - thick / 2} width={Math.min(4, width)} height={thick} fill={s.color} />}
              {fits(text, width) && (
                <text className="seg-label" fill={inkOn(s.color)} x={xs + gapL + width / 2} y={yc + 4} textAnchor="middle">
                  {text}
                </text>
              )}
            </g>,
          );
        }
        return (
          <g key={r.id}>
            {p.isModel ? (
              <ModelName id={r.id} x={x0 - 8} y={yc} />
            ) : (
              <text className="row-label" x={x0 - 10} y={yc + 4} textAnchor="end">
                {r.label}
              </text>
            )}
            {segs}
          </g>
        );
      })}
      {p.simulated && <Watermark x={x0} y={y0} w={nx * CM} h={rows.length * pitch * CM} />}
      {showAxis && (
        <>
          <g className="tick">
            {axis.ticks.map((t) => (
              <text key={t} x={x0 + axis.pos(t)} y={y0 + rows.length * pitch * CM + 14} textAnchor="middle">
                {Math.round(t * 100)}%
              </text>
            ))}
          </g>
          <AxisTitle x={x0 + nx * CM} y={y0 + rows.length * pitch * CM + 31} anchor="end">
            {p.xTitle}
          </AxisTitle>
        </>
      )}
      <rect className="hit" x={0} y={y0} width={p.w * CM} height={rows.length * pitch * CM} onPointerMove={onMove} onPointerLeave={tip.hide} />
    </Svg>
  );
}
