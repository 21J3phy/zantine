# Agent guide

Read first: `CONTEXT.md` (terminology; use its terms exactly), `docs/architecture.md` (module map, extension points), `docs/rules.md` (rules as implemented).

## Commands

```sh
uv sync                                  # install (Python 3.12+, uv)
uv run pytest                            # tests; must pass
uv run ruff check && uv run ruff format  # lint and format; must be clean
uv run zantine play --seed 7             # play one scripted game
```

## Conventions

- **Determinism:** all randomness via `GameRng` (`game.rng.fork("<name>")`). Same seed ⇒ byte-identical transcript.
- **No network in tests.** Sockets are blocked in `tests/conftest.py`.
- **Never call a paid API by default.** Live Model code must require an explicit flag plus `ZANTINE_LIVE=1`, read keys from the environment, and enforce a spend cap. Live tests use `@pytest.mark.live` and skip unless `ZANTINE_LIVE=1` and `pytest --live`. All paid calls go through a `harness.ledger.Ledger`.
- **New rules go in their own `GameModule` file** under `src/zantine/rules/`, registered with one line in `default_modules()`. Keep state in `GameState.ext[module]`; prefix new action kinds and event/observation types with the module name.
- **Hidden information:** players learn things only through `game.observe(...)`; never expose ground truth in a context.
- Update `docs/rules.md` when you change rules, and `docs/architecture.md` when you add a module or extension point.
