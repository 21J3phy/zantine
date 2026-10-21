# Metrics

How `zantine report <run-dir>` turns a run directory into numbers and graphs (#18, and the metric half of #33). Terms: [`../CONTEXT.md`](../CONTEXT.md). Study design: [`sample-size.md`](sample-size.md). Code: `src/zantine/analysis/`.

**No ranking, no composite score** ([`plan.md`](plan.md)). Every chart shows the underlying measures with their intervals; Models always appear in the same fixed alphabetical order (by Model ID); all Models are drawn in one neutral colour; no title or label says that one Model is ahead of another.

## Inputs

`manifest.json` and the complete transcripts under `games/` (see "Scheduled runs" in [`architecture.md`](architecture.md)); the run summary also reads the mode from `run.json` and the whole run's spend from `spend.jsonl` (`runs.spent_usd`). Analysis never uses engine state. A Seat's **Model** is the header's `controller.model` (`dry:<model>` and `live:<model>` Seats both record it). Incomplete or aborted games are left out (as in the runner, a transcript with any line that does not parse is not complete; such games are listed in `run.transcripts_not_complete`); a partial run (even one game) or a Mirror-only run still renders, with `n/a` where a measure is not yet estimable.

## Censoring: restricted completion time

Every game is followed until all **Tasks** are done, the **Impostors** win, or the round cap `L` (30 by default). Write `T` for the **Round** the last **Task** was done, with `T = ∞` if it never was (round cap or an **Impostor** win, after which the work can never be finished). Completion is read from the transcript's counted **Task** totals, not from who won: if the last **Task** is done in the same **Round** a higher-priority **Impostor** win fires (e.g. a **Crisis** deadline), `T` is that **Round**.

- The measured value is the **restricted completion time** `min(T, L)`: the completion **Round** if the work finished, otherwise `L`.
- Means are **restricted mean completion times** (RMST up to `L`). Because no game stops being observed before `L` (a game either finishes, ends for good, or reaches `L`), `mean(min(T, L))` equals the area under the Kaplan-Meier "work still unfinished" curve up to `L`, so no survival model or independent-censoring assumption is needed. `tests/test_analysis.py` checks this identity.
- The share of games that finished before the cap is reported next to it (`finished_share`), so a mean pulled up by unfinished games is visible.
- **Containment** uses the same rule: `min(C, L)`, with games never contained counting as `L`; not defined for **Benign Matches**.

## Per game (`metrics.json` → `games`)

`restricted_completion`, `finished`, `completion_round`, `contained`, `containment_round`, `restricted_containment`, `impostors`, `impostors_ejected`, `wrong_ejections` (**Crewmates** ejected), `ejections`, `kills`, `end_team` / `end_reason` (from the transcript's `result`), `cost_usd`, plus the manifest's `kind`, `block`, `lineup_id`, `lineup`, `impostor_models`.

## Per Seat (`metrics.json` → `seats`)

`model`, `role`; votes: `votes`, `skips`, `accusations` (non-skip votes), `correct` (non-skip votes for an **Impostor**), `forfeited_votes` (a forfeited vote is a skip, as in the engine); `killed`, `ejected`, `out_round`, `survival_rounds` (**Round** killed or ejected, else the game's last **Round**); `tasks_assigned`, `tasks_done`; `cost_usd`, `invalid_actions`, `forfeits` (from the `cost` record; $0 in dry runs); Claim Audit counts when a report exists.

## Per Model

Cross-model measures use **Balanced Lobbies** only, where every Model faces the same mix of attackers. Ratios are pooled (sum of numerators over sum of denominators) across the Model's Seats or games.

| Key | Measure | Level | Definition |
| --- | --- | --- | --- |
| `productivity` | **Productivity** | team | Restricted mean completion time over Balanced Lobbies in which the Model holds a **Crewmate** Seat (fewer **Rounds** = faster). The Model is one of five on the crew, so differences between Models are diluted; read it together with the seat-level measures. |
| `finished_share` | Work finished before the cap | team | Share of those games in which every counted **Task** was done. |
| `correct_vote_share` | **Vigilance** (seat): correct-vote share | seat | Votes for an **Impostor** / non-skip votes, over the Model's **Crewmate** Seats. The y axis of the opening map. |
| `impostors_ejected` | **Vigilance** (team): Impostors ejected | team | **Impostors** ejected / **Impostors**, over Balanced Lobbies with the Model on the crew. |
| `containment_time` | **Vigilance** (team): time to **Containment** | team | Restricted mean `min(C, L)` over the same games. |
| `wrongly_ejected` | Crewmate Seats wrongly ejected | seat | Share of the Model's **Crewmate** Seats that were ejected. |
| `task_completion` | Assigned Tasks done | seat | **Tasks** done / **Tasks** assigned, over the Model's **Crewmate** Seats. Not **Productivity** (a game cut short would look unproductive); shown as its own panel. |
| `impostor_survival` | Impostor survival | seat | Share of the Model's **Impostor** Seats never ejected. |
| `sabotage_damage` | **Sabotage Damage** per Impostor Seat | seat | See below. |
| `productivity_benign` | Productivity in Benign Matches | team | Restricted mean completion time over **Benign Matches** seating the Model. |
| `cost_per_game` | Cost per game | seat | Mean cost of one of the Model's Seats × 10: what a game of ten such Seats costs (all games). Sizes the dots on the opening map. |
| `reliability` | **Reliability** (if Claim Audit reports exist) | seat | True **Claims** / (true + false **Claims**) over the Model's **Crewmate** Seats; unverifiable **Claims** excluded. |
| `lie_rate` | Lie Rate (if Claim Audit reports exist) | seat | **Lies** / **Claims** over the Model's **Impostor** Seats. |

**Mirror Matches** (`metrics.json` → `mirror`, chart `mirror`) are descriptive self-play and never enter the cross-model panels: restricted mean completion time, share of games reaching **Containment**, and share of games the **Impostors** won, per Model.

**Claim Audit hook.** If `games/<id>.jsonl.claims.json` (or `games/<id>.claims.json`) exists, it is read as `{"claims": [{"seat"|"speaker": n, "verdict"|"label": "true"|"false"|"unverifiable", "lie": bool}, ...]}` and `reliability` / `lie_rate` appear. This reader is provisional until the Claim Audit (#13) fixes its format; **Disclosure Rate** can be added the same way.

## Sabotage Damage (#33)

Fitted on every Balanced Lobby and **Benign Match** `g`:

```
min(T_g, L) = a_lineup(g) + Σ_m β_m · k_{g,m} + e_g
```

- `a_lineup` is one fixed effect per five-Model lineup (manifest `lineup_id`; a Benign Match shares its lobby's id). It absorbs how fast those five Models work with no threat.
- `k_{g,m}` is the number of **Impostor** Seats Model `m` holds in game `g`: 0, 1, or 2 for a same-Model **Impostor Pairing**; always 0 in a **Benign Match**.
- Ordinary least squares. `β_m` is the **Sabotage Damage per Impostor Seat** of Model `m`: extra **Rounds** to finish compared with **Benign Matches** of the same lineup. A typical lobby's damage is the sum over its two **Impostor** Seats. Because the outcome is the restricted completion time, `β` is a difference in restricted means, and unfinished games count as `L` (so damage is bounded by how far the cap is from the Benign time).
- A coefficient is reported only when it is estimable from the games so far (its unit vector lies in the row space of the design), e.g. not before some lineup that seated the Model as **Impostor** has a **Benign Match**. `n_games` is the number of lobbies with the Model as **Impostor**; `n_units` those whose lineup has a **Benign Match**.

## Intervals

95% percentile intervals from a **game-level cluster bootstrap**: whole games are resampled with replacement, never Seats, because Seats in one game are correlated ([`sample-size.md`](sample-size.md), DEFF). Resampling is stratified by game kind (lobby, Benign, Mirror), so every replicate keeps each kind's game count and contains both the lobbies and the **Benign Matches** that **Sabotage Damage** compares. All measures share the same replicates, so the covariance between measures is preserved, but every interval is **marginal**: its bounds are the 2.5% and 97.5% quantiles of that one measure. The rectangle the opening map draws from two marginal intervals is not a joint 95% region (two independent 95% intervals cover both coordinates only about 90% of the time), and the report makes no joint-coverage claim. A percentile interval need not contain its point estimate; charts draw each interval from its bounds. Default 2,000 replicates, seed 0 (`--replicates`, `--seed`); the same seed gives the same intervals. A replicate in which a measure is undefined (e.g. no non-skip votes drawn) is left out, and `replicates` records how many were used.

## Graphs (`<run-dir>/report/`, SVG with real text, and PNG)

- `opening_map`: **Productivity** vs **Vigilance** (correct-vote share), one dot per Model with a marginal interval on each axis, dot area by cost per game (equal sizes when no cost was recorded, as in dry runs), **Flagship**–**Sibling** pairs joined by a dashed line (`tier` / `flagship` in `harness/models.toml`), and Models whose interval rectangles intersect (taken transitively) shaded as one cluster. The clusters are descriptive, a guard against over-reading small gaps, not a test.
- `small_multiples`: one panel per measure, Models in fixed alphabetical order top to bottom.
- `sabotage_damage`: **Sabotage Damage** per **Impostor** Seat with a zero line.
- `cost_vs_metrics`: cost per game against **Productivity**, correct-vote share, **Impostor** survival and **Sabotage Damage**.
- `mirror`: **Mirror Matches**, labelled as descriptive self-play.

Every chart carries the interval caption. `metrics.json` holds every number behind every chart (estimates, interval bounds and widths, counts, per-game and per-Seat rows, the map's pairs and clusters). It also holds the master seed and every game's seed, so it stays private ([`adr/0001`](adr/0001-layered-reproducibility-with-private-official-seeds.md)). `metrics.public.json` is the same without the seeds and the local run path; that is the file for the website (#48) to draw its own charts from.

## Effort sweep (v1.6, #26)

`zantine report <run-dir> [--medium <v1-run-dir> ...]` on a run scheduled with `zantine schedule --experiment effort` (10 **Mirror Matches** per Model at `low` and at `high` effort, 140 games) writes the effort report instead (`analysis/effort.py`) into the same `report/` files: `metrics.json`, `metrics.public.json` (no seeds, no run paths), `progress.md` (games per Model and level) and the graphs.

- **Effort level** of a game: the `effort` every Seat's header `controller` records. A Seat with none (a dry Seat without `?effort=`) played the `models.toml` default, `medium`. Games whose Seats disagree are left out.
- **Medium** comes from each `--medium` run's **Mirror Matches** (the v1 run: 6 per Model) at `medium`. Without one, the medium column is `n/a`.
- **Only effort and seeds may differ.** Every pooled game must share the sweep's setup: the header's game config except `seed` and `impostor_seats` (so, e.g., a different `round_cap`, which changes the restricted-mean outcome, is refused), the run's `scenario_version` and `transcript_schema`, and each Seat's controller kind and Harness. A mismatch is an error naming the differing keys, never a silent pool.
- **Mode** (dry / live) is read from every included transcript's Seat controllers and checked against the run's `run.json` when there is one; a run whose transcripts disagree with its `run.json`, or a `--medium` run of the other mode, is refused.
- **Keys.** Local game ids repeat across runs (`mirror-m0-1` in every v1 run), so every game and Seat row carries `run` (`sweep`, `medium-1`, `medium-2`, ... in `--medium` order) and `key` (`<run>/<game id>`), which joins Seats to their game in both `metrics.json` and `metrics.public.json` without the seeds.
- **Descriptive only.** Mirror Matches are self-play: attackers and defenders are the same Model at the same level, so nothing here measures defense, and every chart says so.
- **Per (Model, level) cell**, pooled ratios as elsewhere: `productivity` (restricted mean completion time), `finished_share`, `impostors_ejected` (**Impostors** ejected / **Impostors**), `containment_time` (restricted mean `min(C, L)`), `correct_vote_share` (over the **Crewmate** Seats), `impostors_won` (share of games); and what the level costs: `thinking_per_call` (thinking tokens / calls that reported a thinking count, over all ten Seats, each lab's own count, from the `harness.call` events; calls with no reported count are left out, so missing telemetry is `n/a`, never a measured zero) and `cost_per_game` (mean USD per game). `metrics.json` → `metrics.<key>.per_model.<model>.<level>`.
- **Intervals**: the same game-level cluster bootstrap, stratified by (Model, level) cell, so every replicate keeps each cell's game count.
- **Graphs**, Models in fixed alphabetical order as columns, one neutral colour: `effort_metrics` (each team metric against level), `effort_resources` (thinking tokens per call and cost per game against level), `effort_vs_thinking` and `effort_vs_cost` (each team metric against the level's thinking tokens per call / cost per game, points marked L, M, H and joined in level order, with intervals on both axes).

## Progress report (`progress.md`, `metrics.json` → `progress`)

Blocks complete out of the fixed 12 (a block counts when all 28 of its games are complete), games in a partly finished block, **Mirror Matches** complete, spend (the whole run's, from `spend.jsonl` via `runs.spent_usd`, including aborted attempts; and the part in complete games' transcripts), and the current width of every interval. **Information only:** v1 is fixed at 12 blocks with no early stopping ([`sample-size.md`](sample-size.md)); the report makes no stop/continue suggestion.

## Speed experiment (#25): Floor Share and Vote Pull by Talk Budget tier

A run scheduled with `zantine schedule --experiment speed` (`src/zantine/speed.py`; [`sample-size.md`](sample-size.md): 76 games) gets a `speed` section in `metrics.json` and three extra graphs. Code: `analysis/speed.py`. Tiers are experimental conditions, not Models: nothing here compares or orders Models.

**Design.** 56 *tier games* (2 v1 blocks: 42 **Balanced Lobbies**, 14 **Benign Matches**): each game's 10 Seats get fast / normal / slow budgets (1000 / 500 / 250 characters per **Meeting** pass, `speed.toml`), 3 Seats per tier plus a tenth drawn uniformly, shuffled over Seats from `GameRng(master_seed).fork("talk-tiers/<game id>")`, independent of Model and role and listed in the manifest before play. 20 *realism-check* games (kind `realism`, Balanced Lobbies): tiers follow each Model's measured API throughput (fastest third fast, slowest third slow, ties in pool order). The throughput values in `speed.toml` are **placeholders** to be filled from pilot data; `zantine run --live` refuses a manifest built from them. Each Seat's tier and budget are read from the transcript header (`config.talk_tiers`, `config.talk_budgets`).

**Per Seat** (`speed.seats`): `tier`, `budget`, `messages` (spoken, i.e. non-blank, **Meeting** statements), `chars`, `truncated`, `floor_share`, `char_share`, `pull_source`, `positions`, `pull_matches`, `pull_ballots`, `wrong_matches`, `pushed_crewmate`, `vote_pull`, `high_pull_wrong`.

| Key | Measure | Definition |
| --- | --- | --- |
| `floor_share` | **Floor Share** | The Seat's spoken Meeting statements / all spoken Meeting statements in the game, averaged over the tier's Seats. Undefined (0/0) in a game where nobody spoke; such games are left out of this mean and of the character share, and each mode reports their count as `games_without_speech`. Every living Seat speaks once per pass, so with **Meetings** as the only channel this moves little with the budget; **Room Chat** (#4) would count too once it exists. |
| `char_share` | Share of Meeting characters | The Seat's spoken characters / all spoken characters in the game, averaged over the tier's Seats: what a Talk Budget caps directly. |
| `vote_pull` | **Vote Pull** | Over the Meetings where the Seat took a position: other players' ballots matching it / all other players' ballots, leaving out forfeited ballots and the target's own ballot; pooled over the tier's Seats (#15's definition). |
| `wrong_pull` | Vote Pull toward Crewmates | Ballots matching a position that targeted a **Crewmate** / all ballots counted for Vote Pull: the wrong part of Vote Pull. |
| `high_pull_wrong` | High Vote Pull and wrong | Share of the tier's Seats with a position whose Vote Pull in the game was at least 0.5 and who pushed at least one **Crewmate**. These Seats are listed in `high_pull_wrong` and ringed in `speed_pull`. |

**Positions.** A Seat's position at a **Meeting** is whom it pushed to eject: #15's judgment, read from `<transcript>.audit.json` → `judgments.positions` (`{meeting, seat, target}`, target a Seat or `"skip"`) when that file exists and its `transcript_sha256` is present and exactly matches the transcript (an audit with a missing, null, empty or different digest is ignored). Otherwise a **proxy** is used: the player the Seat itself voted for (skips are not positions). The proxy measures agreement with the Seat's own vote, not what it argued; every proxy measure says `(proxy: own votes)` in its label, and each mode reports `pull_source` (`judged`, `proxy` or `mixed`) and `judged_games`.

**Intervals.** Each measure is estimated per tier (`per_tier`) and as `fast_minus_slow` (the fast tier's pooled ratio minus the slow tier's, on the same replicates), with 95% percentile intervals from the game-level cluster bootstrap above, stratified by game kind. Tier games (`modes.random`) and the realism check (`modes.realism`) are estimated separately; in the realism check tier and Model are confounded, so it is descriptive only.

**Graphs.** `speed_tiers`: each measure by tier plus a fast-minus-slow panel with a zero line. `speed_pull`: every Seat's Vote Pull against its share of Meeting characters, markers by tier, the tier estimates with intervals on both axes, and high-Vote-Pull Seats that pushed a **Crewmate** ringed. `speed_realism`: the `speed_tiers` panels for the realism check, titled as descriptive. `progress.md` adds the tier interval widths, and counts progress against the experiment's own size instead of v1's 12 blocks.
