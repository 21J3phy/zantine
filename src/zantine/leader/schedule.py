"""The v1.4 Leader schedule: 126 crew-led and 70 Compromised-Leader games (``sample-size.md``).

``make_leader_manifest(pool, master_seed)`` plans the experiment as plain data
in the manifest format of the lineup scheduler (#55, ``schedule.py``): the same
top-level keys and game fields (``id``, ``kind``, ``lineup``, ``impostors``,
``config``, ``index``, ``seed``), plus a ``leader`` field per game. Until #55
merges it is self-contained here; ``zantine schedule`` / ``zantine run`` wiring
follows then (``leader_game_setup`` is the leader-aware ``runs.game_setup``).

**Conditions.** Known or Hidden Leader x Crewmate or Compromised Leader. Every
game is a Balanced Lobby (5 Models, 2 Seats each) that includes the Leader's
Model, and the Leader Model rotates through the whole pool:

- crew-led: 63 games per condition, 9 per Leader Model (126 in all);
- compromised: 35 games per condition, 5 per Leader Model (70 in all).

**Paired cells.** Known and Hidden games come in pairs with the same lineup,
Impostor Models and Leader position; only the prompt lines differ (and the game
seed, which as in #55 is derived from the game id).

**Rotation.** The cells for the pool's first Model are chosen once, then every
Model index is rotated by +t (mod n) for the t-th Model, so every Model leads
the same set of lobbies up to relabelling and holds the same number of Impostor
Seats. Model 0's lineups are the ``per_model`` of the lobbies containing it
whose co-player counts are most even (exhaustive search; ties broken by the
master seed). Impostor positions are drawn from the master seed among the
non-Leader positions; a Compromised Leader is always one of the two Impostors.

Lineup positions follow #55's ``seat_lineup``: each Model holds 2 consecutive
positions, each Impostor Model takes its next unused one, and the Leader takes
its Model's Impostor position (compromised) or first non-Impostor one (crew).
Which Seat a position fills is decided by the game seed.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from collections.abc import Sequence
from dataclasses import replace
from itertools import combinations
from typing import Any

from zantine.config import GameConfig, LeaderConfig, condition_name
from zantine.harness import neutral
from zantine.harness.pricing import load_models
from zantine.rng import GameRng
from zantine.scenario import SCENARIO_VERSION, build_scenario
from zantine.transcript import SCHEMA_VERSION

MANIFEST_VERSION = 1
LOBBY_MODELS = 5
SEATS_PER_MODEL = 2
IMPOSTORS = 2
# Games per condition per Leader Model (docs/sample-size.md, v1.4).
CREW_PER_MODEL = 9
COMPROMISED_PER_MODEL = 5
CONDITIONS = ("known_crew", "hidden_crew", "known_compromised", "hidden_compromised")
# Same derivation as the lineup scheduler (#55), so a game id always gives one seed.
SEED_DERIVATION = "int(sha256('zantine-run/<master_seed>/<game_id>')[:8], big) >> 1"


def load_pool() -> list[str]:
    return list(load_models())


def game_seed(master_seed: int, game_id: str) -> int:
    digest = hashlib.sha256(f"zantine-run/{master_seed}/{game_id}".encode()).digest()
    return int.from_bytes(digest[:8], "big") >> 1


def lineups(pool: Sequence[str]) -> list[tuple[str, ...]]:
    """Every Balanced Lobby lineup in canonical order; a lineup's index is its id (as in #55)."""
    return list(combinations(pool, LOBBY_MODELS))


def per_model(compromised: bool) -> int:
    return COMPROMISED_PER_MODEL if compromised else CREW_PER_MODEL


def _cells(n: int, k: int, compromised: bool, rng: GameRng) -> list[tuple[list[int], list[int]]]:
    """Model 0's cells: (lineup as Model indices, Impostor Model indices, Leader's first)."""
    containing = [c for c in combinations(range(n), LOBBY_MODELS) if 0 in c]
    if k > len(containing):
        raise ValueError(f"only {len(containing)} lobbies include each Model; need {k}")

    def spread(subset: tuple[tuple[int, ...], ...]) -> tuple[int, int]:
        counts = Counter(m for lu in subset for m in lu if m != 0)
        values = [counts.get(m, 0) for m in range(1, n)]
        return max(values) - min(values), sum(v * v for v in values)

    subsets = list(combinations(containing, k))
    best = min(spread(s) for s in subsets)
    chosen = rng.fork("lineups").choice([s for s in subsets if spread(s) == best])
    cells = []
    for i, lineup in enumerate(chosen):
        entries = [m for m in lineup for _ in range(SEATS_PER_MODEL)]
        # Position 0 is the Leader Model's first position; draw among the other positions.
        others = list(range(1, len(entries)))
        picks = rng.fork(f"impostors-{i}").sample(others, IMPOSTORS - int(compromised))
        impostors = ([0] if compromised else []) + sorted(entries[p] for p in picks)
        cells.append((list(lineup), impostors))
    return cells


def make_leader_manifest(
    pool: Sequence[str], master_seed: int, *, config: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Every v1.4 Leader game, in a seeded run order, with its seed."""
    n = len(pool)
    if len(set(pool)) != n or n < LOBBY_MODELS:
        raise ValueError(f"need at least {LOBBY_MODELS} distinct Models")
    root = GameRng(master_seed).fork("leader")
    index = {lu: i for i, lu in enumerate(lineups(pool))}
    games: list[dict[str, Any]] = []
    for compromised in (False, True):
        role = "compromised" if compromised else "crew"
        base = _cells(n, per_model(compromised), compromised, root.fork(role))
        for t, model in enumerate(pool):
            for c, (lineup, impostors) in enumerate(base, start=1):
                models = sorted((x + t) % n for x in lineup)
                lineup_models = tuple(pool[x] for x in models)
                for known in (True, False):
                    condition = condition_name(known, compromised)
                    game = {
                        "id": f"leader-{condition.replace('_', '-')}-m{t}-{c}",
                        "block": None,
                        "kind": "leader",
                        "lineup_id": index[lineup_models],
                        "lineup": list(lineup_models),
                        "impostors": [pool[(x + t) % n] for x in impostors],
                        "config": {},
                        "cell": f"{role}-m{t}-{c}",
                        "leader": {
                            "model": model,
                            "known": known,
                            "compromised": compromised,
                            "condition": condition,
                        },
                    }
                    game["leader"]["position"] = leader_position(game)
                    games.append(game)
    games = root.fork("order").shuffled(games)
    for i, game in enumerate(games):
        game["index"] = i
        game["seed"] = game_seed(master_seed, game["id"])
    manifest = {
        "manifest_version": MANIFEST_VERSION,
        "experiment": "leader",
        "master_seed": master_seed,
        "seed_derivation": SEED_DERIVATION,
        "pool": list(pool),
        "blocks": 0,
        "conditions": {
            cond: {
                "games": per_model("compromised" in cond) * n,
                "per_model": per_model("compromised" in cond),
            }
            for cond in CONDITIONS
        },
        "engine": engine_versions(),
        "config": dict(config or {}),
        "games": games,
    }
    problems = verify_leader(manifest)
    if problems:
        raise AssertionError(f"leader schedule is invalid: {problems[:5]}")
    return manifest


def engine_versions() -> dict[str, Any]:
    return {
        "scenario_version": SCENARIO_VERSION,
        "transcript_schema": SCHEMA_VERSION,
        "harness": f"{neutral.HARNESS_NAME}-v{neutral.HARNESS_VERSION}",
    }


def seat_lineup(game: dict[str, Any]) -> tuple[list[str], set[int]]:
    """Lineup entries and Impostor positions, by the same rule as #55's ``seat_lineup``."""
    entries = [m for m in game["lineup"] for _ in range(SEATS_PER_MODEL)]
    impostors: set[int] = set()
    for model in game["impostors"]:
        pos = next(i for i, m in enumerate(entries) if m == model and i not in impostors)
        impostors.add(pos)
    return entries, impostors


def leader_position(game: dict[str, Any]) -> int:
    entries, impostors = seat_lineup(game)
    lead = game["leader"]
    mine = [i for i, m in enumerate(entries) if m == lead["model"]]
    fits = [i for i in mine if (i in impostors) == lead["compromised"]]
    if not fits:
        raise ValueError(f"{game['id']}: no position fits the Leader's role")
    return fits[0]


def leader_game_setup(
    manifest: dict[str, Any], game: dict[str, Any], mode: str
) -> tuple[GameConfig, str]:
    """The ``GameConfig`` and bot spec for one Leader game (``runs.game_setup`` plus the Leader).

    The scenario (names, seat order, map) comes from the game seed; Impostor and
    Leader Seats are wherever that seat order puts their lineup positions.
    """
    entries, impostor_positions = seat_lineup(game)
    config = GameConfig(seed=game["seed"], **{**manifest.get("config", {}), **game["config"]})
    order = build_scenario(config, GameRng(config.seed).fork("scenario")).seat_order
    lead = game["leader"]
    config = replace(
        config,
        impostor_seats=tuple(s for s, pos in enumerate(order) if pos in impostor_positions),
        leader=LeaderConfig(
            seat=order.index(lead["position"]),
            known=lead["known"],
            compromised=lead["compromised"],
        ),
    )
    return config, ",".join(f"{mode}:{model}" for model in entries)


def verify_leader(manifest: dict[str, Any]) -> list[str]:
    """Every invariant of the Leader schedule; returns the problems found (empty = valid)."""
    pool = manifest["pool"]
    games = manifest["games"]
    out: list[str] = []
    if len({g["id"] for g in games}) != len(games):
        out.append("duplicate game ids")
    if len({g["seed"] for g in games}) != len(games):
        out.append("duplicate seeds")
    for g in games:
        lead = g["leader"]
        if g["seed"] != game_seed(manifest["master_seed"], g["id"]):
            out.append(f"{g['id']}: seed is not derived from the master seed")
        if tuple(g["lineup"]) not in lineups(pool) or len(set(g["lineup"])) != LOBBY_MODELS:
            out.append(f"{g['id']}: not a Balanced Lobby lineup")
        if lead["model"] not in g["lineup"] or not set(g["impostors"]) <= set(g["lineup"]):
            out.append(f"{g['id']}: Leader or Impostor Model not in the lineup")
        if len(g["impostors"]) != IMPOSTORS:
            out.append(f"{g['id']}: wrong number of Impostors")
        _, impostors = seat_lineup(g)
        if (lead["position"] in impostors) != lead["compromised"]:
            out.append(f"{g['id']}: Leader position has the wrong role")
        if lead["condition"] != condition_name(lead["known"], lead["compromised"]):
            out.append(f"{g['id']}: condition mismatch")
    for cond in CONDITIONS:
        mine = [g for g in games if g["leader"]["condition"] == cond]
        want = per_model("compromised" in cond)
        counts = Counter(g["leader"]["model"] for g in mine)
        if counts != dict.fromkeys(pool, want):
            out.append(f"{cond}: games per Leader Model {dict(counts)} != {want}")
    seats = Counter(m for g in games for m in g["impostors"])
    if len(set(seats.values())) > 1 or set(seats) != set(pool):
        out.append(f"Impostor Seats per Model are unequal: {dict(seats)}")
    cells: dict[str, list[dict[str, Any]]] = {}
    for g in games:
        cells.setdefault(g["cell"], []).append(g)
    for cell, pair in cells.items():
        shape = {(tuple(g["lineup"]), tuple(g["impostors"]), g["leader"]["position"]) for g in pair}
        if sorted(g["leader"]["known"] for g in pair) != [False, True] or len(shape) != 1:
            out.append(f"{cell}: Known and Hidden games are not a matched pair")
    return out


def summary(manifest: dict[str, Any]) -> str:
    counts = Counter(g["leader"]["condition"] for g in manifest["games"])
    parts = ", ".join(f"{c} {counts[c]}" for c in CONDITIONS)
    return (
        f"{len(manifest['games'])} Leader games ({parts}); "
        f"Leader rotates through a pool of {len(manifest['pool'])}"
    )
