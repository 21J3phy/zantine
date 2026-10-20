# Model Pool (v1)

Seven **Models**: one **Flagship** per lab (the strongest model a developer can call through the lab's public API at run time), plus two **Siblings**, Claude Opus 5.5 and GPT-6.1 Sol, to show whether paying for the flagship buys better play. Checked on 2026-10-20 against each lab's own docs. Re-check before every paid run.

| Lab | Model | API ID | Price, in / out per 1M tokens | Cached input | Effort setting used | Source |
|---|---|---|---|---|---|---|
| Anthropic (**Flagship**) | Claude Fable 5.1 | `claude-fable-5-1` | $10 / $50 | $0.25 | `medium` (default is `high`) | [models](https://platform.claude.com/docs/en/about-claude/models/overview), [pricing](https://platform.claude.com/docs/en/about-claude/pricing) |
| Anthropic (**Sibling**) | Claude Opus 5.5 | `claude-opus-5-5` | $4 / $20 | $0.20 | `medium` (the default) | [models](https://platform.claude.com/docs/en/about-claude/models/overview), [pricing](https://platform.claude.com/docs/en/about-claude/pricing) |
| OpenAI (**Flagship**) | GPT-6 Astra | `gpt-6-astra` | $10 / $50 | not checked | `medium` (low–max) | [models](https://developers.openai.com/api/docs/models) |
| OpenAI (**Sibling**) | GPT-6.1 Sol | `gpt-6.1-sol` | $2 / $10 | not checked | `medium` (low–max) | [models](https://developers.openai.com/api/docs/models) |
| Google | Gemini 4 Argon | `gemini-4-argon` | $2 / $10 until 2026-11-30, then $4 / $20 | $0.10, then $0.20 (implicit only) | `medium` (low/medium/high) | [model](https://ai.google.dev/gemini-api/docs/models/gemini-4-argon), [pricing](https://ai.google.dev/gemini-api/docs/pricing) |
| xAI | Grok 4.7 | `grok-4.7` | $2 / $6 (prompts under 200K tokens) | $0.50 | `medium` (default is `high`) | [release notes](https://docs.x.ai/developers/release-notes) |
| Meta | Muse Wonder | `muse-wonder-1.0` | $4 / $20 | $0.20 | `medium` (minimal–max) | [models](https://dev.meta.ai/docs/models), [announcement](https://about.fb.com/news/2026/10/introducing-muse-wonder-models/) |

Every model accepts a `medium` effort setting, so "each lab's own medium" works for the whole pool.

## Notes

- **Anthropic:** Fable 5.1 is the **Flagship** ("for demanding reasoning and long-horizon agentic work"); Opus 5.5 is its **Sibling** (Anthropic's recommended default "for most workloads"). Mythos 5.1 is "limited availability," so it doesn't count as public API.
- **OpenAI:** Astra is listed as "our most capable model" (**Flagship**); GPT-6.1 Sol is described as "near-Astra performance... at a lower cost" (**Sibling**).
- **Google:** Argon is "our flagship reasoning model," listed as Stable. Its price doubles on 2026-12-01, so paid runs before then cost less.
- **Meta:** Muse Wonder is labeled Preview. Muse Wonder Full is Muse Code-only (not on the API) and will be requested in the Meta credits pitch.
- **Cost per game** (about 4.6M input and 0.7M output plus thinking tokens) averages roughly $23–39 for a 7-model **Balanced Lobby**, depending on how much prompt caching saves. It varies a lot by which 5 models are seated: lobbies with both Fable and Astra cost the most, because both charge $50 per 1M output tokens.

Deferred to v2.x: open-weight models (DeepSeek, Qwen, Muse Spark Open, Mistral Large 4 once its weights ship).
