# Zantine

**Zantine Labs** builds benchmarks for AI agents and teams. Results are published as graphs of the underlying data, not as a ranking or a single score.

The first benchmark is a text-based social-deduction game that asks: **Can a team of agents notice and contain a compromised member while still getting its shared work done?** It measures productivity and vigilance separately, alongside cost and other behavioral metrics.

## Status

The game engine's tracer bullet works: 10 scripted bot **Seats** play a complete game (simultaneous **Rounds**, kills, reports, **Meetings** with a secret vote, hidden-role **Ejection**, all win conditions) and the full game is written to a JSONL transcript. **Tasks**, **Room Chat**, live model **Seats** and the evaluation pipeline are not implemented yet.

```sh
uv sync
uv run zantine play --seed 7 --out runs/seed-7.jsonl
uv run pytest
```

## Project documents

- [Architecture and conventions](docs/architecture.md)
- [Game rules as implemented](docs/rules.md)
- [Plan and decisions](docs/plan.md)
- [Prioritized roadmap](docs/roadmap.md)
- [Model pool research](docs/model-pool.md)
- [Terminology](CONTEXT.md)

The model research and pricing are planning snapshots; verify availability, identifiers, and prices before any paid runs.

## First milestone

Build the deterministic game engine and validate it with scripted bots, then run a small model pilot within the planned approximately $400 budget. See the roadmap for the full sequence and open design questions.
