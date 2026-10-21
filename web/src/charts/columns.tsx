import type { ReactNode } from 'react';
import { useTip, type TipRow } from '../components/Tooltip';
import { EFFORTS, EFFORT_LABEL, EFFORT_TAG, LABS, effortOf, entryName, fillOf, modelOf } from '../data/models';
import { CM } from '../lib/units';
import { AxisTitle, Legend, Svg, localPoint, textWidth, vBarPath, type ChartProps } from './kit';
import { ModelBars, ModelStacks } from './modelbars';
import { fitAxis, type AxisOptions } from './scale';
import type { Segment } from './bars';

export interface ValueRow {
  id: string;
  v: number;
  lo?: number;
  hi?: number;
}

export interface RangeRow {
  id: string;
  lo: number;
  hi: number;
  /** False when only the high end is known (no published cached price). */
  hasRange: boolean;
}

export interface StackRow {
  id: string;
  values: Record<string, number>;
}

type Mode =
  | { mode: 'value'; rows: ValueRow[]; refLine?: { v: number; label: string } }
  | { mode: 'range'; rows: RangeRow[]; lowLabel: string; highLabel: string }
  | { mode: 'stack'; rows: StackRow[]; segments: Segment[] };

type EntryColumnsProps = ChartProps &
  Mode & {
    label: string;
    /** Value format for tooltips and labels; stacked shares are always percentages. */
    format?: (v: number) => string;
    tickFormat?: (v: number) => string;
    axis?: AxisOptions;
    yTitle: string;
    /** Column order on a card, where each model shows one column: the highest value first, or the lowest. */
    order?: 'high-first' | 'low-first';
    simulated?: boolean;
    note?: string;
  };

const effortRank = (id: string) => EFFORTS.indexOf(effortOf(id) ?? 'medium');

/** White or dark text, whichever reads on the fill. */
function inkOn(hex: string) {
  const c = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255).map((v) => (v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4));
  return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2] < 0.3 ? '#ffffff' : '#141b26';
}

/** Split a model name over two lines at the space that best balances them. */
const twoLines = (s: string): [string, string] => {
  let best: [string, string] = [s, ''];
  for (let i = s.indexOf(' '); i >= 0; i = s.indexOf(' ', i + 1)) {
    const cand: [string, string] = [s.slice(0, i), s.slice(i + 1)];
    if (best[1] === '' || Math.max(cand[0].length, cand[1].length) < Math.max(best[0].length, best[1].length)) best = cand;
  }
  return best;
};

/** Model names under groups, as set in the stylesheet (Geist runs narrower than textWidth's 0.56 em). */
const NAME_FONT = 10.6;

/**
 * Vertical columns, one per model and effort level, grouped by model in fixed order and
 * effort from low to high within each group. Effort tags sit under each column and the model
 * name under its group.
 */
export function EntryColumns(p: EntryColumnsProps) {
  if (!p.expanded) return <EntryCard {...p} />;
  return <EntryDetail {...p} />;
}

/** On a card: one column per model at its highest effort, as Artificial Analysis lays them out. */
function EntryCard(p: EntryColumnsProps) {
  const format = p.format ?? ((v: number) => String(v));
  if (p.mode === 'stack') return <ModelStacks {...p} rows={p.rows} segments={p.segments} />;
  if (p.mode === 'range')
    return <ModelBars {...p} rows={p.rows.map((r) => ({ id: r.id, v: r.hi }))} order={p.order ?? 'low-first'} format={format} valueLabel={p.highLabel} />;
  return <ModelBars {...p} rows={p.rows} order={p.order ?? 'high-first'} format={format} valueLabel={p.yTitle} />;
}

/** Full screen: every effort level, grouped by model, with 95% intervals. */
function EntryDetail(p: EntryColumnsProps) {
  const tip = useTip();
  const format = p.format ?? ((v: number) => String(v));
  const ids = (p.rows as { id: string }[]).map((r) => r.id).filter((id) => p.models.includes(id));
  const order = p.models.filter((id) => ids.includes(id));
  // Groups in model order; within a group, low to high effort.
  const groups: { model: string; ids: string[] }[] = [];
  for (const id of order) {
    const m = modelOf(id).id;
    let g = groups.find((x) => x.model === m);
    if (!g) groups.push((g = { model: m, ids: [] }));
    g.ids.push(id);
  }
  for (const g of groups) g.ids.sort((a, b) => effortRank(a) - effortRank(b));
  const flat = groups.flatMap((g) => g.ids);

  const bottomCm = 2;
  const x0 = CM;
  const nx = p.w - 2;
  const W = nx * CM;
  const legendRow = p.mode === 'stack';
  const topCm = 1;
  const ny = p.h - topCm - bottomCm;
  // The axis fills the whole plot with round ticks; columns always start from zero.
  let axis: ReturnType<typeof fitAxis>;
  if (p.mode === 'stack') {
    axis = fitAxis(0, 1, ny * CM, { minGap: 40 });
  } else {
    const rows = p.rows.filter((r) => flat.includes(r.id));
    const vals = p.mode === 'value' ? rows.flatMap((r) => [(r as ValueRow).lo ?? (r as ValueRow).v, (r as ValueRow).hi ?? (r as ValueRow).v]) : rows.flatMap((r) => [(r as RangeRow).lo, (r as RangeRow).hi]);
    if (p.mode === 'value' && p.refLine) vals.push(p.refLine.v);
    axis = fitAxis(Math.min(0, ...vals), Math.max(...vals), ny * CM, { minGap: 60, ceil: p.axis?.ceil });
  }
  const tickFormat = p.tickFormat ?? (p.mode === 'stack' ? (v: number) => `${Math.round(v * 100)}%` : (v: number) => String(+v.toFixed(4)));
  const y0 = topCm * CM;
  const yBase = y0 + ny * CM;
  const Y = (v: number) => yBase - axis.pos(v);

  // Horizontal slots: one per column, a gap between groups, a margin at both ends.
  const GAP = 0.9;
  const PAD = 0.5;
  const slots = flat.length + GAP * Math.max(0, groups.length - 1) + 2 * PAD;
  const u = W / Math.max(slots, 1);
  const barW = Math.min(30, u * 0.68);
  const xs: Record<string, number> = {};
  const spans: { model: string; x1: number; x2: number }[] = [];
  let cursor = x0 + PAD * u;
  groups.forEach((g, gi) => {
    if (gi > 0) cursor += GAP * u;
    const start = cursor;
    for (const id of g.ids) {
      xs[id] = cursor + u / 2;
      cursor += u;
    }
    spans.push({ model: g.model, x1: start, x2: cursor });
  });

  const showTags = u >= 19;
  // One treatment for every group name: one line if all fit, else two lines, else all angled.
  const centers = spans.map((sp) => (sp.x1 + sp.x2) / 2);
  const fits = (widths: number[]) =>
    centers[0] - widths[0] / 2 >= 2 &&
    centers[centers.length - 1] + widths[widths.length - 1] / 2 <= p.w * CM - 2 &&
    widths.every((w, i) => i === 0 || widths[i - 1] / 2 + w / 2 + 8 <= centers[i] - centers[i - 1]);
  const names = spans.map((sp) => modelOf(sp.model).short);
  const nameMode: 'one' | 'two' | 'angled' =
    spans.length === 0 || fits(names.map((n) => textWidth(n, NAME_FONT)))
      ? 'one'
      : fits(names.map((n) => Math.max(...twoLines(n).map((l) => textWidth(l, NAME_FONT)))))
        ? 'two'
        : 'angled';
  const showValues = u >= 30 && p.mode !== 'stack';

  const tipFor = (id: string): { title: string; rows: TipRow[] } => {
    const m = modelOf(id);
    const color = LABS[m.lab].color;
    if (p.mode === 'value') {
      const r = p.rows.find((x) => x.id === id)!;
      return {
        title: entryName(id),
        rows: [
          { value: format(r.v), label: p.yTitle, color, hollow: m.tier === 'sibling' },
          ...(r.lo !== undefined && r.hi !== undefined ? [{ value: `${format(r.lo)} – ${format(r.hi)}`, label: '95% interval' }] : []),
        ],
      };
    }
    if (p.mode === 'range') {
      const r = p.rows.find((x) => x.id === id)!;
      return {
        title: entryName(id),
        rows: [
          { value: format(r.hi), label: p.highLabel, color },
          { value: r.hasRange ? format(r.lo) : 'not published', label: p.lowLabel },
        ],
      };
    }
    const r = p.rows.find((x) => x.id === id)!;
    return { title: entryName(id), rows: p.segments.map((s) => ({ value: `${Math.round((r.values[s.key] ?? 0) * 100)}%`, label: s.label, color: s.color })) };
  };

  const onMove = (e: React.PointerEvent<SVGRectElement>) => {
    const pt = localPoint(e);
    let best: string | null = null;
    let bd = u;
    for (const id of flat) {
      const d = Math.abs(xs[id] - pt.x);
      if (d < bd) {
        bd = d;
        best = id;
      }
    }
    if (!best) return tip.hide();
    tip.show({ x: e.clientX, y: e.clientY, ...tipFor(best), note: p.note });
  };

  const refKeyX = x0 + textWidth(`↑ ${p.yTitle}`, 10.5) + 20;

  const marks: ReactNode[] = [];
  for (const id of flat) {
    const m = modelOf(id);
    const color = LABS[m.lab].color;
    const xc = xs[id];
    if (p.mode === 'value') {
      const r = p.rows.find((x) => x.id === id)!;
      const len = axis.pos(r.v) - axis.pos(0);
      marks.push(
        <g key={id}>
          <path d={vBarPath(xc, Y(0), len, barW)} fill={fillOf(id)} />
          {r.lo !== undefined && r.hi !== undefined && (
            <g className="whisker">
              <line x1={xc} x2={xc} y1={Y(r.lo)} y2={Y(r.hi)} />
              <line x1={xc - 4} x2={xc + 4} y1={Y(r.lo)} y2={Y(r.lo)} />
              <line x1={xc - 4} x2={xc + 4} y1={Y(r.hi)} y2={Y(r.hi)} />
            </g>
          )}
          {showValues && (
            <text className="value small-value" x={xc} y={Y(r.hi ?? r.v) - 6} textAnchor="middle">
              {format(r.v)}
            </text>
          )}
        </g>,
      );
    } else if (p.mode === 'range') {
      const r = p.rows.find((x) => x.id === id)!;
      marks.push(
        <g key={id}>
          {r.hasRange && <line x1={xc} x2={xc} y1={Y(r.lo)} y2={Y(r.hi)} stroke={color} strokeWidth={Math.min(7, barW * 0.4)} strokeLinecap="round" strokeOpacity={0.5} />}
          {r.hasRange && <circle cx={xc} cy={Y(r.lo)} r={Math.min(4, barW / 3)} fill="var(--panel)" stroke={color} strokeWidth={1.75} />}
          <circle cx={xc} cy={Y(r.hi)} r={Math.min(4.5, barW / 2.6)} fill={color} />
          {showValues && (
            <text className="value small-value" x={xc} y={Y(r.hi) - 9} textAnchor="middle">
              {format(r.hi)}
            </text>
          )}
        </g>,
      );
    } else {
      const r = p.rows.find((x) => x.id === id)!;
      let acc = 0;
      const segs: ReactNode[] = [];
      for (const s of p.segments) {
        const v = r.values[s.key] ?? 0;
        if (v <= 0) continue;
        const ya = Y(acc);
        const yb = Y(acc + v);
        acc += v;
        const top = acc >= 0.9999;
        const h = Math.max(0, ya - yb - (acc - v > 0 ? 1 : 0) - (top ? 0 : 1));
        const yTop = yb + (top ? 0 : 1);
        const text = `${Math.round(v * 100)}`;
        segs.push(
          <g key={s.key}>
            <rect x={xc - barW / 2} y={yTop} width={barW} height={h} rx={top ? 3 : 0} fill={s.color} />
            {top && <rect x={xc - barW / 2} y={yTop + h - Math.min(4, h)} width={barW} height={Math.min(4, h)} fill={s.color} />}
            {barW >= 22 && h >= 14 && (
              <text className="seg-label" fill={inkOn(s.color)} x={xc} y={yTop + h / 2 + 4} textAnchor="middle">
                {text}
              </text>
            )}
          </g>,
        );
      }
      marks.push(<g key={id}>{segs}</g>);
    }
  }

  return (
    <Svg w={p.w} h={p.h} label={p.label}>
      {axis.ticks.map((t, i) => (
        <line key={`g${i}`} className="grid" x1={x0} x2={x0 + W} y1={Y(t)} y2={Y(t)} />
      ))}
      <line className="baseline" x1={x0} x2={x0 + W} y1={Y(0)} y2={Y(0)} />
      {marks}
      {p.mode === 'value' && p.refLine && (
        <g>
          <line className="refline" x1={x0} x2={x0 + W} y1={Y(p.refLine.v)} y2={Y(p.refLine.v)} strokeDasharray="4 3" />
          {/* Keyed beside the axis title: a label on the line itself would sit behind the columns. */}
          <line className="refline" x1={refKeyX} x2={refKeyX + 16} y1={y0 - 16} y2={y0 - 16} strokeDasharray="4 3" />
          <text className="ref-label" x={refKeyX + 22} y={y0 - 12}>
            {p.refLine.label}
          </text>
        </g>
      )}
      {/* Effort tags under each column, model names under each group. */}
      {showTags &&
        flat.map((id) => (
          <text key={`t${id}`} className="effort-tag" x={xs[id]} y={yBase + 14} textAnchor="middle">
            {EFFORT_TAG[effortOf(id) ?? 'medium']}
          </text>
        ))}
      {spans.map((s) => {
        const m = modelOf(s.model);
        const cx = (s.x1 + s.x2) / 2;
        const yName = yBase + (showTags ? 28 : 14);
        return (
          <g key={`n${s.model}`} className="group-name">
            {nameMode === 'one' ? (
              <text x={cx} y={yName + 4} textAnchor="middle">
                {m.short}
              </text>
            ) : nameMode === 'two' ? (
              <text x={cx} y={yName + 4} textAnchor="middle">
                <tspan x={cx}>{twoLines(m.short)[0]}</tspan>
                <tspan x={cx} dy={13}>
                  {twoLines(m.short)[1]}
                </tspan>
              </text>
            ) : (
              <text x={cx + 3} y={yName} textAnchor="end" transform={`rotate(-35 ${cx + 3} ${yName})`}>
                {m.short}
              </text>
            )}
          </g>
        );
      })}
      <g className="tick">
        {axis.ticks.map((t) => (
          <text key={t} x={x0 - 6} y={Y(t) + 4} textAnchor="end">
            {tickFormat(t)}
          </text>
        ))}
      </g>
      {legendRow ? (
        <Legend items={(p as { segments: Segment[] }).segments.map((s) => ({ label: s.label, color: s.color }))} x={x0} y={y0 - 16} />
      ) : (
        <AxisTitle x={x0} y={y0 - 12}>
          ↑ {p.yTitle}
        </AxisTitle>
      )}
      <rect className="hit" x={x0} y={y0 - 4} width={W} height={ny * CM + bottomCm * CM} onPointerMove={onMove} onPointerLeave={tip.hide} />
    </Svg>
  );
}

/** Rows for the data table: model, effort, then the caller's columns. */
export const effortCell = (id: string) => {
  const e = effortOf(id);
  return e ? EFFORT_LABEL[e] : '';
};
