// After `vite build`: give crawlers and AI agents a site they can read without JavaScript.
//
// The app draws everything client-side behind hash routes, so a crawler that does not run
// JavaScript (GPTBot, ClaudeBot, PerplexityBot, CCBot…) would see an empty <div id="root">.
// This script loads the site's own content modules through Vite and writes, into dist/:
//
//   /index.html, /<page>/index.html, /research/<slug>/index.html
//       Real HTML per page: the page's text inside #root (React replaces it on mount), plus
//       title, description, canonical, Open Graph and JSON-LD. A one-line script maps the path
//       to the app's hash route, so /methodology/ opens the Methodology tab.
//   /index.md, /<page>.md, /research/<slug>.md      The same pages as Markdown.
//   /llms.txt, /llms-full.txt                       The llmstxt.org index and the full text.
//   /data/*.json                                    Benchmark status, model pool, glossary, figures.
//   /robots.txt, /sitemap.xml
//
// Simulated preview numbers are never written to any of these files: a figure whose status is
// 'simulated' or 'planned' is described (what it measures, method, sample) without its values,
// so no model comparison can leak into an index or a model's training data before v1.
//
// The canonical origin comes from SITE_URL, e.g. `SITE_URL=https://zantine.ai npm run build`.

import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import TurndownService from 'turndown';
import { gfm } from 'turndown-plugin-gfm';
import { createServer } from 'vite';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const DIST = join(ROOT, 'dist');
const SITE = (process.env.SITE_URL ?? 'https://zantine.ai').replace(/\/+$/, '');
const TODAY = new Date().toISOString().slice(0, 10);

const vite = await createServer({ root: ROOT, logLevel: 'error', appType: 'custom', server: { middlewareMode: true, hmr: false, ws: false } });
const load = (p) => vite.ssrLoadModule(p);
const [{ PAGES }, { FIGURES, FIGURE_BY_ID, STATUS_LABEL }, { Block }, { PAPERS }, { GLOSSARY }, { FAQ }, { MODELS, LABS, ENTRIES }] = await Promise.all(
  ['/src/layout.ts', '/src/figures.tsx', '/src/components/blocks.tsx', '/src/content/papers.ts', '/src/content/glossary.ts', '/src/content/faq.ts', '/src/data/models.ts'].map(load),
);

// ---------------------------------------------------------------------------------------------
// Copy for machines. Plain statements of fact; nothing here may imply a result exists.

const ORG_DESCRIPTION =
  'Zantine Labs builds benchmarks for AI agents and the teams they form: single agents, multi-agent systems, harnesses and orchestration. Results are published as graphs of the underlying data with the cost of every result, never as a ranking or a single score.';

const BENCH_DESCRIPTION =
  'Zantine-1 is a benchmark for AI agent teams and multi-agent orchestration. Five of seven AI Models fill ten Seats, two each, and complete interdependent, automatically graded Tasks while up to two Seats are secretly Impostors working against the team. It scores Productivity, Communication and Vigilance separately, each out of 100 against a Perfect Team, and measures Sabotage Damage, with thinking tokens and list-price cost beside each result.';

const KEYWORDS = [
  'AI agent benchmark',
  'multi-agent benchmark',
  'agent team evaluation',
  'agent orchestration',
  'orchestrator model',
  'multi-agent systems',
  'LLM evaluation',
  'agent reliability',
  'AI safety',
  'sabotage detection',
  'compromised agent',
  'insider threat',
  'social deduction',
  'Among Us',
  'reasoning effort',
];

const STATUS_LINE =
  'Status: the v1 study design is fixed but no model has been scored. Figures on the site use simulated preview data and are stamped “Simulated data”; they are not results, and no model comparison is published before the v1 run.';

const PAGE_META = {
  results: {
    title: 'Zantine-1: the frontier benchmark for how well AI agents work together · Zantine Labs',
    description:
      'Zantine-1 evaluates how well AI agents work together: Productivity, Communication and Vigilance, each scored out of 100 against a Perfect Team, per model and reasoning effort, with cost. Results pending; figures are a simulated preview.',
  },
  methodology: {
    title: 'Methodology · Zantine-1 agent team benchmark',
    description: 'How the Zantine-1 benchmark works: ten AI models, checkable interdependent Tasks, hidden Impostors, Meetings and votes; the 378-game block design, the neutral harness, the Claim Audit and the statistics.',
  },
  research: {
    title: 'Research · Zantine Labs',
    description: 'The Zantine-1 protocol, technical notes and planned studies on multi-agent teamwork, verification, leaders and compromised orchestrators, with related work.',
  },
  reproduce: {
    title: 'Reproduce · Zantine Labs',
    description: 'Rerun Zantine-1 on the open, seeded engine with the Public Seed Set, and check official runs against their published SHA-256 Seed Commitment.',
  },
  glossary: {
    title: 'Glossary · Zantine Labs',
    description: 'Definitions of every Zantine term: Crewmate, Impostor, Round, Meeting, Ejection, Task Graph, Sabotage Damage, Verification Protocol, Leader, Compromised Leader and more.',
  },
  about: {
    title: 'About Zantine Labs · benchmarks for AI agents and teams',
    description: ORG_DESCRIPTION,
  },
};

// ---------------------------------------------------------------------------------------------
// Helpers

const esc = (s) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');

/** The site's inline markup (**bold**, `code`, [text](url)) as HTML, matching components/Rich.tsx. */
const rich = (text) =>
  esc(text)
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\[([^\]]+)\]\(([^)]+)\)/g, (_, t, href) => `<a href="${href.startsWith('#') ? hashToPath(href.slice(1)) : /^(https?:|\/|mailto:)/.test(href) ? href : '#'}">${t}</a>`);

const pagePath = (id) => (id === PAGES[0].id ? '/' : `/${id}/`);
const pageMd = (id) => (id === PAGES[0].id ? '/index.md' : `/${id}.md`);
const paperPath = (slug) => `/research/${slug}/`;
const paperMd = (slug) => `/research/${slug}.md`;
const abs = (p) => SITE + p;
const figUrl = (id) => `${pagePath(FIGURE_BY_ID[id] ? pageOfFigure(id) : PAGES[0].id)}#fig/${id}`;
function pageOfFigure(id) {
  return PAGES.find((p) => p.items.some((it) => it.kind === 'figure' && it.id === id))?.id ?? PAGES[0].id;
}

/** Figures whose values are real (design facts and list-price estimates), and so may be published. */
const hasRealValues = (f) => f.status === 'design' || f.status === 'estimate';

function tableHtml({ columns, rows }, caption) {
  return `<table>${caption ? `<caption>${rich(caption)}</caption>` : ''}<thead><tr>${columns.map((c) => `<th>${rich(c)}</th>`).join('')}</tr></thead><tbody>${rows
    .map((r) => `<tr>${r.map((c) => `<td>${rich(c)}</td>`).join('')}</tr>`)
    .join('')}</tbody></table>`;
}

function figureTable(f) {
  try {
    const ids = f.legend === 'entries' ? ENTRIES.map((e) => e.id) : MODELS.map((m) => m.id);
    const t = f.table(ids);
    return t && t.columns?.length && t.rows?.length ? t : null;
  } catch {
    return null;
  }
}

const list = (items) => (items?.length ? `<ul>${items.map((s) => `<li>${rich(s)}</li>`).join('')}</ul>` : '');

function figureHtml(f) {
  const name = f.num ? `Figure ${f.num}. ${f.title}` : f.title;
  const t = hasRealValues(f) ? figureTable(f) : null;
  return `<section class="figure" id="fig-${esc(f.id)}">
<h3><a href="${figUrl(f.id)}">${rich(name)}</a></h3>
<p><em>${rich(f.subtitle)}</em></p>
<p>${rich(f.why)}</p>
<p>${rich(f.about)}</p>
${f.read?.length ? `<h4>How to read it</h4>${list(f.read)}` : ''}
${f.method?.length ? `<h4>Method</h4>${list(f.method)}` : ''}
<p>Sample: ${rich(f.sample)}</p>
<p>Data: ${esc(STATUS_LABEL[f.status] ?? f.status)}.${hasRealValues(f) ? '' : ' The figure shows simulated preview data, so its values are not reproduced here. No model has been scored yet.'}</p>
${t ? tableHtml(t) : ''}
</section>`;
}

/** A block rendered by the site's own component, with its drawings reduced to their labels. */
function blockHtml(k) {
  const html = renderToStaticMarkup(createElement(Block, { k }));
  return html
    .replace(/<svg\b[^>]*?aria-label="([^"]*)"[\s\S]*?<\/svg>/g, '<p><em>Diagram: $1.</em></p>')
    .replace(/<svg\b[\s\S]*?<\/svg>/g, '')
    .replace(/<button\b[^>]*>([\s\S]*?)<\/button>/g, '<span>$1</span>')
    .replace(/\s(?:style|class)="[^"]*"/g, '')
    .replace(/href="#([^"]*)"/g, (_, h) => `href="${hashToPath(decodeURIComponent(h))}"`);
}

/** The app's hash routes as the crawlable paths this script writes. */
function hashToPath(h) {
  if (PAGES.some((p) => p.id === h)) return pagePath(h);
  if (h.startsWith('fig/') && FIGURE_BY_ID[h.slice(4)]) return figUrl(h.slice(4));
  if (h.startsWith('paper/')) return paperPath(h.slice(6));
  return `#${h}`;
}

function itemsHtml(page) {
  const hasIntro = page.items.some((it) => it.kind === 'block' && it.key === 'intro');
  let h1Done = hasIntro;
  return page.items
    .map((it) => {
      if (it.kind === 'figure') return FIGURE_BY_ID[it.id] ? figureHtml(FIGURE_BY_ID[it.id]) : '';
      if (it.kind === 'heading') {
        const tag = !h1Done && !it.small ? 'h1' : 'h2';
        h1Done = true;
        return `<${tag}>${rich(it.title)}</${tag}>${it.sub ? `<p>${rich(it.sub)}</p>` : ''}`;
      }
      if (it.key === 'footer') return '';
      try {
        return blockHtml(it.key);
      } catch (e) {
        console.warn(`agent-seo: block ${it.key} did not render (${e.message})`);
        return '';
      }
    })
    .join('\n');
}

function paperHtml(p) {
  const body = p.body
    .map((b) => {
      switch (b.t) {
        case 'h2':
          return `<h2>${rich(b.text)}</h2>`;
        case 'h3':
          return `<h3>${rich(b.text)}</h3>`;
        case 'p':
          return `<p>${rich(b.text)}</p>`;
        case 'list': {
          const tag = b.ordered ? 'ol' : 'ul';
          return `<${tag}>${b.items.map((s) => `<li>${rich(s)}</li>`).join('')}</${tag}>`;
        }
        case 'table':
          return tableHtml(b, b.caption);
        case 'eq':
          return `<p><code>${esc(b.text)}</code> (${esc(b.label)})</p>`;
        case 'fig':
          return `<p><a href="${FIGURE_BY_ID[b.id] ? figUrl(b.id) : '#'}">${rich(b.caption)}</a></p>`;
        case 'note':
          return `<blockquote><p>${rich(b.text)}</p></blockquote>`;
        case 'code':
          return `<pre><code>${esc(b.text)}</code></pre>`;
        default:
          return '';
      }
    })
    .join('\n');
  const refs = p.references.length
    ? `<h2>References</h2><ol>${p.references.map((r) => `<li>${esc(r.text)}${r.url ? ` <a href="${esc(r.url)}">${esc(r.url)}</a>` : ''}</li>`).join('')}</ol>`
    : '';
  return `<article>
<h1>${rich(p.title)}</h1>
<p>${esc(p.number)} · ${esc(p.kind)} · ${esc(p.version)} · ${esc(p.date)} · ${esc(p.status)}</p>
<h2>Abstract</h2>
<p>${rich(p.abstract)}</p>
<p>Keywords: ${p.keywords.map(esc).join(', ')}</p>
${body}
${refs}
</article>`;
}

const faqHtml = () =>
  `<section id="faq"><h2>Questions</h2>${FAQ.map((f) => `<h3>${esc(f.q)}</h3><p>${rich(f.a)}</p>`).join('\n')}</section>`;

const papersListHtml = () =>
  `<section><h2>Documents</h2><ul>${PAPERS.map((p) => `<li><a href="${paperPath(p.slug)}">${esc(p.number)}: ${rich(p.title)}</a> (${esc(p.kind)}, ${esc(p.status)})</li>`).join('')}</ul></section>`;

// ---------------------------------------------------------------------------------------------
// Structured data (schema.org JSON-LD)

const ORG = {
  '@type': 'Organization',
  '@id': `${SITE}/#org`,
  name: 'Zantine Labs',
  url: `${SITE}/`,
  logo: `${SITE}/favicon.svg`,
  description: ORG_DESCRIPTION,
  knowsAbout: KEYWORDS,
};
const WEBSITE = { '@type': 'WebSite', '@id': `${SITE}/#website`, url: `${SITE}/`, name: 'Zantine Labs', description: ORG_DESCRIPTION, publisher: { '@id': ORG['@id'] }, inLanguage: 'en' };
const BENCH = {
  '@type': 'CreativeWork',
  '@id': `${SITE}/#zantine-1`,
  name: 'Zantine-1',
  alternateName: 'Zantine-1 benchmark for AI agent teams',
  description: BENCH_DESCRIPTION,
  creator: { '@id': ORG['@id'] },
  keywords: KEYWORDS.join(', '),
  creativeWorkStatus: 'Design fixed; results not yet run',
  url: `${SITE}/`,
  subjectOf: PAPERS.map((p) => ({ '@id': `${abs(paperPath(p.slug))}#article` })),
};

function webPage(id, url, meta, extra = {}) {
  return {
    '@type': 'WebPage',
    '@id': `${url}#webpage`,
    url,
    name: meta.title,
    description: meta.description,
    isPartOf: { '@id': WEBSITE['@id'] },
    about: { '@id': BENCH['@id'] },
    publisher: { '@id': ORG['@id'] },
    dateModified: TODAY,
    inLanguage: 'en',
    ...extra,
  };
}

const faqLd = (url) => ({
  '@type': 'FAQPage',
  '@id': `${url}#faq`,
  url,
  mainEntity: FAQ.map((f) => ({
    '@type': 'Question',
    name: f.q,
    acceptedAnswer: { '@type': 'Answer', text: f.a.replace(/\*\*([^*]+)\*\*/g, '$1').replace(/\[([^\]]+)\]\((\/[^)]*)\)/g, (_, t, h) => `${t} (${abs(h)})`) },
  })),
});

const glossaryLd = (url) => ({
  '@type': 'DefinedTermSet',
  '@id': `${url}#terms`,
  name: 'Zantine glossary',
  url,
  hasDefinedTerm: GLOSSARY.flatMap((g) => g.terms.map(([term, def]) => ({ '@type': 'DefinedTerm', name: term, description: def, inDefinedTermSet: `${url}#terms` }))),
});

const paperLd = (p, url) => ({
  '@type': 'ScholarlyArticle',
  '@id': `${url}#article`,
  url,
  headline: p.title.length > 110 ? `${p.title.slice(0, 107)}…` : p.title,
  name: p.title,
  abstract: p.abstract,
  keywords: p.keywords.join(', '),
  identifier: p.number,
  version: p.version,
  genre: p.kind,
  creativeWorkStatus: p.status,
  author: { '@id': ORG['@id'] },
  publisher: { '@id': ORG['@id'] },
  about: { '@id': BENCH['@id'] },
  dateModified: TODAY,
  inLanguage: 'en',
  citation: p.references.map((r) => r.url ?? r.text),
});

const jsonLd = (nodes) => `<script type="application/ld+json">${JSON.stringify({ '@context': 'https://schema.org', '@graph': nodes }).replace(/</g, '\\u003c')}</script>`;

// ---------------------------------------------------------------------------------------------
// Pages

const template = await readFile(join(DIST, 'index.html'), 'utf8');

const navHtml = () =>
  `<nav aria-label="Pages"><a href="/">Zantine Labs</a> · ${PAGES.map((p) => `<a href="${pagePath(p.id)}">${esc(p.title)}</a>`).join(' · ')} · <a href="/llms.txt">llms.txt</a></nav>`;

const footerHtml = (md) =>
  `<footer><p>${esc(STATUS_LINE)}</p><p>Machine-readable: <a href="${md}">this page as Markdown</a> · <a href="/llms.txt">llms.txt</a> · <a href="/llms-full.txt">full text</a> · <a href="/data/results.json">benchmark status (JSON)</a> · <a href="/data/models.json">model pool (JSON)</a></p><p>Zantine Labs. Benchmarks for AI agents and teams. Zantine is a working name.</p></footer>`;

function renderPage({ path, md, hash, meta, content, ld, ogType = 'website' }) {
  const url = abs(path);
  const head = [
    `<link rel="canonical" href="${url}" />`,
    `<link rel="alternate" type="text/markdown" href="${md}" title="${esc(meta.title)} (Markdown)" />`,
    `<link rel="alternate" type="text/plain" href="/llms.txt" title="llms.txt" />`,
    `<meta name="robots" content="index, follow, max-snippet:-1, max-image-preview:large" />`,
    `<meta name="keywords" content="${esc(KEYWORDS.join(', '))}" />`,
    `<meta property="og:site_name" content="Zantine Labs" />`,
    `<meta property="og:type" content="${ogType}" />`,
    `<meta property="og:title" content="${esc(meta.title)}" />`,
    `<meta property="og:description" content="${esc(meta.description)}" />`,
    `<meta property="og:url" content="${url}" />`,
    `<meta name="twitter:card" content="summary" />`,
    `<meta name="twitter:title" content="${esc(meta.title)}" />`,
    `<meta name="twitter:description" content="${esc(meta.description)}" />`,
    // The app routes by hash; a path such as /methodology/ opens the matching tab.
    hash ? `<script>if(!location.hash)history.replaceState(null,'',location.pathname+location.search+'#${hash}')</script>` : '',
    // The static copy is for readers that do not run JavaScript. Everyone else sees the app,
    // which replaces it on mount, so it is kept out of sight instead of flashing unstyled.
    `<style>#root>.static{position:absolute;width:1px;height:1px;overflow:hidden;clip-path:inset(50%)}</style>`,
    `<noscript><style>#root>.static{position:static;width:auto;height:auto;overflow:visible;clip-path:none;max-width:46rem;margin:2rem auto;padding:0 1rem;font-family:system-ui,sans-serif;line-height:1.5}</style></noscript>`,
    jsonLd(ld),
  ]
    .filter(Boolean)
    .join('\n    ');
  const body = `<div class="static">${navHtml()}<main>${content}</main>${footerHtml(md)}</div>`;
  const html = template
    .replace(/<title>[\s\S]*?<\/title>/, `<title>${esc(meta.title)}</title>`)
    .replace(/<meta name="description"[^>]*>/, `<meta name="description" content="${esc(meta.description)}" />`)
    .replace('</head>', `    ${head}\n  </head>`)
    .replace('<div id="root"></div>', `<div id="root">${body}</div>`)
    .replace(/<noscript>[\s\S]*?<\/noscript>\s*(?=<\/body>|<script)/, '');
  return { url, html, md, meta, content };
}

const out = [];

for (const page of PAGES) {
  const path = pagePath(page.id);
  const url = abs(path);
  const meta = PAGE_META[page.id] ?? { title: `${page.title} · Zantine Labs`, description: ORG_DESCRIPTION };
  let content = itemsHtml(page);
  const ld = [ORG, WEBSITE, BENCH];
  if (page.id === 'research') content += papersListHtml();
  if (page.id === 'glossary') ld.push(glossaryLd(url));
  if (page.id === 'about') {
    content += faqHtml();
    ld.push(faqLd(url));
  }
  ld.push(webPage(page.id, url, meta, page.id === 'about' ? { '@type': 'AboutPage' } : {}));
  out.push({ file: path === '/' ? 'index.html' : join(page.id, 'index.html'), mdFile: pageMd(page.id).slice(1), ...renderPage({ path, md: pageMd(page.id), hash: path === '/' ? '' : page.id, meta, content, ld }) });
}

for (const p of PAPERS) {
  const path = paperPath(p.slug);
  const url = abs(path);
  const meta = { title: `${p.title} · Zantine Labs`, description: p.abstract.length > 300 ? `${p.abstract.slice(0, 297).replace(/\s+\S*$/, '')}…` : p.abstract };
  out.push({
    file: join('research', p.slug, 'index.html'),
    mdFile: paperMd(p.slug).slice(1),
    paper: p,
    ...renderPage({ path, md: paperMd(p.slug), hash: `paper/${p.slug}`, meta, content: paperHtml(p), ld: [ORG, WEBSITE, BENCH, paperLd(p, url), webPage(p.slug, url, meta, { '@type': 'WebPage', mainEntity: { '@id': `${url}#article` } })], ogType: 'article' }),
  });
}

// ---------------------------------------------------------------------------------------------
// Markdown

const td = new TurndownService({ headingStyle: 'atx', bulletListMarker: '-', codeBlockStyle: 'fenced' });
td.use(gfm);
td.remove(['script', 'style', 'nav']);
/** Site-relative links become absolute, so a page still works once pasted into a context window. */
const absLinks = (md) => md.replace(/\]\((\/[^)\s]*)\)/g, (_, p) => `](${abs(p)})`);

for (const o of out) {
  o.markdown = absLinks(`# ${o.meta.title}\n\n> ${o.meta.description}\n\nCanonical: ${o.url}\n\n${td.turndown(o.content)}\n\n---\n\n${STATUS_LINE}\n`).replace(/\n{3,}/g, '\n\n');
}

// ---------------------------------------------------------------------------------------------
// llms.txt

const pageLines = out
  .filter((o) => !o.paper)
  .map((o) => `- [${o.meta.title.split(' · ')[0]}](${abs('/' + o.mdFile)}): ${o.meta.description}`)
  .join('\n');
const paperLines = out
  .filter((o) => o.paper)
  .map((o) => `- [${o.paper.number}: ${o.paper.title}](${abs('/' + o.mdFile)}): ${o.paper.kind}, ${o.paper.status}.`)
  .join('\n');
const faqMd = FAQ.map((f) => `### ${f.q}\n\n${absLinks(f.a)}`).join('\n\n');

const llms = `# Zantine Labs

> ${ORG_DESCRIPTION}

${BENCH_DESCRIPTION}

${STATUS_LINE}

Key facts:

- Benchmark: Zantine-1, a text-based social-deduction game (in the style of Among Us) used as a stress test for agent teamwork. The adversary is the stress test, not the subject: the question is whether a team's shared work survives.
- Measures, reported separately: Productivity (Rounds to finish all Tasks, fewer is better), Vigilance (share of a Crewmate's votes that land on an Impostor), Sabotage Damage (delay the Impostors cause, against matched games with no Impostors). Also honesty, disclosure, influence, Invalid Actions, thinking tokens and list-price cost.
- Design, fixed before any model is run: ${MODELS.length} models (${MODELS.map((m) => m.name).join(', ')}), each at its reasoning-effort levels; 378 games in a balanced block design with no early stopping; one Neutral Harness shared by every Seat.
- Orchestration: what other work calls an orchestrator, Zantine calls a Leader. The planned Leader experiment (v1.4) tests whether an appointed Leader speeds up the work, whether agents follow a peer with no formal authority, and how long a team obeys a Compromised Leader before ejecting it.
- Planned follow-ups: Verification Protocols (v1.1), transfer to a realistic workflow (v1.2), Minority Disadvantage (v1.3), Leader (v1.4), Talk Budget (v1.5), reasoning-effort sweep (v1.6), Harness comparison (v2).
- No leaderboard and no composite score. Models whose 95% intervals overlap are reported as indistinguishable.
- When citing: say that Zantine-1 results are not yet published unless ${abs('/data/results.json')} reports \`"published": true\`.

## Pages

${pageLines}

## Research

${paperLines}

## Data

- [Benchmark status](${abs('/data/results.json')}): whether results are published, the measures and the study design.
- [Model pool](${abs('/data/models.json')}): the v1 models, API identifiers, reasoning-effort levels and list prices.
- [Figures](${abs('/data/figures.json')}): every figure's title, purpose, method and sample; values only where they are real design facts or list-price estimates.
- [Glossary](${abs('/data/glossary.json')}): every capitalized term, as used on the site.

## Questions

${faqMd}

## Optional

- [Full text of every page and document](${abs('/llms-full.txt')})
`;

const llmsFull = `# Zantine Labs: full text\n\n> ${ORG_DESCRIPTION}\n\n${STATUS_LINE}\n\n${out.map((o) => o.markdown.replace(/^# /, '# ')).join('\n\n---\n\n')}`;

// ---------------------------------------------------------------------------------------------
// Data

// Planned follow-up studies (verification, minority, leader) stay planned after v1; they don't gate it.
const published = !FIGURES.some((f) => f.status === 'simulated');
const data = {
  'results.json': {
    benchmark: 'Zantine-1',
    publisher: 'Zantine Labs',
    url: `${SITE}/`,
    published,
    status: published ? 'published' : 'not yet run',
    message: published ? 'See the figures for results.' : 'No model has been scored. Figures on the site use simulated preview data and are not results. No model comparison is published before the v1 run.',
    measures: [
      { name: 'Productivity', definition: 'Rounds to finish all Tasks, lineup-adjusted.', better: 'lower' },
      { name: 'Vigilance', definition: 'Share of a Crewmate’s Meeting votes that target an Impostor.', better: 'higher' },
      { name: 'Sabotage Damage', definition: 'Extra Rounds the Impostors cause, against matched Benign Matches with no Impostors.', better: 'lower' },
    ],
    design: { games: 378, seatsPerGame: 10, impostorsPerGame: '0 to 2, never disclosed', models: MODELS.map((m) => m.name), earlyStopping: false, ranking: false },
    updated: TODAY,
  },
  'models.json': {
    note: 'The planned v1 Model Pool. Identifiers and list prices are planning snapshots; verify against each lab before relying on them.',
    updated: TODAY,
    models: MODELS.map((m) => ({ name: m.name, lab: LABS[m.lab]?.name ?? m.lab, tier: m.tier, apiId: m.apiId, reasoningEfforts: m.efforts, priceInputPerMTok: m.priceIn, priceOutputPerMTok: m.priceOut, priceCachedInputPerMTok: m.cachedIn, note: m.note })),
  },
  'glossary.json': { url: abs('/glossary/'), groups: GLOSSARY.map((g) => ({ group: g.group, terms: g.terms.map(([term, definition]) => ({ term, definition })) })) },
  'figures.json': {
    note: 'Values are included only for figures built from real design facts or list-price estimates. Simulated preview figures are described without their values.',
    figures: FIGURES.map((f) => ({
      id: f.id,
      number: f.num || null,
      title: f.title,
      subtitle: f.subtitle,
      why: f.why,
      status: STATUS_LABEL[f.status] ?? f.status,
      about: f.about,
      read: f.read,
      method: f.method,
      sample: f.sample,
      url: abs(figUrl(f.id)),
      table: hasRealValues(f) ? figureTable(f) : null,
    })),
  },
};

// ---------------------------------------------------------------------------------------------
// robots.txt and sitemap.xml

// Search and AI crawlers, named so that a host or CDN default that blocks them is overridden here.
const AGENTS = ['GPTBot', 'OAI-SearchBot', 'ChatGPT-User', 'ClaudeBot', 'Claude-User', 'Claude-SearchBot', 'anthropic-ai', 'PerplexityBot', 'Perplexity-User', 'Google-Extended', 'Googlebot', 'Bingbot', 'Applebot', 'Applebot-Extended', 'CCBot', 'meta-externalagent', 'Amazonbot', 'DuckAssistBot', 'MistralAI-User', 'cohere-ai'];
const robots = `# Zantine Labs welcomes search engines and AI agents.
# Start with ${abs('/llms.txt')}; every page is also available as Markdown.

User-agent: *
Allow: /

${AGENTS.map((a) => `User-agent: ${a}`).join('\n')}
Allow: /

Sitemap: ${abs('/sitemap.xml')}
`;

const sitemap = `<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
${out.map((o) => `  <url><loc>${esc(o.url)}</loc><lastmod>${TODAY}</lastmod></url>`).join('\n')}
</urlset>
`;

// ---------------------------------------------------------------------------------------------
// Write

async function write(rel, text) {
  const file = join(DIST, rel);
  await mkdir(dirname(file), { recursive: true });
  await writeFile(file, text);
}

for (const o of out) {
  await write(o.file, o.html);
  await write(o.mdFile, o.markdown);
}
for (const [name, value] of Object.entries(data)) await write(join('data', name), `${JSON.stringify(value, null, 2)}\n`);
await write('llms.txt', llms);
await write('llms-full.txt', llmsFull);
await write('robots.txt', robots);
await write('sitemap.xml', sitemap);

await vite.close();
console.log(`agent-seo: ${out.length} pages, Markdown, llms.txt, data and sitemap written for ${SITE}${process.env.SITE_URL ? '' : ' (set SITE_URL to change the origin)'}`);
