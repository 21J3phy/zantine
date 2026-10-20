# Sample Size and Stopping Rules

How many games each experiment needs, and why. Decided on 2026-10-20. The variance and correlation figures are **assumptions** until the micro-pilot measures them. After the pilot, re-run these formulas with the measured values and update this file **before** any full run.

## Method

- **The goal is precise estimates, not winners.** Results are graphs, not rankings, so v1 is sized so that every model's error bars are narrow enough to read. Where an experiment does test a difference (bias, leader, speed), it is sized for **80% power at α = 0.05, two-sided**.
- **Seats in the same game are correlated.** Players in one game share its events. Every estimate is inflated by a design effect DEFF = 1 + (m − 1)ρ, where m is the number of seats per game in the estimate and ρ is the within-game correlation. Assumed ρ = 0.3 for the 2 Impostors (DEFF 1.3), and ρ = 0.1 across 10 seats (DEFF 1.9).
- **Confidence intervals** are 95% intervals from a cluster bootstrap that resamples whole games, so seat-level metrics aren't treated as independent.
- **Formulas:**
    - CI half-width h for a proportion: n = 1.96² · p(1−p) / h², with worst case p = 0.5.
    - CI half-width h for a mean: n = (1.96 · sd / h)².
    - Two proportions: the standard two-sample formula, with allocation ratio k where groups are unequal.
    - Two means: n per group = 2 · ((1.96 + 0.84) · sd / d)².
    - Each result is multiplied by its DEFF.
- **Stopping only on precision.** Experiments run in blocks and stop when the CIs are narrow enough, never when a result looks significant. A rule based on CI width doesn't bias the estimates; stopping when a result looks interesting would.

## v1 core comparison

**Block** = 20 games: 15 **Balanced Lobby** games (each **Impostor Pairing** once) plus 5 **Mirror Matches** (one per model). Every block is balanced on its own, so stopping after any block keeps the design balanced.

Per model, each block yields 6 Impostor seats in Balanced Lobby games, 24 Crewmate seats in Balanced Lobby games, and 1 Mirror Match.

**Seat-level vs team-level metrics.** In a mixed Balanced Lobby, team results like crew win or team **Vigilance** can't be credited to one model. There, models are compared on seat-level metrics: Impostor Survival, Lie Rate, a crewmate's correct-vote share, tasks completed, **Reliability**, **Disclosure Rate**. Team-level **Productivity** and **Vigilance** for each model come from **Mirror Matches**.

| Primary metric (per model) | Source | Target 95% CI half-width | Assumption | Needed | Blocks |
|---|---|---|---|---|---|
| Impostor Survival | Balanced Lobby Impostor seats | ±0.15 | p = 0.5, DEFF 1.3 | 56 seats | 10 |
| Detect a 0.50 vs 0.75 Survival gap between two models | Balanced Lobby Impostor seats | 80% power | DEFF 1.3 | 75 seats | 12.5 |
| Crewmate correct-vote share | Balanced Lobby crew votes | ±0.05 | p = 0.5, ~3 votes per seat | 384 votes | 6 |
| Team Productivity and Vigilance | Mirror Match | ±0.125 | sd = 0.2 per game | 10 games | 10 |

**Decision:**
- **Plan 12 blocks (240 games)**: 180 Balanced Lobby games and 60 Mirror Matches.
- **Earliest stop is 6 blocks.** From block 6 on, stop at the end of any block where every model's primary metrics meet the targets above.
- **Hard cap of 15 blocks (300 games).**
- **Estimated cost:** about $6K–11K at $25–45 per game, so it's for lab credits, not out of pocket.

## Micro-pilot (before v1)

- **About 10 games (half a block):** 5 Balanced Lobby games with 5 different Impostor Pairings, plus all 5 Mirror Matches. Hard spend cap of $400.
- **It does not estimate model results.** It measures cost per game, the per-game sd of team metrics, the within-game correlation ρ, and votes per seat. Those numbers replace the assumptions above.

## Supporting checks

| Check | Rule | Why |
|---|---|---|
| Solo task calibration | **60 solo trials per task kind per model**, each from a freshly generated variant. Keep the task kind only if all 60 succeed. | Zero failures in 60 trials puts the 95% upper bound on the failure rate under 5% (rule of three). The earlier "20 trials" could only show it was under about 14%. |
| Planted Claims | **400 Planted Claims**, split evenly across plain, hedged and subtle phrasings | Measures extractor accuracy (about 0.9) to within ±0.03, with about 130 per kind for reporting accuracy by phrasing. |

## Follow-up experiments

| Experiment | Main comparison | Assumption | Needed | Games |
|---|---|---|---|---|
| v1.1 **Out-group Bias** | Wrongful ejection rate, minority vs majority crewmates (pooled over the 20 ordered pairs) | 0.20 vs 0.10, majority:minority innocent seats 3:1, DEFF 1.3 | 162 minority seats | **100**: 5 per ordered pair. Per-pair results are exploratory. |
| v1.2 **Leader**, crew-led | **Known Leader** vs **Hidden Leader**: team **Productivity** | sd 0.2, difference 0.1 | 63 per condition | **130**: 65 per condition, 13 per **Leader** model |
| v1.2 **Leader**, compromised | Share of games where the **Compromised Leader** is ejected | p = 0.5, ±0.18 | 30 per condition | **60**: 30 Known, 30 Hidden |
| v1.3 Speed | **Vote Pull**, fast vs slow **Talk Budget** seats | sd 0.25, difference 0.1, DEFF 1.9, about 3.3 seats per tier per game | 186 seats per tier | **56**, plus 20 for the realism check = **76** |
| v1.4 Effort sweep | Team metrics at low and high effort, per model (medium comes from v1) | Same as the v1 Mirror Match target | 10 per model per level | **100** Mirror Matches |

All effect sizes above are guesses. They will be replaced with measured values from v1 before each follow-up is run.
