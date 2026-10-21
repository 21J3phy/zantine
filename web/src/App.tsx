import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties } from 'react';
import { ActionsContext, type Actions } from './components/actions';
import { Block } from './components/blocks';
import { FigureCard } from './components/FigureCard';
import { FigureView } from './components/FigureView';
import { Mark } from './components/icons';
import { Reader } from './components/Reader';
import { PAPER_BY_SLUG } from './content/papers';
import { FIGURE_BY_ID } from './figures';
import { PAGES, PAGE_BY_ID, PAGE_OF_FIGURE, SHEET_W, type Item } from './layout';
import { CM, clamp } from './lib/units';

type Overlay = { kind: 'none' } | { kind: 'figure'; id: string } | { kind: 'paper'; slug: string };

interface Route {
  page: string;
  overlay: Overlay;
}

const HOME = PAGES[0].id;

/** `#methodology` is a page; `#fig/<id>` and `#paper/<slug>` open over the current page. */
function parseHash(current?: string): Route {
  const h = decodeURIComponent(location.hash.replace(/^#\/?/, ''));
  if (PAGE_BY_ID[h]) return { page: h, overlay: { kind: 'none' } };
  if (h.startsWith('fig/') && FIGURE_BY_ID[h.slice(4)]) {
    const id = h.slice(4);
    return { page: current ?? PAGE_OF_FIGURE[id] ?? HOME, overlay: { kind: 'figure', id } };
  }
  if (h.startsWith('paper/') && PAPER_BY_SLUG[h.slice(6)]) return { page: current ?? 'research', overlay: { kind: 'paper', slug: h.slice(6) } };
  return { page: current ?? HOME, overlay: { kind: 'none' } };
}

/** Narrow screens stack the items in one 12 cm column. */
const COLUMN_W = 12;

interface Sheet {
  narrow: boolean;
  /** CSS zoom applied to the board so it fits the window width. */
  z: number;
  /** Left margin in cm. */
  left: number;
}

function sheetFor(vw: number): Sheet {
  // At least 1 cm of margin on either side of the board.
  const fit = vw / ((SHEET_W + 2) * CM);
  if (fit >= 0.62) {
    const z = Math.min(1, fit);
    return { narrow: false, z, left: Math.floor((vw / z / CM - SHEET_W) / 2) };
  }
  const z = clamp((vw - 24) / (COLUMN_W * CM), 0.5, 1);
  return { narrow: true, z, left: Math.max(0, (vw / z / CM - COLUMN_W) / 2) };
}

/**
 * Width actually available to the page. Measured on the app container: with a
 * reserved scrollbar gutter, documentElement.clientWidth can still report the full window.
 */
const rootWidth = () => document.getElementById('root')?.clientWidth ?? window.innerWidth;

function useViewportWidth() {
  const [vw, setVw] = useState(rootWidth);
  useEffect(() => {
    const ro = new ResizeObserver(() => setVw(rootWidth()));
    const root = document.getElementById('root');
    if (root) ro.observe(root);
    return () => ro.disconnect();
  }, []);
  return vw;
}

function ItemView({ it, narrow }: { it: Item; narrow: boolean }) {
  const style: CSSProperties = narrow ? {} : { position: 'absolute', left: it.x * CM, top: it.y * CM, width: it.w * CM, height: it.h * CM };
  if (it.kind === 'figure') {
    const f = FIGURE_BY_ID[it.id];
    if (narrow) {
      // Wide fixed diagrams keep their geometry at full size and scroll sideways inside the card, under
      // its title, so their labels stay readable; charts redraw at column width.
      if (f.fixed && it.w > COLUMN_W) {
        return (
          <div className="item" id={`fig-${it.id}`}>
            <FigureCard fig={f} w={COLUMN_W} h={it.h} chartW={it.w} />
          </div>
        );
      }
      return (
        <div className="item" id={`fig-${it.id}`}>
          <FigureCard fig={f} w={COLUMN_W} h={13} featured={it.featured} />
        </div>
      );
    }
    return (
      <div className="item" style={style} id={`fig-${it.id}`}>
        <FigureCard fig={f} w={it.w} h={it.h} featured={it.featured} />
      </div>
    );
  }
  if (it.kind === 'heading') {
    return (
      <div className={it.small ? 'item heading small' : 'item heading'} style={style}>
        <h2>{it.title}</h2>
        {it.sub && <p>{it.sub}</p>}
      </div>
    );
  }
  return (
    <div className={`item block block-${it.key}`} style={style}>
      <Block k={it.key} />
    </div>
  );
}

function TopBar({ sheet, current, goTo }: { sheet: Sheet; current: string; goTo: (id: string) => void }) {
  const width = (sheet.narrow ? COLUMN_W : SHEET_W) * CM * sheet.z;
  // On a phone the links scroll sideways; keep the current page's link in view.
  const navRef = useRef<HTMLElement>(null);
  useEffect(() => {
    const nav = navRef.current;
    const link = nav?.querySelector<HTMLElement>('.nav-link.active');
    if (nav && link) nav.scrollLeft = link.offsetLeft - (nav.clientWidth - link.offsetWidth) / 2;
  }, [current, sheet.narrow]);
  return (
    <header className={sheet.narrow ? 'topbar narrow' : 'topbar'}>
      <div className="topbar-inner" style={{ width, marginLeft: sheet.left * CM * sheet.z }}>
        <button className="brand" onClick={() => goTo(HOME)} aria-label="Zantine Labs, results">
          <Mark size={20} />
          <span className="brand-name">Zantine Labs</span>
        </button>
        <nav className="nav" aria-label="Pages" ref={navRef}>
          {PAGES.map((p) => (
            <a
              key={p.id}
              href={`#${p.id}`}
              className={current === p.id ? 'nav-link active' : 'nav-link'}
              aria-current={current === p.id ? 'page' : undefined}
              onClick={(e) => {
                e.preventDefault();
                goTo(p.id);
              }}
            >
              {p.title}
            </a>
          ))}
        </nav>
      </div>
    </header>
  );
}

export default function App() {
  const [route, setRoute] = useState<Route>(() => parseHash());
  const vw = useViewportWidth();
  const sheet = sheetFor(vw);
  const page = PAGE_BY_ID[route.page] ?? PAGES[0];

  useEffect(() => {
    const on = () => setRoute((prev) => parseHash(prev.page));
    window.addEventListener('hashchange', on);
    return () => window.removeEventListener('hashchange', on);
  }, []);

  // A new page starts at its top.
  useEffect(() => {
    window.scrollTo({ top: 0 });
    document.title = page.id === HOME ? 'Zantine Labs: benchmarks for AI agent teams' : `${page.title} · Zantine Labs`;
  }, [page]);

  // The page behind an open figure or document stays still.
  const overlay = route.overlay.kind !== 'none';
  useEffect(() => {
    document.documentElement.classList.toggle('locked', overlay);
  }, [overlay]);

  const closeOverlay = useCallback(() => {
    history.pushState(null, '', `#${route.page}`);
    setRoute((prev) => ({ ...prev, overlay: { kind: 'none' } }));
  }, [route.page]);

  const actions = useMemo<Actions>(
    () => ({
      openFigure: (id) => {
        location.hash = `fig/${id}`;
      },
      openPaper: (slug) => {
        location.hash = `paper/${slug}`;
      },
      goTo: (id) => {
        if (location.hash === `#${id}`) window.scrollTo({ top: 0 });
        else location.hash = id;
      },
    }),
    [],
  );

  const boardStyle: CSSProperties = sheet.narrow
    ? { width: COLUMN_W * CM, marginLeft: sheet.left * CM }
    : { width: SHEET_W * CM, height: page.h * CM, marginLeft: sheet.left * CM };

  return (
    <ActionsContext.Provider value={actions}>
      <TopBar sheet={sheet} current={page.id} goTo={actions.goTo} />
      <main className="page" style={{ zoom: sheet.z, width: vw / sheet.z }} aria-label={page.title}>
        <div key={page.id} className={sheet.narrow ? 'board narrow' : 'board'} style={boardStyle}>
          {page.items.map((it, i) => (
            <ItemView key={i} it={it} narrow={sheet.narrow} />
          ))}
        </div>
      </main>
      {route.overlay.kind === 'figure' && <FigureView id={route.overlay.id} onClose={closeOverlay} onNavigate={actions.openFigure} />}
      {route.overlay.kind === 'paper' && <Reader slug={route.overlay.slug} onClose={closeOverlay} onOpenFigure={actions.openFigure} onOpenPaper={actions.openPaper} />}
    </ActionsContext.Provider>
  );
}
