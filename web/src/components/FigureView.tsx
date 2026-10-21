import { useEffect, useRef, useState } from 'react';
import { FIGURES, FIGURE_BY_ID, STATUS_LABEL, type DataTable } from '../figures';
import { EFFORTS, EFFORT_LABEL, LABS, MODELS } from '../data/models';
import { CM } from '../lib/units';
import { ParetoToggle, figLabel } from './FigureCard';
import { useFilter } from './Filter';
import { IconClose, IconDownload, IconLink, IconNext, IconPrev, IconWarning } from './icons';

function useWindowSize() {
  const [size, setSize] = useState({ w: window.innerWidth, h: window.innerHeight });
  useEffect(() => {
    const on = () => setSize({ w: window.innerWidth, h: window.innerHeight });
    window.addEventListener('resize', on);
    return () => window.removeEventListener('resize', on);
  }, []);
  return size;
}

function toCsv(t: DataTable) {
  const esc = (v: string | number) => {
    const s = String(v);
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  return [t.columns, ...t.rows].map((r) => r.map(esc).join(',')).join('\n');
}

/** Full-screen view of one figure: the chart on the grid, plus everything behind it. */
export function FigureView({ id, onClose, onNavigate }: { id: string; onClose: () => void; onNavigate: (id: string) => void }) {
  const fig = FIGURE_BY_ID[id];
  const { active: activeFor, isActive, toggle, isEffortActive, toggleEffort, paretoOn } = useFilter();
  const active = activeFor(fig.id);
  const { w: vw, h: vh } = useWindowSize();
  const closeRef = useRef<HTMLButtonElement>(null);
  const [copied, setCopied] = useState(false);
  const index = FIGURES.findIndex((f) => f.id === id);
  const prev = FIGURES[(index - 1 + FIGURES.length) % FIGURES.length];
  const next = FIGURES[(index + 1) % FIGURES.length];

  useEffect(() => {
    const restore = document.activeElement as HTMLElement | null;
    closeRef.current?.focus({ preventScroll: true });
    return () => restore?.focus?.({ preventScroll: true });
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
      else if (e.key === 'ArrowLeft' && !(e.target as HTMLElement).closest('input, textarea')) onNavigate(prev.id);
      else if (e.key === 'ArrowRight' && !(e.target as HTMLElement).closest('input, textarea')) onNavigate(next.id);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose, onNavigate, prev.id, next.id]);

  const cols = Math.floor(vw / CM);
  const rows = Math.floor(vh / CM);
  const wide = cols >= 30;
  const side = wide ? Math.min(14, Math.max(11, Math.floor(cols * 0.3))) : 0;
  let cw = wide ? cols - side - 3 : cols - 2;
  let ch = wide ? rows - 5 : Math.min(14, Math.max(9, rows - 6));
  if (fig.fixed) {
    cw = fig.w;
    ch = fig.h - 3;
  } else {
    cw = Math.max(12, Math.min(cw, 36));
    ch = Math.max(8, Math.min(ch, 20));
  }

  // On a phone a chart shrinks to the screen width; fixed diagrams keep their size and scroll sideways.
  const fit = !wide && !fig.fixed ? Math.min(1, (vw - 2 * CM) / (cw * CM)) : 1;

  const table = fig.table(active);
  const simulated = fig.status === 'simulated' || fig.status === 'planned';

  const download = () => {
    const blob = new Blob([toCsv(table)], { type: 'text/csv' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `zantine-fig-${fig.num}-${fig.id}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  };
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(`${location.origin}${location.pathname}#fig/${fig.id}`);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1600);
    } catch {
      setCopied(false);
    }
  };

  return (
    <div className="overlay fig-view" role="dialog" aria-modal="true" aria-labelledby="fv-title" data-overlay-open>
      <header className="fv-head">
        <div className="fv-titles">
          <h2 id="fv-title">
            <span className="fig-num">{figLabel(fig.num)}</span>
            {fig.title}
          </h2>
          <p>{fig.subtitle}</p>
        </div>
        <div className="fv-tools">
          {fig.pareto && <ParetoToggle fig={fig} />}
          <button className="icon-btn" onClick={() => onNavigate(prev.id)} aria-label={`Previous: Fig. ${prev.num} ${prev.title}`} title={`Fig. ${prev.num}`}>
            <IconPrev />
          </button>
          <span className="fv-count">
            {index + 1} / {FIGURES.length}
          </span>
          <button className="icon-btn" onClick={() => onNavigate(next.id)} aria-label={`Next: Fig. ${next.num} ${next.title}`} title={`Fig. ${next.num}`}>
            <IconNext />
          </button>
          <button className="icon-btn close" ref={closeRef} onClick={onClose} aria-label="Close figure (Esc)">
            <IconClose />
          </button>
        </div>
      </header>
      <div className={wide ? 'fv-body wide' : 'fv-body'}>
        <div className="fv-chart-wrap" style={wide ? { width: (cols - side - 2) * CM } : undefined}>
          <div className={`fv-chart status-${fig.status}`} style={{ width: cw * CM, height: ch * CM, zoom: fit }}>
            {fig.render({ w: cw, h: ch, models: active, expanded: true, pareto: paretoOn(fig.id) })}
          </div>
        </div>
        <aside className="fv-side" style={wide ? { width: side * CM } : undefined}>
          {simulated && (
            <p className="notice inline" role="note">
              <span className="notice-icon">
                <IconWarning />
              </span>
              <span>
                <strong>{STATUS_LABEL[fig.status]}.</strong> The numbers are invented to show the format. Do not cite them.
              </span>
            </p>
          )}
          <section>
            <h3>Why it matters</h3>
            <p>{fig.why}</p>
          </section>
          <section>
            <h3>What it shows</h3>
            <p>{fig.about}</p>
          </section>
          <section>
            <h3>How to read it</h3>
            <ul>
              {fig.read.map((r, i) => (
                <li key={i}>{r}</li>
              ))}
            </ul>
          </section>
          <section>
            <h3>Method</h3>
            <ul>
              {fig.method.map((r, i) => (
                <li key={i}>{r}</li>
              ))}
            </ul>
            <p className="muted">
              <strong>Sample.</strong> {fig.sample}
            </p>
          </section>
          {fig.legend && (
            <section>
              <h3>Shown</h3>
              <div className="filter compact">
                {MODELS.map((m) => (
                  <button key={m.id} className={isActive(fig.id, m.id) ? 'chip on' : 'chip'} aria-pressed={isActive(fig.id, m.id)} onClick={() => toggle(fig.id, m.id)}>
                    <span className="dot" style={m.tier === 'sibling' ? { borderColor: LABS[m.lab].color } : { background: LABS[m.lab].color, borderColor: LABS[m.lab].color }} aria-hidden />
                    {m.short}
                  </button>
                ))}
              </div>
              {fig.legend === 'entries' && (
                <div className="filter compact">
                  {EFFORTS.map((e) => (
                    <button key={e} className={isEffortActive(fig.id, e) ? 'chip on' : 'chip'} aria-pressed={isEffortActive(fig.id, e)} onClick={() => toggleEffort(fig.id, e)}>
                      {EFFORT_LABEL[e]}
                    </button>
                  ))}
                </div>
              )}
            </section>
          )}
          <section>
            <h3>Data</h3>
            <div className="table-scroll">
              <table className="data-table compact">
                <thead>
                  <tr>
                    {table.columns.map((c) => (
                      <th key={c} scope="col">
                        {c}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {table.rows.map((r, i) => (
                    <tr key={i}>
                      {r.map((c, j) => (
                        <td key={j}>{c}</td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="fv-actions">
              <button className="btn small" onClick={download}>
                <IconDownload /> Download CSV
              </button>
              <button className="btn small" onClick={copy}>
                <IconLink /> {copied ? 'Link copied' : 'Copy link'}
              </button>
            </div>
          </section>
        </aside>
      </div>
    </div>
  );
}
