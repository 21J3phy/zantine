import type { FigureDef } from '../figures';
import { useFilter } from './Filter';
import { IconPareto } from './icons';

/** Show or hide the Pareto frontier on one figure; the card and the full-screen view share the state. */
export function ParetoToggle({ fig }: { fig: FigureDef }) {
  const { paretoOn, togglePareto } = useFilter();
  const on = paretoOn(fig.id);
  return (
    <button
      className={on ? 'toggle-btn on' : 'toggle-btn'}
      aria-pressed={on}
      onClick={() => togglePareto(fig.id)}
      title={on ? 'Hide the Pareto line' : 'Show the Pareto line: the settings no other setting beats on both axes at once'}
    >
      <IconPareto />
      Pareto line
    </button>
  );
}
