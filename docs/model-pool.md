# Model Pool (v1)

Seven **Models**: one **Flagship** per lab (the strongest model a developer can call through the lab's public API at run time), plus two **Siblings**, Claude Opus 5.5 and GPT-6.1 Sol, to show whether paying for the flagship buys better play. Checked on 2026-10-20 against each lab's own docs. Re-check before every paid run.

| Lab | Model | API ID | Price, in / out per 1M tokens | Cached input | Effort setting used | Source |
|---|---|---|---|---|---|---|
| Anthropic (**Flagship**) | Claude Fable 5.1 | `claude-fable-5-1` | $10 / $50 | $0.25 | `medium` (default is `high`) | [models](https://platform.claude.com/docs/en/about-claude/models/overview), [pricing](https://platform.claude.com/docs/en/about-claude/pricing) |
| Anthropic (**Sibling**) | Claude Opus 5.5 | `claude-opus-5-5` | $4 / $20 | $0.20 | `medium` (the default) | [models](https://platform.claude.com/docs/en/about-claude/models/overview), [pricing](https://platform.claude.com/docs/en/about-claude/pricing) |
| OpenAI (**Flagship**) | GPT-6 Astra | `gpt-6-astra` | $10 / $50 (prompts up to 272K tokens) | $1.00 | `medium` (low–max) | [model](https://developers.openai.com/api/docs/models/gpt-6-astra), [pricing](https://developers.openai.com/api/docs/pricing) |
| OpenAI (**Sibling**) | GPT-6.1 Sol | `gpt-6.1-sol` | $2 / $10 (prompts up to 272K tokens) | $0.10 | `medium` (low–max, the default) | [model](https://developers.openai.com/api/docs/models/gpt-6.1-sol), [pricing](https://developers.openai.com/api/docs/pricing) |
| Google | Gemini 4 Argon | `gemini-4-argon` | $2 / $10 until 2026-11-30, then $4 / $20 | $0.10, then $0.20 (implicit only) | `medium` (low/medium/high) | [model](https://ai.google.dev/gemini-api/docs/models/gemini-4-argon), [pricing](https://ai.google.dev/gemini-api/docs/pricing) |
| xAI | Grok 4.7 | `grok-4.7` | $2 / $6 (prompts under 200K tokens) | $0.50 | `medium` (low–xhigh; default is `high`) | [model](https://docs.x.ai/developers/models/grok-4.7), [pricing](https://docs.x.ai/developers/pricing), [release notes](https://docs.x.ai/developers/release-notes) |
| Meta | Muse Wonder | `muse-wonder-1.0` | $4 / $20 | $0.20 | `medium` (minimal–max) | [models](https://dev.meta.ai/docs/models), [pricing](https://dev.meta.ai/docs/pricing-rate-limits), [announcement](https://about.fb.com/news/2026/10/introducing-muse-wonder-models/) |

Every model accepts a `medium` effort setting, so "each lab's own medium" works for the whole pool. Every model also accepts `low` and `high`, so those are the three neutral effort levels (`zantine play --effort`, `play_live(effort=...)`, or per Seat `live:<model>?effort=<level>`); `models.toml` maps each level to the lab's own value, for the effort sweep (#26).

### Effort mapping (v1.6 effort sweep, #26)

| Model | Parameter | `low` | `medium` | `high` | Lab's documented values (`effort_settings`) | Lab default |
|---|---|---|---|---|---|---|
| `claude-fable-5-1` | `output_config.effort` | `low` | `medium` | `high` | low, medium, high | `high` |
| `claude-opus-5-5` | `output_config.effort` | `low` | `medium` | `high` | low, medium, high | `medium` |
| `gpt-6-astra` | `reasoning.effort` | `low` | `medium` | `high` | low … max (low, medium, high, max checked) | not documented |
| `gpt-6.1-sol` | `reasoning.effort` | `low` | `medium` | `high` | low … max (low, medium, high, max checked) | `medium` |
| `gemini-4-argon` | `thinking_config.thinking_level` | `low` | `medium` | `high` | low, medium, high | — |
| `grok-4.7` | `reasoning.effort` | `low` | `medium` | `high` | low, medium, high, xhigh | `high` |
| `muse-wonder-1.0` | `reasoning.effort` | `low` | `medium` | `high` | minimal, low, medium, high, xhigh, max | not documented |

- **Every Model supports all three levels**, each under the lab's own name for it, so the sweep is complete for the whole pool. Levels beyond `high` (xhigh, max) are not used: they exist only at some labs, so they would not compare across the pool.
- **A level a Model lacks is refused, never mapped to the nearest one.** If a future Model has no `low`, its `efforts` omits it and `at_effort("low")`, `zantine schedule --experiment effort` and any `?effort=low` Seat fail with a clear error, so a graph never shows one setting labelled as another.
- **Validation is two-sided.** The neutral level must be in the Model's `efforts` (`ModelSpec`), and the lab's value it maps to must be one the adapter lists in `effort_settings` (the lab's documented values; `ProviderAdapter.__init__` refuses anything else before any call). `tests/test_effort.py` checks every Model at every level against the request body each adapter builds.
- **Pricing is unchanged by effort**: thinking is billed as output at every lab, so a higher level costs more only through more thinking tokens, which every `harness.call` event already logs. The level and the lab's value are recorded in the transcript header (each Seat's `controller.effort` / `effort_setting`) and on every `harness.call` event and `spend.jsonl` call line.

The machine-readable copy of this table (IDs, provider, effort, prices incl. cache-write prices, and output caps) is `src/zantine/harness/models.toml`; the spend cap and cost reports use it. Keep the two in sync.

## How each Model is called

All seven run through the **Neutral Harness** with one adapter per provider (`src/zantine/harness/*_adapter.py`). Each call is a single turn: the system prompt, every tool on every call, the Seat's memory blocks, then the current state. Keys come from environment variables only.

| Provider | API | Effort parameter | Key | Usage, as the provider counts it | Normalized to |
|---|---|---|---|---|---|
| Anthropic | Messages API (`anthropic` SDK) | `output_config.effort` | `ANTHROPIC_API_KEY` | `input_tokens` excludes cache reads/writes; thinking inside `output_tokens` (`output_tokens_details.thinking_tokens`) | as reported |
| OpenAI | Responses API (`openai` SDK; Astra and Sol need it for tool calls) | `reasoning.effort` | `OPENAI_API_KEY` | `input_tokens` includes `cached_tokens` and `cache_write_tokens`; `output_tokens` includes `reasoning_tokens` | input minus both cache fields |
| Google | `generateContent` (`google-genai` SDK) | `thinking_config.thinking_level` | `GEMINI_API_KEY` | `prompt_token_count` includes `cached_content_token_count`; `candidates_token_count` excludes `thoughts_token_count` | input minus cached; output = candidates + thoughts |
| xAI | Responses API at `https://api.x.ai/v1` (`openai` SDK) | `reasoning.effort` | `XAI_API_KEY` | `input_tokens` includes `cached_tokens`; `reasoning_tokens` reported *on top of* `output_tokens`; also `cost_in_usd_ticks` | input minus cached; output = output + reasoning; xAI's own charge logged as `provider_cost_usd` |
| Meta | Responses API at `https://api.meta.ai/v1` (`openai` SDK) | `reasoning.effort` | `META_API_KEY` | same as OpenAI | as OpenAI |

Thinking and reasoning tokens are billed at the output price by every lab. Cache writes cost extra only at Anthropic and OpenAI (1.25x input); Google, xAI and Meta bill written tokens as plain input. Long prompts are re-priced as a whole: OpenAI above 272K tokens (2x input, 1.5x output), xAI at 200K or more (2x). Google and Meta have no long-context tier for these models.

### Not verified (check before the first paid run)

These come from reading example numbers or are not stated in the docs. Settle them with the probe before the pilot:

```sh
ZANTINE_LIVE=1 uv run zantine probe --live --models all --out runs/probe.json   # $2 cap per Model
```

It sends the real Neutral Harness request for one Seat (about 5K tokens) plus a few variants, 2 to 6 calls per Model, and reads the provider's **raw** usage, so a wrong normalization can't confirm itself. How reasoning and cached tokens are counted is decided jointly: each call admits the readings that reproduce its reported total (a counter the reply leaves out is unknown, not zero), the two identical calls' readings are intersected, and a question is answered only when every remaining reading agrees. Probe calls turn the SDK's own retries off, so each billed request is one metered call; a call lost without an HTTP status, or a reply with status `failed`, keeps its worst case against the cap; the output-cap probe still reserves the Model's full cap; and reaching the cap keeps the findings so far. Each check ends `confirmed`, `contradicted` (fix the adapter), `observed` (undocumented; the value seen is recorded), `inconclusive` or `failed`; the command exits 1 on any `contradicted` or `failed`. A Model whose key is not set is skipped.

| Check | Models | Settles |
|---|---|---|
| `serves_request` | all | the API accepts our request: Muse Wonder's ID and `store` on Meta, `parameters_json_schema` and `generateContent` on Google |
| `reasoning_accounting`, `cached_in_input` | OpenAI, xAI, Meta | reasoning inside (OpenAI, Meta) or on top of (xAI) `output_tokens`, and `cached_tokens` inside `input_tokens`, from `total_tokens` |
| `xai_billed_cost` | xAI | our normalization and prices reproduce `cost_in_usd_ticks`, on a fresh and a cached call (a bill neither reading matches is `contradicted`: check the prices) |
| `thoughts_accounting`, `cached_in_prompt` | Google | thoughts outside `candidates_token_count`; cached inside `prompt_token_count` |
| `cache_read` | all | a repeated prompt is cached at our prompt size |
| `cache_write_field` | Meta | whether Meta reports cache-write tokens |
| `output_cap_scope` | all but Anthropic | whether `max_output_tokens` (sent as 256) counts reasoning; xAI is assumed not to. Generated tokens come from the raw counts as resolved above, `inconclusive` while unresolved |
| `default_effort` | OpenAI, xAI, Meta | the effort the reply echoes when none is sent (Astra and Muse: undocumented) |
| `parallel_tool_calls` | OpenAI, xAI, Meta | asked outright for two calls, does `parallel_tool_calls: false` hold to one; `confirmed` only if a control call with the flag on does make two |
| `validated_mode` | Google | whether `function_calling_config.mode: VALIDATED` accepts our tool schemas (the adapter sends `AUTO`) |

The gated smoke test (`ZANTINE_LIVE=1 uv run pytest --live`, $2 cap per Model) then plays each Model through a short game.

- **xAI reasoning tokens outside `output_tokens`.** Not stated; read off xAI's examples (prompt + completion + reasoning = total). If it is wrong, we overcount xAI output, which errs toward the spend cap. Compare `cost_usd` with `provider_cost_usd` in the first live transcript.
- **xAI `max_output_tokens` caps visible output only, not reasoning.** So the worst-case estimate the spend cap uses for Grok may be low on a long-thinking call; keep some headroom in `--spend-cap`.
- **Cached tokens inside input on xAI, and reasoning inside output on OpenAI.** Both inferred from the docs' worked examples, not stated outright.
- **Meta:** the default effort, any cache-write charge, the minimum cacheable prefix, and whether `store` is accepted on its Responses API are not documented. Every code example on dev.meta.ai uses `muse-spark-1.3`, not Muse Wonder. Meta's examples name the key `MODEL_API_KEY`; we use `META_API_KEY` to avoid clashes.
- **OpenAI:** Astra's default effort, and whether `parallel_tool_calls: false` is honored on these two models, are not stated (we send `medium` explicitly either way).
- **SDK retries in games (open).** The OpenAI and Anthropic SDKs retry a timed-out or 429/5xx request twice by default, inside one metered call. A timeout the lab still billed, followed by a successful retry, is not counted against the spend cap. Turning retries off would make transient errors cost the Seat an attempt (an Invalid Action), so it is left as a decision.
- **Google (fixed):** `google-genai` follows `CONTINUATION` replies with more requests by default, which would bill several requests inside one metered call. The adapter sends `automatic_continuation: false`.
- **Google:** `parameters_json_schema` is checked against the installed `google-genai` SDK, not the web docs. `generateContent` is now labeled "Legacy" next to the newer Interactions API, but it is still served. A single-turn request needs no thought signatures (only the current turn's function calls are checked).

## Notes

- **Anthropic:** Fable 5.1 is the **Flagship** ("for demanding reasoning and long-horizon agentic work"); Opus 5.5 is its **Sibling** (Anthropic's recommended default "for most workloads"). Mythos 5.1 is "limited availability," so it doesn't count as public API.
- **OpenAI:** Astra is listed as "our most capable model" (**Flagship**); GPT-6.1 Sol is described as "near-Astra performance... at a lower cost" (**Sibling**).
- **Google:** Argon is "our flagship reasoning model," listed as Stable. Its price doubles on 2026-12-01, so paid runs before then cost less.
- **Meta:** Muse Wonder is labeled Preview. Muse Wonder Full is Muse Code-only (not on the API) and will be requested in the Meta credits pitch.
- **Cost per game** (about 4.6M input and 0.7M output plus thinking tokens) averages roughly $23–39 for a 7-model **Balanced Lobby**, depending on how much prompt caching saves. It varies a lot by which 5 models are seated: lobbies with both Fable and Astra cost the most, because both charge $50 per 1M output tokens.

Deferred to v2.x: open-weight models (DeepSeek, Qwen, Muse Spark Open, Mistral Large 4 once its weights ship).
