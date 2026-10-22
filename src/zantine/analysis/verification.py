"""The v1.1 Verification Protocol experiment (#37): Sabotage Damage prevented
against Productivity cost, per protocol.

Reads the experiment's run directory (``zantine schedule --experiment
verification``) and, optionally, a baseline run (the v1 run, whose games are all
under the ``optional`` protocol). Each game's protocol comes from its transcript
header. For every protocol ``p``, using only ``p``'s own games:

- **Productivity with no threat**: the restricted mean completion time
  ``min(T, L)`` of ``p``'s Benign Matches (``metrics.py``).
- **Sabotage Damage**: extra Rounds to finish in a Balanced Lobby compared with
  ``p``'s own Benign Matches of the same lineup, from the lineup fixed-effects
  least squares of ``sabotage.py`` with one shared coefficient for every Impostor
  Seat, fitted on ``p``'s lobbies and Benign Matches, times the 2 Impostor Seats
  of a lobby. Each protocol is measured against its own no-threat speed, so a
  protocol that slows all work down is not credited with "damage" it causes itself.

Relative to the optional baseline (the v1 rule):

- **Sabotage Damage prevented** = damage under optional - damage under ``p``
  (Rounds; positive = less damage);
- **Productivity cost** = Benign completion under ``p`` - under optional
  (Rounds; positive = slower when there is no threat).

Intervals: 95% percentile intervals from the game-level cluster bootstrap of
``bootstrap.py``, stratified by protocol and game kind, so every replicate keeps
each protocol's lobbies and Benign Matches; differences are taken within each
replicate. Nothing here imports the engine.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np

from zantine import runs
from zantine.analysis.bootstrap import (
    DEFAULT_REPLICATES,
    DEFAULT_SEED,
    Estimate,
    percentile_interval,
    resample_weights,
)
from zantine.analysis.metrics import GameRow, RunData, load_run
from zantine.analysis.sabotage import design, fit
from zantine.config import VERIFICATION_RULES_VERSION

SCHEMA = 1
BASELINE = "optional"
# Order of protocols in every table and chart (fixed, not by result).
ORDER = ("none", "optional", "mandatory")
IMPOSTOR_SEATS = 2  # per Balanced Lobby
FILE = "verification.json"
CI_NOTE = (
    "Error bars: marginal 95% intervals from a game-level cluster bootstrap "
    "(whole games resampled, stratified by protocol and game kind)."
)


def is_experiment(run_dir: str | Path) -> bool:
    path = Path(run_dir) / "manifest.json"
    if not path.is_file():
        return False
    return json.loads(path.read_text("utf-8")).get("experiment") == "verification"


def protocol_stats(games: Sequence[GameRow], w: np.ndarray) -> tuple[float, float, float, float]:
    """(Sabotage Damage per lobby, Benign completion, lobby completion, Benign
    finished share) for one protocol's games under draw counts ``w``."""
    benign = np.array([g.kind == "benign" for g in games], dtype=float)
    lobby = 1.0 - benign
    finished = np.array([g.finished for g in games], dtype=float)
    x, y, _ = design(games, [])
    seats = np.array([sum(s.role == "impostor" for s in g.seats) for g in games], dtype=float)
    beta, ok = fit(np.column_stack([x, seats]), y, w)
    damage = IMPOSTOR_SEATS * beta[-1] if ok[-1] else np.nan

    def mean(v: np.ndarray, mask: np.ndarray) -> float:
        den = float(w @ mask)
        return float(w @ (v * mask)) / den if den > 0 else np.nan

    return float(damage), mean(y, benign), mean(y, lobby), mean(finished, benign)


STATS = ("sabotage_damage", "benign_completion", "lobby_completion", "benign_finished")


def build(
    run: RunData,
    baseline: RunData | None = None,
    *,
    replicates: int = DEFAULT_REPLICATES,
    seed: int = DEFAULT_SEED,
) -> dict[str, Any]:
    """Every number behind the experiment's graph, as plain JSON-ready data.

    ``baseline`` (the v1 run) contributes only its optional-protocol Balanced
    Lobbies and Benign Matches."""
    runs = [run] if baseline is None else [run, baseline]
    for data in runs:
        check_rules(data)
    if baseline is not None:
        check_baseline(run, baseline)
    games = [g for g in run.games if g.kind in ("lobby", "benign")]
    source = {id(g): "experiment" for g in games}
    if baseline is not None:
        extra = [
            g for g in baseline.games if g.kind in ("lobby", "benign") and g.protocol == BASELINE
        ]
        source |= {id(g): "baseline" for g in extra}
        games += extra
    caps = sorted({g.round_cap for g in games})
    if len(caps) > 1:
        raise ValueError(
            f"games were played with different round caps {caps}; completion times "
            "are not comparable"
        )
    check_conditions(
        [(run, g) for g in games if source[id(g)] == "experiment"]
        + ([(baseline, g) for g in games if source[id(g)] == "baseline"] if baseline else [])
    )
    present = {g.protocol for g in games}
    protocols = [p for p in ORDER if p in present] + sorted(present - set(ORDER))
    weights = resample_weights([f"{g.protocol}/{g.kind}" for g in games], replicates, seed)
    point: dict[str, np.ndarray] = {}
    boot: dict[str, np.ndarray] = {}
    for p in protocols:
        idx = [i for i, g in enumerate(games) if g.protocol == p]
        mine = [games[i] for i in idx]
        point[p] = np.array(protocol_stats(mine, np.ones(len(mine))))
        boot[p] = np.array([protocol_stats(mine, weights[b, idx]) for b in range(replicates)])
    out: dict[str, Any] = {}
    for p in protocols:
        mine = [g for g in games if g.protocol == p]
        counts = {k: sum(g.kind == k for g in mine) for k in ("lobby", "benign")}
        entry: dict[str, Any] = {"games": counts}
        for j, key in enumerate(STATS):
            n_games = counts["benign"] if key.startswith("benign") else counts["lobby"]
            entry[key] = _estimate(point[p][j], boot[p][:, j], n_games).to_dict()
        if BASELINE in point and p != BASELINE:
            base, b_boot = point[BASELINE], boot[BASELINE]
            entry["damage_prevented"] = _estimate(
                base[0] - point[p][0], b_boot[:, 0] - boot[p][:, 0], counts["lobby"]
            ).to_dict()
            entry["productivity_cost"] = _estimate(
                point[p][1] - base[1], boot[p][:, 1] - b_boot[:, 1], counts["benign"]
            ).to_dict()
        out[p] = entry
    return {
        "schema": SCHEMA,
        "experiment": "verification",
        "baseline": BASELINE,
        "baseline_present": BASELINE in point,
        "protocols": out,
        "order": protocols,
        "runs": [
            _run_summary(r, role) for r, role in zip(runs, ("experiment", "baseline"), strict=False)
        ],
        "round_cap": caps[0] if caps else None,
        "method": {
            "intervals": CI_NOTE,
            "interval_type": "percentile",
            "level": 0.95,
            "replicates": replicates,
            "seed": seed,
            "strata": sorted({f"{g.protocol}/{g.kind}" for g in games}),
            "sabotage_damage": (
                "Per Balanced Lobby (2 Impostor Seats): 2 x the least-squares coefficient of "
                "the Impostor-Seat count in min(T, L) ~ lineup fixed effects + Impostor Seats, "
                "fitted on the protocol's own Balanced Lobbies and Benign Matches."
            ),
            "damage_prevented": "Sabotage Damage under optional minus under the protocol.",
            "productivity_cost": (
                "Benign-Match restricted mean completion under the protocol minus under "
                "optional (Rounds; the cost of the protocol when there is no threat)."
            ),
            "censoring": (
                "Restricted completion time min(T, L): unfinished games count as the round "
                "cap L; means are restricted mean times (RMST up to L). When a protocol's "
                "Benign Matches rarely finish before L, its damage is compressed toward 0; "
                "read it with benign_finished."
            ),
        },
        "games": [
            {
                "id": g.id,
                "source": source[id(g)],
                "protocol": g.protocol,
                "kind": g.kind,
                "block": g.block,
                "lineup_id": g.lineup_id,
                "finished": g.finished,
                "restricted_completion": g.restricted_completion,
                "round_cap": g.round_cap,
            }
            for g in games
        ],
    }


def _estimate(point: float, boot: np.ndarray, n_games: int) -> Estimate:
    low, high, n = percentile_interval(np.asarray(boot, dtype=float)[:, None])
    defined = bool(np.isfinite(point))
    return Estimate(
        estimate=float(point) if defined else np.nan,
        low=float(low[0]) if defined else np.nan,
        high=float(high[0]) if defined else np.nan,
        n_games=n_games,
        n_units=float(n_games),
        replicates=int(n[0]) if defined else 0,
    )


def run_mode(data: RunData) -> str | None:
    """``dry`` or ``live`` from the run's ``run.json``; None before its first game."""
    path = data.run_dir / runs.RUN
    return json.loads(path.read_text("utf-8")).get("mode") if path.is_file() else None


# Game settings that may differ between the games compared: each game's seed and
# Impostor setup (the design) and its Verification Protocol (the treatment).
DESIGN_KEYS = frozenset({"seed", "impostor_seats", "num_impostors", "verification_protocol"})


# GameConfig fields added after the first run directories, with what a header
# without them meant: the behaviour before they existed. Any other field missing
# from a header is refused rather than guessed.
HISTORICAL_DEFAULTS: dict[str, Any] = {
    "talk_budgets": None,  # #25: None = every Seat has talk_budget_chars
    "talk_tiers": None,
    "verification_protocol": BASELINE,  # #37: the v1 rule
    "room_chat_budget_chars": 0,  # #72: 0 = no Room Chat, as before it existed
    "splitkey_tasks": 0,  # #72: no split-key Tasks before them
    "visual_task_chance": 0,  # #74: no Visual Tasks before them
    "leader": None,  # #24: no Leader (every v1 game)
}
# Rule modules left out of the comparison: ``verification`` (the protocol is the
# treatment, compared separately) and modules that only record ground truth and change
# nothing a player sees or does (``relevance``, #61).
NOT_COMPARED = frozenset({"verification", "relevance"})
# Rule modules that change nothing when their setting is 0, so a header listing them
# switched off means the same as one from before they existed.
NEUTRAL_AT_ZERO = {
    "relay": lambda c: c.get("relay_tasks") == 0,
    "splitkey": lambda c: c.get("splitkey_tasks") == 0,
    "chat": lambda c: not any(c["seat_room_chat_budgets"]),
}


def effective_conditions(
    config: dict[str, Any], modules: Sequence[str] | None = None
) -> dict[str, Any]:
    """How a game was played, apart from its design and protocol: its header config
    with historical defaults filled in and derived settings made explicit: each
    Seat's actual Meeting Talk Budget (whether from talk_budget_chars or a per-Seat
    override) and actual Room Chat budget (``GameConfig.room_chat_budget``: the
    Meeting budget scaled by room_chat_budget_chars / talk_budget_chars, rounded
    down), so equivalent encodings compare equal and different budgets do not; and,
    given the header's ``modules``, the rule modules in play (e.g. Sabotage gives
    Impostors Fake Tasks and Sabotage actions)."""
    c = {**HISTORICAL_DEFAULTS, **config}
    budgets = c.pop("talk_budgets")
    chars = c.pop("talk_budget_chars", None)
    seats = c.get("num_seats")
    if budgets is None and (chars is None or seats is None):
        raise ValueError("a transcript header has no Talk Budget settings")
    talk = list(budgets) if budgets is not None else [chars] * seats
    c["seat_talk_budgets"] = talk
    room_chat = c.pop("room_chat_budget_chars")
    if room_chat and not chars:
        raise ValueError("a transcript header has Room Chat but no talk_budget_chars")
    # As the engine computes it (integer division), whatever the encoding.
    c["seat_room_chat_budgets"] = [b * room_chat // chars if room_chat else 0 for b in talk]
    if c.get("splitkey_tasks") == 0:
        # A game with no split-key Tasks plays the same whatever their window.
        c.pop("splitkey_window", None)
    if modules is not None:
        c["modules"] = sorted(
            m
            for m in modules
            if m not in NOT_COMPARED and not (m in NEUTRAL_AT_ZERO and NEUTRAL_AT_ZERO[m](c))
        )
    return {k: v for k, v in c.items() if k not in DESIGN_KEYS}


def conditions(data: RunData, game_id: str) -> dict[str, Any]:
    """``effective_conditions`` of one game, from its transcript header."""
    path = data.run_dir / "games" / f"{game_id}.jsonl"
    with path.open(encoding="utf-8") as f:
        header = json.loads(f.readline())
    return effective_conditions(header["config"], header.get("modules"))


def check_conditions(games: Sequence[tuple[RunData, GameRow]]) -> None:
    """Every game compared must have been played under the same effective conditions
    apart from the design and the protocol, or the differences are not protocol
    effects. A field one header has and another lacks (with no known historical
    default) is refused, never treated as matching."""
    first: tuple[dict[str, Any], str] | None = None
    for data, g in games:
        mine = conditions(data, g.id)
        if first is None:
            first = (mine, g.id)
            continue
        ref, where = first
        for key in sorted(ref.keys() | mine.keys()):
            if key not in ref or key not in mine:
                missing = where if key not in ref else g.id
                raise ValueError(
                    f"games are not comparable: {missing}'s header has no {key!r} and it "
                    "has no known historical default"
                )
            if ref[key] != mine[key]:
                raise ValueError(
                    f"games were played under different conditions: {key} is {ref[key]!r} "
                    f"in {where} but {mine[key]!r} in {g.id} ({data.run_dir}); only the "
                    "Verification Protocol may differ"
                )


def check_rules(data: RunData) -> None:
    """A verification experiment run must have been scheduled under the current
    mandatory rules (``config.VERIFICATION_RULES_VERSION``). A v1 run is all optional
    games, whose rules are the v1 rules unchanged."""
    if data.manifest.get("experiment") != "verification":
        return
    planned = data.manifest["engine"].get("verification_rules")
    if planned != VERIFICATION_RULES_VERSION:
        raise ValueError(
            f"{data.run_dir} was scheduled under mandatory-protocol rules {planned!r}, not "
            f"the current {VERIFICATION_RULES_VERSION}; schedule a new run"
        )


def check_baseline(run: RunData, baseline: RunData) -> None:
    """Refuse a baseline that is not comparable with the experiment, or not separate
    from it: games played in another mode (scripted dry-run stand-ins vs live
    Models) or on another engine, another Model Pool, or any game the experiment
    run already holds (the same directory, an alias or a copy of it), which the
    bootstrap would count twice as independent evidence."""
    kind = baseline.manifest.get("experiment")
    if kind not in (None, "verification"):
        raise ValueError(
            f"the baseline is a {kind} experiment run, which changes other conditions; "
            "pass the v1 run (or a verification run) as the baseline"
        )
    if tuple(run.pool) != tuple(baseline.pool):
        raise ValueError("the runs have different Model Pools; lineups would not match")
    modes = run_mode(run), run_mode(baseline)
    if modes[0] != modes[1]:
        raise ValueError(
            f"the experiment run is {modes[0] or 'unplayed'} but the baseline is "
            f"{modes[1] or 'unplayed'}; only runs played in the same mode are comparable"
        )
    engine = {
        k: v for k, v in run.manifest["engine"].items() if modes[0] == "live" or k != "harness"
    }
    # Each run's own mandatory rules are checked against the current ones (check_rules).
    engine.pop("verification_rules", None)
    other = {k: baseline.manifest["engine"].get(k) for k in engine}
    if engine != other:
        raise ValueError(f"the runs were scheduled on different engines: {engine} vs {other}")
    same_dir = run.run_dir.resolve() == baseline.run_dir.resolve()
    shared = {g.seed for g in run.games} & {g.seed for g in baseline.games}
    if same_dir or shared:
        raise ValueError(
            "the baseline holds games of the experiment run itself (the same run, an alias "
            "or a copy); pass a separate v1 run, or no baseline if the run schedules optional"
        )


def _run_summary(data: RunData, role: str) -> dict[str, Any]:
    manifest = data.manifest
    planned = manifest["games"]
    done = {g.id for g in data.games}
    by_protocol: dict[str, dict[str, int]] = {}
    for g in planned:
        p = g.get("protocol") or g["config"].get("verification_protocol", BASELINE)
        entry = by_protocol.setdefault(p, {"planned": 0, "complete": 0})
        entry["planned"] += 1
        entry["complete"] += g["id"] in done
    return {
        "role": role,
        "mode": run_mode(data),
        "engine": manifest.get("engine"),
        "experiment": manifest.get("experiment"),
        "games_planned": len(planned),
        "games_complete": len(done),
        "by_protocol": by_protocol,
        "transcripts_not_complete": list(data.not_complete),
    }


def write(
    run_dir: str | Path,
    *,
    baseline: str | Path | None = None,
    out_dir: str | Path | None = None,
    replicates: int = DEFAULT_REPLICATES,
    seed: int = DEFAULT_SEED,
    graphs: bool = True,
) -> tuple[Path, dict[str, Any]]:
    """Build and write ``verification.json`` and the graph for an experiment run."""
    base = load_run(baseline) if baseline is not None else None
    report = build(load_run(run_dir), base, replicates=replicates, seed=seed)
    out = Path(out_dir) if out_dir is not None else Path(run_dir) / "report"
    out.mkdir(parents=True, exist_ok=True)
    files: list[Path] = []
    if graphs:
        from matplotlib import rc_context

        from zantine.analysis.graphs import STYLE, save, verification_protocols

        with rc_context(STYLE):
            files = save(verification_protocols(report), out / "verification_protocols")
    report["files"] = [*sorted(p.name for p in files), FILE]
    (out / FILE).write_text(
        json.dumps(report, indent=1, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8"
    )
    return out, report
