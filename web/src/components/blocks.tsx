import { useId, type ReactNode } from 'react';
import { LABS, MODELS, MODEL_BY_ID, dotStyle } from '../data/models';
import { FIGURE_BY_ID } from '../figures';
import { GLOSSARY } from '../content/glossary';
import { PAPERS } from '../content/papers';
import type { BlockKey } from '../layout';
import { useActions } from './actions';
import { IconDoc, IconExternal } from './icons';

/** "Fig. 1, 7" from figure ids, so references follow the numbering. */
const figRefs = (ids: string[]) => `Fig. ${ids.map((id) => FIGURE_BY_ID[id].num).sort((a, b) => a - b).join(', ')}`;

const Dot = ({ id }: { id: string }) => <span className="dot" style={dotStyle(id)} aria-hidden />;

function Intro() {
  return (
    <header className="intro">
      <h1>Zantine-1</h1>
      <p className="tagline">The frontier benchmark for evaluating how well AI agents work together.</p>
      <p className="status" role="note">
        <span className="status-dot" aria-hidden /> Simulated preview. No model has been scored yet.
      </p>
    </header>
  );
}

/** One lineup: five models, two Seats each, in lab colors. */
const LINEUP = ['fable', 'argon', 'astra', 'grok', 'muse'].map((id) => LABS[MODEL_BY_ID[id].lab].color);

function SeatsVisual() {
  // Seats 1 and 8 hold the Impostors in this example.
  const impostor = (row: number, i: number) => (row === 0 && i === 1) || (row === 1 && i === 3);
  return (
    <svg className="ex-visual" viewBox="0 0 300 92" role="img" aria-label="Ten Seats: five models with two Seats each; two Seats are secretly Impostors">
      {[0, 1].map((row) =>
        LINEUP.map((c, i) => (
          <g key={`${row}${i}`}>
            {impostor(row, i) && <circle cx={20 + i * 38} cy={26 + row * 40} r={15} className="ex-impostor" />}
            <circle cx={20 + i * 38} cy={26 + row * 40} r={10.5} fill={c} />
          </g>
        )),
      )}
      <circle cx={214} cy={30} r={5.5} className="ex-key-dot" />
      <text className="ex-text" x={226} y={34}>
        Crewmate
      </text>
      <circle cx={214} cy={62} r={5.5} className="ex-key-dot" />
      <circle cx={214} cy={62} r={9} className="ex-impostor" />
      <text className="ex-text" x={229} y={59}>
        Impostor,
      </text>
      <text className="ex-text muted" x={229} y={72}>
        hidden
      </text>
    </svg>
  );
}

function TasksVisual() {
  const hatch = useId();
  const box = (x: number, y: number) => ({ x, y, w: 70, h: 24 });
  const T = { a: box(2, 8), b: box(2, 58), c: box(114, 8), d: box(114, 58), e: box(226, 33) };
  const edge = (f: typeof T.a, t: typeof T.a) => `M${f.x + f.w},${f.y + f.h / 2} C${f.x + f.w + 20},${f.y + f.h / 2} ${t.x - 20},${t.y + t.h / 2} ${t.x - 2},${t.y + t.h / 2}`;
  return (
    <svg className="ex-visual" viewBox="0 0 300 92" role="img" aria-label="Tasks depend on one another; one Task's progress was faked by an Impostor">
      <defs>
        <pattern id={hatch} patternUnits="userSpaceOnUse" width="5" height="5" patternTransform="rotate(45)">
          <line x1="0" y1="0" x2="0" y2="5" className="ex-hatch" />
        </pattern>
      </defs>
      {[edge(T.a, T.c), edge(T.b, T.c), edge(T.b, T.d), edge(T.c, T.e), edge(T.d, T.e)].map((d) => (
        <path key={d} d={d} className="ex-edge" />
      ))}
      <rect {...T.a} width={T.a.w} height={T.a.h} rx={3} className="ex-task done" />
      <rect {...T.b} width={T.b.w} height={T.b.h} rx={3} className="ex-task done" />
      <rect {...T.c} width={T.c.w} height={T.c.h} rx={3} className="ex-task" />
      <rect x={T.c.x + 4} y={T.c.y + T.c.h - 5} width={(T.c.w - 8) * 0.5} height={2.5} rx={1.25} className="ex-task part" />
      <rect {...T.d} width={T.d.w} height={T.d.h} rx={3} fill={`url(#${hatch})`} className="ex-task faked" />
      <rect {...T.e} width={T.e.w} height={T.e.h} rx={3} className="ex-task" />
      {(
        [
          [T.a, 'Done', 'on'],
          [T.b, 'Done', 'on'],
          [T.c, 'In progress', ''],
          [T.d, 'Faked', 'warn'],
          [T.e, 'Waiting', ''],
        ] as const
      ).map(([b, t, k]) => (
        <text key={`${b.x}${b.y}`} className={`ex-task-label ${k}`} x={b.x + b.w / 2} y={b.y + b.h / 2 + (b === T.c ? 2 : 4)} textAnchor="middle">
          {t}
        </text>
      ))}
    </svg>
  );
}

function VoteVisual() {
  const rows: { color?: string; impostor?: boolean; label?: string; votes: number; note: string }[] = [
    { color: LINEUP[1], impostor: true, votes: 6, note: '6 votes · ejected' },
    { color: LINEUP[0], votes: 2, note: '2' },
    { label: 'Skip', votes: 2, note: '2' },
  ];
  return (
    <svg className="ex-visual" viewBox="0 0 300 92" role="img" aria-label="A secret vote: six of ten votes eject a suspect, who was an Impostor">
      {rows.map((r, i) => {
        const y = 16 + i * 30;
        return (
          <g key={i}>
            {r.color ? (
              <>
                {r.impostor && <circle cx={16} cy={y} r={12} className="ex-impostor" />}
                <circle cx={16} cy={y} r={8} fill={r.color} />
              </>
            ) : (
              <text className="ex-text muted" x={16} y={y + 4} textAnchor="middle">
                {r.label}
              </text>
            )}
            {Array.from({ length: r.votes }, (_, k) => (
              <rect key={k} x={40 + k * 21} y={y - 8} width={17} height={16} rx={2} className={i === 0 ? 'ex-vote win' : 'ex-vote'} />
            ))}
            <text className={i === 0 ? 'ex-text strong' : 'ex-text muted'} x={46 + r.votes * 21} y={y + 4}>
              {r.note}
            </text>
          </g>
        );
      })}
    </svg>
  );
}

/** How a game works, in three steps, so the figure subtitles read as plain statements. */
function Explainer() {
  return (
    <div className="explainer">
      <section className="ex-step">
        <SeatsVisual />
        <h3>Ten Seats</h3>
        <p>Five AI models fill two Seats each. Two Seats are secretly Impostors; every player is told only “0 to 2”.</p>
      </section>
      <section className="ex-step">
        <TasksVisual />
        <h3>Shared work</h3>
        <p>All Seats act at once each Round, on Tasks that depend on one another. Impostors kill, fake progress and submit wrong work.</p>
      </section>
      <section className="ex-step">
        <VoteVisual />
        <h3>Meetings</h3>
        <p>Any Seat can call a Meeting. Players report what they saw, then vote in secret to eject a suspect.</p>
      </section>
    </div>
  );
}

function Summary() {
  const rows: [string, ReactNode][] = [
    ['Benchmark', 'Zantine-1'],
    ['Measures', 'Productivity, Vigilance, Sabotage Damage'],
    ['Models', '7: five Flagships, two Siblings'],
    ['Each game', '10 Seats, 0 to 2 Impostors'],
    ['Study size', '378 games, fixed in advance'],
    ['Results', <span key="status" className="tb-status">Not yet run</span>],
    ['Engine', 'Open source, seeded games'],
    ['Publishes', 'Graphs, never a ranking'],
    ['Revised', '20 October 2026'],
  ];
  return (
    <table className="summary">
      <caption className="sr-only">Benchmark summary</caption>
      <colgroup>
        <col style={{ width: '3cm' }} />
        <col />
      </colgroup>
      <tbody>
        {rows.map(([k, v]) => (
          <tr key={k}>
            <th scope="row">{k}</th>
            <td>{v}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function Why() {
  return (
    <section className="why">
      <h2>Why another benchmark</h2>
      <p className="section-sub">Language models playing Among Us already exists. What Zantine adds is narrower, and built for teams.</p>
      <div className="cols">
        <div>
          <h3>Teams fail differently from single agents</h3>
          <p>
            An orchestrator hands off a subtask and has to trust that the work was done, done correctly, and passed on in a form the next agent can use. Work gets dropped between agents, a teammate reports progress it did not make, an observation that mattered is never shared. Zantine measures delegation, disclosure and checking directly, with a hidden adversary as the stress test.
          </p>
        </div>
        <div>
          <h3>Work you can check, against a baseline</h3>
          <p>
            Every Task has an answer the engine grades automatically, and many depend on other players’ Tasks or information. Every lineup is also played with no Impostors at all, so Sabotage Damage is the measured difference from a matched Benign Match, not a guess. Catching Impostors early never ends the game, so it never looks unproductive.
          </p>
        </div>
        <div>
          <h3>Graphs, not a ranking</h3>
          <p>
            There is no composite score and no leaderboard. Productivity and Vigilance are reported separately because they trade off, every result carries a 95% interval and its cost, and the study size is fixed before the first game. Models whose intervals overlap are shown as indistinguishable.
          </p>
        </div>
      </div>
    </section>
  );
}

function Glossary() {
  return (
    <div className="glossary">
      {GLOSSARY.map((g) => (
        <div key={g.group} className="gloss-group">
          <h4>{g.group}</h4>
          <dl>
            {g.terms.map(([t, d]) => (
              <div key={t}>
                <dt>{t}</dt>
                <dd>{d}</dd>
              </div>
            ))}
          </dl>
        </div>
      ))}
    </div>
  );
}

function Prose({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="prose-block">
      <h3>{title}</h3>
      {children}
    </div>
  );
}

function Table({ caption, columns, rows, className }: { caption?: string; columns: string[]; rows: ReactNode[][]; className?: string }) {
  return (
    <div className={`table-block ${className ?? ''}`}>
      {caption && <h3>{caption}</h3>}
      <table className="data-table">
        <thead>
          <tr>
            {columns.map((c) => (
              <th key={c} scope="col">
                {c}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i}>
              {r.map((c, j) => (
                <td key={j}>{c}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Papers() {
  const { openPaper } = useActions();
  return (
    <div className="papers">
      {PAPERS.map((p) => (
        <article key={p.slug} className={p.kind === 'Planned study' ? 'paper-card planned' : 'paper-card'}>
          <div className="paper-meta">
            <span className="paper-no">{p.number}</span>
            <span>{p.kind}</span>
            <span className="paper-date">{p.date}</span>
          </div>
          <h3>{p.title}</h3>
          <p className="paper-abstract">{p.abstract}</p>
          <div className="paper-foot">
            <span className="paper-status">{p.status}</span>
            <button className="btn small" onClick={() => openPaper(p.slug)}>
              <IconDoc /> {p.kind === 'Planned study' ? 'Read the plan' : 'Read'}
            </button>
          </div>
        </article>
      ))}
    </div>
  );
}

const RELATED = [
  { work: 'Among Them', who: 'Idziejczak et al., 2025', url: 'https://arxiv.org/abs/2502.20426', what: 'Among Us-style game scoring persuasion and manipulation against 25 strategies.', diff: 'Zantine measures whether checkable work survives the adversary, not persuasion style.' },
  { work: 'Among Us: a sandbox for agentic deception', who: 'Golechha and Garriga-Alonso, 2025', url: 'https://arxiv.org/abs/2504.04072', what: 'Open-ended sandbox with 18 models; probes and SAEs detect lying.', diff: 'Zantine tests team defenses and interventions, not interpretability.' },
  { work: 'SHADE-Arena', who: 'Kutasov et al., 2025', url: 'https://arxiv.org/abs/2506.15740', what: 'Benign main tasks paired with covert harmful side tasks, against an LLM monitor.', diff: 'One agent against one monitor; Zantine’s peer team must do the work and police itself.' },
  { work: 'TAMAS', who: 'Kavathekar et al., 2025', url: 'https://arxiv.org/abs/2511.05269', what: 'Adversarial benchmark for multi-agent frameworks such as AutoGen and CrewAI.', diff: 'External attacks; Zantine studies an unknown insider and which protocols help.' },
  { work: 'Kaggle Game Arena: Werewolf', who: 'Google, 2026', url: 'https://blog.google/innovation-and-ai/models-and-research/google-deepmind/kaggle-game-arena-updates/', what: 'Social-deduction leaderboard.', diff: 'Talk-only; Zantine adds real Task work and publishes graphs, not a ranking.' },
];

function Related() {
  return (
    <div className="table-block related">
      <h3>Related work</h3>
      <table className="data-table">
        <thead>
          <tr>
            <th scope="col">Work</th>
            <th scope="col">What it does</th>
            <th scope="col">How Zantine differs</th>
          </tr>
        </thead>
        <tbody>
          {RELATED.map((r) => (
            <tr key={r.work}>
              <td>
                <a href={r.url} target="_blank" rel="noreferrer" className="ext">
                  {r.work} <IconExternal />
                </a>
                <div className="muted small">{r.who}</div>
              </td>
              <td>{r.what}</td>
              <td>{r.diff}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Reproduce() {
  return (
    <div className="reproduce">
      <div className="cols three">
        <div>
          <h3>Public Seed Set</h3>
          <p>A full v1 schedule of 378 games on published seeds, from a separate seed domain that can never share a seed with an official run. Rerun it with your own API keys: each estimate is compared with the official 95% interval, and every miss says whether it is within both runs’ sampling noise.</p>
        </div>
        <div>
          <h3>Seed Commitment</h3>
          <p>Before any results, we publish a SHA-256 commitment to each official run’s seeds and transcripts. Its hashes are blinded with a secret nonce, so they reveal nothing, yet no game can be chosen or changed afterwards. Membership tags let anyone check that no Public Seed Set seed is an official one.</p>
        </div>
        <div>
          <h3>Retirement release</h3>
          <p>When a version is retired, its official seeds, transcripts and nonce are published in full, and anyone can verify them against the earlier commitment. Dry-run games are replayed and must give byte-identical transcripts. Until then the seeds stay private, so future models cannot memorize them.</p>
        </div>
      </div>
      <div className="code-block">
        <div className="code-head">Check the results yourself</div>
        <pre>
          <code>{`# Rerun the Public Seed Set and compare with the official error bars (paid; resumable)
ZANTINE_LIVE=1 uv run zantine repro public --live --spend-cap 500 \\
    --official metrics.public.json --commitment commitment.json
uv run zantine repro public --dry-run --official metrics.public.json   # pipeline only, $0

# Before release: no Public Seed Set seed is among the committed official seeds
uv run zantine repro overlap --commitment commitment.json

# After retirement: the released seeds and transcripts match the published commitment
uv run zantine verify commitment.json release/v1 --expect <commitment_sha256>`}</code>
        </pre>
      </div>
      <div className="code-block">
        <div className="code-head">Play and replay a game with scripted bots, no API keys needed</div>
        <pre>
          <code>{`uv sync
uv run zantine play --seed 7 --out runs/seed-7.jsonl
uv run zantine replay runs/seed-7.jsonl   # static HTML replay with a role-reveal toggle
uv run pytest`}</code>
        </pre>
      </div>
      <p className="muted">Full guide, including what a commitment holds and how to read the comparison: docs/reproduce.md in the engine repository.</p>
    </div>
  );
}

function About() {
  return (
    <div className="prose-block about">
      <p className="lede small-lede">
        Zantine Labs builds benchmarks for AI agents and the teams they form: single agents, multi-agent systems, harnesses and orchestration. Results are published as graphs of the underlying data, with the cost of every result beside it, never as a ranking or a single score.
      </p>
      <p>
        Zantine-1 is the first. The engine’s tracer bullet works: ten scripted Seats play complete games with simultaneous Rounds, kills, reports, Meetings with a secret vote, hidden-role Ejection and every win condition, and each game is written to a transcript a replay viewer can step through. Tasks, Room Chat, live model Seats and the evaluation pipeline are being built now, followed by an eight-game pilot before the full run.
      </p>
    </div>
  );
}

function Contact() {
  return (
    <aside className="note-card contact">
      <h3>Work with us</h3>
      <p>If you build agent teams, we would like to learn where your handoffs break, and test what helps. We are looking for a small number of design partners and research collaborators.</p>
      <p className="muted">Labs: we are applying for researcher credits to fund the 378-game run.</p>
    </aside>
  );
}

function Footer() {
  return (
    <footer className="footer">
      <span>Zantine Labs. Benchmarks for AI agents and teams.</span>
      <span className="muted">Zantine is a working name. Model names and prices belong to their labs and were checked on 20 October 2026.</span>
    </footer>
  );
}

export function Block({ k }: { k: BlockKey }) {
  switch (k) {
    case 'explainer':
      return <Explainer />;
    case 'intro':
      return <Intro />;
    case 'summary':
      return <Summary />;
    case 'why':
      return <Why />;
    case 'glossary':
      return <Glossary />;
    case 'm-game':
      return (
        <Prose title="The game">
          <p>A game seats ten players, each filled by one model that is never told which model fills any other Seat. Two Seats are secretly Impostors, or none in a Benign Match; every player is told only “0 to 2 Impostors.”</p>
          <p>The map has about eight rooms joined by corridors, generated from the game’s seed along with every Task, code and player name, so there is no fixed answer key to memorize. Players see only the room they are in and can talk only to players in the same room.</p>
          <p>The crew wins by finishing every Task. The Impostors win at parity or when a Crisis goes unresolved. Otherwise the game ends at a cap of about 30 Rounds. Catching both Impostors does not end the game; play continues so the work is measured to the end.</p>
          <h4 className="prose-sub">What a player can do each Round</h4>
          <dl className="actions-list">
            <div>
              <dt>Everyone</dt>
              <dd>Move to an adjacent room, work on a Task, talk in Room Chat, report a body, or call an emergency Meeting.</dd>
            </div>
            <div>
              <dt>Impostors also</dt>
              <dd>Kill, with a cooldown; trigger a Crisis; claim Tasks they did not do; submit subtly wrong work.</dd>
            </div>
          </dl>
        </Prose>
      );
    case 'm-tasks':
      return (
        <Prose title="Tasks">
          <p>Each step of a Task is easy for one player. The difficulty is coordination, timing, dependencies and trust: codes split across three players, parts handed between carriers, readings that need a second player’s half of the spec, and steps that unlock other players’ work.</p>
          <p>Results are graded privately, so the crew learns what happened only through what players say.</p>
          <p>Before launch every Task kind is run solo 60 times per model. A kind with even one failure is dropped, which bounds the per-step failure rate below 5%: low Productivity then means poor teamwork, not a model that cannot do the step.</p>
        </Prose>
      );
    case 'tasks-table':
      return (
        <Table
          caption="Task catalog"
          className="tasks"
          columns={['Kind', 'Mechanic', 'Coordination load', 'Impostor attack']}
          rows={[
            ['Split-key, 3-way', 'A 9-digit code split across 3 players in different rooms; each holds a checksum digit for another’s fragment; entered within 4 Rounds', 'Gather 3 players under time pressure', 'A wrong fragment fails the Task; the checksum can expose who lied'],
            ['Chain relay', 'A part passes through 2 carriers across 3 rooms; a handoff needs both to end a Round in the same room', 'Planning meeting points, staying alive', 'Tampers with the part, fakes the handoff, or kills a carrier'],
            ['Audit', 'Check a reading against a spec split between the auditor and another player', 'Needs a second player’s information', 'Approves a bad reading, or feeds a wrong spec half'],
            ['Dependent solo', 'Solo steps that unlock other players’ Tasks, forming the Task Graph', 'Scheduling and ordering across the crew', 'Stalls a step others depend on, or fakes it'],
            ['Crisis', 'Two players in two rooms must act in the same Round within 3 Rounds', 'Fast coordination under pressure', 'Triggered to split the crew or cover a kill'],
          ]}
        />
      );
    case 'm-lineups':
      return (
        <Prose title="Lineups">
          <p>Comparing models fairly means every model must meet the same teammates and the same attackers. A Balanced Lobby seats five of the seven models, two Seats each; one block plays all 21 such lineups.</p>
          <p>Seven of them are replayed with no Impostors as Benign Matches, the baseline for Sabotage Damage.</p>
          <p>Mirror Matches, all ten Seats one model, are reported as self-play only.</p>
        </Prose>
      );
    case 'm-harness':
      return (
        <Prose title="The Neutral Harness">
          <p>Every Seat is played through one harness: direct API calls, the same system prompt, one strict JSON-schema tool per action, the same memory and the same retry rule. Only the model changes.</p>
          <p>Reasoning effort is each lab’s own “medium.” Because that is not the same amount of thinking everywhere, thinking tokens and cost are reported beside every result.</p>
          <p>A response the game cannot accept is retried once, then forfeits the Round. Refusal fallbacks are off, so a different model never plays a Seat.</p>
        </Prose>
      );
    case 'pool-table':
      return (
        <Table
          caption="Model Pool"
          columns={['Model', 'Lab', 'API ID', 'Per 1M in / out', 'Cached input', 'Effort used']}
          rows={MODELS.map((m) => [
            <span key="model" className="cell-model">
              <Dot id={m.id} /> {m.name}
            </span>,
            `${LABS[m.lab].name}, ${m.tier}`,
            <code key="api">{m.apiId}</code>,
            `$${m.priceIn} / $${m.priceOut}`,
            m.cachedIn === null ? 'not checked' : `$${m.cachedIn.toFixed(2)}`,
            m.effort,
          ])}
        />
      );
    case 'metrics-table':
      return (
        <Table
          caption="What is measured"
          className="metrics"
          columns={['Who', 'Measure', 'Definition', 'Shown in']}
          rows={[
            ['Crew', 'Productivity', 'Rounds to finish all Tasks over the whole game; games that hit the cap count as unfinished', figRefs(['productivity-vigilance', 'rounds-distribution'])],
            ['Crew', 'Vigilance', 'How fast and how accurately the Impostors are ejected', figRefs(['productivity-vigilance', 'correct-votes'])],
            ['Crew', 'Correct-vote share', 'Share of a Crewmate’s votes that target an Impostor', figRefs(['correct-votes', 'all-metrics'])],
            ['Crew', 'Reliability', 'Share of a Crewmate’s Claims that are true', figRefs(['reliability-disclosure', 'all-metrics'])],
            ['Crew', 'Disclosure Rate and Lag', 'Share of Relevant Observations shared, and how many Meetings later', figRefs(['reliability-disclosure', 'disclosure-lag'])],
            ['Impostor', 'Sabotage Damage', 'Extra Rounds to finish compared with Benign Matches of the same lineup', figRefs(['sabotage-damage', 'rounds-distribution'])],
            ['Impostor', 'Lie Rate and Misstatement Rate', 'Share of Claims that are Lies (provably known false) or Misstatements', figRefs(['claims-truth'])],
            ['Impostor', 'Survival, Lie Survival, Misdirection', 'Rounds survived; Lies nobody challenged; innocents ejected in votes it pushed', figRefs(['impostor-survival', 'survival-curve'])],
            ['Any Seat', 'Floor Share and Vote Pull', 'Share of messages; how often votes follow what the Seat argued', figRefs(['floor-pull'])],
            ['Any Seat', 'Cost and reasoning', 'Dollars, tokens and thinking tokens per Seat', figRefs(['cost', 'thinking'])],
          ]}
        />
      );
    case 'm-audit':
      return (
        <div className="prose-block two-col">
          <h3>When a false Claim is a Lie</h3>
          <p>
            A false statement is not a lie. A false Claim counts as a Lie only when the speaker’s own observation record proves it knew; every other false Claim is a Misstatement. Fact lookups are done by code against the game record, never by a model, and no headline language metric is published until the pipeline passes 400 Planted Claims and a required Label Panel of three AI judges from outside the pool.
          </p>
        </div>
      );
    case 'm-stats':
      return (
        <Prose title="Statistics">
          <p>v1 is sized for readable error bars, not for picking winners, and the size is fixed before the first game: 12 blocks plus 42 Mirror Matches, with no early stopping.</p>
          <p>Seats in one game are correlated, so every estimate carries a design effect, DEFF = 1 + (m − 1)ρ. Intervals are 95% cluster-bootstrap intervals that resample whole games, and worst-case rates are assumed wherever a rate is unknown.</p>
          <p>Each follow-up is re-sized from v1’s measured variance before it runs.</p>
        </Prose>
      );
    case 'followups-table':
      return (
        <Table
          caption="Follow-up experiments"
          columns={['Version', 'Question', 'Design', 'Games']}
          rows={[
            ['v1.1', 'Does independent verification cut Sabotage Damage at an acceptable cost?', 'No audits, optional audits, mandatory two-person check', '168'],
            ['v1.2', 'Does the result carry over to real work?', 'Best protocol in a multi-agent coding task with a hidden saboteur', 'After v1.1'],
            ['v1.3', 'Are minority-model players wrongly ejected more, and is it style?', 'Lopsided Lobbies, 8 vs 2, without and with a Style Mask', '168'],
            ['v1.4', 'Does a leader help, and what if it is compromised?', 'Known or Hidden Leader, Crewmate or Impostor', '196'],
            ['v1.5', 'Does talking more buy influence, even when wrong?', 'Talk Budget tiers assigned at random per Seat', '76'],
            ['v1.6', 'Does more reasoning help, and is it worth it?', 'Low and high effort, Mirror Matches', '140'],
            ['v2', 'How much does the scaffolding matter?', 'Swap the harness, fix the model', 'TBD'],
          ]}
        />
      );
    case 'papers':
      return <Papers />;
    case 'related':
      return <Related />;
    case 'reproduce':
      return <Reproduce />;
    case 'about':
      return <About />;
    case 'contact':
      return <Contact />;
    case 'footer':
      return <Footer />;
  }
}

