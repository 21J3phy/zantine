import { EFFORTS, EFFORT_LABEL, EFFORT_TAG, LABS, MODELS } from '../data/models';
import { useFilter } from './Filter';

/**
 * The legend inside a figure: every model in its lab color (hollow for a Sibling) and, on charts
 * that plot each effort level, the effort levels. Selecting an item hides or shows it in this figure.
 */
export function FigLegend({ fig, efforts }: { fig: string; efforts: boolean }) {
  const { isActive, toggle, isEffortActive, toggleEffort, filtered, reset } = useFilter();
  return (
    <div className="fig-legend" role="group" aria-label="Legend. Select an item to hide or show it in this figure.">
      <div className="lg-row">
        {MODELS.map((m) => {
          const on = isActive(fig, m.id);
          const color = LABS[m.lab].color;
          return (
            <button key={m.id} className={on ? 'lg-item' : 'lg-item off'} aria-pressed={on} onClick={() => toggle(fig, m.id)} title={on ? `Hide ${m.name}` : `Show ${m.name}`}>
              <span className="dot" style={m.tier === 'sibling' ? { borderColor: color } : { background: color, borderColor: color }} aria-hidden />
              {m.short}
            </button>
          );
        })}
      </div>
      <div className="lg-row">
        {efforts && (
          <>
            <span className="lg-label">Effort</span>
            {EFFORTS.map((e) => {
              const on = isEffortActive(fig, e);
              return (
                <button key={e} className={on ? 'lg-item lg-effort' : 'lg-item lg-effort off'} aria-pressed={on} onClick={() => toggleEffort(fig, e)} title={`${on ? 'Hide' : 'Show'} ${EFFORT_LABEL[e]} effort`}>
                  {EFFORT_TAG[e]}
                </button>
              );
            })}
          </>
        )}
        <span className="lg-key">
          <span className="dot hollow" aria-hidden /> Sibling
        </span>
        {filtered(fig) && (
          <button className="lg-reset" onClick={() => reset(fig)}>
            Show all
          </button>
        )}
      </div>
    </div>
  );
}
