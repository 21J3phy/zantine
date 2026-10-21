import { useTip, type TipRow } from '../components/Tooltip';
import { EFFORTS, EFFORT_LABEL, LABS, MODELS, effortOf, entryName, modelOf } from '../data/models';
import type { Est } from '../data/sim';
import { paretoFront, type Better } from '../lib/pareto';
import { CM } from '../lib/units';
import { MarkDot, Svg, localPoint, placeLabels, textWidth, type Box, type ChartProps } from './kit';
import { fitLinear, type AxisOptions } from './scale';

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
  simulated?: boolean;
  sizeNote?: string;
  extraRows?: (id: string) => TipRow[];
  note?: string;
}

/**
 * A scatter laid out as Artificial Analysis lays out its own: every effort level of each model as a dot
 * in its lab's color, the levels of one model joined low to high and the model named at its highest,
 * a dotted Pareto line, the most attractive quadrant shaded, and a legend of labs above the plot. Ticks
 * sit on the axes with no gridlines. Full screen adds each point's 95% intervals.
 */
export function Scatter(p: ScatterProps) {
  const tip = useTip();
  const card = !p.expanded;
  const pts = p.pts.filter((d) => p.models.includes(d.id));
  const better = p.better && screenBetter(p.better, p.yReverse);
  const W = p.w * CM;
  const H = p.h * CM;

  // The quadrant: the figure's own thresholds where it has them, else the medians of what is shown.
  const median = (v: number[]) => {
    const s = v.slice().sort((a, b) => a - b);
    return s.length ? (s[(s.length - 1) >> 1] + s[s.length >> 1]) / 2 : 0;
  };
  const quad = p.quadrants ?? (p.better && pts.length > 2 ? { x: median(pts.map((d) => d.x.v)), y: median(pts.map((d) => d.y.v)), labels: null } : null);

  // Legend: the labs shown, then the Pareto line and the quadrant; it wraps onto a second row when narrow.
  const labs = [...new Set(MODELS.filter((m) => pts.some((d) => modelOf(d.id).id === m.id)).map((m) => m.lab))];
  const items: { kind: 'dot' | 'pareto' | 'quad' | 'note'; label: string; color?: string }[] = [
    ...(quad && p.better ? [{ kind: 'quad' as const, label: 'Most attractive quadrant' }] : []),
    ...(p.pareto && better ? [{ kind: 'pareto' as const, label: 'Pareto line' }] : []),
    ...labs.map((l) => ({ kind: 'dot' as const, label: LABS[l].name, color: LABS[l].color })),
    ...(p.sizeNote ? [{ kind: 'note' as const, label: p.sizeNote }] : []),
  ];
  const keyW = (k: string) => (k === 'quad' ? 16 : k === 'pareto' ? 22 : k === 'note' ? 0 : 10);
  const placed: { item: (typeof items)[number]; x: number; row: number }[] = [];
  let lx = 0;
  let row = 0;
  for (const it of items) {
    const w = keyW(it.kind) + (keyW(it.kind) ? 7 : 0) + 0.9 * textWidth(it.label, 12) + 18;
    if (lx > 0 && lx + w > W) {
      lx = 0;
      row++;
    }
    placed.push({ item: it, x: lx, row });
    lx += w;
  }
  const legendH = (row + 1) * 20;

  // Plot box: room on the left for the turned axis title and the tick labels, below for both x lines.
  const y0 = legendH + 16;
  const ph = H - y0 - 44;
  const ys = pts.flatMap((d) => (card ? [d.y.v] : [d.y.lo, d.y.hi]));
  const xs = pts.flatMap((d) => (card ? [d.x.v] : [d.x.lo, d.x.hi]));
  if (quad) {
    xs.push(quad.x);
    ys.push(quad.y);
  }
  const pad = card ? 0.08 : 0.04;
  const ay = fitLinear(Math.min(...ys), Math.max(...ys), ph, { pad, format: p.yTick, floor: p.yAxis?.floor, ceil: p.yAxis?.ceil });
  const yLabel = p.yTick ?? ((v: number) => String(+v.toFixed(4)));
  const xLabel = p.xTick ?? ((v: number) => String(+v.toFixed(4)));
  const x0 = 22 + Math.max(...ay.ticks.map((t) => 0.9 * textWidth(yLabel(t), 11))) + 10;
  const pw = W - x0 - 10;
  const ax = p.xLog
    ? logAxis(Math.min(...xs), Math.max(...xs), pw)
    : fitLinear(Math.min(...xs), Math.max(...xs), pw, { pad, format: p.xTick, floor: p.xAxis?.floor, ceil: p.xAxis?.ceil });
  const X = (v: number) => x0 + ax.pos(v);
  const Y = (v: number) => (p.yReverse ? y0 + ay.pos(v) : y0 + ph - ay.pos(v));

  const marks = pts.map((d) => ({ ...d, px: X(d.x.v), py: Y(d.y.v), rr: d.r ?? 5.5 }));
  const rank = (id: string) => EFFORTS.indexOf(effortOf(id) ?? 'medium');
  // One line per model through its effort levels, low to high.
  const lines = [...new Set(marks.map((m) => modelOf(m.id).id))].map((mid) => ({ mid, pts: marks.filter((m) => modelOf(m.id).id === mid).sort((a, b) => rank(a.id) - rank(b.id)) }));
  const named = new Set(lines.map((l) => l.pts[l.pts.length - 1].id));
  const front = p.pareto && better ? paretoFront(marks.map((m) => ({ ...m, x: m.px, y: m.py })), better) : [];
  const onFront = new Set(front.map((m) => m.id));
  const frontier = better && front.length ? frontierGeometry(front, better) : null;

  // Model names avoid the dots and the Pareto line; a name with no free spot falls back to its usual place.
  const labelPts = marks.filter((m) => named.has(m.id)).map((m) => ({ id: m.id, x: m.px, y: m.py, r: m.rr, text: modelOf(m.id).short }));
  const unnamed: Box[] = marks.filter((m) => !named.has(m.id)).map((m) => ({ x: m.px - m.rr - 2, y: m.py - m.rr - 2, w: 2 * m.rr + 4, h: 2 * m.rr + 4 }));
  const labelBox = { x: x0 + 2, y: y0 + 2, w: pw - 4, h: ph - 4 };
  const labels = placeLabels(labelPts, labelBox, [...unnamed, ...(frontier?.blockers ?? [])]);
  if (frontier) {
    const plain = placeLabels(labelPts, labelBox, unnamed);
    for (const id of Object.keys(labels)) labels[id] ??= plain[id];
  }
  // Every model is named, as Artificial Analysis names every release. A name with no free spot next to
  // its dot moves out in steps, in eight directions, to the nearest place that covers no other name or
  // dot, with a hairline leading back to the dot; failing that it sits beside the dot anyway.
  const box = (t: { x: number; y: number; anchor: string }, text: string): Box => {
    const w = textWidth(text, 11.5) * 0.9;
    const x = t.anchor === 'start' ? t.x : t.anchor === 'end' ? t.x - w : t.x - w / 2;
    return { x, y: t.y - 11, w, h: 14 };
  };
  const overlap = (a: Box, b: Box) => a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;
  const taken = labelPts.flatMap((l) => (labels[l.id] ? [box(labels[l.id]!, l.text)] : []));
  const dots = marks.map((m) => ({ id: m.id, b: { x: m.px - m.rr - 1, y: m.py - m.rr - 1, w: 2 * m.rr + 2, h: 2 * m.rr + 2 } }));
  const leaders: { id: string; x1: number; y1: number; x2: number; y2: number }[] = [];
  for (const l of labelPts) {
    if (labels[l.id]) continue;
    let found: { x: number; y: number; anchor: 'start' | 'end' | 'middle' } | null = null;
    for (let step = 0; step < 6 && !found; step++) {
      const g = l.r + 5 + step * 12;
      const spots = [
        { x: l.x + g, y: l.y + 4, anchor: 'start' as const },
        { x: l.x - g, y: l.y + 4, anchor: 'end' as const },
        { x: l.x, y: l.y - g - 3, anchor: 'middle' as const },
        { x: l.x, y: l.y + g + 11, anchor: 'middle' as const },
        { x: l.x + g * 0.7, y: l.y - g * 0.7, anchor: 'start' as const },
        { x: l.x - g * 0.7, y: l.y - g * 0.7, anchor: 'end' as const },
        { x: l.x + g * 0.7, y: l.y + g * 0.7 + 8, anchor: 'start' as const },
        { x: l.x - g * 0.7, y: l.y + g * 0.7 + 8, anchor: 'end' as const },
      ];
      found =
        spots.find((t) => {
          const b = box(t, l.text);
          return b.x >= x0 && b.x + b.w <= x0 + pw && b.y >= y0 && b.y + b.h <= y0 + ph && !taken.some((o) => overlap(o, b)) && !dots.some((d) => d.id !== l.id && overlap(d.b, b));
        }) ?? null;
      if (found && step > 0) {
        const b = box(found, l.text);
        const cx = Math.max(b.x, Math.min(l.x, b.x + b.w));
        const cy = Math.max(b.y, Math.min(l.y, b.y + b.h));
        const d = Math.hypot(cx - l.x, cy - l.y) || 1;
        leaders.push({ id: l.id, x1: l.x + ((cx - l.x) / d) * (l.r + 2), y1: l.y + ((cy - l.y) / d) * (l.r + 2), x2: cx, y2: cy });
      }
    }
    if (!found) {
      const right = l.x + l.r + 5 + textWidth(l.text, 11.5) * 0.9 <= x0 + pw;
      found = { x: right ? l.x + l.r + 5 : l.x - l.r - 5, y: l.y + 4, anchor: right ? 'start' : 'end' };
    }
    labels[l.id] = found;
    taken.push(box(found, l.text));
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
        ...(front.length ? [{ value: onFront.has(best.id) ? 'Yes' : 'No', label: 'On the Pareto line' }] : []),
      ],
      note: p.note,
    });
  };

  // The quadrant in screen space: from its threshold toward the better side on each axis.
  const qx = quad ? X(quad.x) : 0;
  const qy = quad ? Y(quad.y) : 0;
  const goodLeft = better?.x === 'lower';
  const goodUp = better?.y === 'lower';
  const rectX = (good: boolean) => ((good ? goodLeft : !goodLeft) ? { x: x0, width: qx - x0 } : { x: qx, width: x0 + pw - qx });
  const rectY = (good: boolean) => ((good ? goodUp : !goodUp) ? { y: y0, height: qy - y0 } : { y: qy, height: y0 + ph - qy });

  return (
    <Svg w={p.w} h={p.h} label={p.label}>
      <g className="legend scatter-legend">
        {placed.map(({ item, x, row: r }) => {
          const yy = 10 + r * 20;
          return (
            <g key={item.label}>
              {item.kind === 'dot' && <circle cx={x + 5} cy={yy} r={5} fill={item.color} />}
              {item.kind === 'quad' && <rect className="quad-good" x={x} y={yy - 6} width={16} height={12} rx={2} />}
              {item.kind === 'pareto' && <line className="pareto-key" x1={x + 2} x2={x + 20} y1={yy} y2={yy} />}
              <text x={x + keyW(item.kind) + (keyW(item.kind) ? 7 : 0)} y={yy + 4} className={item.kind === 'note' ? 'legend-note' : undefined}>
                {item.label}
              </text>
            </g>
          );
        })}
      </g>
      {quad && p.better && (
        <g>
          <rect className="quad-good" {...rectX(true)} {...rectY(true)} />
          <rect className="quad-bad" {...rectX(false)} {...rectY(false)} />
        </g>
      )}
      {quad?.labels && (
        <g>
          <text className="quad-label" x={x0 + 8} y={y0 + 16}>
            {quad.labels[0]}
          </text>
          <text className="quad-label" x={x0 + pw - 8} y={y0 + 16} textAnchor="end">
            {quad.labels[1]}
          </text>
          <text className="quad-label" x={x0 + 8} y={y0 + ph - 8}>
            {quad.labels[2]}
          </text>
          <text className="quad-label" x={x0 + pw - 8} y={y0 + ph - 8} textAnchor="end">
            {quad.labels[3]}
          </text>
        </g>
      )}
      {!card &&
        marks.map((m) => (
          <g key={`e${m.id}`} className="ci faint" stroke={LABS[modelOf(m.id).lab].color}>
            <line x1={X(m.x.lo)} x2={X(m.x.hi)} y1={m.py} y2={m.py} />
            <line x1={m.px} x2={m.px} y1={Y(m.y.lo)} y2={Y(m.y.hi)} />
          </g>
        ))}
      {lines.map((l) =>
        l.pts.length > 1 ? <polyline key={`el${l.mid}`} className="effort-line" points={l.pts.map((m) => `${m.px},${m.py}`).join(' ')} stroke={LABS[modelOf(l.mid).lab].color} /> : null,
      )}
      {frontier && <polyline className="pareto-line" points={frontier.path.map((m) => `${m.x},${m.y}`).join(' ')} />}
      {marks.map((m) => {
        const model = modelOf(m.id);
        return <MarkDot key={`d${m.id}`} x={m.px} y={m.py} r={m.rr} color={LABS[model.lab].color} hollow={model.tier === 'sibling'} />;
      })}
      {leaders.map((l) => (
        <line key={`ld${l.id}`} className="leader" x1={l.x1} y1={l.y1} x2={l.x2} y2={l.y2} />
      ))}
      {marks.map((m) => {
        const l = labels[m.id];
        if (!l) return null;
        return (
          <text key={`t${m.id}`} className="point-label" x={l.x} y={l.y} textAnchor={l.anchor}>
            {modelOf(m.id).short}
          </text>
        );
      })}
      <g className="tick">
        {ay.ticks.map((t) => (
          <g key={`y${t}`}>
            <line className="tick-mark" x1={x0 - 5} x2={x0} y1={Y(t)} y2={Y(t)} />
            <text x={x0 - 8} y={Y(t) + 4} textAnchor="end">
              {yLabel(t)}
            </text>
          </g>
        ))}
        {ax.ticks.map((t) => (
          <g key={`x${t}`}>
            <line className="tick-mark" x1={X(t)} x2={X(t)} y1={y0 + ph} y2={y0 + ph + 5} />
            <text x={X(t)} y={y0 + ph + 18} textAnchor="middle">
              {xLabel(t)}
            </text>
          </g>
        ))}
      </g>
      <text className="axis-title" transform={`translate(10 ${y0 + ph / 2}) rotate(-90)`} textAnchor="middle">
        {p.yTitle}
      </text>
      <text className="axis-title" x={x0 + pw / 2} y={y0 + ph + 38} textAnchor="middle">
        {p.xTitle}
        {p.xLog ? ' (log scale)' : ''}
      </text>
      <rect className="hit" x={x0} y={y0} width={pw} height={ph} onPointerMove={onMove} onPointerLeave={tip.hide} />
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
