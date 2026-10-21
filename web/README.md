# Zantine Labs website

The site is white paper with three inks and hairlines; color belongs to the data, and the one other hue, amber, marks simulated numbers. Headings are set in Michelangelus and everything else in Geist; nothing uses a monospaced font. Michelangelus is never served: it shows only on machines where it is installed, and Zodiak, a contemporary serif by Indian Type Foundry, stands in for everyone else. The mark is a stencil-cut Z (`Mark` in `src/components/icons.tsx`, and `public/favicon.svg`). A plain top bar links the six pages, Results, Methodology, Research, Reproduce, Glossary and About (`#results`, `#methodology`, …), with the current one underlined; pages scroll up and down only. The Results page carries only a title, a one-line purpose, a status line and the figures. It opens with a three-step “How a game works” explainer (`Explainer` in `src/components/blocks.tsx`), then the three Scores side by side (Productivity, Communication, Vigilance, each out of 100 where 100 is a Perfect Team; `ENTRY_SCORES` in `src/data/sim.ts`, terms in `CONTEXT.md`), then Productivity and Vigilance against cost, then rows of one large figure beside two small ones, alternating sides. Figure cards follow Artificial Analysis: a framed card (hairline border, rounded corners) with a serif title, one grey line naming the measure and which way is better (`measure` and `direction`), and the chart. Bar charts on cards (`src/charts/modelbars.tsx`) show each model once, at its highest reasoning effort (`topEntries` in `src/data/models.ts`), sorted, scaled to a round number above the data, with the value inside each column, dashed guides without axis labels, and the lab's logo and the model's name under each column. Scatter plots (`src/charts/scatter.tsx`) follow Artificial Analysis's: every effort level as a dot in its lab's color, the levels of one model joined low to high and the model named at its highest, a dotted Pareto line, the most attractive quadrant shaded green (the figure's thresholds, or the medians of what is shown), a legend of labs above the plot, and axes fitted to the data with no gridlines. A Sibling is drawn in its lab's color, lightened. The title or the expand button opens the figure full screen, where every effort level is plotted with its 95% interval, alongside model and effort filters, the Pareto toggle, the method, the data table and a CSV download. Research documents open as printed sheets over the page.

```sh
npm install
npm run dev      # http://localhost:5173
npm run build    # static site in dist/, then the files for crawlers and agents
npm test         # the build scripts' tests (scripts/*.test.mjs)
SITE_URL=https://zantine.ai npm run build   # set the canonical origin (zantine.ai is a placeholder)
```

## For crawlers and AI agents

The app draws everything client-side, so `scripts/agent-seo.mjs` runs after `vite build` and writes what a reader that does not run JavaScript needs: a real HTML page per tab and per research document (`/methodology/`, `/research/<slug>/`, each with its text, canonical URL, Open Graph and schema.org JSON-LD, and a one-line script that opens the matching tab), the same pages as Markdown (`/methodology.md`), `/llms.txt` and `/llms-full.txt`, `/data/*.json` (benchmark status, model pool, figures, glossary), `robots.txt` that names and allows the AI crawlers, and `sitemap.xml`. It reads the site's own modules (`layout.ts`, `blocks.tsx`, `figures.tsx`, `papers.ts`, `glossary.ts`), so new pages and blocks are picked up without changes. Answers to the questions people ask assistants live in `src/content/faq.ts`.

Simulated values never reach these files: figures stamped `simulated` or `planned` are described without their numbers, and `/data/results.json` reports `"published": false` until no figure is simulated.

## Where things live

| Path | What it holds |
| --- | --- |
| `src/layout.ts` | The pages linked from the top bar. Each page's items are placed in whole centimetres on a 38 cm board, in reading order; phones stack them in one 12 cm column. |
| `src/figures.tsx` | The figure registry: chart, copy, method notes, sample and data table for each figure. |
| `src/charts/` | SVG charts. `scale.ts` puts one tick on every centimetre. |
| `src/App.tsx` | The top bar, hash routing between pages, the board (scaled to fit the window width) and the full-screen overlays. |
| `src/content/papers.ts` | Research documents (protocol, technical notes, planned studies). |
| `src/data/models.ts` | The v1 Model Pool, each model's reasoning-effort levels (`ENTRIES` is model × effort, the unit every result chart plots) and the lab palette: muted inks (sienna, ochre, heather, teal, steel), validated for color-vision deficiency on white. |
| `src/data/design.ts` | Real, computed design facts: the block design, planned precision, list-price cost projection, an example map. |
| `src/data/sim.ts` | **Simulated preview numbers.** Every figure that uses them is stamped “Simulated data”. |

## Deploying

Live at https://zantine-labs.vercel.app (Vercel project `zantine-labs`, root directory `web`, connected to this GitHub repo). Every push to `main` deploys to production; every other pushed branch, and so every pull request, gets a preview deployment. A push is skipped when `web/` is unchanged since the branch's last successful deployment (`scripts/ignore-build.sh`, the `ignoreCommand` in `vercel.json`), so engine changes don't redeploy the site, while a web change whose deployment failed is built again on the next push. Vercel runs `npm run build` (see `vercel.json`) with `SITE_URL=https://zantine-labs.vercel.app` set on the project, which sets the canonical links, sitemap and Open Graph tags. To redeploy without a push, use “Redeploy” in the Vercel dashboard or `vercel redeploy <deployment URL>`.

The account also has an unrelated project called `web` (khali.niravsurabhi.com): if you link this folder with the CLI, use `vercel link --project zantine-labs`, never its folder name.

## Open items

- `src/data/sim.ts` is invented. The site went public on 20 October 2026 with these simulated numbers and real model names, every figure stamped “Simulated data”, by decision of the project owner. Replace it with v1 results when they exist.
- Result charts plot every reasoning-effort level, at the owner's request, to preview the layout. The v1 run plays each lab's medium effort only; low and high come from the v1.6 Mirror Match sweep (#26). When real results replace `sim.ts`, v1 figures show medium only, and the effort comparisons wait for v1.6.
- Re-check model IDs and prices in `src/data/models.ts` against each lab’s docs.
- The “Work with us” card has no contact address yet.
- Zodiak is self-hosted from `public/fonts/zodiak/` under the ITF Free Font License. The license allows serving it on our own site, but not sharing the font files through a repository and not modifying, subsetting or converting them, so the files are kept out of git (`.gitignore`). `scripts/fonts.mjs`, run first by `npm run dev` and `npm run build`, downloads the official package from Fontshare and copies out, unmodified, `Zodiak-Variable.woff2`, `Zodiak-VariableItalic.woff2` and the license. It checks the files already there first and downloads again if one is missing or damaged; a download is checked in full and moved into place whole, so a failed run never leaves a partial font. On Vercel a failed download stops the build; offline, a local run warns and headings fall back to Iowan Old Style or Georgia.
- Michelangelus (Microsoft, studiogusto) is named in the font stack but never bundled: its license grants no distribution rights, so it cannot be served as a web font. It renders only where a reader has it installed, and only in browsers that allow installed fonts (Chromium does; Safari limits web pages to system fonts). Everyone else sees Zodiak. Do not add the Michelangelus files to `public/`.
- Geist is bundled from `@fontsource-variable/geist` under the SIL Open Font License.
- The lab logos in `src/charts/logos.tsx` are paths from Lobe Icons (MIT, github.com/lobehub/lobe-icons). The marks are the labs' trademarks, used only to say whose model a column or point is.

URLs: `#results`, `#methodology`, `#research`, `#reproduce`, `#glossary` and `#about` open a page; `#fig/<id>` opens a figure over the page it belongs to; `#paper/<slug>` opens a document.
