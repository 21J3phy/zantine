import { memo } from 'react';
import { STATUS_LABEL, type FigureDef } from '../figures';
import { CM } from '../lib/units';
import { useActions } from './actions';
import { FigLegend } from './FigLegend';
import { useFilter } from './Filter';
import { IconExpand, IconPareto } from './icons';

/** The figure number shown on cards. */
export const figLabel = (n: number) => `Fig. ${n}`;

/** Show or hide the Pareto frontier on one figure; the card and the full-screen view share the state. */
export function ParetoToggle({ fig }: { fig: FigureDef }) {
  const { paretoOn, togglePareto } = useFilter();
  const on = paretoOn(fig.id);
  return (
    <button
      className={on ? 'toggle-btn on' : 'toggle-btn'}
      aria-pressed={on}
      onClick={() => togglePareto(fig.id)}
      title={on ? 'Hide the Pareto frontier' : 'Show the Pareto frontier: the models no other model beats on both axes at once'}
    >
      <IconPareto />
      Pareto
    </button>
  );
}

/**
 * Card chrome in cm around the chart: 2.5 for the title and why-it-matters line, then either a
 * 1.5 cm legend or half a centimetre of padding below. The chart keeps whole centimetres.
 */
export const cardChrome = (fig: FigureDef) => (fig.legend && !fig.highlight ? 4 : 3);

/**
 * A figure card: the title, one line on why the figure matters, the chart and its legend. The method,
 * sample and data live behind the expand button. Estimates and planned studies carry a small tag;
 * simulated data is marked inside every plot and once at the top of the page.
 */
export const FigureCard = memo(function FigureCard({
  fig,
  w = fig.w,
  h = fig.h,
  featured,
  chartW,
}: {
  fig: FigureDef;
  w?: number;
  h?: number;
  featured?: boolean;
  /** Draw the chart this wide and let it scroll sideways inside a narrower card (wide diagrams on a phone). */
  chartW?: number;
}) {
  const { active, paretoOn } = useFilter();
  const { openFigure } = useActions();
  const chartH = h - cardChrome(fig);
  // Headline cards say what the number is in their subtitle, so they carry no tag.
  const tag = !fig.highlight && (fig.status === 'estimate' || fig.status === 'planned') ? STATUS_LABEL[fig.status] : null;
  return (
    <figure
      className={`fig-card status-${fig.status}${featured ? ' featured' : ''}${fig.highlight ? ' highlight' : ''}`}
      style={{ width: w * CM, height: h * CM }}
      aria-labelledby={`fig-${fig.id}-title`}
      aria-describedby={`fig-${fig.id}-sub`}
      tabIndex={0}
      onKeyDown={(e) => {
        if (e.key === 'Enter' && e.target === e.currentTarget) openFigure(fig.id);
      }}
    >
      <header className="fig-head">
        <div className="fig-titles">
          <h3 id={`fig-${fig.id}-title`} className="fig-title">
            {fig.highlight ? <span className="hl-square" style={{ background: fig.highlight }} aria-hidden /> : <span className="fig-num">{figLabel(fig.num)}</span>}
            {fig.title}
          </h3>
          <p id={`fig-${fig.id}-sub`} className="fig-why">
            {tag && <span className="fig-tag">{tag}</span>}
            {fig.highlight ? fig.subtitle : fig.why}
          </p>
        </div>
        {fig.pareto && <ParetoToggle fig={fig} />}
        <button className="icon-btn" onClick={() => openFigure(fig.id)} aria-label={`Expand figure ${fig.num}: ${fig.title}`} title="Details, method and data">
          <IconExpand />
        </button>
      </header>
      <div className={chartW && chartW > w ? 'fig-body scroll' : 'fig-body'} style={{ height: chartH * CM }} tabIndex={chartW && chartW > w ? 0 : undefined}>
        {/* Charts are drawn in whole centimetres; a card a fraction wider centres its chart. */}
        {fig.render({ w: Math.floor(chartW && chartW > w ? chartW : w), h: chartH, models: active(fig.id), expanded: featured, pareto: paretoOn(fig.id) })}
      </div>
      {fig.legend && !fig.highlight && <FigLegend fig={fig.id} efforts={fig.legend === 'entries'} />}
    </figure>
  );
});
