import type { ReactNode } from 'react';
import { useTip } from '../components/Tooltip';
import { BLOCK, exampleMap } from '../data/design';
import { INK, LABS, MODELS } from '../data/models';
import { CM } from '../lib/units';
import { Grid, ModelName, Svg, localPoint, textWidth, type ChartProps } from './kit';

// Block design matrix -------------------------------------------------------------

/** One 28-game block: 21 lineups × 7 models, two Seats per model, Impostor Seats filled. */
export function DesignMatrix(p: ChartProps) {
  const tip = useTip();
  const L = 3;
  const x0 = L * CM;
  const y0 = CM;
  const cols = BLOCK.length;
  const counts = MODELS.map((_, mi) => BLOCK.reduce((n, l) => n + (l.impostors[0] === mi ? 1 : 0) + (l.impostors[1] === mi ? 1 : 0), 0));
  const seat = 11;

  const onMove = (e: React.PointerEvent<SVGRectElement>) => {
    const pt = localPoint(e);
    const c = Math.floor((pt.x - x0) / CM);
    const lobby = BLOCK[c];
    if (!lobby) return tip.hide();
    const [a, b] = lobby.impostors;
    tip.show({
      x: e.clientX,
      y: e.clientY,
      title: `Lineup ${c + 1}${lobby.benign ? ', also a Benign Match' : ''}`,
      rows: lobby.models.map((mi) => {
        const m = MODELS[mi];
        const imp = (a === mi ? 1 : 0) + (b === mi ? 1 : 0);
        return { value: imp === 0 ? '2 crew' : imp === 1 ? '1 Impostor' : '2 Impostors', label: m.name, color: LABS[m.lab].color, hollow: imp === 0 };
      }),
      note: a === b ? 'Same-model Impostor Pairing' : 'Different-model Impostor Pairing',
    });
  };

  return (
    <Svg w={p.w} h={p.h} label="Block design matrix">
      <text className="panel-title" x={x0} y={y0 - 12}>
        Lineup
      </text>
      <text className="panel-title" x={x0 + cols * CM + CM} y={y0 - 12} textAnchor="middle">
        Impostor
      </text>
      <Grid x={x0} y={y0} nx={cols} ny={MODELS.length} v />
      {BLOCK.map((_, c) => (
        <text key={c} className="tick" x={x0 + c * CM + CM / 2} y={y0 + MODELS.length * CM + 14} textAnchor="middle">
          {c + 1}
        </text>
      ))}
      {MODELS.map((m, mi) => (
        <g key={m.id}>
          <ModelName id={m.id} x={x0 - 8} y={y0 + (mi + 0.5) * CM} />
          <text className="value" x={x0 + cols * CM + CM} y={y0 + (mi + 0.5) * CM + 4} textAnchor="middle">
            {counts[mi]}
          </text>
        </g>
      ))}
      {BLOCK.map((l, c) =>
        l.models.map((mi) => {
          const color = LABS[MODELS[mi].lab].color;
          const imp = (l.impostors[0] === mi ? 1 : 0) + (l.impostors[1] === mi ? 1 : 0);
          const cx = x0 + c * CM + CM / 2;
          const cy = y0 + mi * CM + CM / 2;
          return [0, 1].map((s) => {
            const filled = s < imp;
            const x = cx + (s === 0 ? -seat - 1.5 : 1.5);
            return filled ? (
              <rect key={`${c}-${mi}-${s}`} x={x} y={cy - seat / 2} width={seat} height={seat} rx={2} fill={color} />
            ) : (
              <rect key={`${c}-${mi}-${s}`} x={x + 1} y={cy - seat / 2 + 1} width={seat - 2} height={seat - 2} rx={2} fill="none" stroke={color} strokeWidth={1.5} />
            );
          });
        }),
      )}
      {BLOCK.map((l, c) =>
        l.benign ? (
          <path
            key={`b${c}`}
            d={`M${x0 + c * CM + CM / 2},${y0 + MODELS.length * CM + 22}l5,8h-10z`}
            className="benign-mark"
          />
        ) : null,
      )}
      <g className="legend">
        <rect x={x0} y={y0 + (MODELS.length + 1) * CM + 10} width={seat} height={seat} rx={2} fill={INK} />
        <text x={x0 + 18} y={y0 + (MODELS.length + 1) * CM + 20}>
          Impostor Seat
        </text>
        <rect x={x0 + 130} y={y0 + (MODELS.length + 1) * CM + 11} width={seat - 2} height={seat - 2} rx={2} fill="none" stroke={INK} strokeWidth={1.5} />
        <text x={x0 + 148} y={y0 + (MODELS.length + 1) * CM + 20}>
          Crewmate Seat
        </text>
        <path d={`M${x0 + 270},${y0 + (MODELS.length + 1) * CM + 10}l5,8h-10z`} className="benign-mark" />
        <text x={x0 + 284} y={y0 + (MODELS.length + 1) * CM + 20}>
          Lineup also replayed as a Benign Match (no Impostors)
        </text>
      </g>
      <rect className="hit" x={x0} y={y0} width={cols * CM} height={MODELS.length * CM} onPointerMove={onMove} onPointerLeave={tip.hide} />
    </Svg>
  );
}

// Box-and-arrow diagrams -----------------------------------------------------------

function Box({ x, y, w, h, title, lines, strong }: { x: number; y: number; w: number; h: number; title: string; lines?: string[]; strong?: boolean }) {
  return (
    <g className={strong ? 'box strong' : 'box'}>
      <rect x={x * CM} y={y * CM} width={w * CM} height={h * CM} />
      <text className="box-title" x={x * CM + 10} y={y * CM + 20}>
        {title}
      </text>
      {lines?.map((l, i) => (
        <text key={i} className="box-line" x={x * CM + 10} y={y * CM + 38 + i * 15}>
          {l}
        </text>
      ))}
    </g>
  );
}

function Arrow({ from, to, label }: { from: [number, number]; to: [number, number]; label?: string }) {
  const [x1, y1] = [from[0] * CM, from[1] * CM];
  const [x2, y2] = [to[0] * CM, to[1] * CM];
  const ang = Math.atan2(y2 - y1, x2 - x1);
  const ah = 7;
  const head = `M${x2},${y2}L${x2 - ah * Math.cos(ang - 0.4)},${y2 - ah * Math.sin(ang - 0.4)}L${x2 - ah * Math.cos(ang + 0.4)},${y2 - ah * Math.sin(ang + 0.4)}Z`;
  return (
    <g className="arrow">
      <line x1={x1} y1={y1} x2={x2 - 2 * Math.cos(ang)} y2={y2 - 2 * Math.sin(ang)} />
      <path className="head" d={head} />
      {label && (
        <text x={(x1 + x2) / 2 + 6} y={(y1 + y2) / 2 - 6}>
          {label}
        </text>
      )}
    </g>
  );
}

/** Path with right-angle corners through cm waypoints. */
function Elbow({ pts, label, labelAt }: { pts: [number, number][]; label?: string; labelAt?: [number, number] }) {
  const d = pts.map(([x, y], i) => `${i ? 'L' : 'M'}${x * CM},${y * CM}`).join('');
  const [xa, ya] = pts[pts.length - 2];
  const [xb, yb] = pts[pts.length - 1];
  const ang = Math.atan2((yb - ya) * CM, (xb - xa) * CM);
  const x2 = xb * CM;
  const y2 = yb * CM;
  const ah = 7;
  return (
    <g className="arrow">
      <path className="wire" d={d} />
      <path className="head" d={`M${x2},${y2}L${x2 - ah * Math.cos(ang - 0.4)},${y2 - ah * Math.sin(ang - 0.4)}L${x2 - ah * Math.cos(ang + 0.4)},${y2 - ah * Math.sin(ang + 0.4)}Z`} />
      {label && labelAt && (
        <text x={labelAt[0] * CM} y={labelAt[1] * CM}>
          {label}
        </text>
      )}
    </g>
  );
}

export function ClaimPipeline(p: ChartProps) {
  return (
    <Svg w={p.w} h={p.h} label="Claim Audit pipeline">
      <Box x={1} y={1} w={6} h={2} title="Meeting transcript" lines={['Every statement, every pass']} />
      <Box x={9} y={1} w={7} h={2} title="Claim extractor" lines={['Fixed open model, outside the pool']} strong />
      <Box x={18} y={1} w={7} h={2} title="Structured Claims" lines={['Who, where, when, what']} />
      <Box x={27} y={1} w={10} h={2} title="Code check against the game record" lines={['True, false or unverifiable']} strong />
      <Arrow from={[7, 2]} to={[9, 2]} />
      <Arrow from={[16, 2]} to={[18, 2]} />
      <Arrow from={[25, 2]} to={[27, 2]} />

      <Box x={27} y={5} w={10} h={2} title="Speaker’s own observations" lines={['Provably knew it was false: a Lie']} />
      <Arrow from={[32, 3]} to={[32, 5]} label="false Claims" />

      <Box x={9} y={5} w={7} h={2} title="Jev typed judgments" lines={['Claim or hedge? Challenged? Whom?']} />
      <Elbow pts={[[4, 3], [4, 6], [9, 6]]} />
      <Box x={18} y={5} w={7} h={2} title="Language metrics" lines={['Lie Rate, Reliability, Vote Pull …']} strong />
      <Arrow from={[16, 6]} to={[18, 6]} />
      <Arrow from={[27, 6]} to={[25, 6]} />

      <Box x={9} y={9} w={7} h={2} title="400 Planted Claims" lines={['Known truth: plain, hedged, subtle']} />
      <Box x={18} y={9} w={7} h={2} title="Label Panel" lines={['3 AI judges, 600 real Claims, α']} />
      <Elbow pts={[[12, 9], [12, 7]]} />
      <Elbow pts={[[21, 9], [21, 7]]} />
      <text className="diagram-note" x={CM} y={9 * CM + 20}>
        Validation
      </text>
      <text className="diagram-note muted" x={CM} y={9 * CM + 36}>
        Required before any headline
      </text>
      <text className="diagram-note muted" x={CM} y={9 * CM + 51}>
        language metric is published
      </text>
    </Svg>
  );
}

export function RoundSequence(p: ChartProps) {
  return (
    <Svg w={p.w} h={p.h} label="Sequence of one Round">
      <Box x={1} y={1} w={7} h={3} title="1  Act" lines={['Every living Seat picks one', 'action from the same view', 'of its own room']} />
      <Box x={10} y={1} w={7} h={3} title="2  Resolve together" lines={['Kills, then moves, then', 'Meeting requests, Tasks,', 'cooldowns']} />
      <Box x={19} y={1} w={7} h={3} title="3  Check the end" lines={['All Tasks done, parity,', 'unresolved Crisis, or', 'the Round cap']} />
      <Box x={28} y={1} w={9} h={3} title="4  Meeting, if called" lines={['3 discussion passes in a', 'reshuffled order, then a', 'secret vote with skip']} strong />
      <Arrow from={[8, 2.5]} to={[10, 2.5]} />
      <Arrow from={[17, 2.5]} to={[19, 2.5]} />
      <Arrow from={[26, 2.5]} to={[28, 2.5]} />
      <Box x={28} y={6} w={9} h={2} title="Ejection" lines={['Plurality wins; a tie ejects nobody']} />
      <Arrow from={[32, 4]} to={[32, 6]} />
      <Elbow pts={[[28, 7], [4, 7], [4, 4]]} label="Next Round. An ejected player’s role is never revealed." labelAt={[5, 6.6]} />
      <Elbow pts={[[22, 4], [22, 5.5], [5, 5.5], [5, 4]]} label="No Meeting called" labelAt={[6, 5.25]} />
    </Svg>
  );
}

export function MapPlan(p: ChartProps) {
  const map = exampleMap(7);
  const cw = 6;
  const ch = 4;
  const rw = 4;
  const rh = 2;
  const ox = 1;
  const oy = 1;
  const center = (i: number) => {
    const r = map.rooms[i];
    return [ox + r.col * cw + 1 + rw / 2, oy + r.row * ch + 1 + rh / 2];
  };
  const corridors: ReactNode[] = map.corridors.map(([a, b], i) => {
    const [ax, ay] = center(a);
    const [bx, by] = center(b);
    const horizontal = ay === by;
    const half = 0.25;
    if (horizontal) {
      const x1 = Math.min(ax, bx) + rw / 2;
      const x2 = Math.max(ax, bx) - rw / 2;
      return (
        <g key={i} className="corridor">
          <rect x={x1 * CM} y={(ay - half) * CM} width={(x2 - x1) * CM} height={2 * half * CM} />
        </g>
      );
    }
    const y1 = Math.min(ay, by) + rh / 2;
    const y2 = Math.max(ay, by) - rh / 2;
    return (
      <g key={i} className="corridor">
        <rect x={(ax - half) * CM} y={y1 * CM} width={2 * half * CM} height={(y2 - y1) * CM} />
      </g>
    );
  });
  return (
    <Svg w={p.w} h={p.h} label="Example generated map">
      {corridors}
      {map.rooms.map((r, i) => {
        const x = ox + r.col * cw + 1;
        const y = oy + r.row * ch + 1;
        return (
          <g key={i} className={r.meeting ? 'room meeting' : 'room'}>
            <rect x={x * CM} y={y * CM} width={rw * CM} height={rh * CM} />
            <text className="room-name" x={(x + rw / 2) * CM} y={(y + rh / 2) * CM + (r.meeting ? -2 : 4)} textAnchor="middle">
              {r.name}
            </text>
            {r.meeting && (
              <text className="room-sub" x={(x + rw / 2) * CM} y={(y + rh / 2) * CM + 14} textAnchor="middle">
                Meeting room
              </text>
            )}
          </g>
        );
      })}
    </Svg>
  );
}

const ROADMAP = [
  { v: 'v1', title: 'Core comparison', games: '378 games', state: 'In build' },
  { v: 'v1.1', title: 'Verification Protocols', games: '168 games', state: 'Planned' },
  { v: 'v1.2', title: 'Transfer check', games: 'Designed after v1.1', state: 'Planned' },
  { v: 'v1.3', title: 'Minority Disadvantage', games: '168 games', state: 'Planned' },
  { v: 'v1.4', title: 'Leader experiment', games: '196 games', state: 'Planned' },
  { v: 'v1.5', title: 'Talk Budget tiers', games: '76 games', state: 'Planned' },
  { v: 'v1.6', title: 'Reasoning effort', games: '140 games', state: 'Planned' },
  { v: 'v2', title: 'Harness comparison', games: 'One variable at a time', state: 'Planned' },
];

/** Greedy word wrap to a pixel width, using the chart's text-width estimate. */
function wrap(text: string, size: number, max: number) {
  const lines: string[] = [];
  for (const word of text.split(' ')) {
    const last = lines[lines.length - 1];
    if (last !== undefined && textWidth(`${last} ${word}`, size) <= max) lines[lines.length - 1] = `${last} ${word}`;
    else lines.push(word);
  }
  // Two lines: split where they come out most even, so no line is left with one short word.
  if (lines.length === 2) {
    const words = text.split(' ');
    let best = lines;
    for (let i = 1; i < words.length; i++) {
      const cand = [words.slice(0, i).join(' '), words.slice(i).join(' ')];
      const w = Math.max(...cand.map((l) => textWidth(l, size)));
      if (w <= max && w < Math.max(...best.map((l) => textWidth(l, size)))) best = cand;
    }
    return best;
  }
  return lines;
}

/**
 * The research program in priority order. Wide: a horizontal track with each milestone's name and
 * plan wrapped to the space between milestones. Narrow: one row per milestone down a vertical track.
 */
export function Roadmap(p: ChartProps) {
  const n = ROADMAP.length;
  if (p.w < 20) {
    const pitch = Math.min(1.1 * CM, (p.h * CM - 12) / n);
    const top = (p.h * CM - pitch * (n - 1)) / 2;
    const tx = 0.7 * CM;
    return (
      <Svg w={p.w} h={p.h} label="Roadmap in priority order">
        <line className="track" x1={tx} x2={tx} y1={top} y2={top + pitch * (n - 1)} />
        {ROADMAP.map((r, i) => {
          const y = top + i * pitch;
          const active = r.state === 'In build';
          return (
            <g key={r.v} className={active ? 'milestone active' : 'milestone'}>
              <circle cx={tx} cy={y} r={active ? 6.5 : 4.5} />
              <text className="milestone-v" x={tx + 16} y={y + 4}>
                {r.v}
              </text>
              <text className="milestone-title" x={tx + 56} y={y + 4}>
                {r.title}
              </text>
              <text className="milestone-sub" x={p.w * CM - 6} y={y + 4} textAnchor="end">
                {r.games} · {r.state}
              </text>
            </g>
          );
        })}
      </Svg>
    );
  }
  const margin = 1.6 * CM;
  const step = (p.w * CM - 2 * margin) / (n - 1);
  const room = step - 14;
  const rows = ROADMAP.map((r) => ({ ...r, title: wrap(r.title, 11.5, room), games: wrap(r.games, 10.5, room) }));
  const titleLines = Math.max(...rows.map((r) => r.title.length));
  const planLines = Math.max(...rows.map((r) => r.games.length + 1));
  // Above the track: the version. Below: the name, then the plan and state, each block starting on one line.
  const above = 30;
  const below = 26 + titleLines * 14 + 4 + planLines * 14;
  const y = Math.max(above + 6, (p.h * CM - above - below) / 2 + above);
  const yGames = y + 26 + titleLines * 14 + 4;
  return (
    <Svg w={p.w} h={p.h} label="Roadmap in priority order">
      <line className="track" x1={margin} x2={margin + step * (n - 1)} y1={y} y2={y} />
      {rows.map((r, i) => {
        const x = margin + i * step;
        const active = r.state === 'In build';
        return (
          <g key={r.v} className={active ? 'milestone active' : 'milestone'}>
            <circle cx={x} cy={y} r={active ? 7 : 5} />
            <text className="milestone-v" x={x} y={y - 16} textAnchor="middle">
              {r.v}
            </text>
            <text className="milestone-title" x={x} y={y + 26} textAnchor="middle">
              {r.title.map((l, k) => (
                <tspan key={k} x={x} dy={k === 0 ? 0 : 14}>
                  {l}
                </tspan>
              ))}
            </text>
            <text className="milestone-sub" x={x} y={yGames} textAnchor="middle">
              {[...r.games, r.state].map((l, k) => (
                <tspan key={k} x={x} dy={k === 0 ? 0 : 14}>
                  {l}
                </tspan>
              ))}
            </text>
          </g>
        );
      })}
    </Svg>
  );
}
