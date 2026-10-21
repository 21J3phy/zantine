import { useEffect, useRef } from 'react';
import { PAPER_BY_SLUG, PAPERS, type DocBlock } from '../content/papers';
import { FIGURE_BY_ID } from '../figures';
import { useFilter } from './Filter';
import { IconClose, IconExpand, IconExternal } from './icons';
import { Rich } from './Rich';

const slugify = (s: string) => s.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/(^-|-$)/g, '');

function Plate({ id, caption, onOpen }: { id: string; caption: string; onOpen: (id: string) => void }) {
  const fig = FIGURE_BY_ID[id];
  const { active } = useFilter();
  if (!fig) return null;
  return (
    <figure className="plate">
      <div className="plate-sheet">
        <div className="plate-chart">{fig.render({ w: fig.w, h: fig.h - 3, models: active(fig.id) })}</div>
      </div>
      <figcaption>
        <span>{caption}</span>
        <button className="link-btn" onClick={() => onOpen(id)}>
          <IconExpand /> Open Fig. {fig.num}
        </button>
      </figcaption>
    </figure>
  );
}

function Body({ blocks, onOpenFigure }: { blocks: DocBlock[]; onOpenFigure: (id: string) => void }) {
  return (
    <>
      {blocks.map((b, i) => {
        switch (b.t) {
          case 'h2':
            return (
              <h2 key={i} id={slugify(b.text)}>
                {b.text}
              </h2>
            );
          case 'h3':
            return <h3 key={i}>{b.text}</h3>;
          case 'p':
            return (
              <p key={i}>
                <Rich text={b.text} />
              </p>
            );
          case 'list': {
            const items = b.items.map((it, j) => (
              <li key={j}>
                <Rich text={it} />
              </li>
            ));
            return b.ordered ? <ol key={i}>{items}</ol> : <ul key={i}>{items}</ul>;
          }
          case 'table':
            return (
              <figure key={i} className="doc-table">
                <div className="table-scroll">
                  <table>
                    <thead>
                      <tr>
                        {b.columns.map((c) => (
                          <th key={c} scope="col">
                            {c}
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {b.rows.map((r, j) => (
                        <tr key={j}>
                          {r.map((c, k) => (
                            <td key={k}>
                              <Rich text={c} />
                            </td>
                          ))}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                {b.caption && <figcaption>{b.caption}</figcaption>}
              </figure>
            );
          case 'eq':
            return (
              <div key={i} className="equation" role="math" aria-label={b.text}>
                <span className="eq-body">{b.text}</span>
                <span className="eq-label">({b.label})</span>
              </div>
            );
          case 'fig':
            return <Plate key={i} id={b.id} caption={b.caption} onOpen={onOpenFigure} />;
          case 'note':
            return (
              <aside key={i} className="doc-note">
                <Rich text={b.text} />
              </aside>
            );
          case 'code':
            return (
              <pre key={i} className="doc-code">
                <code>{b.text}</code>
              </pre>
            );
        }
      })}
    </>
  );
}

/** A research document, set as a printed sheet over the drawing. */
export function Reader({ slug, onClose, onOpenFigure, onOpenPaper }: { slug: string; onClose: () => void; onOpenFigure: (id: string) => void; onOpenPaper: (slug: string) => void }) {
  const paper = PAPER_BY_SLUG[slug];
  const scroller = useRef<HTMLDivElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    scroller.current?.scrollTo({ top: 0 });
  }, [slug]);

  useEffect(() => {
    const restore = document.activeElement as HTMLElement | null;
    closeRef.current?.focus({ preventScroll: true });
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => {
      window.removeEventListener('keydown', onKey);
      restore?.focus?.({ preventScroll: true });
    };
  }, [onClose]);

  if (!paper) return null;
  const sections = paper.body.filter((b): b is Extract<DocBlock, { t: 'h2' }> => b.t === 'h2');
  const others = PAPERS.filter((p) => p.slug !== slug);
  const cite = `Zantine Labs. ${paper.title} ${paper.number}, ${paper.version}, ${paper.date}.`;

  return (
    <div className="overlay reader" role="dialog" aria-modal="true" aria-labelledby="doc-title" data-overlay-open onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="reader-scroll" ref={scroller} onClick={(e) => e.target === e.currentTarget && onClose()}>
        <div className="reader-layout">
          <nav className="doc-toc" aria-label="Contents">
            <div className="toc-head">Contents</div>
            <a href="#abstract" onClick={(e) => (e.preventDefault(), scroller.current?.querySelector('#abstract')?.scrollIntoView({ behavior: 'smooth' }))}>
              Abstract
            </a>
            {sections.map((s) => (
              <a
                key={s.text}
                href={`#${slugify(s.text)}`}
                onClick={(e) => {
                  e.preventDefault();
                  scroller.current?.querySelector(`#${slugify(s.text)}`)?.scrollIntoView({ behavior: 'smooth' });
                }}
              >
                {s.text}
              </a>
            ))}
            {paper.references.length > 0 && (
              <a href="#references" onClick={(e) => (e.preventDefault(), scroller.current?.querySelector('#references')?.scrollIntoView({ behavior: 'smooth' }))}>
                References
              </a>
            )}
          </nav>
          <article className="sheet">
            <button className="icon-btn sheet-close" ref={closeRef} onClick={onClose} aria-label="Close document (Esc)">
              <IconClose />
            </button>
            <table className="doc-block">
              <tbody>
                <tr>
                  <th scope="row">Document</th>
                  <td>{paper.number}</td>
                  <th scope="row">Type</th>
                  <td>{paper.kind}</td>
                </tr>
                <tr>
                  <th scope="row">Version</th>
                  <td>{paper.version}</td>
                  <th scope="row">Date</th>
                  <td>{paper.date}</td>
                </tr>
                <tr>
                  <th scope="row">Status</th>
                  <td colSpan={3}>{paper.status}</td>
                </tr>
              </tbody>
            </table>
            <h1 id="doc-title">{paper.title}</h1>
            <p className="doc-authors">Zantine Labs</p>
            <section id="abstract" className="abstract">
              <h2>Abstract</h2>
              <p>{paper.abstract}</p>
              <p className="keywords">
                <strong>Keywords.</strong> {paper.keywords.join('; ')}
              </p>
            </section>
            <Body blocks={paper.body} onOpenFigure={onOpenFigure} />
            {paper.references.length > 0 && (
              <section id="references" className="references">
                <h2>References</h2>
                <ol>
                  {paper.references.map((r, i) => (
                    <li key={i}>
                      {r.url ? (
                        <a href={r.url} target="_blank" rel="noreferrer">
                          {r.text} <IconExternal />
                        </a>
                      ) : (
                        r.text
                      )}
                    </li>
                  ))}
                </ol>
              </section>
            )}
            <section className="cite">
              <h2>Cite this document</h2>
              <p className="cite-text">{cite}</p>
            </section>
            <section className="more-docs">
              <h2>Other documents</h2>
              <ul>
                {others.map((o) => (
                  <li key={o.slug}>
                    <button className="link-btn" onClick={() => onOpenPaper(o.slug)}>
                      <span className="muted">{o.number}</span> {o.title}
                    </button>
                  </li>
                ))}
              </ul>
            </section>
          </article>
        </div>
      </div>
    </div>
  );
}
