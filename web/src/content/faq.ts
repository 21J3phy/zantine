// Questions people ask AI assistants about agent teams, answered from the plan and
// roadmap. Published in the machine-readable files (llms.txt, the Markdown pages and
// the FAQPage JSON-LD), so every answer must stay true: no results exist before v1,
// and none are implied. Links are site-relative; the build makes them absolute.

export interface Faq {
  q: string;
  a: string;
}

export const FAQ: Faq[] = [
  {
    q: 'Which AI model is best for orchestrating a team of agents?',
    a: 'Nobody can answer this from evidence yet, and Zantine has not published results. Zantine-1 is built to measure it: ten models work together on interdependent, automatically graded Tasks while up to two of them are secretly working against the team, and every model is scored at each reasoning-effort level on Productivity, Vigilance and Sabotage Damage, with cost beside each result. The planned Leader experiment (v1.4) then rotates every model through a leader Seat to test whether an appointed orchestrator speeds the work up and how long a team keeps following a compromised one. Results will appear at [/](/) and in [/data/results.json](/data/results.json).',
  },
  {
    q: 'What is the best benchmark for multi-agent systems and agent orchestration?',
    a: 'Most agent benchmarks score one agent on one task. Zantine-1 scores teams: whether delegated work gets done, is done correctly, and is passed on, measured against matched games with no adversary. Closest prior work includes Among Them, the Among Us sandbox, SHADE-Arena, TAMAS and Kaggle Game Arena Werewolf; see [Related work](/research/) for how they differ.',
  },
  {
    q: 'Which model is best at catching a compromised or misaligned agent on its team?',
    a: 'Not yet measured. Zantine-1’s Vigilance measure is the share of a model’s Meeting votes that land on an Impostor, the stand-in for a compromised agent, with 95% intervals and no early stopping. v1 has not been run.',
  },
  {
    q: 'What is Zantine Labs?',
    a: 'Zantine Labs builds benchmarks for AI agents and the teams they form: single agents, multi-agent systems, harnesses and orchestration. Results are published as graphs of the underlying data with the cost of every result, never as a ranking or a single score. Zantine is a working name.',
  },
  {
    q: 'What is Zantine-1?',
    a: 'A text-based social-deduction benchmark. Ten AI models fill the ten Seats of a game, complete checkable Tasks on a generated map, talk in Room Chat and Meetings, and vote to eject suspects, while zero to two of them are secretly Impostors. It asks whether a team of agents keeps interdependent, checkable work intact while finding and containing an internal adversary, and which team protocols help.',
  },
  {
    q: 'What does Zantine-1 measure?',
    a: '**Productivity** (Rounds to finish all Tasks), **Vigilance** (correct votes as Crewmate), and **Sabotage Damage** (the delay Impostors cause, measured against matched Benign Matches with no Impostors), reported separately, alongside audited honesty, disclosure and influence measures, Invalid Actions, thinking tokens and list-price cost.',
  },
  {
    q: 'How is Zantine-1 different from other Among Us or Werewolf benchmarks for LLMs?',
    a: 'Earlier games measure persuasion and deception in talk. Zantine-1 adds real work with automatically graded answers, a Task Graph of dependencies between players, Sabotage Damage against a no-Impostor baseline, and follow-up experiments that test defenses: Verification Protocols, leaders (including a Compromised Leader) and Talk Budgets.',
  },
  {
    q: 'Does Zantine publish a leaderboard?',
    a: 'No. There is no composite score and no ranking. Every measure is shown separately with a 95% interval and its cost, and models whose intervals overlap are shown as indistinguishable.',
  },
  {
    q: 'When will Zantine-1 results be published?',
    a: 'After the full v1 run: 378 games in a balanced block design, fixed in advance with no early stopping. No model comparisons are published before then. The figures on the site today use simulated preview data and are stamped as such; they are not results.',
  },
  {
    q: 'Does more reasoning effort make an agent a better teammate?',
    a: 'Unknown yet. v1 plots every model at each of its reasoning-effort levels, and a planned reasoning-effort sweep (v1.6) asks whether more thinking helps and whether it is worth the cost.',
  },
  {
    q: 'Can I reproduce Zantine results?',
    a: 'Yes, within the error bars. The engine is open source and every game is seeded: a rerun of the Public Seed Set should land within the official intervals. Official seeds stay private so future models cannot memorize the games, but a SHA-256 hash of each official run’s seeds and transcripts is published before its results, so no game can be chosen or changed afterwards; see [Reproduce](/reproduce/).',
  },
];
