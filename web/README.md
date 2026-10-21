# Zantine Labs website

The site is white, with blue file-folder tabs across the top. Headings are set in Michelangelus and everything else in Geist; nothing uses a monospaced font. Michelangelus is never served: it shows only on machines where it is installed, and Zodiak, a contemporary serif by Indian Type Foundry, stands in for everyone else. The mark is a stencil-cut Z (`Mark` and `MarkEmblem` in `src/components/icons.tsx`, and `public/favicon.svg`). Each tab is its own page: Results, Methodology, Research, Reproduce, Glossary and About (`#results`, `#methodology`, …), and pages scroll up and down only. The Results tab carries only a title, a one-line purpose and the figures. It opens with a four-panel “How the benchmark works” explainer (`Explainer` in `src/components/blocks.tsx`), then, as Artificial Analysis does, the three headline Scores side by side (Productivity, Communication, Vigilance, each out of 100 where 100 is a Perfect Team: sorted columns in a track up to 100, with values and 95% intervals, the first ten settings on the card and all of them full screen; `ENTRY_SCORES` in `src/data/sim.ts`, terms in `CONTEXT.md`), then Productivity and Vigilance against cost (log-scale cost, Pareto toggle), then rows of one large figure beside two small ones, alternating sides. Each card shows a prominent title, one line on why the figure matters to a team deploying agents (`why` in `src/figures.tsx`), and its own legend: selecting a model or effort level there hides or shows it in that figure only. Explanations live behind each figure's expand button and on the other tabs. Every result chart plots each model at each of its reasoning-effort levels, grouped by model from low to high effort. Every figure opens full screen with its data table, method notes and a CSV download. Research documents open as printed sheets over the page.

```sh
npm install
npm run dev      # http://localhost:5173
npm run build    # static site in dist/, then the files for crawlers and agents
SITE_URL=https://zantine.ai npm run build   # set the canonical origin (zantine.ai is a placeholder)
```

## For crawlers and AI agents

The app draws everything client-side, so `scripts/agent-seo.mjs` runs after `vite build` and writes what a reader that does not run JavaScript needs: a real HTML page per tab and per research document (`/methodology/`, `/research/<slug>/`, each with its text, canonical URL, Open Graph and schema.org JSON-LD, and a one-line script that opens the matching tab), the same pages as Markdown (`/methodology.md`), `/llms.txt` and `/llms-full.txt`, `/data/*.json` (benchmark status, model pool, figures, glossary), `robots.txt` that names and allows the AI crawlers, and `sitemap.xml`. It reads the site's own modules (`layout.ts`, `blocks.tsx`, `figures.tsx`, `papers.ts`, `glossary.ts`), so new pages and blocks are picked up without changes. Answers to the questions people ask assistants live in `src/content/faq.ts`.

Simulated values never reach these files: figures stamped `simulated` or `planned` are described without their numbers, and `/data/results.json` reports `"published": false` until no figure is simulated.

## Where things live

| Path | What it holds |
| --- | --- |
| `src/layout.ts` | The pages behind the folder tabs. Each page's items are placed in whole centimetres on a 38 cm board, in reading order; phones stack them in one 12 cm column. |
| `src/figures.tsx` | The figure registry: chart, copy, method notes, sample and data table for each figure. |
| `src/charts/` | SVG charts. `scale.ts` puts one tick on every centimetre. |
| `src/App.tsx` | The folder-tab header, hash routing between pages, the board (scaled to fit the window width) and the full-screen overlays. |
| `src/content/papers.ts` | Research documents (protocol, technical notes, planned studies). |
| `src/data/models.ts` | The v1 Model Pool, each model's reasoning-effort levels (`ENTRIES` is model × effort, the unit every result chart plots) and the lab palette: muted inks (sienna, ochre, heather, teal, steel), validated for color-vision deficiency on white. |
| `src/data/design.ts` | Real, computed design facts: the block design, planned precision, list-price cost projection, an example map. |
| `src/data/sim.ts` | **Simulated preview numbers.** Every figure that uses them is stamped “Simulated data”. |

## Deploying

Live at https://zantine-labs.vercel.app (Vercel project `zantine-labs`). Link this folder once with `vercel link --project zantine-labs` (`.vercel/` is not in git), then from this folder:

```sh
vercel deploy --prod --build-env SITE_URL=https://zantine-labs.vercel.app
```

Vercel runs `npm run build` (see `vercel.json`); `SITE_URL` sets the canonical links, sitemap and Open Graph tags. The account also has an unrelated project called `web` (khali.niravsurabhi.com): never link this folder by its folder name.

## Open items

- `src/data/sim.ts` is invented. The site went public on 20 October 2026 with these simulated numbers and real model names, every figure stamped “Simulated data”, by decision of the project owner. Replace it with v1 results when they exist.
- Result charts plot every reasoning-effort level, at the owner's request, to preview the layout. The v1 run plays each lab's medium effort only; low and high come from the v1.6 Mirror Match sweep (#26). When real results replace `sim.ts`, v1 figures show medium only, and the effort comparisons wait for v1.6.
- Re-check model IDs and prices in `src/data/models.ts` against each lab’s docs.
- The “Work with us” card has no contact address yet.
- Zodiak is self-hosted from `public/fonts/zodiak/` under the ITF Free Font License. The license allows serving it on our own site, but not sharing the font files through a repository and not modifying, subsetting or converting them, so the files are kept out of git (`.gitignore`). Before building, download Zodiak from Fontshare (https://www.fontshare.com/fonts/zodiak) and put `Zodiak-Variable.woff2`, `Zodiak-VariableItalic.woff2` and `LICENSE-FFL.txt` in that folder. Without them, headings fall back to Iowan Old Style or Georgia.
- Michelangelus (Microsoft, studiogusto) is named in the font stack but never bundled: its license grants no distribution rights, so it cannot be served as a web font. It renders only where a reader has it installed, and only in browsers that allow installed fonts (Chromium does; Safari limits web pages to system fonts). Everyone else sees Zodiak. Do not add the Michelangelus files to `public/`.
- Geist is bundled from `@fontsource-variable/geist` under the SIL Open Font License.

URLs: `#results`, `#methodology`, `#research`, `#reproduce`, `#glossary` and `#about` open a page; `#fig/<id>` opens a figure over the page it belongs to; `#paper/<slug>` opens a document.
