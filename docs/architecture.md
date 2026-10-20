# Architecture

How the code is laid out, where new features plug in, and the conventions every change must follow. Game rules as implemented: [`rules.md`](rules.md). Terms: [`../CONTEXT.md`](../CONTEXT.md).

## Running

```sh
uv sync                                   # create .venv with dev tools
uv run zantine play --seed 7              # one 10-seat game with scripted bots
uv run zantine play --seed 7 --bots 'scripted*8,idle*2' --out runs/seed-7.jsonl
uv run zantine replay runs/seed-7.jsonl   # writes runs/seed-7.html (demo viewer, not scored)
uv run pytest                             # all tests (no network)
uv run ruff check && uv run ruff format --check

# One live Claude Fable 5.1 Seat with 9 bots. Paid: needs both opt-ins and a key.
ZANTINE_LIVE=1 ANTHROPIC_API_KEY=... uv run zantine play --seed 7 \
    --bots 'live:claude-fable-5-1,scripted*9' --live --spend-cap 10 --out runs/live-7.jsonl
ZANTINE_LIVE=1 uv run pytest --live       # the live smoke test (also paid)
```

## Module map (`src/zantine/`)

| Module | Responsibility |
| --- | --- |
| `config.py` | `GameConfig`: every tunable number (seats, Impostors, round cap, cooldowns, Talk Budget, ...). |
| `rng.py` | `GameRng`: the only source of randomness. `fork(label)` gives independent named streams. |
| `scenario.py` | Builds the `Scenario` (generated map, names, seat order, roles) from the seed; `classic_scenario()` is the fixed setup for tests. `SCENARIO_VERSION` is recorded in the header. |
| `map.py` | `GameMap` (rooms, corridors, meeting room) and the hard-coded `tiny_map()` used by `classic_scenario()`. |
| `state.py` | Ground-truth `GameState`, `PlayerState`, `Body`. `GameState.ext` holds per-module state. |
| `roles.py` | `Role` (crewmate/impostor) and `Team` (winner). |
| `rules/actions.py` | `Action` base class and core actions. Each action validates itself. `Invalid` = unacceptable response. |
| `rules/registry.py` | `Rules`, `GameModule`, `RoundContext`, `MeetingRequest`: the plug-in surface. |
| `rules/core.py` | `CoreModule`: kill, move, report, emergency Meeting, cooldowns, snapshots. |
| `rules/crisis.py` | `CrisisModule`: Impostor-triggered **Crisis** (`crisis.trigger` / `crisis.fix`, resolver 500, win condition 300). State in `ext["crisis"]`. |
| `rules/win.py` | Core win conditions (Impostors ejected, parity). |
| `rules/tasks.py` | `TasksModule`: assigns Tasks, `tasks.work`, private grading, Task Graph blocking, reassignment after Meetings, the `tasks_done` win, Productivity in the result. |
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
| `seats/live.py` | `HarnessSeat`: a Seat played by a live Model through the Neutral Harness. |
| `harness/neutral.py` | **The Neutral Harness**: system prompt, tools, memory format, request building and reply parsing. Everything a Model sees is defined here. |
| `harness/provider.py` | Provider-adapter interface: `HarnessRequest` → `ProviderResponse` with normalized `Usage`; `@register_provider`. |
| `harness/anthropic_adapter.py` | Anthropic Messages API adapter (effort, prompt caching, usage incl. thinking tokens). |
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
- `rules.add_win_condition(order, name, fn)`: core uses 100 and 200, Crisis 300, Tasks 400; return `(Team, reason)` or `None`.
- `on_setup(game)`, `on_meeting_end(game, meeting)` hooks.
- `context_extra(game, seat)`: data that appears in that seat's `ctx.extra[module_name]` (e.g. its assigned Tasks).
- `result_extra(game)`: data added to the transcript's `result` record under `result[module_name]` (e.g. Productivity).
- Keep module state in `game.state.ext[module_name]`, not new fields on `GameState`.
- Record ground truth with `game.event(type, **data)` and private views with `game.observe(seat, type, **data)`. Prefix new types with the module name (`tasks.graded`).
- Use `game.rng.fork("<module>")` for randomness.

`tests/test_registry.py::DanceModule` is a complete minimal example.

**New Task kinds (split-key, chain relay, audit, ...)**: the Task framework in `tasks/` has three layers.

1. *Kind* (`tasks/base.py`): subclass `TaskKind`, set a unique `name` (and `seats` if more than one player must take part), implement `generate(rng) -> TaskContent` (content and hidden answer key from the seed only), `grade(content, answer) -> bool` and `solve(brief)` (a reference solution from the brief alone, used by scripted bots and tests). Override `brief(content, slot)` when assignees see different information, and `solo_brief(content)` for solo calibration. Decorate with `@register_task_kind` and import the module from `tasks/__init__.py`.
2. *Instance and graph* (`tasks/graph.py`): a `Task` places one `TaskContent` in a game (id, room, `assignees`, `requires`). `TaskGraph` holds every Task in id order; prerequisites must come earlier, so it is acyclic by construction. `waiting_on(id)` gives unfinished prerequisites, `counted()` the Tasks that count toward Productivity. `build_task_graph` currently only places solo steps (`seats == 1`); multi-seat kinds extend it (or add Tasks to the same `TaskGraph`) to choose several assignees.
3. *Game rules* (`rules/tasks.py`): `TasksModule` owns the `TaskBoard` in `state.ext["tasks"]` (graph, per-seat stats, what each seat was told). `tasks.work` grades through the kind's `grade`; blocking, reassignment, the win and Productivity are kind-independent. Kind-specific timing or handoff rules belong in their own `GameModule` that reads and updates the same board (`rules.tasks.board(state)`).

Hooks for later rules: `Task.fake` marks a Fake Task (shown and worked like a real one, never counted toward Productivity or the crew win); Sabotage of a Shared Task belongs in that kind's `grade`. `tasks.graded` events carry `correct` and `fake`, so analysis can tell real work from fake work. `run_solo_trial(kind, seed, solver)` runs a fresh instance of any registered kind outside a game with any `solver(brief) -> answer` (a Model Harness for calibration); `run_solo_trials` repeats it over many seeds.

**New seat controllers (live Models, other Harnesses)**: subclass `SeatController` (`choose_action`, `speak`, `vote`, and `describe()` for the transcript header, e.g. the model ID). Put it in its own module under `seats/`, register a factory with `@register_controller("name")`, and import that module at the bottom of `seats/registry.py`; it then works as `--bots name:arg`. A Harness turns `ActionContext` / `MeetingContext` into a prompt and parses the reply with `game.rules.parse_action(dict)`; unparseable output should be returned as `Invalid(reason, raw)` so the engine's retry/forfeit applies and is logged.

**Live Models (the Neutral Harness)**: four separate layers, so each can change alone.

1. *Harness* (`harness/neutral.py`, model-agnostic). Builds a `HarnessRequest` from a context: `system` (the one `SYSTEM_PROMPT`), `tools` (one strict JSON-Schema tool per registered action kind, plus `speak`, `vote`, `skip_vote`; all offered on every call), `memory` and `tail`. Memory is the Seat's observations, one rendered line each (plug-in observation types get a generic `[Round r] type: {json}` line), cut into fixed blocks of `MEMORY_BLOCK_LINES`; full blocks never change, so they are a cacheable prefix. `tail` holds the unfinished block, the current state (incl. `ctx.extra` per module), any retry reason and the request. Replies are parsed into an `Action` / speech / vote; anything unusable becomes `Invalid(reason)`, so the engine's one retry then forfeit applies (speech, which the engine doesn't retry, gets the same rule inside `HarnessSeat` and is logged as `harness.invalid_speech`). A plug-in action can override its tool schema with a `tool_parameters` ClassVar; its tool description is its class docstring. Bump `HARNESS_VERSION` when anything a Model sees changes. An alternative Harness (#27) is another module producing `HarnessRequest`s, reusing layers 2–4. The engine-side log summarizer (#11) plugs into memory rendering (`memory_lines` / `split_memory`).
2. *Provider adapters* (`harness/provider.py`). `ProviderAdapter.send(HarnessRequest) -> ProviderResponse` makes exactly one call and returns tool calls, text, a normalized `stop_reason` and `Usage(input_tokens, cached_input_tokens, cache_write_tokens, output_tokens, thinking_tokens)`. `input_tokens` excludes both cache fields and `output_tokens` includes thinking, so `cost` is a plain dot product; adapters for APIs that count differently (e.g. cached tokens inside prompt tokens) must convert. Errors become `ProviderError(fatal=...)`: fatal ones (bad key, unknown model, bad request) stop the run; transient ones (rate limit, overload, network, after the SDK's own retries) become an Invalid Action. New providers (#10): a module under `harness/`, `@register_provider("name")`, imported at the bottom of `provider.py`; keys from env vars only, and the factory must refuse unless `ZANTINE_LIVE=1`.
3. *Pricing* (`harness/models.toml`). One entry per Model: `provider`, `effort`, `input`, `cached_input`, `cache_write`, `output` (USD per 1M tokens), `max_output_tokens`, optional `price_valid_until` (expired prices are refused). Effort sweeps (#26) vary `effort` here.
4. *Ledger and spend cap* (`harness/ledger.py`). `Ledger.send(adapter, request, purpose=..., phase=..., seat=..., round=...)` refuses a call whose worst-case cost (whole request at the dearest input rate plus the full output cap) would take total spend past the cap, raising `SpendCapReached`; otherwise it calls, prices and records a `CallRecord`. Non-seat callers (Claim extractor #13, Jev #15, summarizer #11) use the same ledger with their own `purpose`, so one cap covers the whole run; pass one `Ledger` to several `play_live` calls to cap a multi-game run.

`harness/live.py:play_live` builds the game exactly as `runner.new_game` does (same scenario, seat order and bot RNG streams), binds each `HarnessSeat` to the game's `Rules` and event recorder, and writes every `CallRecord` as a `harness.call` event. On `SpendCapReached` or a fatal `ProviderError` it stops cleanly: the transcript so far is kept, followed by an `aborted` record. It always ends with a `cost` record.

Anthropic specifics (`anthropic_adapter.py`): `output_config.effort` from `models.toml` (Fable 5.1 at `medium`); `thinking` omitted (always on; disabling is a 400); `tool_choice` `auto` with parallel calls off (forced tool choice is a 400 on Fable 5.1, so the prompt asks for exactly one call and tools are `strict`); cache breakpoints at the end of the system prompt (covers tools + system, shared by every Seat on the Model) and on the last memory block; thinking tokens from `usage.output_tokens_details.thinking_tokens`. No refusal `fallbacks`: they would let a different Model play the Seat, so a refusal is an Invalid Action instead.

**Setup generation**: `scenario.build_scenario(config, rng)` returns the `Scenario` (`map`, `names`, `roles`, `seat_order`); the engine only consumes it, via `Game(..., scenario=)` or by building one from `rng.fork("scenario")`. `Game` takes controllers already in seat order; `Scenario.seat(lineup)` rearranges a lineup (spec order) into seats, which `runner.new_game` does for you. Add new generated content (e.g. Task codes) with its own `rng.fork(...)` so existing parts of a seed stay unchanged, and bump `SCENARIO_VERSION` whenever a change alters what an existing seed generates. Tests that need known room names use `tests/helpers.make_game`, which plays on `classic_scenario()` (`tiny_map()`, colour names, identity seat order).

**Analysis (Claim Audit, metrics, graphs)**: read transcripts; don't import engine internals. Ground truth is in `event` records, each seat's view in `observation` records.

**Replay viewer** (`zantine replay`, for demos, never scoring): unknown event/observation types appear as generic log lines, and a record with a speaker (`seat`/`speaker`/`sender`) plus `text`/`message` is shown as speech, so new modules replay with no viewer changes. Roles are hidden until the viewer's toggle is on: records whose data has a `role`/`killers`-style key, and actions or events whose name contains `kill`, `sabotage`, `crisis` or `vent`, are hidden or shown without the actor. For a nicer map, a header `map` may carry `layout: {room: [x, y]}`; otherwise rooms sit on a circle. Teach `replay/model.py` a new type only if the generic line isn't enough (e.g. it moves players or leaves bodies).

## Transcript format

JSON Lines, keys sorted, one record per line, each with `seq` and `kind`:

- `header`: `schema`, `seed`, `scenario_version`, `config`, `map`, `modules`, `seats` (`seat`, `name`, `role`, `controller`).
- `event`: ground truth, `{round, type, data}`. Core types: `action`/`vote` (every attempt, with `valid`/`reason`), `kill`, `move`, `meeting_requested`, `meeting_started`, `bodies_cleared`, `speaking_order`, `statement`, `vote_tally`, `ejection` (with role), `meeting_ended`, `game_over`.
- `observation`: `{round, seat, type, data}`, exactly what that seat was told. Types are listed in `observations.py`.
- `result`: `winner`, `reason`, `rounds`, `meetings`, `killed`, `ejected`, `survivors`, plus one key per module with `result_extra`. `tasks`: `productivity` (team), `done`, `total`, and `seats` (per **Seat**: `assigned`, `done`, `wrong`, `blocked`, `productivity`).

Task records: event `tasks.generated` (every Task with its answer key, at setup), `tasks.graded` (`seat`, `task`, `correct`, `fake`), `tasks.blocked` (`seat`, `task`, `waiting_on`), `tasks.reassigned` (`task`, `from_seat`, `to_seat`); observations `tasks.assigned` (`tasks`: the seat's briefs), `tasks.graded` (`task`, `correct`), `tasks.blocked` (`task`, `waiting_on`), `tasks.reassigned` (`task`, `previous_assignee`).
- Live runs only (`harness/live.py`):
  - event `harness.call`: one per model call: `model`, `purpose`, `phase`, `seat`, `round`, `attempt`, `ok`, `error`, `stop_reason`, `input_tokens`, `cached_input_tokens`, `cache_write_tokens`, `output_tokens`, `thinking_tokens`, `cost_usd`, `total_spent_usd`.
  - event `harness.invalid_speech`: `seat`, `model`, `meeting`, `pass_index`, `attempt`, `reason`, `forfeited`.
  - `aborted` (instead of `result`, when the spend cap or a fatal API error stopped the game): `reason` (`spend_cap` / `provider_error`), `detail`, `round`, `meetings`.
  - `cost` (always last): `complete`, `seats` (per Seat: `controller`, `calls`, `failed_calls`, all token counts, `cost_usd`, `invalid_actions`, `forfeits`), `game` (totals), `spend_cap_usd`, `run_spent_usd`.

Bump `transcript.SCHEMA_VERSION` when changing existing record shapes; adding new event/observation types does not need a bump.

## Conventions

- **Determinism.** Same seed + same controllers ⇒ byte-identical transcript. All randomness goes through `GameRng` forks; never use the global `random`, `hash()`, sets in output order, or wall-clock time in the engine. Iterate seats in seat order.
- **Hidden information.** Controllers only see context objects. Anything a player learns must go through `game.observe` so the transcript records it. Never put ground truth (roles, other rooms, Model identity) in a context.
- **No network in tests.** `tests/conftest.py` blocks sockets for every test.
- **Live Model calls are opt-in.** Code that calls a paid API must refuse to run unless explicitly enabled (an explicit CLI flag plus `ZANTINE_LIVE=1`), read keys from the environment only, and go through a `Ledger` (spend cap). Tests that need a live call are marked `@pytest.mark.live` and skip unless `ZANTINE_LIVE=1` and `pytest --live`; they never run by default. Other tests use a fake client (`tests/fake_provider.py`).
- **Small files.** One concern per module so parallel work rarely touches the same file.
- **Terminology.** Use the terms in `CONTEXT.md` exactly (Round, Seat, Meeting, Ejection, ...).
