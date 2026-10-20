# Architecture

How the code is laid out, where new features plug in, and the conventions every change must follow. Game rules as implemented: [`rules.md`](rules.md). Terms: [`../CONTEXT.md`](../CONTEXT.md).

## Running

```sh
uv sync                                   # create .venv with dev tools
uv run zantine play --seed 7              # one 10-seat game with scripted bots
uv run zantine play --seed 7 --bots 'scripted*8,idle*2' --out runs/seed-7.jsonl
uv run zantine play --seed 7 --impostors 0       # a Benign Match
uv run zantine replay runs/seed-7.jsonl   # writes runs/seed-7.html (demo viewer, not scored)
uv run pytest                             # all tests (no network)
uv run ruff check && uv run ruff format --check

# One live Claude Fable 5.1 Seat with 9 bots. Paid: needs both opt-ins and a key.
ZANTINE_LIVE=1 ANTHROPIC_API_KEY=... uv run zantine play --seed 7 \
    --bots 'live:claude-fable-5-1,scripted*9' --live --spend-cap 10 --out runs/live-7.jsonl
# Any pool Model (keys: ANTHROPIC_/OPENAI_/GEMINI_/XAI_/META_API_KEY); --effort low|medium|high.
ZANTINE_LIVE=1 XAI_API_KEY=... uv run zantine play --seed 7 \
    --bots 'live:grok-4.7*2,scripted*8' --live --spend-cap 10 --effort medium
# The same Model in the Neutral Harness and in the agent-loop Harness (#27), side by side.
ZANTINE_LIVE=1 ANTHROPIC_API_KEY=... uv run zantine play --seed 7 \
    --bots 'live:claude-fable-5-1,live:claude-fable-5-1@agent-loop,scripted*8' --live --spend-cap 10
ZANTINE_LIVE=1 uv run pytest --live       # the live smoke test: one short game per Model whose key is set (paid)
```

## Module map (`src/zantine/`)

| Module | Responsibility |
| --- | --- |
| `config.py` | `GameConfig`: every tunable number (seats, Impostors, round cap, cooldowns, Talk Budget, ...). `num_impostors` may be 0 to `max_impostors`; players only ever see `impostor_count_text` ("0 to 2 Impostors"). |
| `rng.py` | `GameRng`: the only source of randomness. `fork(label)` gives independent named streams. |
| `scenario.py` | Builds the `Scenario` (generated map, names, seat order, roles) from the seed; `classic_scenario()` is the fixed setup for tests. `SCENARIO_VERSION` is recorded in the header. |
| `map.py` | `GameMap` (rooms, corridors, meeting room) and the hard-coded `tiny_map()` used by `classic_scenario()`. |
| `state.py` | Ground-truth `GameState`, `PlayerState`, `Body`. `GameState.ext` holds per-module state. |
| `roles.py` | `Role` (crewmate/impostor) and `Team` (winner). |
| `rules/actions.py` | `Action` base class and core actions. Each action validates itself. `Invalid` = unacceptable response. |
| `rules/registry.py` | `Rules`, `GameModule`, `RoundContext`, `MeetingRequest`: the plug-in surface. |
| `rules/core.py` | `CoreModule`: kill, move, report, emergency Meeting, cooldowns, snapshots. |
| `rules/crisis.py` | `CrisisModule`: Impostor-triggered **Crisis** (`crisis.trigger` / `crisis.fix`, resolver 500, win condition 300). State in `ext["crisis"]`. |
| `rules/win.py` | Core win condition (parity) and the round-cap winner. |
| `rules/containment.py` | `ContainmentModule`: records **Containment** (every Impostor ejected) as a ground-truth event and in `result["containment"]`; never ends the game, never tells players. |
| `rules/tasks.py` | `TasksModule`: assigns Tasks, `tasks.work`, private grading, Task Graph blocking, reassignment after Meetings, the `tasks_done` win, Productivity in the result. |
| `rules/relay.py` | `RelayModule`: places chain relays, pickup/handoff/delivery at order 450, dropped-part recovery, hidden `Part` state in `ext["relay"]`. Engine hooks `part` and `tamper` for Audit/Sabotage. |
| `tasks/relay.py` | `chain_relay` TaskKind: seeded part/serial, bound three-room route, carrier briefs and reference action plans. |
| `rules/audit.py` | `AuditModule`: optional audit placement, private readings, `audit.share` / `audit.check` (resolver 475), independent reassignment; state in `ext["audit"]`. |
| `tasks/audit.py` | Registered two-player `audit` kind: split specs, normalized readings and verdicts, plus solo calibration. |
| `tasks/base.py` | `TaskKind`, `TaskContent` and the Task-kind registry (`register_task_kind`, `task_kind`). |
| `tasks/steps.py` | Solo step kinds (`sort_list`, `lookup_value`): the dependent-solo family. |
| `tasks/graph.py` | `Task` (a placed instance), `TaskGraph` (dependencies), `build_task_graph` (seeded). |
| `tasks/trial.py` | `run_solo_trial(s)`: one Task alone, outside a game (solo calibration). |
| `rules/__init__.py` | `default_modules()` / `default_rules()`: the list of modules a normal game uses. |
| `engine.py` | `Game`: the Round loop, asking seats (with the Invalid Action retry), contexts, recording. |
| `meeting.py` | Meeting flow, secret vote, `tally()`, Ejection. |
| `vision.py` | What a player perceives of its own room. |
| `observations.py` | `Observation`, `ActionContext`, `MeetingContext`, `Statement`: everything a seat may know. |
| `transcript.py` | JSONL game record: header, ground-truth events, per-player observations, result. |
| `seats/base.py` | `SeatController`: the interface for scripted bots now and live Model Harnesses later. |
| `seats/bots.py` | `ScriptedBot`, `IdleBot`, `PlaybookBot` (fixed script, for tests). |
| `seats/registry.py` | Named controller factories and the `--bots` spec parser. |
| `seats/live.py` | `HarnessSeat`: a Seat played by a live Model through the Neutral Harness; `register_harness` and `parse_live_arg` pick a Seat's Harness from `live:<model>[@<harness>]`. |
| `seats/agent_loop.py` | `AgentLoopSeat`: a live Seat played through the agent-loop Harness (the decision loop, notes, `harness.notes` events). |
| `harness/neutral.py` | **The Neutral Harness**: system prompt, tools, memory format, request building and reply parsing. Everything a Model sees is defined here. |
| `harness/agent_loop.py` | **The agent-loop Harness** (#27), an alternative to the Neutral Harness: a lean port of the OpenAI Agents SDK run loop. Its prompt, tools and request layout; see "Alternative Harnesses" below. |
| `harness/summarizer.py` | Fixed, deterministic, pure-code condensation of older public Meeting observations; private observations stay verbatim. |
| `harness/provider.py` | Provider-adapter interface: `HarnessRequest` → `ProviderResponse` with normalized `Usage`; `@register_provider`. |
| `harness/anthropic_adapter.py` | Anthropic Messages API adapter (Fable 5.1, Opus 5.5): effort, prompt caching, usage incl. thinking tokens. |
| `harness/openai_adapter.py` | OpenAI Responses API adapter (Astra, Sol); also the base for OpenAI-compatible Responses APIs. |
| `harness/xai_adapter.py`, `harness/meta_adapter.py` | Grok 4.7 and Muse Wonder: `OpenAIAdapter` with the lab's `base_url`, key and usage quirks. |
| `harness/google_adapter.py` | Gemini API adapter (Argon) via `google-genai`: `thinking_level`, implicit caching, thoughts as output. |
| `harness/models.toml`, `harness/pricing.py` | Model IDs, provider, effort and prices per 1M tokens; `ModelSpec.cost(usage)`. |
| `harness/ledger.py` | `Ledger`: every paid call goes through `Ledger.send` (spend cap check, call, price, record). |
| `harness/live.py` | `play_live(...)`: the live gate, wiring live Seats into a game, clean abort, the `cost` record. |
| `runner.py` | `new_game(config, bots)` / `play(...)`: builds the scenario and seats the bot lineup in its seat order. |
| `cli.py` | `zantine play` (`--live`, `--spend-cap` for live Seats), `zantine replay`. |
| `replay/model.py` | Demo replay viewer: transcript records → Round-by-Round view model (map positions, moves, bodies, log, Meetings). Reads transcripts only. |
| `replay/render.py`, `replay/viewer.html` | Writes the view model into one self-contained HTML page (inline JS/CSS, no network). |

## Game loop

`Game.run()` → `start()` (header, `on_setup` hooks, role and room observations) → repeat `play_round()`:

1. Ask every living seat for an action from the same start-of-Round context (`Game.ask` handles the one retry, then forfeit).
2. Run every registered resolver in `order` with a shared `RoundContext`.
3. Check win conditions; if a Meeting was requested and the game isn't over, run it (`meeting.run_meeting`) and check again.
4. Enforce the round cap; on game over, write `game_over` observations and the `result` record.

## Extension points

**New rules (Task kinds, Room Chat, Crisis, Sabotage, ...)**: write a `GameModule` in its own file (e.g. `rules/tasks.py`) and add one line to `default_modules()`. A module can:

- `rules.register_action(cls)`: new `Action` subclasses with a unique `kind`; prefix with the module name if it could collide (e.g. `tasks.work`).
- `rules.add_resolver(order, name, fn)`: a step run every Round. Order bands: 100 kills, 200 movement, 300 meeting requests, **400–899 free for plug-ins**, 900 cooldowns, 1000 snapshots. Use `rc.acting(MyAction)` to get surviving actors; set `rc.meeting` to request a Meeting.
- `rules.add_win_condition(order, name, fn)`: core uses 200 (parity), Crisis 300, Tasks 400; return `(Team, reason)` or `None`. Ejecting every Impostor is not a win (see `rules/containment.py`). `Team.NONE` is only used for a round cap with no Impostor in play.
- `place_tasks(game, graph)`: called by `TasksModule` before the board is initialized or briefs sent; append kind-specific Tasks to the common graph. Install modules that need the board after `TasksModule`.
- `on_setup(game)`, `on_meeting_end(game, meeting)` hooks.
- `context_extra(game, seat)`: data that appears in that seat's `ctx.extra[module_name]` (e.g. its assigned Tasks).
- `result_extra(game)`: data added to the transcript's `result` record under `result[module_name]` (e.g. Productivity).
- Keep module state in `game.state.ext[module_name]`, not new fields on `GameState`.
- Record ground truth with `game.event(type, **data)` and private views with `game.observe(seat, type, **data)`. Prefix new types with the module name (`tasks.graded`).
- Use `game.rng.fork("<module>")` for randomness.

`tests/test_registry.py::DanceModule` is a complete minimal example.

**New Task kinds (split-key, chain relay, audit, ...)**: the Task framework in `tasks/` has three layers.

1. *Kind* (`tasks/base.py`): subclass `TaskKind`, set a unique `name` (and `seats` if more than one player must take part), implement `generate(rng) -> TaskContent` (content and hidden answer key from the seed only), `grade(content, answer) -> bool` and `solve(brief)` (a reference solution from the brief alone, used by scripted bots and tests). Override `brief(content, slot)` when assignees see different information, and `solo_brief(content)` for solo calibration. Decorate with `@register_task_kind` and import the module from `tasks/__init__.py`.
2. *Instance and graph* (`tasks/graph.py`): a `Task` places one `TaskContent` in a game (id, room, `assignees`, `requires`). `TaskGraph` holds every Task in generation order; prerequisites must come earlier, so it is acyclic by construction. `waiting_on(id)` gives unfinished prerequisites, `counted()` the real Tasks whose kind has `required_for_completion = True` (optional audits are excluded). Ids come from `task_ids(rng, n)` (`T10`–`T99`, never sequential, so they don't reveal the crew size); use it for new Tasks too. `build_task_graph` places solo steps (`seats == 1`); multi-seat modules use `place_tasks(game, graph)` to append Tasks with several assignees before briefs are sent. `TaskGraph.add` enforces unique ids and earlier prerequisites. Use `task_ids(rng, n, exclude=(t.id for t in graph))` to allocate additional ids without collisions.
3. *Game rules* (`rules/tasks.py`): `TasksModule` owns the `TaskBoard` in `state.ext["tasks"]` (graph, per-seat stats, what each seat was told). `tasks.work` grades through the kind's `grade` and rejects kinds whose `work_actions` omit `tasks.work`; blocking, reassignment, the win and Productivity are kind-independent. Kind-specific timing or handoff rules belong in their own `GameModule` that reads and updates the same board (`rules.tasks.board(state)`). `resolve_work` saves the start-of-Round completion set in `rc.scratch["tasks.done_before"]` for later Task resolvers. Kinds with `manages_reassignment = True` handle their own Meeting reassignment (audits need independent assignees). `Task.closed` prevents reassignment of delivered work even if its hidden grade is wrong.

**Chain relay hooks** (`rules/relay.py`): `parts(state)` / `part(state, task_id)` return hidden `Part` state (serial, holder or dropped room, leg, delivery, tamper flag/history). `tamper(game, task_id, seat)` corrupts an undelivered part and records `relay.tampered`, with no player observations. The calling Sabotage module owns authorization. An Audit module may inspect this state and issue its own observations; never pass a `Part` into a player context. Relay contexts derive progress from `relay.*` observations; setup, handoffs, and carrier replacement explicitly disclose only the relevant private progress. Delivery records the real grade but sends an identical receipt for intact and tampered parts. `ChainRelay.bind` places three rooms into generated content; `solve` returns each carrier's action plan. This coordination-only kind raises `NotImplementedError` for a standalone solo trial.

Hooks for later rules: `Task.fake` marks a Fake Task (shown and worked like a real one, never counted toward Productivity or the crew win); Sabotage of a Shared Task belongs in that kind's `grade`. `tasks.graded` events carry `correct` and `fake`, so analysis can tell real work from fake work. `run_solo_trial(kind, seed, solver)` runs a fresh instance of any registered kind outside a game with any `solver(brief) -> answer` (a Model Harness for calibration); `run_solo_trials` repeats it over many seeds.

**Audit extension points** (`rules/audit.py`, `tasks/audit.py`): `TaskKind.auditable = True` opts a kind into audits. Its `audit_items(answer)` must normalize submissions and answer keys consistently with `grade` (equal item lists iff the answer is correct), with at least two answer-key items so both players receive a nonempty half. `TasksModule` publishes graded submissions in `RoundContext.scratch[GRADED]` (`GRADED = "tasks.graded"`) as `(seat, task_id, answer, correct)`. Other Task resolvers may append the same tuple before the audit resolver (475); blocked work must not publish a submission. The audit module records all submissions in `audit_board(state).submissions`, but only shows an audit's latest reading through `game.observe`, never its correctness. Checks resolve against the pre-Round observed reading before new readings are delivered.

`place_audit(game, target_id, auditor, holder, room)` places a new optional audit on any auditable Task at setup or during play, rejecting self-audits (including prior submission authors). It uses `TaskGraph.add` and seeded unused ids, assigns each player a private half, and catches a new auditor up on submitted readings. `audit_board(state).checks` and `audit.checked` events retain verdicts tied to individual submission numbers, so a later Verification Protocol can decide when checked work counts. The current optional rule leaves target completion untouched. Audit kinds set `required_for_completion = False` and `manages_reassignment = True`; their module preserves independent assignees and invalidates stale partner shares after Meetings. No engine changes are needed to add audit-capable kinds.

**New seat controllers (live Models, other Harnesses)**: subclass `SeatController` (`choose_action`, `speak`, `vote`, and `describe()` for the transcript header, e.g. the model ID). Put it in its own module under `seats/`, register a factory with `@register_controller("name")`, and import that module at the bottom of `seats/registry.py`; it then works as `--bots name:arg`. A Harness turns `ActionContext` / `MeetingContext` into a prompt and parses the reply with `game.rules.parse_action(dict)`; unparseable output should be returned as `Invalid(reason, raw)` so the engine's retry/forfeit applies and is logged.

**Live Models (the Neutral Harness)**: four separate layers, so each can change alone.

1. *Harness* (`harness/neutral.py`, model-agnostic). Builds a `HarnessRequest` from a context: `system` (the one `SYSTEM_PROMPT`), `tools` (one strict JSON-Schema tool per registered action kind, plus `speak`, `vote`, `skip_vote`; all offered on every call), `memory` and `tail`. Memory is the Seat's observations, one rendered line each (plug-in observation types get a generic `[Round r] type: {json}` line), condensed by `harness/summarizer.py` and cut into fixed blocks of `MEMORY_BLOCK_LINES` (8); between compactions, full blocks are a cacheable prefix. `tail` holds the unfinished block, the current state (incl. `ctx.extra` per module), any retry reason and the request. Replies are parsed into an `Action` / speech / vote; anything unusable becomes `Invalid(reason)`, so the engine's one retry then forfeit applies (speech, which the engine doesn't retry, gets the same rule inside `HarnessSeat` and is logged as `harness.invalid_speech`). A plug-in action can override its tool schema with a `tool_parameters` ClassVar; its tool description is its class docstring. Bump `HARNESS_VERSION` when anything a Model sees changes. An alternative Harness is another module producing `HarnessRequest`s, reusing layers 2–4 unchanged (see "Alternative Harnesses" below). The engine-side log summarizer runs in `memory_lines` before `split_memory` on action, speech and vote requests (including retries); see below.
2. *Provider adapters* (`harness/provider.py`). `ProviderAdapter.send(HarnessRequest) -> ProviderResponse` makes exactly one call and returns tool calls, text, a normalized `stop_reason` and `Usage(input_tokens, cached_input_tokens, cache_write_tokens, output_tokens, thinking_tokens)`. `input_tokens` excludes both cache fields and `output_tokens` includes thinking, so `cost` is a plain dot product; adapters for APIs that count differently (e.g. cached tokens inside prompt tokens) must convert. Errors become `ProviderError(fatal=...)`: fatal ones (bad key, unknown model, bad request) stop the run; transient ones (rate limit, overload, network, after the SDK's own retries) become an Invalid Action. New providers: a module under `harness/`, `@register_provider("name")`, imported at the bottom of `provider.py`; keys from env vars only via `live_api_key(key_env)`, which refuses unless `ZANTINE_LIVE=1`. A lab with an OpenAI-compatible Responses API needs only a subclass of `OpenAIAdapter` setting `provider`, `key_env`, `base_url` and, if reasoning is reported outside `output_tokens`, `reasoning_in_output = False` (see `xai_adapter.py`). An adapter may put the provider's own charge for a call in `ProviderResponse.raw["provider_cost_usd"]`; the ledger logs it next to ours.
3. *Pricing and effort* (`harness/models.toml`, `harness/pricing.py`). One entry per Model: `provider`, `effort` (the neutral level for this run: `low`/`medium`/`high`, default `medium`), `efforts` (each neutral level -> the lab's own value; adapters send `ModelSpec.effort_setting`), `input`, `cached_input`, `cache_write`, `output` (USD per 1M tokens), `max_output_tokens`, optional `price_valid_until` (expired prices are refused) and optional `long_context` tier (when a call's prompt is over `above_input_tokens`, the whole call is billed at the tier's prices). Effort is a per-run parameter: `ModelSpec.at_effort(level)`, `play_live(effort=...)` or `zantine play --effort`; the effort sweep (#26) uses these, never per-adapter code. Per-provider details and what is unverified: [`model-pool.md`](model-pool.md).
4. *Ledger and spend cap* (`harness/ledger.py`). `Ledger.send(adapter, request, purpose=..., phase=..., seat=..., round=..., harness=..., harness_version=...)` refuses a call whose worst-case cost (whole request at the dearest input rate plus the full output cap) would take total spend past the cap, raising `SpendCapReached`; otherwise it calls, prices and records a `CallRecord`. Non-seat callers (Claim extractor #13, Jev #15) use the same ledger with their own `purpose`, so one cap covers the whole run; pass one `Ledger` to several `play_live` calls to cap a multi-game run.

**Fixed log summarization** (`harness/summarizer.py`, Neutral Harness v3; the agent-loop Harness doesn't use it): no Model, provider tokenizer, network, randomness, player-written notes or paid call. The only inputs are the Seat's observation log and the public roster. `memory_lines(..., summarize=False)` renders the original log for offline comparison; live requests always summarize. The transcript and original observations are never changed.

- **Classification:** observations have no visibility field, so `PUBLIC_TYPES` is an explicit allowlist of types whose producers broadcast identical data to every living Seat: `meeting_called`, `statement`, `vote_result`, `ejected`, `returned_to_meeting_room` (in `meeting.py`), `crisis.started`, `crisis.resolved`, `crisis.failed` (in `rules/crisis.py`), and `game_over` (in `engine.py`). Everyone still alive has received the same completed public history. During a Meeting the history grows with each statement.
- **Private by default:** every other type, including `role_assigned`, room snapshots, kill witnesses, Invalid Actions, Task assignments/grades/blocking/reassignment, `crisis.fix_attempted`, Room Chat and unknown plug-in types. Room-scoped information is private even when several players share it. Private lines are never condensed, clipped or dropped, and their relative order and multiplicity stay unchanged. A new plug-in type must remain private unless its producer is verified to broadcast identical data to all living Seats.
- **Trigger:** sum `estimate_tokens(rendered_line + "\n")` over public observations only; above 6,000 estimated tokens, condense older completed Meetings. The fixed estimate is `ceil(characters / 4)`, for reproducible size comparisons rather than provider billing. Private history cannot change when the public log compacts.
- **Retention:** the in-progress Meeting and most recent completed Meeting remain verbatim. The next three completed Meetings become digests: the original call/dead list, each speaker's statements clipped to 100 characters apiece in speaking order (grouped by speaker), and the anonymous tally/Ejection. Older Meetings become one-line briefs: call/dead list, statement counts, who mentioned whom by name or `seat N`, and tally/Ejection. Mentions indicate neither accusations nor truth; quoted text remains a player's Claim. Other public announcements remain verbatim. A vote result marks a Meeting complete.
- **Cache behavior:** each summary replaces its Meeting's first public entry; private entries stay in sequence. A digest stays byte-identical until it becomes a brief, and a brief never changes. Compaction can invalidate blocks from the changed Meeting onward; between compactions the existing eight-line blocks remain a cacheable prefix. The extra digest-to-brief transition retains more detail about recent Meetings while keeping long histories small.
- **Size envelope:** recent speech is limited by the Talk Budget, Seat count and Meeting passes; older Meetings add only briefs. Private observations remain bounded by the game's Round cap and observation producers, not by deletion. Offline tests check full requests below 20,000 estimated tokens throughout a default 30-Round game with ten full-budget emergency Meetings and through every pass of a synthetic 30-Meeting log. The latter's final memory falls from about 126,000 to 11,000 estimated tokens. This is a tested envelope for these rules/budgets, not a hard truncation cap: larger configurations or plug-ins emitting more private information require a larger context envelope while preserving that information.

**Alternative Harnesses** (roadmap P8; first one #27). A Harness is selected per Seat in the bot spec, `live:<model>@<harness>` (default `neutral`), and recorded in the transcript header as the Seat's `controller.harness` (`neutral-v5`, `agent-loop-v1`). Only layer 1 changes: every Harness sends through the same provider adapter, effort setting, prices, `Ledger` and spend cap, and every call is a `harness.call` event with the same fields plus `harness` and `harness_version`. So token and cost logs for one Model compare directly across Harnesses, even in one game; the `cost` record also totals each Harness (`harnesses`). New Harness: a request-building module under `harness/` and a `HarnessSeat` subclass under `seats/` that sets `harness_name` / `harness_version`, is decorated with `@register_harness`, sends with `self._send(...)` (which passes both to the ledger) and is imported at the bottom of `seats/live.py`.

*Why an in-repo port and not the SDKs themselves.* The OpenAI Agents SDK, LangGraph and the Claude Agent SDK each bring their own model clients: LangGraph's `create_react_agent` uses LangChain chat-model wrappers per lab; the Agents SDK calls OpenAI (other labs via LiteLLM) and exports traces to OpenAI by default; the Claude Agent SDK runs the Claude Code CLI as a subprocess with its own tools and serves only Anthropic Models. Using any of them as-is would bypass our adapters, so pricing, usage normalization (cached and thinking tokens), the Ledger and the spend cap would no longer be shared, and the Claude Agent SDK could not play the other six Models at all. Wrapping our adapters inside one (e.g. a custom Agents SDK `Model`) would still add an async runtime, tracing to disable, and a message history our single-turn adapters don't send. Porting one framework's loop into a few hundred lines of our own keeps layers 2–4 byte-for-byte shared and every test offline and deterministic. The OpenAI Agents SDK's `Runner` loop is the one ported because it is the smallest well-defined loop of the candidates (call the model, run tools, stop on a stop tool or final output, give up after `max_turns`), with no graph or subprocess to emulate.

*The agent-loop Harness* (`harness/agent_loop.py`, `seats/agent_loop.py`, v1). Settings that differ from the Neutral Harness (v5), and why:

| Setting | Neutral Harness | agent-loop Harness | Why |
| --- | --- | --- | --- |
| Model calls per decision | exactly 1 | 1 to `MAX_TURNS` (4) | The SDK's run loop: the model may act, see the result and call again. 4, not the SDK's default `max_turns` of 10, because each step resends the whole prompt; the spend cap still covers every step. |
| End of a decision | the one tool call | the first usable game-tool call (`tool_use_behavior=StopAtTools` with the game tools as stop tools); a reply with no tool call ends it unanswered, like the SDK's final text output; running out of turns (`MaxTurnsExceeded`) is an Invalid Action | Matches how a Runner run ends. |
| Unusable tool call (wrong tool for the request, bad arguments, several calls) | Invalid Action, so the engine retries once | fed back to the model as that step's result, and the loop goes on | The SDK's default `failure_error_function` returns tool errors to the model. The engine's one retry then forfeit still applies to whatever the loop returns (incl. game-illegal actions such as a move to a non-adjacent room), and speech keeps the one-retry rule. |
| Tools | one strict tool per action kind, `speak`, `vote`, `skip_vote` | the same, plus `update_notes` (not a stop tool) | An agent-written memory, a common addition to framework agents; it is what gives the loop something to do besides answer. |
| Memory | the Seat's observations, older Meetings condensed by `harness/summarizer.py` | the Seat's observations verbatim, never condensed, plus the Seat's notes (at most 1,500 characters, replaced by each `update_notes`, shown in every later request) | SDK sessions keep the full history; the notes are the agent's own summary instead of the fixed one. Notes are logged as `harness.notes` events and never reach another player. |
| System prompt | `neutral.SYSTEM_PROMPT` | the same rules text (`neutral.RULES_PROMPT`) and tool note; no condensed-log note; a response section describing steps, notes and the turn limit | The game is described identically; only the scaffolding text changes. |
| Earlier steps in a decision | none | written into the tail as a numbered list (call echoed, arguments clipped to 200 characters, result), with calls left | Adapters stay single-turn: no native tool-result messages, so no adapter, cache layout or price changes. The cost: thinking is not carried between steps, and the step list is plain text. |

Unchanged: the provider adapter and its settings (effort, strict tools, `tool_choice` `auto`, parallel calls off, cache breakpoints on the system prompt and last memory block), `MEMORY_BLOCK_LINES` blocks as the cacheable prefix, the state lines and request wording (`neutral.action_state`, `meeting_state`, `ACTION_ASK`, `speak_ask`, `VOTE_ASK`), reply parsing (`neutral.parse_*`), pricing, the Ledger and spend cap, the live gate, and all game rules. Every loop step is its own `harness.call` with the decision's engine `attempt`, so calls per decision are visible in the log. Like the Model, a Seat's Harness is ground truth: it appears only in the header and events, never in any player's context (Hidden Identity).

`harness/live.py:play_live` builds the game exactly as `runner.new_game` does (same scenario, seat order and bot RNG streams), binds each `HarnessSeat` to the game's `Rules` and event recorder, and writes every `CallRecord` as a `harness.call` event. On `SpendCapReached` or a fatal `ProviderError` it stops cleanly: the transcript so far is kept, followed by an `aborted` record. It always ends with a `cost` record.

Anthropic specifics (`anthropic_adapter.py`): `output_config.effort` from `models.toml` (Fable 5.1 at `medium`); `thinking` omitted (always on; disabling is a 400); `tool_choice` `auto` with parallel calls off (forced tool choice is a 400 on Fable 5.1, so the prompt asks for exactly one call and tools are `strict`); cache breakpoints at the end of the system prompt (covers tools + system, shared by every Seat on the Model) and on the last memory block; thinking tokens from `usage.output_tokens_details.thinking_tokens`. No refusal `fallbacks`: they would let a different Model play the Seat, so a refusal is an Invalid Action instead.

OpenAI-compatible specifics (`openai_adapter.py`, and xAI/Meta through it): Responses API; system prompt in `instructions`, memory blocks then the tail as `input_text` parts of one user message (implicit prefix caching); `reasoning.effort`; function tools `strict`, `tool_choice` `auto`, `parallel_tool_calls` off; `store` off. Usage: cache reads and writes are subtracted from `input_tokens`; xAI's `reasoning_tokens` are added to `output_tokens` (OpenAI's and Meta's already include them). Google specifics (`google_adapter.py`): `system_instruction`, memory + tail as parts of one user turn, `thinking_config.thinking_level`, `function_calling_config` `AUTO`, `parameters_json_schema`, automatic function calling off; output = `candidates_token_count` + `thoughts_token_count`.

**Setup generation**: `scenario.build_scenario(config, rng)` returns the `Scenario` (`map`, `names`, `roles`, `seat_order`); the engine only consumes it, via `Game(..., scenario=)` or by building one from `rng.fork("scenario")`. `Game` takes controllers already in seat order; `Scenario.seat(lineup)` rearranges a lineup (spec order) into seats, which `runner.new_game` does for you. Add new generated content (e.g. Task codes) with its own `rng.fork(...)` so existing parts of a seed stay unchanged, and bump `SCENARIO_VERSION` whenever a change alters what an existing seed generates. Tests that need known room names use `tests/helpers.make_game`, which plays on `classic_scenario()` (`tiny_map()`, colour names, identity seat order).

**Analysis (Claim Audit, metrics, graphs)**: read transcripts; don't import engine internals. Ground truth is in `event` records, each seat's view in `observation` records.

**Replay viewer** (`zantine replay`, for demos, never scoring): unknown event/observation types appear as generic log lines, and a record with a speaker (`seat`/`speaker`/`sender`) plus `text`/`message` is shown as speech, so new modules replay with no viewer changes. Roles are hidden until the viewer's toggle is on: records whose data has a `role`/`killers`-style key, and actions or events whose name contains `kill`, `sabotage`, `crisis` or `vent`, are hidden or shown without the actor. For a nicer map, a header `map` may carry `layout: {room: [x, y]}`; otherwise rooms sit on a circle. Teach `replay/model.py` a new type only if the generic line isn't enough (e.g. it moves players or leaves bodies).

## Transcript format

JSON Lines, keys sorted, one record per line, each with `seq` and `kind`:

- `header`: `schema`, `seed`, `scenario_version`, `config`, `map`, `modules`, `seats` (`seat`, `name`, `role`, `controller`).
- `event`: ground truth, `{round, type, data}`. Core types: `action`/`vote` (every attempt, with `valid`/`reason`), `kill`, `move`, `meeting_requested`, `meeting_started`, `bodies_cleared`, `speaking_order`, `statement`, `vote_tally`, `ejection` (with role), `containment` (`meeting`, `impostors`), `meeting_ended`, `game_over`.
- `observation`: `{round, seat, type, data}`, exactly what that seat was told. Types are listed in `observations.py`.
- `result`: `winner` (`crew`, `impostors`, or `none`), `reason`, `rounds`, `meetings`, `killed`, `ejected`, `survivors`, plus one key per module with `result_extra`. `containment`: `contained`, `round`, `meeting`. `tasks`: `productivity` (team), `done`, `total`, and `seats` (per **Seat**: `assigned`, `done`, `wrong`, `blocked`, `productivity`).

Audit records: events `audit.placed`, `audit.shared`, `audit.checked`, `audit.reassigned`; observations `audit.assigned`, `audit.reading`, `audit.spec_shared`, `audit.shared`, `audit.result`, `audit.closed`, `audit.partner`, `audit.unassigned` (and `tasks.reassigned` for replacement briefs). See [`rules.md`](rules.md#audit-tasks) for data and visibility.

Task records: event `tasks.generated` (every Task with its answer key, at setup), `tasks.graded` (`seat`, `task`, `correct`, `fake`), `tasks.blocked` (`seat`, `task`, `waiting_on`), `tasks.reassigned` (`task`, `from_seat`, `to_seat`); observations `tasks.assigned` (`tasks`: the seat's briefs), `tasks.graded` (`task`, `correct`), `tasks.blocked` (`task`, `waiting_on`), `tasks.reassigned` (`task`, `previous_assignee`).
Relay records: ground-truth `relay.generated` (initial parts with serial and tamper state), `relay.picked_up`, `relay.handed_off`, `relay.handoff_failed`, `relay.dropped` (room/reason), `relay.carrier_replaced`, `relay.tampered` (serial and actor), and `relay.delivered` (real `correct` / `tampered`), plus `tasks.graded` on delivery. Private observations: `relay.assigned`, `relay.picked_up`, `relay.handed_off`, `relay.received`, `relay.handoff_failed`, `relay.delivered`, `relay.took_over`, `relay.partner_replaced`. Progress observations include `status` and `next` (action/room). `result["relay"]["relays"]` contains each part's final ground truth. None of these private observations or contexts includes the serial, tamper flag, or real delivery grade.

- Live runs only (`harness/live.py`):
  - event `harness.call`: one per model call: `model`, `purpose`, `phase`, `seat`, `round`, `attempt`, `ok`, `error`, `stop_reason`, `input_tokens`, `cached_input_tokens`, `cache_write_tokens`, `output_tokens`, `thinking_tokens`, `cost_usd`, `total_spent_usd`, `provider_cost_usd` (the provider's own charge, where its API reports one: xAI; else null), `harness` and `harness_version` (the Seat's Harness, e.g. `neutral`, 5; null for non-seat calls).
  - event `harness.invalid_speech`: `seat`, `model`, `meeting`, `pass_index`, `attempt`, `reason`, `forfeited`.
  - event `harness.notes` (agent-loop Harness only): `seat`, `model`, `round`, `phase`, `text` (the Seat's notes after an `update_notes` call).
  - `aborted` (instead of `result`, when the spend cap or a fatal API error stopped the game): `reason` (`spend_cap` / `provider_error`), `detail`, `round`, `meetings`.
  - `cost` (always last): `complete`, `seats` (per Seat: `controller`, `calls`, `failed_calls`, all token counts, `cost_usd`, `invalid_actions`, `forfeits`), `models` (per live Model: `provider`, `effort`, `effort_setting`, `calls`, `failed_calls`, all token counts incl. `thinking_tokens`, `cost_usd`), `harnesses` (per Harness label such as `neutral-v5`: `seats`, `calls`, `failed_calls`, all token counts, `cost_usd`), `game` (totals), `spend_cap_usd`, `run_spent_usd`.

Bump `transcript.SCHEMA_VERSION` when changing existing record shapes; adding new event/observation types does not need a bump.

## Conventions

- **Determinism.** Same seed + same controllers ⇒ byte-identical transcript. All randomness goes through `GameRng` forks; never use the global `random`, `hash()`, sets in output order, or wall-clock time in the engine. Iterate seats in seat order.
- **Hidden information.** Controllers only see context objects. Anything a player learns must go through `game.observe` so the transcript records it. Never put ground truth (roles, other rooms, Model identity) in a context.
- **Hidden Impostor Count.** Never show players the real number of Impostors or anything it can be derived from (config, crew size, Task count, sequential ids, Containment). Harness prompts say "0 to 2 Impostors" (`ctx.impostor_count`). `tests/test_hidden_count.py` scans every context and observation for leaks; keep it passing.
- **No network in tests.** `tests/conftest.py` blocks sockets for every test.
- **Live Model calls are opt-in.** Code that calls a paid API must refuse to run unless explicitly enabled (an explicit CLI flag plus `ZANTINE_LIVE=1`), read keys from the environment only, and go through a `Ledger` (spend cap). Tests that need a live call are marked `@pytest.mark.live` and skip unless `ZANTINE_LIVE=1` and `pytest --live`; they never run by default. Other tests use fake clients (`tests/fake_provider.py` for Anthropic; `tests/fake_providers.py` for the other providers, in each API's recorded response shape, and `fake_adapter(spec)` for any pool Model).
- **Small files.** One concern per module so parallel work rarely touches the same file.
- **Terminology.** Use the terms in `CONTEXT.md` exactly (Round, Seat, Meeting, Ejection, ...).
