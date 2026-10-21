import { useTip, type TipRow } from '../components/Tooltip';
import { LABS, entryLabel, entryName, fillOf, modelOf, topEntries } from '../data/models';
import { CM } from '../lib/units';
import { Svg, inkOn, localPoint, textWidth, vBarPath, type ChartProps } from './kit';
import { LabLogo } from './logos';

// A figure card's bar chart, laid out as Artificial Analysis lays out its own: one column per model at
// its highest reasoning effort, sorted, the value inside the column, dashed guides with no axis labels,
// and the lab's mark and the model's name under each column. The full-screen view shows every effort
// level with its 95% interval.

export interface ModelBarRow {
  id: string;
  v: number;
  lo?: number;
  hi?: number;
}

interface ModelBarsProps extends ChartProps {
  label: string;
  rows: ModelBarRow[];
  order: 'high-first' | 'low-first';
  format: (v: number) => string;
  /** Tooltip label for the value. */
  valueLabel: string;
  /** Where the scale ends, when the measure has a maximum (a Score out of 100). */
  max?: number;
  note?: string;
  detail?: (id: string) => TipRow[];
}

/** A round step giving three to five guides up to `max`. */
function niceStep(max: number) {
  const raw = max / 4;
  const p = 10 ** Math.floor(Math.log10(raw));
  return [1, 2, 2.5, 5, 10].map((k) => k * p).find((s) => s >= raw) ?? raw;
}

const NAME_FONT = 11.5;
const ANGLE = -55;
const SIN = Math.sin((-ANGLE * Math.PI) / 180);
const COS = Math.cos((-ANGLE * Math.PI) / 180);
const LINE = 13;
/** Characters per line before a name wraps; a second line that is still too long ends in an ellipsis. */
const LINE_CHARS = 13;

/** A name over at most two lines, broken between words. */
export function nameLines(s: string): string[] {
  const words = s.split(' ');
  const lines: string[] = [''];
  for (const w of words) {
    const cur = lines[lines.length - 1];
    if (!cur || (cur + ' ' + w).length <= LINE_CHARS || lines.length === 2) lines[lines.length - 1] = cur ? `${cur} ${w}` : w;
    else lines.push(w);
  }
  if (lines[1] && lines[1].length > LINE_CHARS + 2) lines[1] = `${lines[1].slice(0, LINE_CHARS)}…`;
  return lines;
}

/** Rendered width of a line of the name (textWidth runs about a tenth wide for Geist). */
const lineW = (s: string) => 0.9 * textWidth(s, NAME_FONT);

/** Height below the baseline that the marks and the longest angled name need. */
export function tickHeight(ids: string[]) {
  const longest = Math.max(0, ...ids.map((id) => Math.max(...nameLines(entryLabel(id)).map(lineW))));
  return 34 + longest * SIN + LINE * COS + 6;
}

/** Under each column: the lab's mark, then the model's name running down to the left. */
export function ModelTicks({ ids, xs, y }: { ids: string[]; xs: Record<string, number>; y: number }) {
  return (
    <g className="model-ticks">
      {ids.map((id) => {
        const x = xs[id];
        const lines = nameLines(entryLabel(id));
        return (
          <g key={id}>
            <LabLogo lab={modelOf(id).lab} x={x} y={y + 13} size={18} />
            <text className="tick-name" transform={`translate(${x + 5} ${y + 34}) rotate(${ANGLE})`} textAnchor="end">
              {lines.map((l, i) => (
                <tspan key={i} x={0} dy={i === 0 ? 0 : LINE}>
                  {l}
                </tspan>
              ))}
            </text>
          </g>
        );
      })}
    </g>
  );
}

/** Room on the left for the first column's name, which runs past the column. */
export function leftRoom(firstId: string | undefined, slot: number) {
  if (!firstId) return 6;
  const w = Math.max(...nameLines(entryLabel(firstId)).map(lineW));
  return Math.max(6, w * COS + LINE * SIN - slot / 2 + 2);
}

export function ModelBars(p: ModelBarsProps) {
  const tip = useTip();
  const ids = topEntries(p.models.filter((id) => p.rows.some((r) => r.id === id)));
  const rows = ids
    .map((id) => p.rows.find((r) => r.id === id)!)
    .sort((a, b) => (p.order === 'high-first' ? b.v - a.v : a.v - b.v));
  const n = Math.max(rows.length, 1);

  const W = p.w * CM;
  let x0 = 6;
  for (let k = 0; k < 2; k++) x0 = leftRoom(rows[0]?.id, (W - x0 - 4) / n);
  const plotW = W - x0 - 4;
  const u = plotW / n;
  const barW = Math.min(84, u * 0.62);
  const xs: Record<string, number> = Object.fromEntries(rows.map((r, i) => [r.id, x0 + u * (i + 0.5)]));

  const y0 = 10;
  const base = p.h * CM - tickHeight(rows.map((r) => r.id));
  const vmax = Math.max(0, ...rows.map((r) => r.v));
  // All zeros (no Invalid Actions at all, say) still needs a scale.
  const step = niceStep((p.max ?? vmax) || 1);
  const top = p.max ?? Math.max(step, Math.ceil(vmax / step) * step);
  const Y = (v: number) => base - (v / top) * (base - y0);
  const guides = Array.from({ length: Math.round(top / step) + 1 }, (_, i) => i * step);

  const onMove = (e: React.PointerEvent<SVGRectElement>) => {
    const r = rows[Math.floor((localPoint(e).x - x0) / u)];
    if (!r) return tip.hide();
    const m = modelOf(r.id);
    tip.show({
      x: e.clientX,
      y: e.clientY,
      title: entryName(r.id),
      rows: [
        { value: p.format(r.v), label: p.valueLabel, color: LABS[m.lab].color, hollow: m.tier === 'sibling' },
        ...(r.lo !== undefined && r.hi !== undefined && r.hi > r.lo ? [{ value: `${p.format(r.lo)} – ${p.format(r.hi)}`, label: '95% interval' }] : []),
        ...(p.detail?.(r.id) ?? []),
      ],
      note: p.note,
    });
  };

  return (
    <Svg w={p.w} h={p.h} label={p.label}>
      {guides.map((g) => (
        <line key={g} className="guide-dash" x1={x0} x2={x0 + plotW} y1={Y(g)} y2={Y(g)} />
      ))}
      {rows.map((r) => {
        const x = xs[r.id];
        const fill = fillOf(r.id);
        const len = Math.max(0, base - Y(r.v));
        const value = p.format(r.v);
        const inside = len >= 28 && barW >= textWidth(value, 14) * 0.85 + 4;
        return (
          <g key={r.id}>
            <path d={vBarPath(x, base, len, barW)} fill={fill} />
            <text className="bar-value" x={x} y={inside ? base - len / 2 + 5 : base - len - 7} textAnchor="middle" fill={inside ? inkOn(fill) : 'var(--ink)'}>
              {value}
            </text>
          </g>
        );
      })}
      <ModelTicks ids={rows.map((r) => r.id)} xs={xs} y={base} />
      <rect className="hit" x={x0} y={y0} width={plotW} height={p.h * CM - y0} onPointerMove={onMove} onPointerLeave={tip.hide} />
    </Svg>
  );
}

interface ModelStacksProps extends ChartProps {
  label: string;
  rows: { id: string; values: Record<string, number> }[];
  segments: { key: string; label: string; color: string }[];
  note?: string;
}

/** The card version of a stacked share chart: one column per model at its highest effort, in fixed order. */
export function ModelStacks(p: ModelStacksProps) {
  const tip = useTip();
  const ids = topEntries(p.models.filter((id) => p.rows.some((r) => r.id === id)));
  const n = Math.max(ids.length, 1);
  const W = p.w * CM;
  let x0 = 6;
  for (let k = 0; k < 2; k++) x0 = leftRoom(ids[0], (W - x0 - 4) / n);
  const plotW = W - x0 - 4;
  const u = plotW / n;
  const barW = Math.min(84, u * 0.62);
  const xs: Record<string, number> = Object.fromEntries(ids.map((id, i) => [id, x0 + u * (i + 0.5)]));
  const y0 = 34;
  const base = p.h * CM - tickHeight(ids);
  const Y = (v: number) => base - v * (base - y0);

  const onMove = (e: React.PointerEvent<SVGRectElement>) => {
    const id = ids[Math.floor((localPoint(e).x - x0) / u)];
    if (!id) return tip.hide();
    const r = p.rows.find((x) => x.id === id)!;
    tip.show({ x: e.clientX, y: e.clientY, title: entryName(id), rows: p.segments.map((s) => ({ value: `${Math.round((r.values[s.key] ?? 0) * 100)}%`, label: s.label, color: s.color })), note: p.note });
  };

  let lx = x0;
  return (
    <Svg w={p.w} h={p.h} label={p.label}>
      <g className="legend">
        {p.segments.map((s) => {
          const at = lx;
          lx += 30 + textWidth(s.label, 11) * 0.9;
          return (
            <g key={s.key}>
              <rect x={at} y={5} width={11} height={11} rx={2} fill={s.color} />
              <text x={at + 17} y={14.5}>
                {s.label}
              </text>
            </g>
          );
        })}
      </g>
      {[0, 0.25, 0.5, 0.75, 1].map((g) => (
        <line key={g} className="guide-dash" x1={x0} x2={x0 + plotW} y1={Y(g)} y2={Y(g)} />
      ))}
      {ids.map((id) => {
        const r = p.rows.find((x) => x.id === id)!;
        const x = xs[id];
        let acc = 0;
        return (
          <g key={id}>
            {p.segments.map((s) => {
              const v = r.values[s.key] ?? 0;
              if (v <= 0) return null;
              const yb = Y(acc);
              acc += v;
              const yt = Y(acc);
              const top = acc >= 0.9999;
              // A 2px gap of page white between segments.
              const h = Math.max(0, yb - yt - (yb < base ? 2 : 0));
              const text = `${Math.round(v * 100)}`;
              return (
                <g key={s.key}>
                  <path d={top ? vBarPath(x, yt + h, h, barW) : `M${x - barW / 2},${yt}h${barW}v${h}h${-barW}z`} fill={s.color} />
                  {h >= 16 && barW >= 26 && (
                    <text className="seg-value" x={x} y={yt + h / 2 + 4} textAnchor="middle" fill={inkOn(s.color)}>
                      {text}
                    </text>
                  )}
                </g>
              );
            })}
          </g>
        );
      })}
      <ModelTicks ids={ids} xs={xs} y={base} />
      <rect className="hit" x={x0} y={y0} width={plotW} height={p.h * CM - y0} onPointerMove={onMove} onPointerLeave={tip.hide} />
    </Svg>
  );
}
