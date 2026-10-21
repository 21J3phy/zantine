"""The v1.5 speed experiment: randomized Talk Budget tiers (#25, docs/roadmap.md P6).

``make_speed_manifest(pool, master_seed)`` plans the experiment's 76 games
(``docs/sample-size.md``) as plain data, in the same manifest format ``runs.py``
plays:

- **Tier games** (56 = 2 blocks of the v1 design, ``schedule.make_manifest``:
  42 Balanced Lobbies and 14 Benign Matches). Every Seat of every game gets a
  fast, normal or slow **Talk Budget**, drawn independently of its Model and
  role: each tier goes to 3 of the 10 Seats, the tenth Seat's tier is drawn
  uniformly, and the 10 tiers are shuffled over the Seats. The draw for game
  ``<id>`` uses ``GameRng(master_seed).fork("talk-tiers/<id>")`` and is listed
  per Seat in the manifest (``game["talk"]["tiers"]``), so it is seeded,
  reproducible and logged before any game is played. Seats are numbered as in
  the engine (the game seed decides which Model sits where), so the tiers are
  independent of the Models whatever the seating.
- **Realism check** (20 games, kind ``realism``): Balanced Lobbies whose Talk
  Budgets follow each Model's measured API throughput instead. The Models are
  ranked by the throughput in ``speed.toml``; the fastest third get the fast
  tier, the slowest third the slow tier, the rest normal. The lineups are 20 of
  the 21 Balanced Lobby lineups (one dropped at random), each with an Impostor
  Pairing from the v1 cyclic design. Here tier and Model are confounded by
  design, so it is descriptive only: it shows whether the tier effect looks the
  same when speed follows the Models as it would in real-time play.

Tier budgets and the throughput table live in ``speed.toml``. The throughput
values there are placeholders until the pilot measures them; a manifest built
from placeholders records ``measured: false`` and ``zantine run --live``
refuses it (``check_live``). Dry runs work either way.

Players only ever learn their own budget (``GameConfig.talk_budget``); the
tiers are recorded in the transcript header's config (``talk_budgets``,
``talk_tiers``) for analysis, never in any player's view.
"""

from __future__ import annotations

import tomllib
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from zantine.rng import GameRng
from zantine.schedule import (
    CyclicDesign,
    game_seed,
    make_manifest,
    seat_lineup,
    verify,
)

EXPERIMENT = "speed"
TIERS = ("fast", "normal", "slow")
SEATS = 10
TIER_BLOCKS = 2  # 56 tier games
REALISM_GAMES = 20
TABLE = Path(__file__).with_name("speed.toml")


@dataclass(frozen=True)
class SpeedTable:
    tiers: dict[str, int]  # tier -> characters per Meeting pass
    throughput: dict[str, float]  # Model -> output tokens per second
    measured: bool  # False while the throughput values are placeholders
    source: str


def load_table(path: Path = TABLE) -> SpeedTable:
    data = tomllib.loads(path.read_text("utf-8"))
    tiers = {t: int(data["tiers"][t]) for t in TIERS}
    if any(chars < 1 for chars in tiers.values()):
        raise ValueError(f"{path}: every tier needs a budget of at least 1 character")
    tp = data["throughput"]
    return SpeedTable(
        tiers=tiers,
        throughput={m: float(v) for m, v in tp["tokens_per_second"].items()},
        measured=bool(tp["measured"]),
        source=str(tp["source"]),
    )


def draw_tiers(rng: GameRng, seats: int = SEATS) -> list[str]:
    """One tier per Seat: each tier on ``seats // 3`` Seats, the remainder drawn
    without replacement, all shuffled over the Seats."""
    base, extra = divmod(seats, len(TIERS))
    tiers = [t for t in TIERS for _ in range(base)] + rng.sample(TIERS, extra)
    return rng.shuffled(tiers)


def realism_tiers(pool: Sequence[str], throughput: dict[str, float]) -> dict[str, str]:
    """Model -> tier by measured throughput: fastest third fast, slowest third slow.

    Ties keep Model Pool order (an earlier Model counts as faster), so equal
    placeholder values still give a well-defined, documented split.
    """
    missing = [m for m in pool if m not in throughput]
    if missing:
        raise ValueError(f"speed.toml has no throughput for {', '.join(missing)}")
    order = sorted(pool, key=lambda m: (-throughput[m], list(pool).index(m)))
    third = len(order) // 3
    out = {}
    for i, m in enumerate(order):
        out[m] = "fast" if i < third else "slow" if i >= len(order) - third else "normal"
    return out


def make_speed_manifest(
    pool: Sequence[str],
    master_seed: int,
    *,
    blocks: int = TIER_BLOCKS,
    realism_games: int = REALISM_GAMES,
    config: dict[str, Any] | None = None,
    table: SpeedTable | None = None,
) -> dict[str, Any]:
    """The speed experiment's plan: tier games in v1 blocks, then the realism check."""
    if realism_games < 0:
        raise ValueError("realism_games must be >= 0")
    table = table or load_table()
    manifest = make_manifest(pool, master_seed, blocks=blocks, mirror=False, config=config)
    root = GameRng(master_seed)
    for game in manifest["games"]:
        game["talk"] = {
            "mode": "random",
            "tiers": draw_tiers(root.fork(f"talk-tiers/{game['id']}")),
        }

    by_model = realism_tiers(manifest["pool"], table.throughput)
    design = CyclicDesign(manifest["pool"])
    lobbies: list[dict[str, Any]] = []
    rounds = 0
    while len(lobbies) < realism_games:
        rounds += 1
        pairings = design.assign(blocks + rounds - 1, root.fork(f"pairings/realism-{rounds}"))
        order = root.fork(f"order/realism-{rounds}").shuffled(range(len(design.lineups)))
        for lid in order[: realism_games - len(lobbies)]:
            lineup = list(design.lineups[lid])
            lobbies.append(
                {
                    "id": f"realism-{len(lobbies) + 1:02d}",
                    "block": None,
                    "kind": "realism",
                    "lineup_id": lid,
                    "lineup": lineup,
                    "impostors": list(pairings[lid]),
                    "config": {},
                    "talk": {"mode": "realism", "tier_by_model": {m: by_model[m] for m in lineup}},
                }
            )
    start = len(manifest["games"])
    for i, game in enumerate(lobbies):
        game["index"] = start + i
        game["seed"] = game_seed(master_seed, game["id"])
    manifest["games"] += lobbies

    manifest["experiment"] = EXPERIMENT
    manifest["speed"] = {
        "tiers": dict(table.tiers),
        "tier_draw": (
            "per game: each tier on 3 of the 10 Seats, the tenth Seat's tier drawn "
            "uniformly, shuffled over Seats; GameRng(master_seed).fork('talk-tiers/<game id>')"
        ),
        "tier_games": start,
        "realism": {
            "games": len(lobbies),
            "tier_by_model": by_model,
            "tokens_per_second": {m: table.throughput[m] for m in manifest["pool"]},
            "measured": table.measured,
            "source": table.source,
        },
    }
    problems = verify_speed(manifest)
    if problems:
        raise AssertionError(f"speed scheduler produced an invalid plan: {problems[:5]}")
    return manifest


def talk_config(
    manifest: dict[str, Any], game: dict[str, Any], seat_order: Sequence[int]
) -> dict[str, Any]:
    """``GameConfig`` fields giving each Seat its Talk Budget, or {} for a v1 game.

    ``seat_order[seat]`` is the lineup position seated at ``seat``
    (``Scenario.seat_order``); a realism game maps each Seat's Model to its tier.
    """
    talk = game.get("talk")
    if not talk:
        return {}
    chars = manifest["speed"]["tiers"]
    if talk["mode"] == "random":
        tiers = list(talk["tiers"])
    elif talk["mode"] == "realism":
        entries, _ = seat_lineup(game)
        tiers = [talk["tier_by_model"][entries[pos]] for pos in seat_order]
    else:
        raise ValueError(f"{game['id']}: unknown Talk Budget mode {talk['mode']!r}")
    return {"talk_budgets": tuple(chars[t] for t in tiers), "talk_tiers": tuple(tiers)}


def check_live(manifest: dict[str, Any]) -> None:
    """A live run may not use placeholder throughput for its realism check."""
    realism = (manifest.get("speed") or {}).get("realism") or {}
    if realism.get("games") and not realism.get("measured"):
        raise ValueError(
            "this manifest's realism check uses placeholder throughput (speed.toml); "
            "fill it from pilot data and schedule a new run before playing it live"
        )


def verify_speed(manifest: dict[str, Any]) -> list[str]:
    """The v1 invariants for the tier games, plus every Talk Budget invariant."""
    out = verify(manifest)
    speed = manifest["speed"]
    pool = manifest["pool"]
    games = manifest["games"]
    if set(speed["tiers"]) != set(TIERS):
        out.append(f"tiers must be exactly {TIERS}")
    root = GameRng(manifest["master_seed"])
    base = SEATS // len(TIERS)
    for g in games:
        talk = g.get("talk") or {}
        if g["kind"] == "realism":
            want = {m: speed["realism"]["tier_by_model"][m] for m in g["lineup"]}
            if talk.get("mode") != "realism" or talk.get("tier_by_model") != want:
                out.append(f"{g['id']}: realism tiers do not follow the throughput table")
            if g["seed"] != game_seed(manifest["master_seed"], g["id"]):
                out.append(f"{g['id']}: seed is not derived from the master seed")
            continue
        tiers = talk.get("tiers")
        if talk.get("mode") != "random" or tiers is None:
            out.append(f"{g['id']}: no Talk Budget tiers")
            continue
        counts = Counter(tiers)
        if len(tiers) != SEATS or set(counts) - set(TIERS):
            out.append(f"{g['id']}: needs one known tier per Seat")
        elif min(counts[t] for t in TIERS) < base or max(counts.values()) > base + 1:
            out.append(f"{g['id']}: tiers are not balanced over the Seats")
        elif tiers != draw_tiers(root.fork(f"talk-tiers/{g['id']}")):
            out.append(f"{g['id']}: tiers are not the seeded draw")
    if (
        realism_tiers(pool, speed["realism"]["tokens_per_second"])
        != speed["realism"]["tier_by_model"]
    ):
        out.append("realism tier_by_model does not follow tokens_per_second")
    realism = [g for g in games if g["kind"] == "realism"]
    if len(realism) != speed["realism"]["games"]:
        out.append("realism game count does not match the plan")
    if len({g["seed"] for g in games}) != len(games):
        out.append("duplicate seeds")
    if len({g["id"] for g in games}) != len(games):
        out.append("duplicate game ids")
    if [g["index"] for g in games] != list(range(len(games))):
        out.append("game indexes are not the run order")
    return out


def summary(manifest: dict[str, Any]) -> str:
    kinds = Counter(g["kind"] for g in manifest["games"])
    speed = manifest["speed"]
    realism = speed["realism"]
    chars = ", ".join(f"{t} {speed['tiers'][t]}" for t in TIERS)
    note = "" if realism["measured"] else " (PLACEHOLDER throughput: dry runs only)"
    return (
        f"speed experiment, {len(manifest['games'])} games: {manifest['blocks']} blocks x "
        f"{manifest['block_size']} tier games ({kinds['lobby']} Balanced Lobby, "
        f"{kinds['benign']} Benign) with random per-Seat Talk Budget tiers ({chars} "
        f"characters per pass), and {kinds['realism']} realism-check games with tiers by "
        f"measured throughput{note}"
    )
