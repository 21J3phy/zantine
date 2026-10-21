import { memo } from 'react';
import type { FigureDef } from '../figures';
import { CM } from '../lib/units';
import { useActions } from './actions';
import { useFilter } from './Filter';
import { IconExpand } from './icons';

/** The figure number, shown in the full-screen view. */
export const figLabel = (n: number) => `Fig. ${n}`;

/** Card chrome in cm around the chart: half a centimetre of padding, 2 for the title and subtitle, and half under the chart. */
const CHROME = 3;

/** The short status word at the end of a card's subtitle. Study designs carry none. */
const CARD_TAG: Partial<Record<FigureDef['status'], string>> = {
  simulated: 'Simulated',
  estimate: 'List-price estimate',
  planned: 'Planned, simulated',
};

/**
 * A figure card, laid out as Artificial Analysis lays out its own: a framed card with the title, one
 * line naming the measure and which way is better, and the chart. Each model appears
 * once, at its highest reasoning effort. The title or the expand button opens the figure full screen,
 * with every effort level, intervals, filters, method and data.
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
  const chartH = h - CHROME;
  const tag = CARD_TAG[fig.status];
  const scroll = !!chartW && chartW > w;
  return (
    <figure
      className={`fig-card status-${fig.status}${featured ? ' featured' : ''}${fig.highlight ? ' headline' : ''}`}
      style={{ width: w * CM, height: h * CM }}
      aria-labelledby={`fig-${fig.id}-title`}
      aria-describedby={`fig-${fig.id}-sub`}
    >
      <header className="fig-head">
        <div className="fig-top">
          <h3 id={`fig-${fig.id}-title`} className="fig-title">
            <button className="fig-open" onClick={() => openFigure(fig.id)} title="Every effort level, method and data">
              {fig.title}
            </button>
          </h3>
          <button className="icon-btn" onClick={() => openFigure(fig.id)} aria-label={`Open ${fig.title} full screen`} title="Every effort level, method and data">
            <IconExpand />
          </button>
        </div>
        <p id={`fig-${fig.id}-sub`} className="fig-sub">
          {[fig.measure ?? fig.subtitle, fig.direction].filter(Boolean).join(' · ')}
          {tag && (
            <>
              {fig.measure === '' && !fig.direction ? '' : ' · '}
              <span className="fig-tag">{tag}</span>
            </>
          )}
        </p>
      </header>
      <div className={scroll ? 'fig-body scroll' : 'fig-body'} style={{ height: chartH * CM }} tabIndex={scroll ? 0 : undefined}>
        {/* Charts are drawn in whole centimetres; a card a fraction wider centres its chart. */}
        {fig.render({ w: Math.floor((scroll ? chartW : w) - 1), h: chartH, models: active(fig.id), expanded: false, pareto: paretoOn(fig.id) })}
      </div>
    </figure>
  );
});
