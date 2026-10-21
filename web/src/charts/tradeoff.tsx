import { useTip } from '../components/Tooltip';
import type { ProtocolResult } from '../data/sim';
import { CM } from '../lib/units';
import { AxisTitle, Grid, MarkDot, Svg, XTicks, YTicks, localPoint, type ChartProps } from './kit';
import { cmAxis, fmt } from './scale';
import { INK_RAMP } from '../data/models';

/** Verification Protocols: damage prevented against what the protocol costs with no threat. */
export function TradeOff(p: ChartProps & { label: string; pts: ProtocolResult[]; simulated?: boolean }) {
  const tip = useTip();
  const nx = p.w - 2;
  const ny = p.h - 2;
  const x0 = CM;
  const y0 = CM;
  const ax = cmAxis(0, Math.max(...p.pts.map((d) => d.cost.hi)), nx, { zero: true, pad: 0.1, labelCm: 0.9 });
  const ay = cmAxis(0, Math.max(...p.pts.map((d) => d.prevented.hi)), ny, { zero: true, pad: 0.05 });
  const X = (v: number) => x0 + ax.pos(v);
  const Y = (v: number) => y0 + ny * CM - ay.pos(v);
  const colors = [INK_RAMP[3], INK_RAMP[1], INK_RAMP[0]];

  const onMove = (e: React.PointerEvent<SVGRectElement>) => {
    const pt = localPoint(e);
    let best = -1;
    let bd = 40 * 40;
    p.pts.forEach((d, i) => {
      const dd = (X(d.cost.v) - pt.x) ** 2 + (Y(d.prevented.v) - pt.y) ** 2;
      if (dd < bd) {
        bd = dd;
        best = i;
      }
    });
    if (best < 0) return tip.hide();
    const d = p.pts[best];
    tip.show({
      x: e.clientX,
      y: e.clientY,
      title: d.label,
      rows: [
        { value: `${fmt(d.prevented.v, 1)} Rounds`, label: 'Sabotage Damage prevented', color: colors[best] },
        { value: `${fmt(d.cost.v, 1)} Rounds`, label: 'Cost in Benign Matches' },
      ],
    });
  };

  return (
    <Svg w={p.w} h={p.h} label={p.label}>
      <Grid x={x0} y={y0} nx={nx} ny={ny} v vEvery={ax.labelEvery} />
      <line className="refline" x1={X(0)} y1={Y(0)} x2={X(Math.min(ax.d1, ay.d1))} y2={Y(Math.min(ax.d1, ay.d1))} />
      <text className="ref-label" x={X(Math.min(ax.d1, ay.d1) * 0.62) + 6} y={Y(Math.min(ax.d1, ay.d1) * 0.62) + 14}>
        Break-even
      </text>
      {p.pts.map((d, i) => (
        <g key={d.id}>
          <g className="ci" stroke={colors[i]}>
            <line x1={X(d.cost.lo)} x2={X(d.cost.hi)} y1={Y(d.prevented.v)} y2={Y(d.prevented.v)} />
            <line x1={X(d.cost.v)} x2={X(d.cost.v)} y1={Y(d.prevented.lo)} y2={Y(d.prevented.hi)} />
          </g>
          <MarkDot x={X(d.cost.v)} y={Y(d.prevented.v)} r={6} color={colors[i]} />
          <text className="point-label" x={X(d.cost.v) + 11} y={Y(d.prevented.v) + (i === 0 ? -8 : 4)}>
            {d.label}
          </text>
        </g>
      ))}
      <YTicks axis={ay} x={x0} y={y0} />
      <XTicks axis={ax} x={x0} y={y0 + ny * CM} />
      <AxisTitle x={x0} y={y0 - 12}>
        ↑ Sabotage Damage prevented (Rounds)
      </AxisTitle>
      <AxisTitle x={x0 + nx * CM} y={y0 + ny * CM + 31} anchor="end">
        Productivity cost with no threat (Rounds) →
      </AxisTitle>
      <rect className="hit" x={x0} y={y0} width={nx * CM} height={ny * CM} onPointerMove={onMove} onPointerLeave={tip.hide} />
    </Svg>
  );
}
