import { Fragment } from 'react';
import { useTip } from '../components/Tooltip';
import { EFFORT_TAG, INK, LABS, MODEL_BY_ID, MODELS, effortOf, entryName, modelOf, type LabId, topEntries, entryLabel } from '../data/models';
import type { Est } from '../data/sim';
import { CM } from '../lib/units';
import { AxisTitle, Grid, Legend, MarkDot, ModelName, Svg, XTicks, YTicks, localPoint, vBarPath, textWidth, type ChartProps } from './kit';
import { cmAxis, fixedAxis, fmt, type CmAxis } from './scale';

/** 0–100% over the largest whole-cm length with a round step. */
export function unitAxis(available: number): CmAxis {
  const n = [10, 8, 5, 4, 2].find((k) => k <= available) ?? 1;
  const every = n === 10 ? 2 : n === 8 ? 2 : 1;
  return fixedAxis(0, 1, n, every);
}

const pctTick = (v: number) => `${Math.round(v * 100)}%`;

// Histogram -------------------------------------------------------------------

interface HistSeries {
  label: string;
  color: string;
  values: number[];
}

/** Rounds to finish, two series side by side in each 1 cm bin, plus a cap bin. */
export function RoundsHistogram(p: ChartProps & { label: string; series: HistSeries[]; cap: number; simulated?: boolean }) {
  const tip = useTip();
  const L = 1;
  const avail = p.w - L;
  const b = [1, 2, 3, 4, 5, 10].find((s) => Math.ceil(20 / s) + 1 <= avail) ?? 10;
  const bins = Math.ceil(20 / b);
  const start = p.cap + 1 - bins * b;
  const nx = bins + 1;
  const ny = p.h - 2;
  const x0 = L * CM;
  const y0 = CM;
  const binOf = (v: number) => (v > p.cap ? bins : Math.max(0, Math.min(bins - 1, Math.floor((v - start) / b))));
  const shares = p.series.map((s) => {
    const counts = new Array(nx).fill(0);
    for (const v of s.values) counts[binOf(v)]++;
    return counts.map((c) => c / s.values.length);
  });
  const ay = cmAxis(0, Math.max(...shares.flat()), ny, { zero: true });
  const barW = Math.min(14, (CM - 6) / p.series.length);
  const finished = (vals: number[]) => vals.filter((v) => v <= p.cap).sort((a, c) => a - c);
  const medians = p.series.map((s) => {
    const f = finished(s.values);
    return f[Math.floor(f.length / 2)];
  });
  const medX = (m: number) => x0 + ((m - start + 0.5) / b) * CM;

  const onMove = (e: React.PointerEvent<SVGRectElement>) => {
    const pt = localPoint(e);
    const i = Math.floor((pt.x - x0) / CM);
    if (i < 0 || i >= nx) return tip.hide();
    const range = i === bins ? `Unfinished at the ${p.cap}-Round cap` : b === 1 ? `Round ${start + i}` : `Rounds ${start + i * b}–${start + i * b + b - 1}`;
    tip.show({
      x: e.clientX,
      y: e.clientY,
      title: range,
      rows: p.series.map((s, k) => ({ value: `${Math.round(shares[k][i] * 100)}%`, label: `of ${s.label} games`, color: s.color })),
    });
  };

  const gapY = y0 + 16;
  return (
    <Svg w={p.w} h={p.h} label={p.label}>
      <Legend items={p.series.map((s) => ({ label: s.label, color: s.color }))} x={x0} y={CM / 2} />
      <Grid x={x0} y={y0} nx={nx} ny={ny} base="bottom" every={ny > 8 ? 2 : 1} />
      <rect className="cap-band" x={x0 + bins * CM} y={y0} width={CM} height={ny * CM} />
      {shares.map((sh, k) =>
        sh.map((v, i) => {
          const xc = x0 + i * CM + CM / 2 + (k - (p.series.length - 1) / 2) * (barW + 2);
          return <path key={`${k}-${i}`} d={vBarPath(xc, y0 + ny * CM, ay.pos(v), barW)} fill={p.series[k].color} />;
        }),
      )}
      {medians.map((m, k) => (
        <line key={k} x1={medX(m)} x2={medX(m)} y1={gapY} y2={y0 + ny * CM} stroke={p.series[k].color} strokeWidth={1.5} />
      ))}
      {medians.length === 2 && (
        <DimensionLine x1={medX(Math.min(...medians))} x2={medX(Math.max(...medians))} y={gapY} label={`${Math.abs(medians[0] - medians[1])} Rounds between medians`} />
      )}
      <YTicks axis={ay} x={x0} y={y0} format={pctTick} every={ny > 8 ? 2 : 1} />
      <g className="tick">
        {Array.from({ length: bins }, (_, i) =>
          i % Math.max(1, Math.round(2 / Math.max(1, b / 2))) === 0 ? (
            <text key={i} x={x0 + i * CM} y={y0 + ny * CM + 14} textAnchor="middle">
              {start + i * b}
            </text>
          ) : null,
        )}
        <text x={x0 + bins * CM + CM / 2} y={y0 + ny * CM + 14} textAnchor="middle">
          cap
        </text>
      </g>
      <AxisTitle x={x0 + nx * CM} y={y0 + ny * CM + 31} anchor="end">
        Rounds to finish all Tasks →
      </AxisTitle>
      <rect className="hit" x={x0} y={y0} width={nx * CM} height={ny * CM} onPointerMove={onMove} onPointerLeave={tip.hide} />
    </Svg>
  );
}

/** A drafting dimension line: extension ticks, arrowheads and a centred label. */
export function DimensionLine({ x1, x2, y, label }: { x1: number; x2: number; y: number; label: string }) {
  const mid = (x1 + x2) / 2;
  return (
    <g className="dimension">
      <line x1={x1} x2={x2} y1={y} y2={y} />
      <path d={`M${x1},${y}l6,-3v6z M${x2},${y}l-6,-3v6z`} className="dimension-head" />
      <line x1={x1} x2={x1} y1={y - 6} y2={y + 6} />
      <line x1={x2} x2={x2} y1={y - 6} y2={y + 6} />
      <text x={mid} y={y - 6} textAnchor="middle">
        {label}
      </text>
    </g>
  );
}

// Step lines (survival) -----------------------------------------------------------

interface LineSeries {
  label: string;
  color: string;
  values: number[];
}

export function StepLines(p: ChartProps & { label: string; series: LineSeries[]; xTitle: string; yTitle: string; simulated?: boolean }) {
  const tip = useTip();
  const L = 1;
  const R = p.expanded ? 4 : 3;
  // Whole Rounds per centimetre that divide the 30-Round cap.
  const nx = [30, 15, 10, 6, 5, 3].find((n) => n <= p.w - L - R) ?? p.w - L - R;
  const ay = unitAxis(p.h - 2);
  const ny = ay.n;
  const x0 = L * CM;
  const y0 = CM + (p.h - 2 - ny) * CM;
  const last = p.series[0].values.length - 1;
  const ax = cmAxis(0, last, nx, { zero: true, steps: [1, 2, 3, 5, 10], labelCm: 0.9 });
  const X = (t: number) => x0 + ax.pos(t);
  const Y = (v: number) => y0 + ny * CM - (v / 1) * ny * CM;
  const path = (vals: number[]) => vals.map((v, t) => (t === 0 ? `M${X(0)},${Y(v)}` : `H${X(t)}V${Y(v)}`)).join('');

  const onMove = (e: React.PointerEvent<SVGRectElement>) => {
    const pt = localPoint(e);
    const t = Math.max(0, Math.min(last, Math.round(ax.d0 + (pt.x - x0) / CM * ax.step)));
    tip.show({
      x: e.clientX,
      y: e.clientY,
      title: `Start of Round ${t}`,
      rows: p.series.map((s) => ({ value: `${Math.round(s.values[t] * 100)}%`, label: s.label, color: s.color })),
    });
  };

  return (
    <Svg w={p.w} h={p.h} label={p.label}>
      <Grid x={x0} y={y0} nx={nx} ny={ny} base="bottom" every={ay.labelEvery} />
      {p.series.map((s) => (
        <path key={s.label} d={path(s.values)} fill="none" stroke={s.color} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
      ))}
      {p.series.map((s, k) => {
        const v = s.values[last];
        const yy = Y(v) + (k === 0 ? -6 : 12);
        return (
          <g key={`end${k}`}>
            <circle cx={X(last)} cy={Y(v)} r={4} fill={s.color} />
            <text className="end-label" x={X(last) + 10} y={yy}>
              {s.label} {Math.round(v * 100)}%
            </text>
          </g>
        );
      })}
      <YTicks axis={ay} x={x0} y={y0} format={pctTick} every={ay.labelEvery} />
      <XTicks axis={ax} x={x0} y={y0 + ny * CM} />
      <AxisTitle x={x0} y={y0 - 12}>
        ↑ {p.yTitle}
      </AxisTitle>
      <AxisTitle x={x0 + nx * CM} y={y0 + ny * CM + 31} anchor="end">
        {p.xTitle} →
      </AxisTitle>
      <rect className="hit" x={x0} y={y0} width={nx * CM} height={ny * CM} onPointerMove={onMove} onPointerLeave={tip.hide} />
    </Svg>
  );
}

// Small multiples: one panel per metric, models in fixed order ----------------------

export interface Metric {
  key: string;
  title: string;
  /** Used when the panel is too narrow for the title. */
  short?: string;
  get: (id: string) => Est;
  format: (v: number) => string;
  tick?: (v: number) => string;
  zero?: boolean;
  /** Higher is better for the crew; shown in the panel title. */
  better?: 'higher' | 'lower';
}

export function Forest(p: ChartProps & { label: string; metrics: Metric[]; simulated?: boolean }) {
  const tip = useTip();
  // A card shows one row per model, at its highest effort; full screen shows every effort level.
  const card = !p.expanded;
  const byEffort = !card && p.models.some((id) => effortOf(id));
  const L = 4;
  const k = p.metrics.length;
  const pw = Math.floor((p.w - L - (k - 1)) / k);
  const rows = card ? topEntries(p.models) : byEffort ? p.models : MODELS.filter((m) => p.models.includes(m.id)).map((m) => m.id);
  // Rows share the plot height; whole centimetres when they fit, tighter for many effort levels.
  const avail = (p.h - 2) * CM;
  const pitch = Math.min(CM, avail / Math.max(rows.length, 1));
  const y0 = CM;
  const H = rows.length * pitch;
  const groupStart = (i: number) => i === 0 || modelOf(rows[i - 1]).id !== modelOf(rows[i]).id;
  const r = Math.min(4.5, pitch * 0.28);
  return (
    <Svg w={p.w} h={p.h} label={p.label}>
      {rows.map((id, i) => {
        const yc = y0 + (i + 0.5) * pitch;
        const e = effortOf(id);
        return (
          <g key={id}>
            {groupStart(i) && <ModelName id={id} x={(L - (byEffort ? 1.1 : 0)) * CM - 8} y={yc} text={card ? entryLabel(id, true) : undefined} />}
            {byEffort && e && (
              <text className="effort-tag" x={L * CM - 8} y={yc + 3.5} textAnchor="end">
                {EFFORT_TAG[e]}
              </text>
            )}
            {byEffort && groupStart(i) && i > 0 && <line className="group-rule" x1={6} x2={p.w * CM - 6} y1={y0 + i * pitch} y2={y0 + i * pitch} />}
          </g>
        );
      })}
      {p.metrics.map((met, j) => {
        const x0 = (L + j * (pw + 1)) * CM;
        const ests = rows.map((id) => met.get(id));
        const ax = cmAxis(Math.min(...ests.map((e) => e.lo)), Math.max(...ests.map((e) => e.hi)), pw, { pad: 0.05, zero: met.zero, labelCm: 1.2 });
        const onMove = (e: React.PointerEvent<SVGRectElement>) => {
          const pt = localPoint(e);
          const id = rows[Math.floor((pt.y - y0) / pitch)];
          if (!id) return tip.hide();
          const m = modelOf(id);
          const est = met.get(id);
          tip.show({
            x: e.clientX,
            y: e.clientY,
            title: entryName(id),
            rows: [
              { value: met.format(est.v), label: met.title, color: LABS[m.lab].color, hollow: m.tier === 'sibling' },
              { value: `${met.format(est.lo)} – ${met.format(est.hi)}`, label: '95% interval' },
            ],
          });
        };
        return (
          <g key={met.key}>
            <text className="panel-title" x={x0} y={y0 - 12}>
              {met.title}
              {met.better && <tspan className="panel-sub">{met.better === 'higher' ? '  higher is better' : '  lower is better'}</tspan>}
            </text>
            {Array.from({ length: pw + 1 }, (_, c) =>
              c % ax.labelEvery === 0 ? <line key={c} className="grid" x1={x0 + c * CM} x2={x0 + c * CM} y1={y0} y2={y0 + H} /> : null,
            )}
            <line className="grid" x1={x0} x2={x0 + pw * CM} y1={y0} y2={y0} />
            <line className="grid" x1={x0} x2={x0 + pw * CM} y1={y0 + H} y2={y0 + H} />
            {rows.map((id, i) => {
              const est = met.get(id);
              const m = modelOf(id);
              const yc = y0 + (i + 0.5) * pitch;
              const color = LABS[m.lab].color;
              return (
                <g key={id}>
                  <line x1={x0 + ax.pos(est.lo)} x2={x0 + ax.pos(est.hi)} y1={yc} y2={yc} stroke={color} strokeWidth={byEffort ? 1.5 : 2} strokeOpacity={0.6} strokeLinecap="round" />
                  <MarkDot x={x0 + ax.pos(est.v)} y={yc} r={r} color={color} hollow={m.tier === 'sibling'} />
                </g>
              );
            })}
            <XTicks axis={ax} x={x0} y={y0 + H} format={met.tick} />
            <rect className="hit" x={x0} y={y0} width={pw * CM} height={H} onPointerMove={onMove} onPointerLeave={tip.hide} />
          </g>
        );
      })}
    </Svg>
  );
}

// Flagship vs Sibling --------------------------------------------------------------

export function Dumbbell(p: ChartProps & { label: string; metrics: Metric[]; simulated?: boolean }) {
  const tip = useTip();
  const L = 3;
  const k = p.metrics.length;
  const pw = Math.floor((p.w - L - (k - 1) - 0) / k);
  const pairs = MODELS.filter((m) => m.tier === 'flagship' && m.pair && p.models.includes(m.id) && p.models.includes(m.pair));
  // Two centimetres per lab at least; taller cards (phones) give the rows the spare height.
  const pitch = Math.max(2, Math.floor((p.h - 3.2) / Math.max(1, pairs.length)));
  const y0 = CM;
  const ny = Math.max(1, pairs.length) * pitch;
  return (
    <Svg w={p.w} h={p.h} label={p.label}>
      <Legend
        items={[
          { label: 'Flagship', color: INK, kind: 'dot' },
          { label: 'Sibling', color: INK, kind: 'ring' },
        ]}
        x={L * CM}
        y={y0 + ny * CM + CM + 14}
      />
      {pairs.length === 0 && (
        <text className="empty" x={(p.w * CM) / 2} y={y0 + CM} textAnchor="middle">
          Turn on a Flagship and its Sibling to compare them.
        </text>
      )}
      {pairs.map((m, i) => (
        <text key={m.id} className="row-label" x={L * CM - 10} y={y0 + (i * pitch + 1) * CM + 4} textAnchor="end">
          {LABS[m.lab as LabId].name}
        </text>
      ))}
      {p.metrics.map((met, j) => {
        const x0 = (L + j * (pw + 1)) * CM;
        const ests = pairs.flatMap((m) => [met.get(m.id), met.get(m.pair!)]);
        if (ests.length === 0) return null;
        const ax = cmAxis(Math.min(...ests.map((e) => e.v)), Math.max(...ests.map((e) => e.v)), pw, { pad: 0.25, zero: met.zero, labelCm: 1.2 });
        return (
          <g key={met.key}>
            <text className="panel-title" x={x0} y={y0 - 12}>
              {met.short && textWidth(met.title, 12) > (pw + 0.8) * CM ? met.short : met.title}
            </text>
            <Grid x={x0} y={y0} nx={pw} ny={ny} h={false} v vEvery={ax.labelEvery} />
            {pairs.map((m, i) => {
              const f = met.get(m.id).v;
              const s = met.get(m.pair!).v;
              const yc = y0 + (i * pitch + 1) * CM;
              const color = LABS[m.lab].color;
              const fx = x0 + ax.pos(f);
              const sx = x0 + ax.pos(s);
              const onMove = (e: React.PointerEvent<SVGRectElement>) =>
                tip.show({
                  x: e.clientX,
                  y: e.clientY,
                  title: `${LABS[m.lab].name}: ${met.title}`,
                  rows: [
                    { value: met.format(f), label: MODEL_BY_ID[m.id].name, color },
                    { value: met.format(s), label: MODEL_BY_ID[m.pair!].name, color, hollow: true },
                  ],
                });
              return (
                <Fragment key={m.id}>
                  <line x1={sx} x2={fx} y1={yc} y2={yc} stroke={color} strokeWidth={2} strokeOpacity={0.7} />
                  <MarkDot x={sx} y={yc} r={5} color={color} hollow />
                  <MarkDot x={fx} y={yc} r={5} color={color} />
                  <text className="value" x={fx} y={yc - 12} textAnchor="middle">
                    {met.format(f)}
                  </text>
                  <text className="value muted" x={sx} y={yc + 22} textAnchor="middle">
                    {met.format(s)}
                  </text>
                  <rect className="hit" x={x0} y={yc - CM} width={pw * CM} height={pitch * CM} onPointerMove={onMove} onPointerLeave={tip.hide} />
                </Fragment>
              );
            })}
            <XTicks axis={ax} x={x0} y={y0 + ny * CM} format={met.tick} />
          </g>
        );
      })}
    </Svg>
  );
}

// Grouped columns with intervals ------------------------------------------------------

interface ColumnBar {
  key: string;
  label: string;
  color: string;
  v: Est;
}

export function Columns(p: ChartProps & { label: string; groups: { label: string; bars: ColumnBar[] }[]; legend: { label: string; color: string }[]; yTitle: string; format: (v: number) => string; simulated?: boolean }) {
  const tip = useTip();
  const L = 1;
  const nx = p.w - L - 1;
  const ny = p.h - 3;
  const x0 = L * CM;
  const y0 = CM * 2;
  const gw = Math.floor(nx / p.groups.length);
  const all = p.groups.flatMap((g) => g.bars.map((b) => b.v.hi));
  const ay = cmAxis(0, Math.max(...all) * 1.12, ny, { zero: true, ceil: 1 });
  const barW = Math.min(24, (gw * CM - 16) / Math.max(...p.groups.map((g) => g.bars.length)) - 2);
  const base = y0 + ny * CM;
  return (
    <Svg w={p.w} h={p.h} label={p.label}>
      <Legend items={p.legend} x={x0} y={CM / 2} />
      <AxisTitle x={x0} y={y0 - 12}>
        ↑ {p.yTitle}
      </AxisTitle>
      <Grid x={x0} y={y0} nx={nx} ny={ny} base="bottom" every={ny > 8 ? 2 : 1} />
      {p.groups.map((g, gi) => {
        const gx = x0 + gi * gw * CM;
        const n = g.bars.length;
        return (
          <g key={gi}>
            {g.bars.map((b, bi) => {
              const xc = gx + (gw * CM) / 2 + (bi - (n - 1) / 2) * (barW + 2);
              const onMove = (e: React.PointerEvent<SVGRectElement>) =>
                tip.show({
                  x: e.clientX,
                  y: e.clientY,
                  title: `${g.label}: ${b.label}`,
                  rows: [
                    { value: p.format(b.v.v), label: p.yTitle, color: b.color },
                    { value: `${p.format(b.v.lo)} – ${p.format(b.v.hi)}`, label: '95% interval' },
                  ],
                });
              return (
                <g key={b.key}>
                  <path d={vBarPath(xc, base, ay.pos(b.v.v), barW)} fill={b.color} />
                  <g className="whisker">
                    <line x1={xc} x2={xc} y1={base - ay.pos(b.v.lo)} y2={base - ay.pos(b.v.hi)} />
                    <line x1={xc - 5} x2={xc + 5} y1={base - ay.pos(b.v.lo)} y2={base - ay.pos(b.v.lo)} />
                    <line x1={xc - 5} x2={xc + 5} y1={base - ay.pos(b.v.hi)} y2={base - ay.pos(b.v.hi)} />
                  </g>
                  <text className="value" x={xc} y={base - ay.pos(b.v.hi) - 6} textAnchor="middle">
                    {p.format(b.v.v)}
                  </text>
                  <rect className="hit" x={xc - barW / 2 - 4} y={y0} width={barW + 8} height={ny * CM} onPointerMove={onMove} onPointerLeave={tip.hide} />
                </g>
              );
            })}
            <text className="group-label" x={gx + (gw * CM) / 2} y={base + 16} textAnchor="middle">
              {g.label}
            </text>
          </g>
        );
      })}
      <YTicks axis={ay} x={x0} y={y0} format={(v) => `${Math.round(v * 100)}%`} every={ny > 8 ? 2 : 1} />
    </Svg>
  );
}

// Small-multiple lines (planned precision) ------------------------------------------

export function LineMultiples(p: ChartProps & { label: string; panels: { title: string; f: (x: number) => number; format: (v: number) => string }[]; xMax: number; mark: number; xTitle: string }) {
  const tip = useTip();
  const k = p.panels.length;
  const pw = Math.floor((p.w - k * 1 - (k - 1)) / k);
  const ny = p.h - 2;
  const y0 = CM;
  const xsAll = Array.from({ length: p.xMax }, (_, i) => i + 1);
  return (
    <Svg w={p.w} h={p.h} label={p.label}>
      {p.panels.map((panel, j) => {
        const x0 = (1 + j * (pw + 2)) * CM;
        const ax = cmAxis(0, p.xMax, pw, { zero: true, steps: [1, 2, 4, 5, 8, 10] });
        const vals = xsAll.map(panel.f);
        const ay = cmAxis(0, Math.max(...vals), ny, { zero: true });
        const X = (x: number) => x0 + ax.pos(x);
        const Y = (v: number) => y0 + ny * CM - ay.pos(v);
        const d = xsAll.map((x, i) => `${i ? 'L' : 'M'}${X(x)},${Y(vals[i])}`).join('');
        const mv = panel.f(p.mark);
        const onMove = (e: React.PointerEvent<SVGRectElement>) => {
          const pt = localPoint(e);
          const x = Math.max(1, Math.min(p.xMax, Math.round(ax.d0 + ((pt.x - x0) / CM) * ax.step)));
          tip.show({ x: e.clientX, y: e.clientY, title: `${x} block${x > 1 ? 's' : ''} (${x * 28} games)`, rows: [{ value: `±${panel.format(panel.f(x))}`, label: panel.title, color: INK }] });
        };
        return (
          <g key={panel.title}>
            <text className="panel-title" x={x0} y={y0 - 12}>
              {panel.title}
            </text>
            <Grid x={x0} y={y0} nx={pw} ny={ny} base="bottom" every={2} />
            <line className="refline" x1={X(p.mark)} x2={X(p.mark)} y1={y0} y2={y0 + ny * CM} />
            <path d={d} fill="none" stroke={INK} strokeWidth={2} strokeLinejoin="round" />
            <MarkDot x={X(p.mark)} y={Y(mv)} r={4.5} color={INK} />
            <text className="value" x={X(p.mark) + 8} y={Y(mv) - 8}>
              ±{panel.format(mv)}
            </text>
            <YTicks axis={ay} x={x0} y={y0} format={(v) => fmt(v, ay.decimals)} every={2} />
            <XTicks axis={ax} x={x0} y={y0 + ny * CM} />
            <rect className="hit" x={x0} y={y0} width={pw * CM} height={ny * CM} onPointerMove={onMove} onPointerLeave={tip.hide} />
          </g>
        );
      })}
      <AxisTitle x={p.w * CM - CM} y={y0 + ny * CM + 31} anchor="end">
        {p.xTitle} →
      </AxisTitle>
    </Svg>
  );
}
