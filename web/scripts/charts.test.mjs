// `npm test`: chart scales against the site's own data, loaded through Vite so the TypeScript
// modules run as they do in the app.

import { after, before, test } from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { createServer } from 'vite';

const ROOT = fileURLToPath(new URL('..', import.meta.url));
let vite;
let scale;
let sim;

before(async () => {
  vite = await createServer({ root: ROOT, logLevel: 'silent', appType: 'custom', server: { middlewareMode: true, hmr: false } });
  scale = await vite.ssrLoadModule('/src/charts/scale.ts');
  sim = await vite.ssrLoadModule('/src/data/sim.ts');
});

after(() => vite?.close());

// The tick formatters the scatter figures use (src/figures.tsx).
const wholeRounds = (v) => v.toFixed(0);
const pct = (v) => `${Math.round(v * 100)}%`;

/** The number a label states: "18" is 18, "62%" is 62. */
const stated = (label) => parseFloat(label.replace(/[−–]/g, '-').replace(/[^0-9.-]/g, ''));

/** A scatter card's axis over [lo, hi] (pad 0.08, as Scatter uses on cards) must label every tick with its own value. */
function assertFaithful(lo, hi, px, format, per, opts, what) {
  const { ticks } = scale.fitLinear(lo, hi, px, { pad: 0.08, format, ...opts });
  const labels = ticks.map(format);
  assert.equal(new Set(labels).size, labels.length, `${what}: repeated labels ${labels.join(', ')}`);
  ticks.forEach((t, i) => assert.ok(Math.abs(stated(labels[i]) - t * per) < 1e-6, `${what}: "${labels[i]}" marks ${t}`));
}

test('a card filtered to Fable low and medium labels whole Rounds faithfully', () => {
  // The review's case: 17.78 and 18.1 Rounds on a 335.748 px plot drew "18, 18, 18, 18".
  const v = ['fable:low', 'fable:medium'].map((id) => sim.ENTRY_RESULTS.find((r) => r.id === id).rounds.v);
  assertFaithful(Math.min(...v), Math.max(...v), 335.748, wholeRounds, 1, {}, 'Fable low + medium');
});

test('any two settings, at any card height, get faithful labels on every scatter measure', () => {
  const measures = [
    { key: 'rounds', format: wholeRounds, per: 1, opts: {} },
    { key: 'correctVote', format: pct, per: 100, opts: { ceil: 1 } },
    { key: 'reliability', format: pct, per: 100, opts: { ceil: 1 } },
    { key: 'disclosure', format: pct, per: 100, opts: { ceil: 1 } },
  ];
  const rows = sim.ENTRY_RESULTS;
  for (const m of measures)
    for (let i = 0; i < rows.length; i++)
      for (let j = i; j < rows.length; j++)
        for (const px of [150, 260, 335.748, 480, 700]) {
          const a = rows[i][m.key].v;
          const b = rows[j][m.key].v;
          assertFaithful(Math.min(a, b), Math.max(a, b), px, m.format, m.per, m.opts, `${m.key} ${rows[i].id} + ${rows[j].id} at ${px}px`);
        }
});

test('the Lobe Icons notice ships with the logo paths', () => {
  const notice = 'Copyright (c) 2023 LobeHub';
  assert.ok(readFileSync(new URL('../src/charts/logos.tsx', import.meta.url), 'utf8').includes(notice), 'logos.tsx header');
  // Vite copies public/ into the build, so the notice is served beside the bundled marks.
  const shipped = new URL('../public/third-party-notices.txt', import.meta.url);
  assert.ok(existsSync(shipped) && readFileSync(shipped, 'utf8').includes(notice), 'public/third-party-notices.txt');
});
