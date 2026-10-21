import { FIGURE_BY_ID } from './figures';

// Each folder tab is its own page. Items are positioned in whole centimetres on a
// 38 cm board and pushed in reading order; phones stack them in that order.

export type BlockKey =
  | 'intro'
  | 'explainer'
  | 'summary'
  | 'why'
  | 'glossary'
  | 'm-game'
  | 'm-tasks'
  | 'tasks-table'
  | 'm-lineups'
  | 'm-harness'
  | 'pool-table'
  | 'metrics-table'
  | 'm-audit'
  | 'm-stats'
  | 'followups-table'
  | 'papers'
  | 'related'
  | 'reproduce'
  | 'about'
  | 'contact'
  | 'footer';

export type Item =
  | { kind: 'figure'; id: string; x: number; y: number; w: number; h: number; featured?: boolean }
  | { kind: 'block'; key: BlockKey; x: number; y: number; w: number; h: number }
  | { kind: 'heading'; key: string; title: string; sub?: string; small?: boolean; x: number; y: number; w: number; h: number };

export interface Page {
  id: string;
  title: string;
  items: Item[];
  /** Board height in cm. */
  h: number;
}

export const SHEET_W = 38;

let items: Item[] = [];
let y = 1;

/** A running cursor down the board; every gap is 1 cm. */
const row = (h: number, gap = 1) => {
  const top = y;
  y += h + gap;
  return top;
};
const fig = (id: string, x: number, top: number, size?: { w: number; h: number; featured?: boolean }) => {
  const f = FIGURE_BY_ID[id];
  if (!f) throw new Error(`Unknown figure ${id}`);
  items.push({ kind: 'figure', id, x, y: top, w: size?.w ?? f.w, h: size?.h ?? f.h, featured: size?.featured });
};
const block = (key: BlockKey, x: number, top: number, w: number, h: number) => items.push({ kind: 'block', key, x, y: top, w, h });
const heading = (key: string, title: string, top: number, sub?: string) =>
  items.push({ kind: 'heading', key, title, sub, x: 0, y: top, w: 38, h: sub ? 3 : 2 });
/** A one-line section label above a row of figures. */
const label = (key: string, title: string, top: number) => items.push({ kind: 'heading', key, title, small: true, x: 0, y: top, w: 38, h: 1 });

const pages: Page[] = [];
const page = (id: string, title: string, build: () => void) => {
  items = [];
  y = 1;
  build();
  block('footer', 0, row(3, 0), 38, 3);
  pages.push({ id, title, items, h: y + 1 });
};

// One emphasized figure beside two small ones; rows alternate sides.
const BIG = { w: 25, h: 25, featured: true };
const SMALL = { w: 12, h: 12 };
const featuredRow = (big: string, small: [string, string], side: 'left' | 'right', gap = 2) => {
  const t = row(BIG.h, gap);
  if (side === 'left') {
    fig(big, 0, t, BIG);
    fig(small[0], 26, t, SMALL);
    fig(small[1], 26, t + SMALL.h + 1, SMALL);
  } else {
    fig(small[0], 0, t, SMALL);
    fig(small[1], 0, t + SMALL.h + 1, SMALL);
    fig(big, 13, t, BIG);
  }
};

// Results: the figures, nothing else. Explanations live behind each figure's expand button and on the other tabs.
page('results', 'Results', () => {
  block('intro', 0, row(4, 1.5), 38, 4);
  label('how', 'How the benchmark works', row(1, 0.6));
  block('explainer', 0, row(7, 2), 38, 7);
  // The three headline measures side by side, then each against cost, before the detail.
  label('headline', 'Headline results', row(1, 0.6));
  const hl = row(12, 1);
  fig('hl-productivity', 0, hl);
  fig('hl-communication', 13, hl);
  fig('hl-vigilance', 26, hl);
  const vc = row(14, 2);
  fig('productivity-cost', 0, vc, { w: 18.5, h: 14 });
  fig('vigilance-cost', 19.5, vc, { w: 18.5, h: 14 });
  label('team', 'Team performance', row(1, 0.6));
  featuredRow('productivity-vigilance', ['rounds-distribution', 'outcomes'], 'left');
  label('vigilance', 'Vigilance and sabotage', row(1, 0.6));
  featuredRow('correct-votes', ['sabotage-damage', 'impostor-survival'], 'right');
  label('communication', 'Communication', row(1, 0.6));
  featuredRow('reliability-disclosure', ['claims-truth', 'floor-pull'], 'left');
  label('cost', 'Cost and reasoning', row(1, 0.6));
  featuredRow('cost', ['thinking', 'invalid-actions'], 'right');
  label('all', 'Every metric', row(1, 0.6));
  fig('all-metrics', 0, row(17, 2), { w: 38, h: 17 });
  label('more', 'More', row(1, 0.6));
  const t = row(13);
  fig('disclosure-lag', 0, t, { w: 25, h: 13 });
  fig('survival-curve', 26, t, { w: 12, h: 13 });
  fig('flagship-sibling', 0, row(11, 2), { w: 38, h: 11 });
});

page('methodology', 'Methodology', () => {
  heading('methodology', 'Methodology', row(3), 'How the benchmark works, what is measured, and why each choice was made.');
  let t = row(17);
  block('m-game', 0, t, 11, 17);
  fig('map', 12, t);
  fig('round-sequence', 0, row(11));
  t = row(11);
  block('m-tasks', 0, t, 12, 11);
  block('tasks-table', 13, t, 25, 11);
  t = row(13);
  block('m-lineups', 0, t, 10, 13);
  fig('block-design', 11, t);
  t = row(9);
  block('m-harness', 0, t, 12, 9);
  block('pool-table', 13, t, 25, 9);
  block('metrics-table', 0, row(12), 38, 12);
  block('m-audit', 0, row(4), 38, 4);
  fig('claim-audit', 0, row(14));
  t = row(10);
  block('m-stats', 0, t, 12, 10);
  block('followups-table', 13, t, 25, 10);
  fig('precision', 0, row(12, 2), { w: 25, h: 12 });
});

page('research', 'Research', () => {
  heading('research', 'Research', row(3), 'Protocols, technical notes and planned studies. Each opens as a full document.');
  block('papers', 0, row(19), 38, 19);
  block('related', 0, row(9, 2), 38, 9);
  heading('planned', 'Planned experiments', row(3), 'Follow-ups that test defenses and causes, sized from v1’s measured variance before they run.');
  const t = row(11, 2);
  fig('verification', 0, t);
  fig('minority', 13, t);
  fig('leader', 26, t);
});

page('reproduce', 'Reproduce', () => {
  heading('reproduce', 'Reproduce', row(3), 'Rerun the method on the open engine, and check that official runs were not cherry-picked.');
  block('reproduce', 0, row(17, 2), 38, 17);
});

page('glossary', 'Glossary', () => {
  heading('glossary', 'Glossary', row(3), 'Every capitalized term on this site is used in exactly this sense.');
  block('glossary', 0, row(32, 2), 38, 32);
});

page('about', 'About', () => {
  heading('about', 'About Zantine Labs', row(2));
  let t = row(9);
  block('about', 0, t, 25, 9);
  block('summary', 26, t, 12, 9);
  block('why', 0, row(10), 38, 10);
  t = row(8, 2);
  fig('roadmap', 0, t, { w: 25, h: 8 });
  block('contact', 26, t, 12, 8);
});

export const PAGES = pages;
export const PAGE_BY_ID: Record<string, Page> = Object.fromEntries(pages.map((p) => [p.id, p]));

/** The page a figure appears on, so a shared figure link opens over the right page. */
export const PAGE_OF_FIGURE: Record<string, string> = Object.fromEntries(
  pages.flatMap((p) => p.items.flatMap((it) => (it.kind === 'figure' ? [[it.id, p.id]] : []))),
);
