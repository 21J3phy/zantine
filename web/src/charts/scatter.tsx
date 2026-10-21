import { useTip, type TipRow } from '../components/Tooltip';
import { EFFORTS, EFFORT_LABEL, LABS, effortOf, entryName, modelOf } from '../data/models';
import type { Est } from '../data/sim';
import { paretoFront, type Better } from '../lib/pareto';
import { CM } from '../lib/units';
import { AxisTitle, Grid, MarkDot, Svg, Watermark, XTicks, YTicks, localPoint, placeLabels, textWidth, type Box, type ChartProps } from './kit';
import { cmAxis, type AxisOptions } from './scale';

export interface ScatterPt {
  id: string;
  x: Est;
  y: Est;
  /** Marker radius in px; defaults to 5. */
  r?: number;
}

interface ScatterProps extends ChartProps {
  label: string;
  pts: ScatterPt[];
  xTitle: string;
  yTitle: string;
  xFormat: (v: number) => string;
  yFormat: (v: number) => string;
  xTick?: (v: number) => string;
  yTick?: (v: number) => string;
  xAxis?: AxisOptions;
  yAxis?: AxisOptions;
  /** Lower values plot higher (e.g. Rounds to finish, where fewer is better). */
  yReverse?: boolean;
  /** Log scale on x (for cost, which spans more than an order of magnitude). */
  xLog?: boolean;
  /** Which way is better on each axis; needed for the Pareto frontier. */
  better?: { x: Better; y: Better };
  quadrants?: { x: number; y: number; labels: [string, string, string, string] };
  links?: boolean;
  simulated?: boolean;
  sizeNote?: string;
  extraRows?: (id: string) => TipRow[];
  note?: string;
}

/**
 * Scatter of models (or models at each effort level) with 95% intervals on both axes.
 * Effort levels of one model are joined in order, low to high, and the model is named once,
 * at its highest-effort point.
 */
export function Scatter(p: ScatterProps) {
  const tip = useTip();
  const pts = p.pts.filter((d) => p.models.includes(d.id));
  const nx = p.w - 2;
  const ny = p.h - 2;
  const x0 = CM;
  const y0 = CM;
  const xs = pts.flatMap((d) => [d.x.lo, d.x.hi]);
  const ys = pts.flatMap((d) => [d.y.lo, d.y.hi]);
  if (p.quadrants) {
    xs.push(p.quadrants.x);
    ys.push(p.quadrants.y);
  }
  const ax = p.xLog ? logAxis(Math.min(...xs), Math.max(...xs), nx * CM) : cmAxis(Math.min(...xs), Math.max(...xs), nx, { pad: 0.04, labelCm: 1.1, ...p.xAxis });
  const ay = cmAxis(Math.min(...ys), Math.max(...ys), ny, { pad: 0.04, ...p.yAxis });
  const X = (v: number) => x0 + ax.pos(v);
  const Y = (v: number) => (p.yReverse ? y0 + ay.pos(v) : y0 + ny * CM - ay.pos(v));

  const marks = pts.map((d) => ({ ...d, px: X(d.x.v), py: Y(d.y.v), rr: d.r ?? 5 }));
  const byEffort = marks.some((m) => effortOf(m.id));
  const rank = (id: string) => EFFORTS.indexOf(effortOf(id) ?? 'medium');
  // One polyline per model through its effort levels.
  const effortLines = byEffort
    ? [...new Set(marks.map((m) => modelOf(m.id).id))].map((mid) => ({ mid, pts: marks.filter((m) => modelOf(m.id).id === mid).sort((a, b) => rank(a.id) - rank(b.id)) }))
    : [];
  const named = new Set(byEffort ? effortLines.map((l) => l.pts[l.pts.length - 1].id) : marks.map((m) => m.id));
  const better = p.better && screenBetter(p.better, p.yReverse);
  const front = p.pareto && better ? paretoFront(marks.map((m) => ({ ...m, x: m.px, y: m.py })), better) : [];
  const onFront = new Set(front.map((m) => m.id));
  const frontier = better && front.length ? frontierGeometry(front, better) : null;

  // Model names avoid the frontier line and its label; a name with no free spot falls back to its usual place.
  const labelPts = marks.filter((m) => named.has(m.id)).map((m) => ({ id: m.id, x: m.px, y: m.py, r: m.rr, text: modelOf(m.id).short }));
  const unnamed: Box[] = marks.filter((m) => !named.has(m.id)).map((m) => ({ x: m.px - m.rr - 2, y: m.py - m.rr - 2, w: 2 * m.rr + 4, h: 2 * m.rr + 4 }));
  const labelBox = { x: x0 + 2, y: y0 + 2, w: nx * CM - 4, h: ny * CM - 4 };
  const labels = placeLabels(labelPts, labelBox, [...unnamed, ...(frontier?.blockers ?? [])]);
  if (frontier) {
    const plain = placeLabels(labelPts, labelBox, unnamed);
    for (const id of Object.keys(labels)) labels[id] ??= plain[id];
  }

  const onMove = (e: React.PointerEvent<SVGRectElement>) => {
    const pt = localPoint(e);
    let best: (typeof marks)[number] | null = null;
    let bd = 40 * 40;
    for (const m of marks) {
      const d = (m.px - pt.x) ** 2 + (m.py - pt.y) ** 2;
      if (d < bd) {
        bd = d;
        best = m;
      }
    }
    if (!best) return tip.hide();
    const model = modelOf(best.id);
    tip.show({
      x: e.clientX,
      y: e.clientY,
      title: entryName(best.id),
      rows: [
        { value: p.yFormat(best.y.v), label: `${p.yTitle} (${p.yFormat(best.y.lo)} – ${p.yFormat(best.y.hi)})`, color: LABS[model.lab].color, hollow: model.tier === 'sibling' },
        { value: p.xFormat(best.x.v), label: `${p.xTitle} (${p.xFormat(best.x.lo)} – ${p.xFormat(best.x.hi)})` },
        ...effortRow(best.id),
        ...(p.extraRows?.(best.id) ?? []),
        ...(front.length ? [{ value: onFront.has(best.id) ? 'Yes' : 'No', label: 'On the Pareto frontier' }] : []),
      ],
      note: p.note,
    });
  };

  const q = p.quadrants;
  // The note beside the axis title keeps only the parts that fit before it.
  const noteRoom = nx * CM - textWidth(`↑ ${p.yTitle}`, 10.5) - 24;
  const noteParts = [byEffort ? 'Lines join effort levels, low → high' : '', p.sizeNote ?? ''].filter(Boolean);
  while (noteParts.length && textWidth(noteParts.join(' · '), 10) > noteRoom) noteParts.pop();
  const note = noteParts.join(' · ');

  return (
    <Svg w={p.w} h={p.h} label={p.label}>
      {'log' in ax ? (
        <g>
          {Array.from({ length: ny + 1 }, (_, j) => (
            <line key={`h${j}`} className="grid" x1={x0} x2={x0 + nx * CM} y1={y0 + j * CM} y2={y0 + j * CM} />
          ))}
          {ax.ticks.map((t) => (
            <line key={`v${t}`} className="grid" x1={X(t)} x2={X(t)} y1={y0} y2={y0 + ny * CM} />
          ))}
        </g>
      ) : (
        <Grid x={x0} y={y0} nx={nx} ny={ny} />
      )}
      {p.simulated && <Watermark x={x0} y={y0} w={nx * CM} h={ny * CM} />}
      {q && (
        <g>
          <line className="quad" x1={X(q.x)} x2={X(q.x)} y1={y0} y2={y0 + ny * CM} />
          <line className="quad" x1={x0} x2={x0 + nx * CM} y1={Y(q.y)} y2={Y(q.y)} />
          <text className="quad-label" x={x0 + 8} y={y0 + 16}>
            {q.labels[0]}
          </text>
          <text className="quad-label" x={x0 + nx * CM - 8} y={y0 + 16} textAnchor="end">
            {q.labels[1]}
          </text>
          <text className="quad-label" x={x0 + 8} y={y0 + ny * CM - 8}>
            {q.labels[2]}
          </text>
          <text className="quad-label" x={x0 + nx * CM - 8} y={y0 + ny * CM - 8} textAnchor="end">
            {q.labels[3]}
          </text>
        </g>
      )}
      {effortLines.map((l) =>
        l.pts.length > 1 ? (
          <polyline
            key={`el${l.mid}`}
            className="effort-line"
            points={l.pts.map((m) => `${m.px},${m.py}`).join(' ')}
            stroke={LABS[modelOf(l.mid).lab].color}
          />
        ) : null,
      )}
      {p.links &&
        !byEffort &&
        marks.map((m) => {
          const model = modelOf(m.id);
          if (model.tier !== 'flagship' || !model.pair) return null;
          const s = marks.find((o) => o.id === model.pair);
          if (!s) return null;
          return <line key={`l${m.id}`} x1={m.px} y1={m.py} x2={s.px} y2={s.py} stroke={LABS[model.lab].color} strokeWidth={1.5} strokeOpacity={0.7} />;
        })}
      {marks.map((m) => {
        const color = LABS[modelOf(m.id).lab].color;
        return (
          <g key={`e${m.id}`} className={byEffort ? 'ci faint' : 'ci'} stroke={color}>
            <line x1={X(m.x.lo)} x2={X(m.x.hi)} y1={m.py} y2={m.py} />
            <line x1={m.px} x2={m.px} y1={Y(m.y.lo)} y2={Y(m.y.hi)} />
            <line x1={X(m.x.lo)} x2={X(m.x.lo)} y1={m.py - 3} y2={m.py + 3} />
            <line x1={X(m.x.hi)} x2={X(m.x.hi)} y1={m.py - 3} y2={m.py + 3} />
            <line x1={m.px - 3} x2={m.px + 3} y1={Y(m.y.lo)} y2={Y(m.y.lo)} />
            <line x1={m.px - 3} x2={m.px + 3} y1={Y(m.y.hi)} y2={Y(m.y.hi)} />
          </g>
        );
      })}
      {frontier && <Frontier pts={front} geometry={frontier} />}
      {marks.map((m) => {
        const model = modelOf(m.id);
        return <MarkDot key={`d${m.id}`} x={m.px} y={m.py} r={m.rr} color={LABS[model.lab].color} hollow={model.tier === 'sibling'} />;
      })}
      {marks.map((m) => {
        const l = labels[m.id];
        if (!l) return null;
        return (
          <text key={`t${m.id}`} className="point-label" x={l.x} y={l.y} textAnchor={l.anchor}>
            {modelOf(m.id).short}
          </text>
        );
      })}
      <YTicks axis={ay} x={x0} y={y0} reverse={p.yReverse} format={p.yTick} every={ny > 9 ? 2 : 1} />
      {'log' in ax ? (
        <g className="tick">
          {ax.ticks.map((t) => (
            <text key={t} x={X(t)} y={y0 + ny * CM + 14} textAnchor="middle">
              {(p.xTick ?? String)(t)}
            </text>
          ))}
        </g>
      ) : (
        <XTicks axis={ax} x={x0} y={y0 + ny * CM} format={p.xTick} />
      )}
      <AxisTitle x={x0} y={y0 - 12}>
        ↑ {p.yTitle}
      </AxisTitle>
      {note && (
        <text className="axis-note" x={x0 + nx * CM} y={y0 - 12} textAnchor="end">
          {note}
        </text>
      )}
      <AxisTitle x={x0 + nx * CM} y={y0 + ny * CM + 31} anchor="end">
        {p.xTitle}
        {p.xLog ? ' (log scale)' : ''} →
      </AxisTitle>
      <rect className="hit" x={x0} y={y0} width={nx * CM} height={ny * CM} onPointerMove={onMove} onPointerLeave={tip.hide} />
    </Svg>
  );
}

const effortRow = (id: string): TipRow[] => {
  const e = effortOf(id);
  return e ? [{ value: EFFORT_LABEL[e], label: 'Reasoning effort' }] : [];
};

/** Better directions in screen space, where y grows downward. */
function screenBetter(b: { x: Better; y: Better }, yReverse?: boolean): { x: Better; y: Better } {
  const up = b.y === 'higher' ? !yReverse : !!yReverse;
  return { x: b.x, y: up ? 'lower' : 'higher' };
}

const FRONTIER_LABEL = 'Pareto frontier';

/**
 * A log axis over a 1-2-5 range that covers the data. Gridlines and labels sit on 1, 2 and 5 of each
 * decade rather than on centimetre lines.
 */
function logAxis(min: number, max: number, px: number) {
  const seq = (k: number) => [1, 2, 5][((k % 3) + 3) % 3] * 10 ** Math.floor(k / 3);
  let k0 = -12;
  while (seq(k0 + 1) <= min * 0.92) k0++;
  let k1 = k0;
  while (seq(k1) < max * 1.08) k1++;
  const lo = seq(k0);
  const hi = seq(k1);
  const ticks = Array.from({ length: k1 - k0 + 1 }, (_, i) => seq(k0 + i));
  return { log: true as const, ticks, pos: (v: number) => ((Math.log10(v) - Math.log10(lo)) / (Math.log10(hi) - Math.log10(lo))) * px };
}

type Pt = { x: number; y: number };
type FrontierGeometry = { path: Pt[]; label: { x: number; y: number; angle: number } | null; blockers: Box[] };

/** Small boxes along a segment, so labels can avoid a diagonal line without blocking its whole bounding box. */
function alongBoxes(a: Pt, b: Pt, half: number): Box[] {
  const len = Math.hypot(b.x - a.x, b.y - a.y);
  const n = Math.max(1, Math.ceil(len / 8));
  return Array.from({ length: n + 1 }, (_, k) => {
    const x = a.x + ((b.x - a.x) * k) / n;
    const y = a.y + ((b.y - a.y) * k) / n;
    return { x: x - half, y: y - half, w: 2 * half, h: 2 * half };
  });
}

/**
 * The frontier as a line joining the frontier models, from the best on y to the best on x.
 * The label runs along the longest segment, on the better side, where no model sits.
 */
function frontierGeometry(pts: Pt[], better: { x: Better; y: Better }): FrontierGeometry {
  const sx = better.x === 'higher' ? 1 : -1;
  const sy = better.y === 'higher' ? 1 : -1;
  const path = pts.slice().sort((a, b) => sy * b.y - sy * a.y);
  const runs = path.slice(1).map((b, i) => {
    const a = path[i];
    return { a, b, len: Math.hypot(b.x - a.x, b.y - a.y) };
  });
  const blockers: Box[] = runs.flatMap(({ a, b }) => alongBoxes(a, b, 4));

  const tw = textWidth(FRONTIER_LABEL, 10.5);
  const run = runs.filter((r) => r.len >= tw + 16).sort((r1, r2) => r2.len - r1.len)[0];
  let label: FrontierGeometry['label'] = null;
  if (run) {
    const { a, b, len } = run;
    const ux = (b.x - a.x) / len;
    const uy = (b.y - a.y) / len;
    // Unit normal toward the better side, where no model sits.
    let nx = -uy;
    let ny = ux;
    if (nx * sx + ny * sy < 0) {
      nx = -nx;
      ny = -ny;
    }
    const mid = { x: (a.x + b.x) / 2 + nx * 10, y: (a.y + b.y) / 2 + ny * 10 };
    let angle = (Math.atan2(uy, ux) * 180) / Math.PI;
    if (angle >= 90) angle -= 180;
    if (angle < -90) angle += 180;
    label = { ...mid, angle };
    blockers.push(...alongBoxes({ x: mid.x - (ux * tw) / 2, y: mid.y - (uy * tw) / 2 }, { x: mid.x + (ux * tw) / 2, y: mid.y + (uy * tw) / 2 }, 8));
  } else if (path.length) {
    // No segment is long enough to carry the name: set it just past the best-on-y end, on the better side.
    const end = path[0];
    label = { x: end.x + sx * (tw / 2 + 20), y: end.y + sy * 16, angle: 0 };
    blockers.push({ x: label.x - tw / 2 - 4, y: label.y - 8, w: tw + 8, h: 16 });
  }
  return { path, label, blockers };
}

/** The frontier line, a ring on each frontier model, and the label. */
function Frontier({ pts, geometry }: { pts: { id: string; x: number; y: number; rr: number }[]; geometry: FrontierGeometry }) {
  const { path, label } = geometry;
  return (
    <g className="pareto">
      <polyline points={path.map((m) => `${m.x},${m.y}`).join(' ')} />
      {pts.map((m) => (
        <circle key={m.id} cx={m.x} cy={m.y} r={m.rr + 5} />
      ))}
      {label && (
        <text className="pareto-label" x={label.x} y={label.y} textAnchor="middle" dominantBaseline="middle" transform={`rotate(${label.angle} ${label.x} ${label.y})`}>
          {FRONTIER_LABEL}
        </text>
      )}
    </g>
  );
}
