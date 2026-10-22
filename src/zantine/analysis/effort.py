"""The effort sweep (v1.6, #26): Mirror Match team metrics against effort, thinking and cost.

``zantine report <effort-run>`` (a run scheduled with ``--experiment effort``)
writes this report instead of the v1 one. Each Model's Mirror Matches are
grouped by effort level, read from the transcript header (every Seat records
its ``effort``); ``medium`` comes from the v1 run's Mirror Matches when
``--medium <v1-run>`` is given. A Seat with no recorded level (a dry Seat
without ``?effort=``) played the ``models.toml`` default, ``medium``.

Mirror Matches are self-play, so everything here is descriptive: how a Model's
own team plays at each level, and what that level costs in thinking tokens and
dollars. No ranking, no composite score; Models in fixed alphabetical order.

Intervals: the same game-level cluster bootstrap as the v1 report, stratified by
(Model, level) cell, so every replicate keeps each cell's game count.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from zantine import runs
from zantine.analysis.bootstrap import (
    DEFAULT_REPLICATES,
    DEFAULT_SEED,
    ratio_estimates,
    resample_weights,
)
from zantine.analysis.metrics import GameRow, RunData, load_run
from zantine.analysis.report import CI_NOTE, REPORT_DIR, model_info, run_mode, strip_private
from zantine.config import with_historical_defaults
from zantine.harness.pricing import EFFORT_LEVELS

EFFORT_SCHEMA = 1
# A Seat without a recorded level played the models.toml default.
DEFAULT_LEVEL = "medium"

Contribution = Callable[[GameRow], tuple[float, float]]


@dataclass(frozen=True)
class EffortMetric:
    key: str
    label: str
    unit: str
    group: str  # "team": a team metric; "resource": what the level costs
    definition: str
    contribution: Contribution  # (numerator, denominator) of one Mirror Match


def _game(value: Callable[[GameRow], float | None]) -> Contribution:
    def f(g: GameRow) -> tuple[float, float]:
        v = value(g)
        return (0.0, 0.0) if v is None else (float(v), 1.0)

    return f


def _seats(num: Callable[[GameRow], float], den: Callable[[GameRow], float]) -> Contribution:
    return lambda g: (float(num(g)), float(den(g)))


def _crew_votes(g: GameRow) -> tuple[float, float]:
    crew = [s for s in g.seats if s.role == "crewmate"]
    return float(sum(s.correct for s in crew)), float(sum(s.accusations for s in crew))


METRICS: tuple[EffortMetric, ...] = (
    EffortMetric(
        "productivity",
        "Productivity",
        "Rounds to finish all Tasks (restricted mean)",
        "team",
        "Restricted mean completion time min(T, L) over the Model's Mirror Matches at this "
        "level; unfinished games count as the round cap L (fewer = faster).",
        _game(lambda g: g.restricted_completion),
    ),
    EffortMetric(
        "finished_share",
        "Work finished before the cap",
        "share of games",
        "team",
        "Share of those games in which every counted Task was done.",
        _game(lambda g: float(g.finished)),
    ),
    EffortMetric(
        "impostors_ejected",
        "Vigilance: Impostors ejected",
        "share of Impostors",
        "team",
        "Impostors ejected / Impostors, pooled over the games.",
        lambda g: (float(g.impostors_ejected), float(g.impostors)),
    ),
    EffortMetric(
        "containment_time",
        "Vigilance: time to Containment",
        "Rounds (restricted mean)",
        "team",
        "Restricted mean Round of Containment min(C, L); games never contained count as L.",
        _game(lambda g: g.restricted_containment),
    ),
    EffortMetric(
        "correct_vote_share",
        "Vigilance: correct-vote share",
        "share of Crewmates' non-skip votes",
        "team",
        "Votes for an Impostor / non-skip votes, pooled over the games' Crewmate Seats.",
        _crew_votes,
    ),
    EffortMetric(
        "impostors_won",
        "Games the Impostors won",
        "share of games",
        "team",
        "Share of the games that ended with an Impostor win.",
        _game(lambda g: float(g.end_team == "impostors")),
    ),
    EffortMetric(
        "thinking_per_call",
        "Thinking tokens per call",
        "tokens per call (mean)",
        "resource",
        "Thinking tokens / calls that reported a thinking count, pooled over every Seat of "
        "the games (each lab's own count; billed as output). Calls whose provider reported "
        "none are left out, so missing telemetry is n/a, never a measured zero. Dry runs "
        "make no calls.",
        _seats(
            lambda g: sum(s.thinking_tokens for s in g.seats),
            lambda g: sum(s.thinking_calls for s in g.seats),
        ),
    ),
    EffortMetric(
        "cost_per_game",
        "Cost per game",
        "USD per game (mean)",
        "resource",
        "Mean API cost of a Mirror Match (all ten Seats) at this level. Dry runs cost $0.",
        _game(lambda g: g.cost_usd),
    ),
)
TEAM = tuple(m.key for m in METRICS if m.group == "team")


@dataclass
class EffortGame:
    row: GameRow
    model: str
    level: str
    source: str  # "sweep" (the effort run) or "medium" (a v1 run's Mirror Matches)
    run: str  # "sweep", or "medium-<n>" for the n-th --medium run (seed-free)
    run_dir: Path

    @property
    def key(self) -> str:
        """Unique across every run in the report (local game ids repeat between runs)."""
        return f"{self.run}/{self.row.id}"


def game_level(g: GameRow) -> str | None:
    """The one effort level every Seat played at, or None if the Seats disagree."""
    levels = {s.effort or DEFAULT_LEVEL for s in g.seats}
    return levels.pop() if len(levels) == 1 else None


def mirror_games(
    data: RunData, source: str, run: str, keep: Callable[[str], bool]
) -> list[EffortGame]:
    out = []
    for g in data.of_kind("mirror"):
        models = {s.model for s in g.seats}
        level = game_level(g)
        if len(models) == 1 and level is not None and keep(level):
            out.append(EffortGame(g, models.pop(), level, source, run, data.run_dir))
    return out


# Config keys that differ between games by design; everything else must match to pool.
PER_GAME_CONFIG = ("seed", "impostor_seats")
ENGINE_KEYS = ("scenario_version", "transcript_schema")


def game_mode(g: GameRow) -> str | None:
    """``dry`` or ``live`` from the header's Seat controllers; None if neither or mixed."""
    kinds = {s.controller for s in g.seats}
    return kinds.pop() if len(kinds) == 1 and kinds <= set(runs.MODES) else None


def setup(g: GameRow) -> dict[str, Any]:
    """Everything that must match for two games to sit on one effort curve: the
    effective game config (but its seed), the engine versions, and each Seat's
    controller kind and Harness. Effort and the seed are the permitted differences."""
    return {
        "config": {
            k: v
            for k, v in sorted(with_historical_defaults(g.config).items())
            if k not in PER_GAME_CONFIG
        },
        "engine": {k: g.engine.get(k) for k in ENGINE_KEYS},
        "seats": sorted({f"{s.controller}/{s.harness}" for s in g.seats}),
    }


def check_mode(data: RunData, games: list[EffortGame], label: str) -> str | None:
    """The run's mode from its transcripts, checked against ``run.json`` when present."""
    modes = {game_mode(g.row) for g in games}
    if None in modes:
        bad = next(g.key for g in games if game_mode(g.row) is None)
        raise ValueError(f"{label}: {bad} is neither a dry nor a live game")
    if len(modes) > 1:
        raise ValueError(f"{label} mixes dry and live games")
    declared = run_mode(data.run_dir)
    if declared and modes and modes != {declared}:
        raise ValueError(f"{label}: run.json says {declared} but its transcripts are {modes.pop()}")
    return modes.pop() if modes else declared


def check_compatible(games: list[EffortGame]) -> None:
    """Refuse to pool games whose setup differs (e.g. a different round cap would make
    a different restricted-mean outcome, and look like an effort effect)."""
    if not games:
        return
    ref = games[0]
    want = setup(ref.row)
    for g in games[1:]:
        have = setup(g.row)
        if have != want:
            diff = {
                f"{part}.{k}": (want[part].get(k), have[part].get(k))
                for part in ("config", "engine")
                for k in sorted(set(want[part]) | set(have[part]))
                if want[part].get(k) != have[part].get(k)
            }
            if want["seats"] != have["seats"]:
                diff["seats"] = (want["seats"], have["seats"])
            raise ValueError(
                f"{g.key} was played under a different setup from {ref.key}: "
                + "; ".join(f"{k} {a!r} vs {b!r}" for k, (a, b) in diff.items())
                + ". Only effort and seeds may differ within one effort report."
            )


@dataclass
class EffortData:
    sweep: RunData
    medium: list[RunData]
    games: list[EffortGame]
    # dry / live, from the transcripts (checked against each run.json); None if no games.
    mode: str | None = None

    @property
    def levels(self) -> list[str]:
        """Every level, low to high (medium shows as n/a without a v1 run)."""
        return list(EFFORT_LEVELS)


def is_effort_run(run_dir: str | Path) -> bool:
    path = Path(run_dir) / runs.MANIFEST
    if not path.is_file():
        return False
    return json.loads(path.read_text("utf-8")).get("experiment") == "effort"


def load_effort(run_dir: str | Path, medium: Sequence[str | Path] = ()) -> EffortData:
    """The sweep's games and, from each ``medium`` run, its medium-effort Mirror Matches."""
    sweep = load_run(run_dir)
    if sweep.manifest.get("experiment") != "effort":
        raise ValueError(f"{run_dir} is not an effort sweep (schedule it with --experiment effort)")
    swept = set(sweep.manifest["effort_levels"])
    pool = set(sweep.pool)
    games = mirror_games(sweep, "sweep", "sweep", lambda level: level in swept)
    mode = check_mode(sweep, games, str(run_dir))
    seen = {Path(run_dir).resolve()}
    others = []
    for n, path in enumerate(medium, start=1):
        if Path(path).resolve() in seen:
            raise ValueError(f"{path} is given twice (or is the sweep itself)")
        seen.add(Path(path).resolve())
        data = load_run(path)
        found = [
            g
            for g in mirror_games(data, "medium", f"medium-{n}", lambda level: level == "medium")
            if g.model in pool
        ]
        other = check_mode(data, found, str(path))
        if mode and other and other != mode:
            raise ValueError(f"{path} is a {other} run but {run_dir} is {mode}; do not mix them")
        mode = mode or other
        others.append(data)
        games += found
    check_compatible(games)
    return EffortData(sweep=sweep, medium=others, games=games, mode=mode)


def build_effort_report(
    data: EffortData, *, replicates: int = DEFAULT_REPLICATES, seed: int = DEFAULT_SEED
) -> dict[str, Any]:
    """Every number behind the effort graphs, as plain JSON-ready data."""
    models = sorted(data.sweep.pool)
    levels = data.levels
    cells = [(m, lvl) for m in models for lvl in levels]
    column = {cell: j for j, cell in enumerate(cells)}
    games = data.games
    weights = resample_weights([f"{g.model}/{g.level}" for g in games], replicates, seed)

    metrics: dict[str, Any] = {}
    for spec in METRICS:
        num = np.zeros((len(games), len(cells)))
        den = np.zeros((len(games), len(cells)))
        for i, g in enumerate(games):
            j = column.get((g.model, g.level))
            if j is not None:
                num[i, j], den[i, j] = spec.contribution(g.row)
        ests = ratio_estimates(num, den, weights)
        metrics[spec.key] = {
            "label": spec.label,
            "unit": spec.unit,
            "group": spec.group,
            "definition": spec.definition,
            "per_model": {
                m: {lvl: ests[column[(m, lvl)]].to_dict() for lvl in levels} for m in models
            },
        }

    planned = {(m, lvl): 0 for m in models for lvl in levels}
    for g in data.sweep.manifest["games"]:
        key = (g["lineup"][0], g.get("effort"))
        if key in planned:
            planned[key] += 1
    have = {(m, lvl): 0 for m in models for lvl in levels}
    for g in games:
        if (g.model, g.level) in have:
            have[(g.model, g.level)] += 1
    sweep_done = sum(g.source == "sweep" for g in games)
    return {
        "schema": EFFORT_SCHEMA,
        "experiment": "effort",
        "run": {
            "run_dir": data.sweep.run_dir.as_posix(),
            "master_seed": data.sweep.manifest.get("master_seed"),
            "pool": data.sweep.pool,
            "mode": data.mode or run_mode(data.sweep.run_dir),
            "games_planned": len(data.sweep.manifest["games"]),
            "games_complete": sweep_done,
            "transcripts_not_complete": list(data.sweep.not_complete),
            "swept_levels": list(data.sweep.manifest["effort_levels"]),
            "total_cost_usd": round(sum(g.row.cost_usd for g in games if g.source == "sweep"), 6),
            "run_spend_usd": runs.spent_usd(data.sweep.run_dir),
            "medium_runs": [
                {
                    "run": f"medium-{n}",
                    "run_dir": m.run_dir.as_posix(),
                    "master_seed": m.manifest.get("master_seed"),
                    "mode": data.mode,
                    "mirror_games_used": sum(
                        g.source == "medium" and g.run_dir == m.run_dir for g in games
                    ),
                }
                for n, m in enumerate(data.medium, start=1)
            ],
        },
        "method": {
            "intervals": CI_NOTE.replace("game kind", "(Model, effort level) cell"),
            "interval_type": "percentile",
            "level": 0.95,
            "replicates": replicates,
            "seed": seed,
            "strata": "(Model, effort level)",
            "scope": (
                "Mirror Matches only: self-play, descriptive. Attackers and defenders are the "
                "same Model at the same level, so this is not a measure of defense."
            ),
            "censoring": "Restricted completion time min(T, L), as in the v1 report.",
            "unrecorded_effort": (
                f"A Seat whose header records no effort played the models.toml default "
                f"({DEFAULT_LEVEL})."
            ),
            "model_order": "alphabetical by Model ID, fixed in every chart",
        },
        "levels": levels,
        "models": model_info(models),
        "metrics": metrics,
        "cells": {
            m: {lvl: {"complete": have[(m, lvl)], "planned": planned[(m, lvl)]} for lvl in levels}
            for m in models
        },
        "games": [
            g.row.to_dict()
            | {"key": g.key, "run": g.run, "model": g.model, "effort": g.level, "source": g.source}
            for g in games
        ],
        # ``key`` joins a Seat to its game; ``game`` alone repeats across runs.
        "seats": [s.__dict__ | {"key": g.key, "run": g.run} for g in games for s in g.row.seats],
    }


def public_effort_report(report: dict[str, Any]) -> dict[str, Any]:
    """``report`` without seeds (docs/adr/0001) or local run paths."""
    public = strip_private(report)
    hide = ("master_seed", "run_dir")
    public["run"] = {k: v for k, v in public["run"].items() if k not in hide}
    public["run"]["medium_runs"] = [
        {k: v for k, v in r.items() if k not in hide} for r in public["run"]["medium_runs"]
    ]
    public["games"] = [{k: v for k, v in g.items() if k != "seed"} for g in public["games"]]
    return public


def progress_markdown(report: dict[str, Any]) -> str:
    run, levels = report["run"], report["levels"]
    lines = [
        "# Effort sweep progress",
        "",
        f"Run: `{run['run_dir']}` ({run['mode'] or 'not started'})",
        "",
        f"- Sweep games complete: **{run['games_complete']} of {run['games_planned']}** "
        f"(levels {', '.join(run['swept_levels'])}).",
    ]
    if run["medium_runs"]:
        for m in run["medium_runs"]:
            lines.append(
                f"- Medium effort: {m['mirror_games_used']} Mirror Matches from `{m['run_dir']}`."
            )
    else:
        lines.append("- Medium effort: no v1 run given (`--medium <run-dir>`).")
    if run["transcripts_not_complete"]:
        lines.append(
            "- Transcripts left out as not complete: "
            + ", ".join(f"`{g}`" for g in run["transcripts_not_complete"])
            + "."
        )
    lines += [
        f"- Spend: ${run['run_spend_usd']:.2f} for the sweep run (spend log).",
        "",
        "## Mirror Matches complete per Model and level",
        "",
        "Medium cells have no plan in this run (they come from v1). Models in fixed "
        "alphabetical order.",
        "",
        "| Model | " + " | ".join(levels) + " |",
        "| --- | " + " | ".join("---:" for _ in levels) + " |",
    ]
    for m, cells in report["cells"].items():
        row = []
        for lvl in levels:
            c = cells[lvl]
            row.append(f"{c['complete']}/{c['planned']}" if c["planned"] else f"{c['complete']}")
        lines.append(f"| {m} | " + " | ".join(row) + " |")
    return "\n".join(lines) + "\n"


def write_effort_report(
    run_dir: str | Path,
    *,
    medium: Sequence[str | Path] = (),
    out_dir: str | Path | None = None,
    replicates: int = DEFAULT_REPLICATES,
    seed: int = DEFAULT_SEED,
    graphs: bool = True,
) -> tuple[Path, dict[str, Any]]:
    data = load_effort(run_dir, medium)
    report = build_effort_report(data, replicates=replicates, seed=seed)
    out = Path(out_dir) if out_dir is not None else Path(run_dir) / REPORT_DIR
    out.mkdir(parents=True, exist_ok=True)
    files: list[Path] = []
    if graphs:
        from zantine.analysis.graphs import render_effort

        files = render_effort(report, out)
    report["files"] = [
        *sorted(p.name for p in files),
        "metrics.json",
        "metrics.public.json",
        "progress.md",
    ]
    for name, body in (
        ("metrics.json", strip_private(report)),
        ("metrics.public.json", public_effort_report(report)),
    ):
        (out / name).write_text(
            json.dumps(body, indent=1, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8"
        )
    (out / "progress.md").write_text(progress_markdown(report), encoding="utf-8")
    return out, report
