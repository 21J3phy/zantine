# Related Work

Checked on 2026-10-20 (arXiv abstract pages). "LLMs playing Among Us" is not new. Zantine's contribution has to be the narrower claim below.

| Work | What it does | How Zantine differs |
|---|---|---|
| [Among Them](https://arxiv.org/abs/2502.20426) (Idziejczak et al., Feb 2025) | Among Us-style game for testing LLM persuasion and deception; scores manipulation against 25 persuasion strategies | Zantine measures whether real, checkable, interdependent work survives the adversary, not persuasion style |
| [Among Us: A Sandbox for Measuring and Detecting Agentic Deception](https://arxiv.org/abs/2504.04072) (Golechha & Garriga-Alonso, 2025–26) | Open-ended Among Us sandbox, 18 models; finds RL-trained models deceive better than they detect; probes and SAEs detect lying | Zantine focuses on team defenses and interventions, not interpretability-based lie detection |
| [SHADE-Arena](https://arxiv.org/abs/2506.15740) (Kutasov et al., Jun 2025) | Paired benign main tasks and covert harmful side tasks; tests sabotage against an LLM monitor | One agent vs one monitor; Zantine studies a peer team that must both do the work and police itself |
| [TAMAS](https://arxiv.org/abs/2511.05269) (Kavathekar et al., Nov 2025) | Adversarial benchmark for multi-agent systems (AutoGen, CrewAI); Effective Robustness Score trading safety against effectiveness | External attacks on agent frameworks; Zantine studies an insider whose identity is unknown, plus which team protocols help |
| [Kaggle Game Arena: Werewolf](https://blog.google/innovation-and-ai/models-and-research/google-deepmind/kaggle-game-arena-updates/) (Google, 2026) | Social-deduction leaderboard | Talk-only; Zantine adds real task work, and publishes graphs, not a ranking |

## Zantine's claim

**Can a team of agents keep interdependent, checkable work intact while finding and containing an internal adversary, and which team protocols help?**

The likely novel parts:
- Work that **can be verified** and that **depends on other players' work** (**Task Graph**, **Shared Tasks**).
- **Sabotage Damage** measured against **Benign Matches**.
- **Verification Protocol** experiments that test defenses.
- **Compromised Leader** conditions.

This literature check suggests these are new; it doesn't prove nobody has combined them before.

## Statistics reference

- [Time-uniform, nonparametric, nonasymptotic confidence sequences](https://arxiv.org/abs/2103.06476) (Howard et al.). This is the approach to use if v1 ever needs valid early stopping. v1 currently uses a fixed sample size instead.
