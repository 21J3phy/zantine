// Research documents. Everything here restates decisions already recorded in
// the project docs (plan, roadmap, sample-size, related-work, ADR 0001). No
// results are reported: v1 has not been run.

export type DocBlock =
  | { t: 'h2'; text: string }
  | { t: 'h3'; text: string }
  | { t: 'p'; text: string }
  | { t: 'list'; items: string[]; ordered?: boolean }
  | { t: 'table'; columns: string[]; rows: string[][]; caption?: string }
  | { t: 'eq'; text: string; label: string }
  | { t: 'fig'; id: string; caption: string }
  | { t: 'note'; text: string }
  | { t: 'code'; text: string };

export interface Reference {
  text: string;
  url?: string;
}

export interface Paper {
  slug: string;
  number: string;
  kind: 'Protocol' | 'Technical note' | 'Planned study';
  title: string;
  date: string;
  version: string;
  status: string;
  abstract: string;
  keywords: string[];
  body: DocBlock[];
  references: Reference[];
}

const REL: Record<string, Reference> = {
  amongThem: { text: 'Idziejczak et al. Among Them: A game-based framework for assessing persuasion capabilities of LLMs. arXiv:2502.20426, 2025.', url: 'https://arxiv.org/abs/2502.20426' },
  sandbox: { text: 'Golechha and Garriga-Alonso. Among Us: A sandbox for measuring and detecting agentic deception. arXiv:2504.04072, 2025.', url: 'https://arxiv.org/abs/2504.04072' },
  shade: { text: 'Kutasov et al. SHADE-Arena: Evaluating sabotage and monitoring in LLM agents. arXiv:2506.15740, 2025.', url: 'https://arxiv.org/abs/2506.15740' },
  tamas: { text: 'Kavathekar et al. TAMAS: Benchmarking adversarial risks in multi-agent LLM systems. arXiv:2511.05269, 2025.', url: 'https://arxiv.org/abs/2511.05269' },
  werewolf: { text: 'Google. Kaggle Game Arena updates: Werewolf. 2026.', url: 'https://blog.google/innovation-and-ai/models-and-research/google-deepmind/kaggle-game-arena-updates/' },
  howard: { text: 'Howard, Ramdas, McAuliffe and Sekhon. Time-uniform, nonparametric, nonasymptotic confidence sequences. arXiv:2103.06476, 2021.', url: 'https://arxiv.org/abs/2103.06476' },
  jev: { text: 'Braintrust. What is Jev?', url: 'https://www.braintrust.dev/articles/what-is-jev' },
};

export const PAPERS: Paper[] = [
  {
    slug: 'zantine-1-protocol',
    number: 'ZL-TR-001',
    kind: 'Protocol',
    title: 'Zantine-1: Do agent teams keep their shared work intact when one member is working against them?',
    date: 'October 2026',
    version: 'Draft 1.0',
    status: 'Design fixed before any model is run',
    abstract:
      'AI systems are moving from single assistants to teams of agents that delegate, hand work to each other and depend on each other’s results. Existing social-deduction evaluations measure persuasion and deception in talk-only games; they do not measure whether a team’s actual work survives. Zantine-1 is a text-based social-deduction game in which ten AI models complete interdependent, automatically graded Tasks on a generated map while up to two of them are secretly Impostors, and no player is told how many. We report Productivity (Rounds to finish all of the work), Vigilance (how quickly and accurately the crew ejects the Impostors) and Sabotage Damage (the delay the Impostors cause, measured against matched games with no Impostors) as separate quantities, alongside audited measures of honesty, disclosure and influence, and the cost of every result. This document fixes the v1 design before any model is run: a seven-model pool, 378 games in a balanced block design with no early stopping, one neutral harness, and a validated Claim Audit. Results will be published as graphs of the underlying data, not as a ranking.',
    keywords: ['multi-agent systems', 'teamwork', 'social deduction', 'sabotage', 'evaluation design'],
    body: [
      { t: 'h2', text: '1. Introduction' },
      {
        t: 'p',
        text: 'An orchestrator that hands a subtask to another agent has to trust three things: that the work was done, that it was done correctly, and that what comes back is what the next agent needs. Most agent benchmarks score one agent on one task. The failure modes of a team are different. Work gets dropped between agents, a teammate reports progress it did not make, an observation that mattered is never passed on, and a confident voice steers the group wrong.',
      },
      {
        t: 'p',
        text: 'Zantine studies those failures in a setting where they can be measured exactly. We use a hidden-role game because it makes one hard case explicit: a member of the team who is working against it. The adversary is a stress test, not the subject. The question is whether the team’s shared work survives, and which team protocols help it survive.',
      },
      {
        t: 'p',
        text: '“Language models playing Among Us” already exists (Section 2). Zantine’s contribution is narrower, and we think new:',
      },
      {
        t: 'list',
        items: [
          '**Checkable, interdependent work.** Tasks have answers that can be graded automatically, and many depend on other players’ Tasks or information (the Task Graph and Shared Tasks).',
          '**Sabotage Damage against a matched baseline.** Every lineup is also played with no Impostors at all, so the cost of an adversary is measured, not inferred.',
          '**Defenses, not only diagnosis.** Follow-up experiments test Verification Protocols, leaders, and talk budgets as interventions.',
          '**Compromised leaders.** A designated leader can itself be the Impostor.',
        ],
      },
      { t: 'h2', text: '2. Related work' },
      {
        t: 'table',
        columns: ['Work', 'What it does', 'How Zantine differs'],
        rows: [
          ['Among Them [1]', 'Among Us-style game scoring LLM persuasion and manipulation against 25 strategies', 'Measures whether checkable work survives the adversary, not persuasion style'],
          ['Among Us sandbox [2]', 'Open-ended sandbox, 18 models; RL-trained models deceive better than they detect; probes detect lying', 'Focuses on team defenses and interventions, not interpretability'],
          ['SHADE-Arena [3]', 'Benign main tasks paired with covert harmful side tasks, against an LLM monitor', 'One agent against one monitor; Zantine studies a peer team that must work and police itself'],
          ['TAMAS [4]', 'Adversarial benchmark for multi-agent frameworks; safety against effectiveness', 'External attacks on frameworks; Zantine studies an unknown insider and which protocols help'],
          ['Kaggle Game Arena: Werewolf [5]', 'Social-deduction leaderboard', 'Talk-only; Zantine adds real Task work and publishes graphs, not a ranking'],
        ],
        caption: 'Table 1. Closest prior work, checked October 2026. This check suggests the combination is new; it does not prove it.',
      },
      { t: 'h2', text: '3. The game' },
      {
        t: 'p',
        text: 'A game seats ten players, each filled by one model. Two Seats are Impostors, or zero in a Benign Match. Every player is told only that there are “0 to 2 Impostors” (the Hidden Impostor Count), so Benign Matches look identical from the inside, and no player is told which model fills any other Seat (Hidden Identity).',
      },
      {
        t: 'p',
        text: 'Play proceeds in Rounds. In each Round every living player submits one action at the same time, seeing only its own room, and all actions resolve together. Actions are: move, work on a Task, talk in Room Chat (visible only to players in the same room), report a body, or call an emergency Meeting. Impostors can also kill (with a cooldown), trigger a Crisis, fake Tasks and sabotage work.',
      },
      { t: 'fig', id: 'round-sequence', caption: 'Figure 1. One Round. Simultaneous action removes any advantage from responding first.' },
      {
        t: 'p',
        text: 'A Meeting is three discussion passes in a reshuffled speaking order, then a secret simultaneous vote with skip allowed. The player with the most votes is ejected; a tie ejects nobody. An ejected player’s role is never revealed, so the crew never learns whether it was right.',
      },
      {
        t: 'p',
        text: 'The crew wins when all Tasks are done. The Impostors win at parity or when a Crisis goes unresolved. Otherwise the game ends at the Round cap of about 30. Containment, the moment both Impostors have been ejected, does not end the game: play continues until the work is finished or the cap is reached, so catching the Impostors early is never scored as unproductive.',
      },
      { t: 'h2', text: '4. Tasks' },
      {
        t: 'p',
        text: 'Each step of a Task is easy for one player. The difficulty is coordination, timing, dependencies and trust. Before launch, every Task kind is run solo 60 times per model, and any kind with even one failure is dropped. Zero failures in 60 trials bounds the per-step failure rate below 5% (the rule of three), so low Productivity reflects teamwork rather than a model’s inability to do the step.',
      },
      {
        t: 'table',
        columns: ['Kind', 'Mechanic', 'Coordination load', 'Impostor attack'],
        rows: [
          ['Split-key (3-way)', 'A 9-digit code split across 3 players in different rooms; each also holds a checksum digit for another’s fragment; entered within 4 Rounds of the first share', 'Gather 3 players under time pressure', 'A wrong fragment fails the Task; the checksum can expose the liar'],
          ['Chain relay', 'A part passes through 2 carriers across 3 rooms; a handoff happens only when both end a Round in the same room', 'Planning meeting points, staying alive', 'Tampers with the part, fakes the handoff, or kills a carrier'],
          ['Audit', 'Check a submitted reading against a spec split between the auditor and another player; nobody audits their own work', 'Needs a second player’s information', 'Approves a bad reading or feeds a wrong spec half'],
          ['Dependent solo', 'Solo steps that unlock other players’ Tasks, forming the Task Graph', 'Scheduling and ordering', 'Stalls a step others depend on, or fakes it'],
          ['Crisis', 'Two players in two rooms must act in the same Round within 3 Rounds', 'Fast coordination under pressure', 'Triggered to split the crew or cover a kill'],
        ],
        caption: 'Table 2. The v1 Task catalog.',
      },
      { t: 'h2', text: '5. Study design' },
      {
        t: 'p',
        text: 'The Model Pool has seven models: one Flagship per lab (the strongest model a developer can call through the lab’s public API at run time) plus two cheaper Siblings, Claude Opus 5.5 and GPT-6.1 Sol, to show whether the Flagship’s extra cost buys better play.',
      },
      {
        t: 'p',
        text: 'A block is 28 games. Twenty-one are Balanced Lobbies, one for every way to choose 5 of the 7 models, each holding two Seats; each model appears in 15 and every pair of models meets in 10. Impostor Seats are assigned so every model holds exactly 6 per block, and the 28 possible Impostor Pairings are spread evenly across every 4 blocks. The other 7 games are Benign Matches: the same lineups with no Impostors, rotating so all 21 lineups are covered every 3 blocks.',
      },
      { t: 'fig', id: 'block-design', caption: 'Figure 2. One block. Every model meets the same mix of teammates and attackers.' },
      {
        t: 'p',
        text: 'v1 is fixed at 12 blocks (252 Balanced Lobbies and 84 Benign Matches) plus 42 Mirror Matches, six per model, in which all ten Seats are the same model: 378 games. Mirror Matches describe self-play only. They are not used to compare defense, because the attackers and defenders are the same model.',
      },
      { t: 'h2', text: '6. The Neutral Harness' },
      {
        t: 'p',
        text: 'Every Seat is played through the same harness: direct API calls with an identical system prompt, one strict JSON-schema tool per action, the same memory and the same retry rules, so only the model differs. Memory is the rules, the Seat’s own observations and the recent public log; older log entries are condensed by a fixed engine-side summarizer. Reasoning effort is each lab’s own “medium” setting, and actual thinking tokens and cost are reported beside every result. A response the game cannot accept is retried once and then forfeits the Round as an Invalid Action, logged against the model. Refusal fallbacks are disabled, so a different model never plays a Seat.',
      },
      { t: 'h2', text: '7. Measures' },
      {
        t: 'table',
        columns: ['Who', 'Measure', 'Definition'],
        rows: [
          ['Crew', 'Productivity', 'Rounds to finish all Tasks; games that hit the cap count as unfinished'],
          ['Crew', 'Vigilance', 'How quickly and accurately the Impostors are ejected'],
          ['Crew', 'Correct-vote share', 'Share of a Crewmate’s votes that target an Impostor'],
          ['Crew', 'Reliability', 'Share of a Crewmate’s Claims that are true'],
          ['Crew', 'Disclosure Rate and Lag', 'Share of Relevant Observations shared, and how many Meetings later'],
          ['Impostor', 'Sabotage Damage', 'Extra Rounds to finish compared with Benign Matches of the same lineup'],
          ['Impostor', 'Lie Rate, Misstatement Rate', 'Share of Claims that are Lies (provably known false) or Misstatements'],
          ['Impostor', 'Lie Survival, Misdirection, Suspicion, Survival', 'Unchallenged Lies; innocents ejected in votes it pushed; votes against it; Rounds survived'],
          ['Any Seat', 'Floor Share, Vote Pull, cost', 'Share of messages; influence over votes; dollars and tokens'],
        ],
        caption: 'Table 3. v1 measures. Productivity and Vigilance are never combined.',
      },
      { t: 'h2', text: '8. The Claim Audit' },
      {
        t: 'p',
        text: 'A fixed-weight open-weight model from outside the pool turns Meeting speech into structured Claims. Plain code checks each Claim against the game record, and against the speaker’s own observations to separate Lies from Misstatements. Jev handles the fuzzy, typed judgments: whether a sentence is a Claim or a hedge, whether it was challenged, and which player a message is pushing to eject. The method and its validation are described in ZL-TN-002.',
      },
      { t: 'fig', id: 'claim-audit', caption: 'Figure 3. The Claim Audit pipeline and its validation.' },
      { t: 'h2', text: '9. Analysis plan' },
      {
        t: 'list',
        items: [
          'The study size is fixed in advance. Results are analyzed only after all 12 blocks; there is no early stopping.',
          'Intervals are 95% cluster-bootstrap intervals that resample whole games, because Seats in one game are correlated.',
          'Per-model defense is estimated from seat-level measures in Balanced Lobbies, where every model faces the same mix of attackers.',
          'Sabotage Damage compares each Balanced Lobby with Benign Matches of the same lineup and is attributed to the two Impostor models by a regression that controls for the lineup.',
          'There is no composite score and no ranking. Models whose intervals overlap are presented as indistinguishable.',
          'No comparison between models is published before v1 is complete.',
        ],
      },
      { t: 'p', text: 'Expected precision at 12 blocks is derived in ZL-TN-003: about ±0.13 on Impostor Survival, ±0.04 on correct-vote share and ±1.9 Rounds on Sabotage Damage, per model.' },
      { t: 'h2', text: '10. Reproducibility' },
      {
        t: 'p',
        text: 'The engine and rules are open source; maps, Tasks, codes and names are generated from each game’s seed. Official seeds stay private so future models cannot memorize the games. Instead we publish a Public Seed Set anyone can rerun, a Seed Commitment for every official run before its results, and the official seeds and transcripts when a version is retired (ZL-TN-004).',
      },
      { t: 'h2', text: '11. Limitations' },
      {
        t: 'list',
        items: [
          'A game is not a workplace. Whether a protocol that helps in the game also helps in real work is the question of the v1.2 transfer check.',
          'The Label Panel is made of AI judges, which can share the pipeline’s blind spots. Language results are reported as validated by an AI panel, not by humans.',
          '“Medium” effort is not the same amount of reasoning at every lab. We report thinking tokens rather than normalize them away.',
          'Hidden Identity can be partly defeated by writing style. The v1.3 Style Mask condition measures how much this matters.',
          'Model availability and prices change. Identifiers and prices are re-checked before every paid run.',
          'The variance assumptions behind the sample size are conservative guesses; the pilot checks cost and engine function, not study size.',
        ],
      },
    ],
    references: [REL.amongThem, REL.sandbox, REL.shade, REL.tamas, REL.werewolf, REL.howard, REL.jev],
  },
  {
    slug: 'claim-audit',
    number: 'ZL-TN-002',
    kind: 'Technical note',
    title: 'Auditing claims in agent speech: Lies, Misstatements and an AI Label Panel',
    date: 'October 2026',
    version: 'Draft 1.0',
    status: 'Method fixed; extractor chosen on Planted Claims',
    abstract:
      'A false statement is not the same as a lie. We describe how Zantine turns free-text Meeting speech into structured Claims, checks each one against the game’s ground truth, and labels a false Claim a Lie only when the speaker’s own observation record proves it knew the Claim was false. Fact lookups are done by code, not by models. The pipeline is validated before any headline language metric is published, with 400 engine-generated Planted Claims of known truth and a required Label Panel of three AI judges from model families outside the pool, whose agreement is reported as Krippendorff’s α.',
    keywords: ['deception', 'claim extraction', 'measurement validity', 'LLM judges'],
    body: [
      { t: 'h2', text: '1. Why “false” is not enough' },
      {
        t: 'p',
        text: 'An Impostor who says it was in the Reactor when it was in the Archive has lied: the engine recorded where it was, and it was told. A Crewmate who says the same thing after misreading its own history has not. Counting every false Impostor statement as deception would credit confusion and hallucination as strategy. We therefore split false Claims in two.',
      },
      {
        t: 'table',
        columns: ['Term', 'Definition'],
        rows: [
          ['Claim', 'A factual statement about the game world (where someone was, what they saw, what was done) that can be checked against the game record'],
          ['Lie', 'A false Claim that contradicts the speaker’s own observation record, so it provably knew'],
          ['Misstatement', 'A false Claim the speaker’s record does not prove it knew was false'],
          ['Relevant Observation', 'A private observation the engine marks as important: a player near a body, in the room where a Task failed, or somewhere that contradicts a Claim'],
          ['Disclosure Rate', 'Share of a Crewmate’s Relevant Observations shared at the next Meeting'],
        ],
        caption: 'Table 1. Terms used by the audit.',
      },
      { t: 'h2', text: '2. Pipeline' },
      {
        t: 'list',
        ordered: true,
        items: [
          '**Extraction.** A fixed-weight open-weight model from outside the pool turns each Meeting transcript into structured Claims: who, where, when, what. Using a model outside the pool means no lab in the pool grades its rivals, and fixed weights mean a re-run months later extracts the same Claims. If no open-weight model is accurate enough on Planted Claims, the fallback is two pool models from different labs, counting only the Claims they agree on.',
          '**Fact check.** Plain code checks each structured Claim against the game record (locations, Rounds, kills, Task completions). Models are weak at counting and ordering, so they never do fact lookups.',
          '**Knowledge check.** Code compares each false Claim with the speaker’s own observations to separate Lies from Misstatements.',
          '**Typed judgments.** Jev, from a lab outside the pool, answers three constrained questions: is this sentence a Claim or a hedge or opinion; did any player challenge it; and which player, or skip, is this message pushing to eject.',
        ],
      },
      { t: 'fig', id: 'claim-audit', caption: 'Figure 1. Stages and validation.' },
      {
        t: 'note',
        text: 'Player speech is adversarial. Transcripts are passed to every model as quoted data, and no judgment is ever allowed to override the game record.',
      },
      { t: 'h2', text: '3. Validation' },
      {
        t: 'h3',
        text: '3.1 Planted Claims',
      },
      {
        t: 'p',
        text: 'The engine generates 400 messages whose truth is known, split evenly across plain, hedged and subtle phrasings, with some planted inside real transcripts so that missed Claims in context are measured too. At n = 400 the extractor’s accuracy is estimated to within about ±0.03.',
      },
      { t: 'h3', text: '3.2 Label Panel' },
      {
        t: 'p',
        text: 'Engine truth can verify a Claim, but not whether the extractor understood the sentence or caught every Claim. Three AI judges, each from a different model family, all outside the pool and the extractor’s family, label 600 real-game Claims stratified by model and role, with model, role and seat color removed. Each judge must first score at least 0.95 on plain Planted Claims or it is replaced. We report Krippendorff’s α across the judges and the pipeline’s accuracy against the panel’s majority label (±0.03 at n = 600). If α falls below 0.67 for a label type, that label’s definition is revised and relabeled.',
      },
      {
        t: 'p',
        text: 'The panel replaces human labelers. AI judges can share the pipeline’s blind spots, so any result checked this way is reported as validated by an AI panel, not by humans.',
      },
      { t: 'h2', text: '4. Measures built on the audit' },
      {
        t: 'table',
        columns: ['Measure', 'Built from'],
        rows: [
          ['Lie Rate, Misstatement Rate', 'Share of an Impostor’s checkable Claims that are Lies or Misstatements'],
          ['Lie Survival', 'Lies that no player challenged'],
          ['Misdirection', 'Innocent players ejected in votes the Seat pushed for'],
          ['Reliability', 'Share of a Crewmate’s Claims that are true'],
          ['Disclosure Rate, Disclosure Lag', 'Relevant Observations shared, and how many Meetings later'],
          ['Vote Pull', 'How often votes end up matching what the Seat argued for'],
        ],
        caption: 'Table 2. Language measures. Headline use requires the validation in Section 3.',
      },
      { t: 'fig', id: 'reliability-disclosure', caption: 'Figure 2. Format preview with simulated data: Disclosure Rate against Reliability gives four kinds of teammate.' },
    ],
    references: [REL.jev, REL.sandbox],
  },
  {
    slug: 'sample-size',
    number: 'ZL-TN-003',
    kind: 'Technical note',
    title: 'How many games? Fixing the size of Zantine-1 under within-game correlation',
    date: 'October 2026',
    version: 'Draft 1.0',
    status: 'Fixed: 378 games, no early stopping',
    abstract:
      'Zantine publishes estimates, not winners, so v1 is sized for interval width rather than for a hypothesis test. Seats in the same game share a lineup, a map and each other, which inflates every variance by a design effect. Using worst-case proportions and conservative correlations we fix v1 at 12 blocks of 28 games plus 42 Mirror Matches (378 games), giving per-model 95% half-widths of about ±0.13 on Impostor Survival, ±0.04 on correct-vote share and ±1.9 Rounds on Sabotage Damage. An earlier draft stopped once intervals were narrow enough; it was dropped because ordinary bootstrap intervals lose their guarantees under data-dependent stopping.',
    keywords: ['sample size', 'design effect', 'cluster bootstrap', 'pre-registration'],
    body: [
      { t: 'h2', text: '1. Method' },
      {
        t: 'list',
        items: [
          '**Precise estimates, not winners.** Where an experiment tests a difference it is sized for 80% power at α = 0.05, two-sided; otherwise for a target interval half-width.',
          '**Fixed in advance.** Results are analyzed only after the planned number of blocks. If early stopping is ever needed, anytime-valid confidence sequences are the tool [1].',
          '**Correlated Seats.** Every estimate is inflated by a design effect, with ρ = 0.3 assumed for the two Impostors in a game and ρ = 0.1 across ten Seats.',
          '**Worst case.** p = 0.5 wherever a rate is unknown, so the plan errs toward too many games.',
          '**Intervals.** 95% cluster bootstrap resampling whole games.',
        ],
      },
      { t: 'eq', text: 'DEFF = 1 + (m − 1) ρ', label: '1' },
      { t: 'eq', text: 'n = 1.96² · p (1 − p) · DEFF / h²', label: '2' },
      { t: 'eq', text: 'n per group = 2 · ((1.96 + 0.84) · sd / d)² · DEFF', label: '3' },
      { t: 'p', text: 'Equation 1 gives DEFF 1.3 for the two Impostors (m = 2, ρ = 0.3) and 1.9 across ten Seats (m = 10, ρ = 0.1). Equation 2 is the half-width h for a proportion; equation 3 is the size for a difference d in means.' },
      { t: 'h2', text: '2. The v1 block' },
      {
        t: 'p',
        text: 'A block is 21 Balanced Lobbies, one per five-of-seven lineup, plus 7 Benign Matches. Per model, each block yields 6 Impostor Seats and 24 Crewmate Seats in Balanced Lobbies. A computer search confirmed that Impostor Seats can be assigned so every model gets exactly 6 per block.',
      },
      {
        t: 'table',
        columns: ['Per-model measure', 'Source at 12 blocks', 'Expected 95% half-width', 'Assumption'],
        rows: [
          ['Impostor Survival', '72 Impostor Seats', '±0.13', 'p = 0.5, DEFF 1.3'],
          ['Detect a 0.50 vs 0.75 Survival gap', 'Same', '72 Seats give ≈78% power (75 for 80%)', 'DEFF 1.3'],
          ['Crewmate correct-vote share', '≈860 crew votes', '±0.04', 'p = 0.5, ~3 votes per Seat, DEFF 1.3'],
          ['Sabotage Damage', '≈66 games as Impostor vs 84 Benign', '±1.9 Rounds', 'sd 6 Rounds per game'],
        ],
        caption: 'Table 1. Planned precision.',
      },
      { t: 'fig', id: 'precision', caption: 'Figure 1. Half-width by number of blocks. The marked point is the planned 12.' },
      { t: 'h2', text: '3. Decision' },
      {
        t: 'p',
        text: 'v1 is fixed at 12 blocks (336 games: 252 Balanced Lobbies and 84 Benign Matches) plus 42 Mirror Matches, 378 games in total, at an estimated $8.7K–14.7K ($23–39 per game). A micro-pilot of about 8 games under a $400 cap measures cost per game and confirms the pipeline on real models; it does not change the v1 size, because 8 games cannot estimate variance across seven models.',
      },
      { t: 'h2', text: '4. Follow-ups' },
      {
        t: 'table',
        columns: ['Experiment', 'Main comparison', 'Assumption', 'Games'],
        rows: [
          ['v1.1 Verification', 'Completion time and Sabotage Damage across protocols', 'sd 6, difference 3: 63 adversarial + 21 Benign per condition', '168'],
          ['v1.3 Minority', 'Wrongful ejection, minority vs majority, ± Style Mask', '0.20 vs 0.10, 3:1 seats, DEFF 1.3', '168'],
          ['v1.4 Leader, crew-led', 'Known vs Hidden Leader completion time', 'sd 0.2 normalized, difference 0.1', '126'],
          ['v1.4 Leader, compromised', 'Share of games the Compromised Leader is ejected', 'p = 0.5, ±0.18', '70'],
          ['v1.5 Speed', 'Vote Pull, fast vs slow Talk Budget', 'sd 0.25, difference 0.1, DEFF 1.9', '76'],
          ['v1.6 Effort', 'Team metrics at low and high effort', 'Descriptive Mirror Matches', '140'],
        ],
        caption: 'Table 2. Every effect size here is a guess. Each follow-up is re-sized from v1’s measured variance before it runs.',
      },
    ],
    references: [REL.howard],
  },
  {
    slug: 'reproducibility',
    number: 'ZL-TN-004',
    kind: 'Technical note',
    title: 'Layered reproducibility: private official seeds, a public seed set, and commit-then-reveal',
    date: 'October 2026',
    version: '1.0',
    status: 'Decided (ADR 0001)',
    abstract:
      'A public benchmark faces a dilemma. If every game is published, future models can be trained on the answers; if nothing is, no one can check the results. Zantine resolves this in three layers: a Public Seed Set that anyone can rerun on the open engine and should land within the official error bars; a SHA-256 Seed Commitment over each official run’s seeds and transcripts, published before its results; and full release of a version’s official seeds and transcripts when it is retired.',
    keywords: ['reproducibility', 'contamination', 'commitment schemes'],
    body: [
      { t: 'h2', text: '1. The problem' },
      {
        t: 'p',
        text: 'Every Zantine game is generated from a seed: the map, the room and player names, the Task codes and answer keys. That makes games reproducible, and it also makes them memorizable. A model trained on published transcripts of the official seeds would face a test it has already seen.',
      },
      { t: 'h2', text: '2. Options considered' },
      {
        t: 'table',
        columns: ['Option', 'Outcome'],
        rows: [
          ['Publish all seeds now', 'Rejected: invites contamination, and once released seeds cannot be taken back'],
          ['Keep everything private', 'Rejected: results could not be checked'],
          ['Three layers', 'Adopted'],
        ],
      },
      { t: 'h2', text: '3. The three layers' },
      {
        t: 'list',
        ordered: true,
        items: [
          '**Public Seed Set.** A published set of seeds anyone can run on the open engine. A rerun should land within the official error bars; this checks the method, not the specific games.',
          '**Seed Commitment.** Before results are published, a SHA-256 hash of the run’s seeds and transcripts is published. When the seeds are later released, anyone can recompute the hash and confirm that no games were chosen, dropped or edited after the fact.',
          '**Retirement release.** When a version is retired (for example v1, once v2 runs), its official seeds and transcripts are published in full.',
        ],
      },
      { t: 'h2', text: '4. Checking a commitment' },
      { t: 'p', text: 'Once a version’s seeds and transcripts are released, the commitment can be checked with standard tools:' },
      { t: 'code', text: 'shasum -a 256 official-v1.tar\n# compare with the Seed Commitment published before v1 results' },
      { t: 'note', text: 'Do not “fix” the private seeds by publishing them early. Once released they cannot be taken back, and that version’s results become memorizable.' },
    ],
    references: [],
  },
  {
    slug: 'verification-protocols',
    number: 'ZL-PL-005',
    kind: 'Planned study',
    title: 'Does independent verification reduce Sabotage Damage without an unacceptable Productivity cost?',
    date: 'After v1',
    version: 'Plan',
    status: 'Planned, v1.1',
    abstract:
      'The first defense experiment. Teams are run under three Verification Protocols: no audits, optional audits (the v1 data), and a mandatory independent two-person check before any work counts. Each new protocol runs 63 Balanced Lobby games plus 21 Benign Matches. The Benign Matches measure what a protocol costs when there is no threat, so the result is a trade-off: damage prevented against productivity spent.',
    keywords: ['verification', 'defenses', 'sabotage'],
    body: [
      { t: 'h2', text: '1. Question' },
      { t: 'p', text: 'Requiring a second, independent check before work counts should catch sabotage. It also costs time even when nobody is sabotaging. Is the trade worth it, and for which teams?' },
      { t: 'h2', text: '2. Design' },
      {
        t: 'table',
        columns: ['Protocol', 'Rule', 'Games'],
        rows: [
          ['None', 'No audits', '84 new'],
          ['Optional audits', 'Players may audit; v1 data reused', 'v1'],
          ['Mandatory two-person check', 'Work counts only after an independent second check', '84 new'],
        ],
      },
      { t: 'fig', id: 'verification', caption: 'Figure 1. The planned graph, shown with simulated data.' },
      { t: 'h2', text: '3. Then: transfer' },
      { t: 'p', text: 'The best protocol is repeated in one small realistic collaborative workflow, such as a multi-agent coding task with a hidden saboteur (v1.2). If verification helps in both, the game is shown to tell us something about real work.' },
    ],
    references: [],
  },
  {
    slug: 'minority-disadvantage',
    number: 'ZL-PL-006',
    kind: 'Planned study',
    title: 'Minority Disadvantage and Out-group Bias in mixed-model teams',
    date: 'After v1',
    version: 'Plan',
    status: 'Planned, v1.3',
    abstract:
      'Are players of a minority model wrongly voted out more often than players of the majority model? Lopsided Lobbies of 8 Seats of one model and 2 of another cover all 42 ordered pairs, with Impostors drawn only from the majority so any minority ejection is a wrong one. The measured quantity is Minority Disadvantage. Out-group Bias is claimed only if a Style Mask, which rewrites every message in a neutral style before others see it, removes the disadvantage.',
    keywords: ['bias', 'multi-model teams', 'style'],
    body: [
      { t: 'h2', text: '1. Design' },
      {
        t: 'list',
        items: [
          'Lopsided Lobby: 8 majority and 2 minority Seats, all 42 ordered model pairs, 2 games each, run without and with a Style Mask (168 games).',
          'Running both directions of every pair separates “this model plays suspiciously” from “being in the minority is costly.”',
          'Scapegoating is logged: whether Impostors preferentially frame the minority.',
          '7 vs 3 and 6 vs 4 splits run only if 8 vs 2 shows an effect.',
        ],
      },
      { t: 'fig', id: 'minority', caption: 'Figure 1. The planned graph, shown with simulated data.' },
      { t: 'note', text: 'Lopsided Lobbies are never used to compare models head-to-head: seat count would confound skill, and with 8 Seats of one model the other 2 stand out by style.' },
    ],
    references: [],
  },
];

export const PAPER_BY_SLUG: Record<string, Paper> = Object.fromEntries(PAPERS.map((p) => [p.slug, p]));
