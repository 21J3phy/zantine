import { useTip, type TipRow } from '../components/Tooltip';
import { EFFORT_LABEL, LABS, effortOf, entryName, fillOf, modelOf } from '../data/models';
import type { Est } from '../data/sim';
import { CM } from '../lib/units';
import { Svg, localPoint, textWidth, vBarPath, type ChartProps } from './kit';
import { ModelBars } from './modelbars';

interface HighlightProps extends ChartProps {
  label: string;
  rows: ({ id: string } & Est)[];
  /** Sort order: the first column is the highest value, or the lowest. */
  order: 'high-first' | 'low-first';
  format: (v: number) => string;
  /** Tooltip label for the value. */
  valueLabel: string;
  simulated?: boolean;
  note?: string;
  /** Extra tooltip rows. */
  detail?: (id: string) => TipRow[];
  /** A score's maximum: the scale ends there, with a labelled line. */
  max?: { v: number; label: string };
}

/**
 * A headline column chart: one column per model and effort setting, sorted, with its value on the cap,
 * its 95% interval, and the name beneath. Full screen only; a card draws ModelBars.
 */
export function Highlight(p: HighlightProps) {
  // A card shows each model once, at its highest effort; full screen shows every setting.
  if (!p.expanded) return <ModelBars {...p} rows={p.rows} order={p.order} format={p.format} valueLabel={p.valueLabel} max={undefined} />;
  return <HighlightDetail {...p} />;
}

function HighlightDetail(p: HighlightProps) {
  const tip = useTip();
  const pool = p.rows.filter((r) => p.models.includes(r.id));
  const sorted = pool.slice().sort((a, b) => (p.order === 'high-first' ? b.v - a.v : a.v - b.v));
  const shown = sorted;

  // Names run down and to the left at 45°, the effort level after the model in a lighter ink.
  const angle = -45;
  const sin = Math.sin((-angle * Math.PI) / 180);
  const cos = Math.cos((-angle * Math.PI) / 180);
  const effortText = (id: string) => {
    const e = effortOf(id);
    return e ? EFFORT_LABEL[e].toLowerCase() : '';
  };
  // textWidth's 0.56 em runs wide for Geist; 0.9 of it is close to the rendered width.
  const nameW = (id: string) => 0.9 * textWidth(`${modelOf(id).short} ${effortText(id)}`, 10.5);
  const n = Math.max(shown.length, 1);
  // Room on the left for the first name, which runs past the first column.
  let x0 = 0.35 * CM;
  for (let k = 0; k < 2; k++) {
    const u0 = (p.w * CM - x0 - 0.35 * CM) / n;
    // The full estimate here (not 0.9 of it): a clipped first name is worse than a little extra margin.
    x0 = Math.max(0.35 * CM, (shown.length ? (nameW(shown[0].id) / 0.9) * cos : 0) - u0 / 2 + 6);
  }
  const W = p.w * CM - x0 - 0.35 * CM;
  // Sized for the longest name of any setting, so the three headline cards share a baseline.
  const labelH = 14 + Math.max(0, ...p.rows.map((r) => nameW(r.id))) * sin + 6;
  const y0 = 0.75 * CM;
  const base = p.h * CM - labelH;
  const top = p.max ? p.max.v : Math.max(...shown.map((r) => r.hi)) * 1.04;
  const Y = (v: number) => base - (v / top) * (base - y0);
  const u = W / n;
  const barW = Math.min(24, u * 0.62);
  const xc = (i: number) => x0 + u * (i + 0.5);

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
      {p.max && (
        <g>
          <line className="hl-max" x1={x0} x2={x0 + W} y1={Y(p.max.v)} y2={Y(p.max.v)} />
          <text className="hl-max-label" x={x0} y={Y(p.max.v) - 6}>
            {p.max.label}
          </text>
        </g>
      )}
      {shown.length < sorted.length && (
        <text className="hl-max-label" x={x0 + W} y={y0 - 6} textAnchor="end">
          Top {shown.length} of {sorted.length}
        </text>
      )}
      <line className="baseline" x1={x0} x2={x0 + W} y1={base} y2={base} />
      {shown.map((r, i) => {
        const m = modelOf(r.id);
        const x = xc(i);
        return (
          <g key={r.id}>
            <path d={vBarPath(x, base, base - Y(r.v), barW)} fill={fillOf(r.id)} />
            {r.hi > r.lo && (
              <g className="whisker">
                <line x1={x} x2={x} y1={Y(r.lo)} y2={Y(r.hi)} />
              </g>
            )}
            <text className="hl-value" x={x} y={Y(r.hi) - 5} textAnchor="middle">
              {p.format(r.v)}
            </text>
            <text className="hl-name" transform={`translate(${x + 3} ${base + 14}) rotate(${angle})`} textAnchor="end">
              {m.short} <tspan className="hl-effort">{effortText(r.id)}</tspan>
            </text>
          </g>
        );
      })}
      <rect className="hit" x={x0} y={y0} width={W} height={base - y0 + labelH * 0.4} onPointerMove={onMove} onPointerLeave={tip.hide} />
    </Svg>
  );
}
