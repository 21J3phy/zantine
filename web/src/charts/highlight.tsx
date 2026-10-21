import { useTip, type TipRow } from '../components/Tooltip';
import { EFFORT_LABEL, LABS, effortOf, entryName, modelOf } from '../data/models';
import type { Est } from '../data/sim';
import { CM } from '../lib/units';
import { Svg, Watermark, localPoint, textWidth, vBarPath, type ChartProps } from './kit';

interface HighlightProps extends ChartProps {
  label: string;
  rows: ({ id: string } & Est)[];
  /** Sort order: the first column is the highest value, or the lowest. */
  order: 'high-first' | 'low-first';
  format: (v: number) => string;
  /** Tooltip label for the value. */
  valueLabel: string;
  /** Columns on the card; the full-screen view shows every active entry. */
  limit?: number;
  simulated?: boolean;
  note?: string;
  /** Extra tooltip rows. */
  detail?: (id: string) => TipRow[];
  /** A score's maximum: the scale ends there, each column sits in a track up to it, and the line is labelled. */
  max?: { v: number; label: string };
}

/** White or dark text, whichever reads on the fill. */
function inkOn(hex: string) {
  const c = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255).map((v) => (v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4));
  return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2] < 0.3 ? '#ffffff' : '#141b26';
}

/** A round step for the dotted guide lines. */
function niceStep(max: number) {
  const raw = max / 4;
  const p = 10 ** Math.floor(Math.log10(raw));
  return [1, 2, 2.5, 5, 10].map((k) => k * p).find((s) => s >= raw) ?? raw;
}

/**
 * A headline column chart: one column per model and effort setting, sorted, with the value on
 * the column, its 95% interval, and the name beneath. The card shows the first `limit` columns.
 */
export function Highlight(p: HighlightProps) {
  const tip = useTip();
  const pool = p.rows.filter((r) => p.models.includes(r.id));
  const sorted = pool.slice().sort((a, b) => (p.order === 'high-first' ? b.v - a.v : a.v - b.v));
  const shown = p.expanded || !p.limit ? sorted : sorted.slice(0, p.limit);

  // Names run down and to the left at 50°: on two lines (model, then effort) where there is room
  // between columns, else on one.
  const angle = -50;
  const sin = Math.sin((-angle * Math.PI) / 180);
  const cos = Math.cos((-angle * Math.PI) / 180);
  const effortText = (id: string) => {
    const e = effortOf(id);
    return e ? `(${EFFORT_LABEL[e].toLowerCase()})` : '';
  };
  const n = Math.max(shown.length, 1);
  const twoLine = ((p.w * CM - 0.7 * CM) / n) * sin >= 25;
  // textWidth's 0.56 em runs wide for Geist; 0.9 of it is close to the rendered width.
  const nameW = (id: string) => {
    const m = modelOf(id).short;
    return 0.9 * (twoLine ? Math.max(textWidth(m, 10.5), textWidth(effortText(id), 10.5)) : textWidth(`${m} ${effortText(id)}`, 10.5));
  };
  // Room on the left for the first name, which runs past the first column.
  let x0 = 0.35 * CM;
  for (let k = 0; k < 2; k++) {
    const u0 = (p.w * CM - x0 - 0.35 * CM) / n;
    // The full estimate here (not 0.9 of it): a clipped first name is worse than a little extra margin.
    x0 = Math.max(0.35 * CM, (shown.length ? (nameW(shown[0].id) / 0.9) * cos : 0) - u0 / 2 + 6);
  }
  const W = p.w * CM - x0 - 0.35 * CM;
  // Sized for the longest name of any setting, so the three headline cards share a baseline.
  const labelH = 21 + Math.max(0, ...p.rows.map((r) => nameW(r.id))) * sin + (twoLine ? 14 : 6);
  const y0 = 0.75 * CM;
  const base = p.h * CM - labelH;
  const top = p.max ? p.max.v : Math.max(...shown.map((r) => r.hi)) * 1.04;
  const Y = (v: number) => base - (v / top) * (base - y0);
  const u = W / n;
  const barW = Math.min(36, u * 0.74);
  const xc = (i: number) => x0 + u * (i + 0.5);
  const step = niceStep(top);
  const guides = Array.from({ length: Math.floor(top / step) }, (_, i) => (i + 1) * step).filter((g) => !p.max || g < p.max.v);

  const onMove = (e: React.PointerEvent<SVGRectElement>) => {
    const i = Math.floor((localPoint(e).x - x0) / u);
    const r = shown[i];
    if (!r) return tip.hide();
    const m = modelOf(r.id);
    tip.show({
      x: e.clientX,
      y: e.clientY,
      title: entryName(r.id),
      rows: [
        { value: p.format(r.v), label: p.valueLabel, color: LABS[m.lab].color, hollow: m.tier === 'sibling' },
        ...(r.hi > r.lo ? [{ value: `${p.format(r.lo)} – ${p.format(r.hi)}`, label: '95% interval' }] : []),
        ...(p.max ? [{ value: p.format(p.max.v - r.v), label: 'Short of a perfect score' }] : []),
        ...(p.detail?.(r.id) ?? []),
      ],
      note: p.note,
    });
  };

  return (
    <Svg w={p.w} h={p.h} label={p.label}>
      {guides.map((g) => (
        <line key={g} className="guide" x1={x0} x2={x0 + W} y1={Y(g)} y2={Y(g)} />
      ))}
      <line className="baseline" x1={x0} x2={x0 + W} y1={base} y2={base} />
      {p.simulated && <Watermark x={x0} y={y0} w={W} h={base - y0} />}
      {shown.map((r, i) => {
        const m = modelOf(r.id);
        const color = LABS[m.lab].color;
        const sib = m.tier === 'sibling';
        const x = xc(i);
        const value = p.format(r.v);
        const len = base - Y(r.v);
        // The value sits inside the column, just under its interval; on a short column, above it.
        const inside = barW >= textWidth(value, 10.5) + 6 && len >= 22;
        const vy = inside ? Math.min(base - 7, Y(r.lo) + 15) : Y(r.hi) - 6;
        const effort = effortText(r.id);
        const ny = base + 21;
        return (
          <g key={r.id}>
            {p.max && <rect className="hl-track" x={x - barW / 2} y={Y(p.max.v)} width={barW} height={base - Y(p.max.v)} rx={4} />}
            <path d={vBarPath(x, base, len, barW)} fill={color} fillOpacity={sib ? 0.3 : 1} stroke={sib ? color : 'none'} strokeWidth={1.5} />
            {r.hi > r.lo && (
              <g className="whisker">
                <line x1={x} x2={x} y1={Y(r.lo)} y2={Y(r.hi)} />
                <line x1={x - 4} x2={x + 4} y1={Y(r.hi)} y2={Y(r.hi)} />
                <line x1={x - 4} x2={x + 4} y1={Y(r.lo)} y2={Y(r.lo)} />
              </g>
            )}
            <text className="hl-value" x={x} y={vy} textAnchor="middle" fill={inside && !sib ? inkOn(color) : 'var(--ink)'}>
              {value}
            </text>
            <circle cx={x} cy={base + 9} r={3.5} fill={sib ? 'var(--panel)' : color} stroke={color} strokeWidth={sib ? 1.75 : 0} />
            <text className="hl-name" transform={`translate(${x + 3} ${ny}) rotate(${angle})`} textAnchor="end">
              {twoLine ? (
                <>
                  <tspan x={0} dy={0}>
                    {m.short}
                  </tspan>
                  <tspan className="hl-effort" x={0} dy={12}>
                    {effort}
                  </tspan>
                </>
              ) : (
                `${m.short} ${effort}`
              )}
            </text>
          </g>
        );
      })}
      {p.max && (
        <g>
          <line className="hl-max" x1={x0} x2={x0 + W} y1={Y(p.max.v)} y2={Y(p.max.v)} />
          <text className="hl-max-label" x={x0} y={Y(p.max.v) - 6}>
            {p.max.label}
          </text>
        </g>
      )}
      {shown.length < sorted.length && (
        <text className="axis-note" x={x0 + W} y={y0 - 10} textAnchor="end">
          {shown.length} of {sorted.length} settings · all in full view
        </text>
      )}
      <rect className="hit" x={x0} y={y0} width={W} height={base - y0 + labelH * 0.4} onPointerMove={onMove} onPointerLeave={tip.hide} />
    </Svg>
  );
}
