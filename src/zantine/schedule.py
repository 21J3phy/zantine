"""The lineup scheduler: Balanced Lobby blocks, Benign Matches and Mirror Matches.

``make_manifest(pool, master_seed, blocks=12, mirror=True)`` plans a whole run
(see ``docs/sample-size.md``) as plain data; ``runs.py`` plays it. Nothing here
touches the engine, so the plan is cheap to build, inspect and verify.

**Block** (28 games with a Model Pool of 7): one Balanced Lobby for each way to
choose 5 of the 7 Models (2 Seats each), plus 7 Benign Matches. Every Model
plays 15 lobbies, every pair of Models meets in 10, and every Model holds
exactly 6 Impostor Seats.

**Impostor assignment (cyclic construction).** Number the Models 0..n-1 in pool
order (n = 7, prime). Rotating every Model index by +1 (mod n) maps lobbies to
lobbies and Impostor Pairings to Impostor Pairings. Because n is prime, every
orbit has exactly n members: the 21 lobbies fall into 3 orbits of 7, and the 28
Impostor Pairings into 4 orbits of 7 (same-Model pairs, and different-Model pairs
whose indices differ by 1, 2 or 3 mod 7). A block's assignment gives each lobby
orbit one pairing orbit: pick one Impostor Pairing for the orbit's first lobby
(both Models must be in it; one is drawn from the master seed) and rotate it
with the lobby, so the orbit's 7 lobbies get each pairing of that pairing orbit
exactly once. A rotation-invariant assignment gives every Model the same number
of Impostor Seats, 42 / 7 = 6, in every block.

**Impostor Pairing balance.** Block b assigns lobby orbit j to pairing orbit
``(3b + j) mod 4``. Over any 4 consecutive blocks the 12 (block, lobby orbit)
slots hit each pairing orbit 3 times, so each of the 28 Impostor Pairings
appears exactly 3 times in every 4-block window (84 lobbies = 3 x 28). The
balance is exact, not approximate, and holds for sliding windows too because
the orbit mapping repeats with period 4. (Other prime pool sizes get the same
construction, with the orbit plan found by bipartite matching; ``verify``
checks every invariant either way.)

**Benign Matches**: block b uses lobby orbit ``b mod 3`` as its 7 lineups, so
all 21 lineups are covered every 3 blocks and every Model is in exactly 5 of a
block's Benign Matches.

**Mirror Matches** (descriptive only): 6 per Model, all 10 Seats one Model, 2
Impostors. They come after every block in the run order.

**Effort sweep** (v1.6, #26; ``make_effort_manifest``): a separate run of
Mirror Matches only, 10 per Model at each of ``low`` and ``high`` effort (140
games with a pool of 7); ``medium`` comes from the v1 run's Mirror Matches. Each
game names its ``effort``, which every Seat plays at (``live:<model>?effort=``).
Games are ordered in rounds of one game per (Model, level), shuffled within the
round, so a run stopped early still has every cell about equally filled.

**Seeds**: every game's seed is derived from the run's master seed and the
game's id (``game_seed``), so it differs per game, is reproducible, and is
listed explicitly in the manifest (for Seed Commitments, ADR 0001). Names, seat
order and the map are drawn from that seed, so they are re-shuffled every game.
A manifest's ``seed_domain`` says which derivation it uses: ``run`` (official
runs; absent in older manifests) gives seeds below 2**63, ``public`` (the Public
Seed Set, ``repro.py``) hashes a different prefix and sets bit 63, so the two
can never share a seed.

**Experiments.** A manifest without an ``experiment`` field is the v1 plan
above (the speed experiment, ``speed.py``, is v1 blocks plus Talk Budgets).
``make_effort_manifest`` plans the v1.6 effort sweep, and
``make_minority_manifest`` the v1.3 Minority Disadvantage experiment
(``"experiment": "minority"``, see its docstring); ``verify`` and ``summary``
dispatch on that field.
"""

from __future__ import annotations

import hashlib
import math
from collections import Counter
from collections.abc import Iterable, Sequence
from itertools import combinations
from typing import Any

from zantine.harness import neutral
from zantine.harness.pricing import EFFORT_LEVELS, load_models
from zantine.rng import GameRng
from zantine.scenario import SCENARIO_VERSION
from zantine.transcript import SCHEMA_VERSION

MANIFEST_VERSION = 1
# A Balanced Lobby seats this many Models, each in SEATS_PER_MODEL Seats.
LOBBY_MODELS = 5
SEATS_PER_MODEL = 2
IMPOSTORS = 2
MIRROR_PER_MODEL = 6
# How a game seed is derived, per seed domain (``game_seed``).
SEED_DERIVATIONS = {
    "run": "int(sha256('zantine-run/<master_seed>/<game_id>')[:8], big) >> 1",
    "public": "2**63 | int(sha256('zantine-public/<master_seed>/<game_id>')[:8], big) >> 1",
}
SEED_DERIVATION = SEED_DERIVATIONS["run"]
# Official (``run``) seeds are below this; Public Seed Set seeds are at or above it.
PUBLIC_SEED_BIT = 1 << 63
EFFORT = "effort"  # manifest["experiment"] of an effort sweep
# Effort sweep: Mirror Matches per Model at each swept level (medium comes from v1).
EFFORT_PER_LEVEL = 10
EFFORT_SWEEP_LEVELS = ("low", "high")

Pairing = tuple[str, str]

# The v1.3 Minority Disadvantage plan's ``experiment`` field (absent = the v1 plan).
MINORITY = "minority"


def load_pool() -> list[str]:
    """The Model Pool, in ``harness/models.toml`` order."""
    return list(load_models())


def game_seed(master_seed: int, game_id: str, domain: str = "run") -> int:
    """A game's seed: a 63-bit hash of the master seed and the game id.

    ``domain="public"`` (the Public Seed Set) hashes another prefix and sets bit
    63, so its seeds are disjoint from every official seed by construction.
    """
    if domain not in SEED_DERIVATIONS:
        raise ValueError(f"unknown seed domain {domain!r}")
    prefix = "zantine-run" if domain == "run" else "zantine-public"
    digest = hashlib.sha256(f"{prefix}/{master_seed}/{game_id}".encode()).digest()
    seed = int.from_bytes(digest[:8], "big") >> 1
    return seed if domain == "run" else PUBLIC_SEED_BIT | seed


def seed_domain(manifest: dict[str, Any]) -> str:
    """``run`` (official) or ``public``; manifests from before seed domains are ``run``."""
    return manifest.get("seed_domain", "run")


def lineups(pool: Sequence[str]) -> list[tuple[str, ...]]:
    """Every Balanced Lobby lineup, in canonical order; a lineup's index is its id."""
    return list(combinations(pool, LOBBY_MODELS))


def pairing_key(a: str, b: str, pool: Sequence[str]) -> Pairing:
    """An Impostor Pairing in pool order (unordered; the same Model twice is allowed)."""
    return (a, b) if pool.index(a) <= pool.index(b) else (b, a)


class CyclicDesign:
    """The rotation-orbit structure behind the Impostor assignment (see module doc)."""

    def __init__(self, pool: Sequence[str]) -> None:
        n = len(pool)
        if len(set(pool)) != n:
            raise ValueError("Model Pool has duplicates")
        if n <= LOBBY_MODELS or not _is_prime(n):
            raise ValueError(
                f"the balanced design needs a prime pool size above {LOBBY_MODELS} "
                f"(rotation orbits must all have n members); got {n} Models"
            )
        self.pool = list(pool)
        self.n = n
        self.lineups = lineups(pool)
        index = {frozenset(self._idx(lu)): i for i, lu in enumerate(self.lineups)}
        # Lobby orbits, in order of first appearance; each starts with its representative.
        self.orbits: list[list[int]] = []
        seen: set[int] = set()
        for i, lu in enumerate(self.lineups):
            if i in seen:
                continue
            base = self._idx(lu)
            orbit = [index[frozenset((x + t) % n for x in base)] for t in range(n)]
            seen.update(orbit)
            self.orbits.append(orbit)
        # Pairing orbits: 0 = same Model twice, d = indices differ by d (1 .. (n-1)/2).
        self.num_pairing_orbits = (n + 1) // 2
        m, p = len(self.orbits), self.num_pairing_orbits
        self.window = p // math.gcd(m, p)  # blocks until every pairing is equally used
        self.per_window = m // math.gcd(m, p)  # times each pairing appears per window
        self.benign_period = m
        self.plan = self._orbit_plan()

    def _idx(self, models: Iterable[str]) -> list[int]:
        return [self.pool.index(x) for x in models]

    def options(self, orbit: int, d: int) -> list[tuple[int, int]]:
        """Impostor Pairings of pairing orbit ``d`` inside the orbit's first lobby."""
        rep = self._idx(self.lineups[self.orbits[orbit][0]])
        return [(x, (x + d) % self.n) for x in rep if (x + d) % self.n in rep]

    def _orbit_plan(self) -> list[list[int]]:
        """plan[phase][lobby orbit] = pairing orbit, for the ``window`` block phases.

        Each pairing orbit must fill exactly ``per_window`` of the window's
        (phase, lobby orbit) slots, and only slots whose lobbies contain such a
        pairing. Slot (b, j) prefers ``(b*m + j) mod p``, which for a pool of 7
        is always feasible and already balanced (each block then uses 3 different
        pairing orbits); otherwise a bipartite matching (augmenting paths) finds
        a balanced plan or proves there is none.
        """
        m, p = len(self.orbits), self.num_pairing_orbits
        slots = [(b, j) for b in range(self.window) for j in range(m)]
        copies = [d for d in range(p) for _ in range(self.per_window)]
        owner: list[int | None] = [None] * len(copies)

        def candidates(s: int) -> list[int]:
            b, j = slots[s]
            preferred = (b * m + j) % p
            ok = [c for c, d in enumerate(copies) if self.options(j, d)]
            return sorted(ok, key=lambda c: (copies[c] != preferred, c))

        def place(s: int, seen: set[int]) -> bool:
            for c in candidates(s):
                if c in seen:
                    continue
                seen.add(c)
                if owner[c] is None or place(owner[c], seen):
                    owner[c] = s
                    return True
            return False

        for s in range(len(slots)):
            if not place(s, set()):
                raise ValueError("no balanced Impostor Pairing plan exists for this pool")
        plan = [[0] * m for _ in range(self.window)]
        for c, s in enumerate(owner):
            assert s is not None
            b, j = slots[s]
            plan[b][j] = copies[c]
        return plan

    def pairing_orbit(self, block: int, orbit: int) -> int:
        """Block ``block`` (0-based) gives lobby orbit ``orbit`` this pairing orbit."""
        return self.plan[block % self.window][orbit]

    def assign(self, block: int, rng: GameRng) -> dict[int, Pairing]:
        """lineup id -> Impostor Pairing for one block (0-based)."""
        n = self.n
        out: dict[int, Pairing] = {}
        for j, orbit in enumerate(self.orbits):
            options = self.options(j, self.pairing_orbit(block, j))
            x, y = rng.fork(f"orbit-{j}").choice(options)
            for t, lineup_id in enumerate(orbit):
                a, b = self.pool[(x + t) % n], self.pool[(y + t) % n]
                out[lineup_id] = pairing_key(a, b, self.pool)
        return out

    def benign(self, block: int) -> list[int]:
        """The lineup ids used for Benign Matches in block ``block`` (0-based)."""
        return sorted(self.orbits[block % self.benign_period])


def make_manifest(
    pool: Sequence[str],
    master_seed: int,
    *,
    blocks: int = 12,
    mirror: bool = False,
    config: dict[str, Any] | None = None,
    domain: str = "run",
) -> dict[str, Any]:
    """The full plan of a run: every game, in run order, with its seed.

    ``domain`` is the seed domain (``game_seed``); only the Public Seed Set uses
    ``public``.
    """
    if blocks < 0:
        raise ValueError("blocks must be >= 0")
    design = CyclicDesign(pool)
    root = GameRng(master_seed)
    games: list[dict[str, Any]] = []
    for b in range(blocks):
        block = b + 1
        pairings = design.assign(b, root.fork(f"pairings/block-{block}"))
        planned = [
            {
                "id": f"b{block:02d}-lobby-{lid:02d}",
                "block": block,
                "kind": "lobby",
                "lineup_id": lid,
                "lineup": list(design.lineups[lid]),
                "impostors": list(pairings[lid]),
                "config": {},
            }
            for lid in range(len(design.lineups))
        ]
        planned += [
            {
                "id": f"b{block:02d}-benign-{lid:02d}",
                "block": block,
                "kind": "benign",
                "lineup_id": lid,
                "lineup": list(design.lineups[lid]),
                "impostors": [],
                "config": {"num_impostors": 0},
            }
            for lid in design.benign(b)
        ]
        # Shuffled within the block, so a run stopped mid-block is not biased by kind.
        games += root.fork(f"order/block-{block}").shuffled(planned)
    if mirror:
        for k in range(MIRROR_PER_MODEL):
            for i, model in enumerate(design.pool):
                games.append(
                    {
                        "id": f"mirror-m{i}-{k + 1}",
                        "block": None,
                        "kind": "mirror",
                        "lineup_id": None,
                        "lineup": [model],
                        "impostors": [model] * IMPOSTORS,
                        "config": {},
                    }
                )
    for index, game in enumerate(games):
        game["index"] = index
        game["seed"] = game_seed(master_seed, game["id"], domain)
    if len({g["seed"] for g in games}) != len(games):  # astronomically unlikely
        raise ValueError("seed collision; choose another master seed")
    manifest = {
        "manifest_version": MANIFEST_VERSION,
        "master_seed": master_seed,
        "seed_derivation": SEED_DERIVATIONS[domain],
        "pool": list(design.pool),
        "blocks": blocks,
        "block_size": len(design.lineups) + design.n,
        "pairing_window": design.window,
        "benign_period": design.benign_period,
        "mirror_per_model": MIRROR_PER_MODEL if mirror else 0,
        "engine": engine_versions(),
        "config": dict(config or {}),
        "games": games,
    }
    if domain != "run":  # official manifests keep their pre-domain format
        manifest["seed_domain"] = domain
    problems = verify(manifest)
    if problems:
        raise AssertionError(f"scheduler produced an invalid plan: {problems[:5]}")
    return manifest


def make_effort_manifest(
    pool: Sequence[str],
    master_seed: int,
    *,
    levels: Sequence[str] = EFFORT_SWEEP_LEVELS,
    per_level: int = EFFORT_PER_LEVEL,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The effort sweep (v1.6): ``per_level`` Mirror Matches per Model per effort level."""
    if per_level < 1:
        raise ValueError("per_level must be >= 1")
    if not levels or len(set(levels)) != len(levels):
        raise ValueError("effort levels must be distinct and non-empty")
    bad = [lvl for lvl in levels if lvl not in EFFORT_LEVELS]
    if bad:
        raise ValueError(f"unknown effort level(s) {bad}; use {', '.join(EFFORT_LEVELS)}")
    if len(set(pool)) != len(pool):
        raise ValueError("Model Pool has duplicates")
    specs = load_models()
    for model in pool:  # refuse a level a Model's lab lacks before anything is played
        if model in specs:
            for level in levels:
                specs[model].at_effort(level)
    root = GameRng(master_seed)
    games: list[dict[str, Any]] = []
    for k in range(per_level):
        planned = [
            {
                "id": f"effort-{level}-m{i}-{k + 1:02d}",
                "block": None,
                "kind": "mirror",
                "lineup_id": None,
                "lineup": [model],
                "impostors": [model] * IMPOSTORS,
                "effort": level,
                "config": {},
            }
            for level in levels
            for i, model in enumerate(pool)
        ]
        games += root.fork(f"order/round-{k + 1}").shuffled(planned)
    for index, game in enumerate(games):
        game["index"] = index
        game["seed"] = game_seed(master_seed, game["id"])
    if len({g["seed"] for g in games}) != len(games):  # astronomically unlikely
        raise ValueError("seed collision; choose another master seed")
    manifest = {
        "manifest_version": MANIFEST_VERSION,
        "experiment": EFFORT,
        "master_seed": master_seed,
        "seed_derivation": SEED_DERIVATION,
        "pool": list(pool),
        "blocks": 0,
        "effort_levels": list(levels),
        "effort_per_level": per_level,
        "mirror_per_model": per_level * len(levels),
        "engine": engine_versions(),
        "config": dict(config or {}),
        "games": games,
    }
    problems = verify(manifest)
    if problems:
        raise AssertionError(f"scheduler produced an invalid plan: {problems[:5]}")
    return manifest


def engine_versions() -> dict[str, Any]:
    """Versions that must not change in the middle of a run."""
    return {
        "scenario_version": SCENARIO_VERSION,
        "transcript_schema": SCHEMA_VERSION,
        "harness": f"{neutral.HARNESS_NAME}-v{neutral.HARNESS_VERSION}",
    }


def seat_lineup(game: dict[str, Any]) -> tuple[list[str], set[int]]:
    """The game's 10 Seats in lineup (spec) order, and which positions are Impostors.

    Each lobby Model holds 2 consecutive positions; a Mirror Match repeats one
    Model 10 times. Each Impostor Model takes its next unused position, so a
    same-Model pairing makes both of that Model's Seats Impostors. Which Seat a
    position fills is decided later by the game seed (``Scenario.seat_order``).
    """
    models = game["lineup"]
    per_model = SEATS_PER_MODEL * LOBBY_MODELS // len(models)
    entries = [m for m in models for _ in range(per_model)]
    impostors: set[int] = set()
    for model in game["impostors"]:
        pos = next(i for i, m in enumerate(entries) if m == model and i not in impostors)
        impostors.add(pos)
    return entries, impostors


# Invariants --------------------------------------------------------------------------


def verify(manifest: dict[str, Any]) -> list[str]:
    """Every scheduling invariant; returns the problems found (empty = valid)."""
    # Speed manifests are v1 blocks plus their own checks on top, so they fall through.
    if manifest.get("experiment") == MINORITY:
        return verify_minority(manifest)
    if manifest.get("experiment") == EFFORT:
        return verify_effort(manifest)
    if manifest.get("experiment") == "leader":
        from zantine.leader.schedule import verify_leader  # imports this module

        return verify_leader(manifest)
    pool = manifest["pool"]
    design = CyclicDesign(pool)
    games = manifest["games"]
    out: list[str] = []
    if len({g["id"] for g in games}) != len(games):
        out.append("duplicate game ids")
    if len({g["seed"] for g in games}) != len(games):
        out.append("duplicate seeds")
    domain = seed_domain(manifest)
    if domain not in SEED_DERIVATIONS:
        return [f"unknown seed domain {domain!r}"]
    for g in games:
        if g["seed"] != game_seed(manifest["master_seed"], g["id"], domain):
            out.append(f"{g['id']}: seed is not derived from the master seed")
        if not set(g["impostors"]) <= set(g["lineup"]):
            out.append(f"{g['id']}: an Impostor Model is not in the lineup")
        if len(g["impostors"]) != (0 if g["kind"] == "benign" else IMPOSTORS):
            out.append(f"{g['id']}: wrong number of Impostors")
    blocks = manifest["blocks"]
    by_block = [[g for g in games if g["block"] == b + 1] for b in range(blocks)]
    for b, block in enumerate(by_block, start=1):
        out += [f"block {b}: {p}" for p in block_problems(block, pool)]
    window = design.window
    pairing_counts = [lobby_pairings(block, pool) for block in by_block]
    for start in range(blocks - window + 1):
        total = sum(pairing_counts[start : start + window], Counter())
        expected = {p: design.per_window for p in all_pairings(pool)}
        if total != expected:
            out.append(f"blocks {start + 1}-{start + window}: Impostor Pairings unbalanced")
    period = design.benign_period
    for start in range(blocks - period + 1):
        covered = sorted(
            g["lineup_id"]
            for block in by_block[start : start + period]
            for g in block
            if g["kind"] == "benign"
        )
        if covered != list(range(len(design.lineups))):
            out.append(f"blocks {start + 1}-{start + period}: Benign lineups not each used once")
    mirrors = Counter(g["lineup"][0] for g in games if g["kind"] == "mirror")
    if mirrors and mirrors != dict.fromkeys(pool, manifest["mirror_per_model"]):
        out.append(f"Mirror Matches unbalanced: {dict(mirrors)}")
    return out


def verify_effort(manifest: dict[str, Any]) -> list[str]:
    """The effort sweep's invariants: only Mirror Matches, each (Model, level) equally often."""
    pool, levels = manifest["pool"], manifest["effort_levels"]
    games = manifest["games"]
    out: list[str] = []
    if len({g["id"] for g in games}) != len(games):
        out.append("duplicate game ids")
    if len({g["seed"] for g in games}) != len(games):
        out.append("duplicate seeds")
    for g in games:
        if g["seed"] != game_seed(manifest["master_seed"], g["id"]):
            out.append(f"{g['id']}: seed is not derived from the master seed")
        if g["kind"] != "mirror" or len(g["lineup"]) != 1:
            out.append(f"{g['id']}: the effort sweep plays only Mirror Matches")
        elif g["impostors"] != [g["lineup"][0]] * IMPOSTORS:
            out.append(f"{g['id']}: wrong Impostors for a Mirror Match")
        if g.get("effort") not in levels:
            out.append(f"{g['id']}: effort {g.get('effort')!r} is not a swept level")
    cells = Counter((g["lineup"][0], g.get("effort")) for g in games)
    want = {(m, lvl): manifest["effort_per_level"] for m in pool for lvl in levels}
    if cells != want:
        out.append(f"effort cells unbalanced: {dict(cells)}")
    return out


def block_problems(block: Sequence[dict[str, Any]], pool: Sequence[str]) -> list[str]:
    n = len(pool)
    lobbies = [g for g in block if g["kind"] == "lobby"]
    benign = [g for g in block if g["kind"] == "benign"]
    out: list[str] = []
    if sorted(tuple(g["lineup"]) for g in lobbies) != sorted(lineups(pool)):
        out.append("lobbies are not every 5-of-n lineup exactly once")
    if len(benign) != n or len({tuple(g["lineup"]) for g in benign}) != n:
        out.append(f"need {n} distinct Benign Match lineups")
    if not {tuple(g["lineup"]) for g in benign} <= set(lineups(pool)):
        out.append("a Benign Match lineup is not a Balanced Lobby lineup")
    plays = Counter(m for g in lobbies for m in g["lineup"])
    meets = Counter(p for g in lobbies for p in combinations(g["lineup"], 2))
    seats = Counter(m for g in lobbies for m in g["impostors"])
    want_plays = math.comb(n - 1, LOBBY_MODELS - 1)
    want_meets = math.comb(n - 2, LOBBY_MODELS - 2)
    want_seats = IMPOSTORS * math.comb(n, LOBBY_MODELS) // n
    if set(plays.values()) != {want_plays}:
        out.append(f"lobby appearances per Model {dict(plays)} != {want_plays}")
    if len(meets) != math.comb(n, 2) or set(meets.values()) != {want_meets}:
        out.append(f"pairs do not all meet {want_meets} times")
    if seats != dict.fromkeys(pool, want_seats):
        out.append(f"Impostor Seats per Model {dict(seats)} != {want_seats}")
    return out


def lobby_pairings(block: Iterable[dict[str, Any]], pool: Sequence[str]) -> Counter[Pairing]:
    return Counter(pairing_key(*g["impostors"], pool=pool) for g in block if g["kind"] == "lobby")


def all_pairings(pool: Sequence[str]) -> list[Pairing]:
    return [(a, b) for i, a in enumerate(pool) for b in pool[i:]]


def summary(manifest: dict[str, Any]) -> str:
    if manifest.get("experiment") == MINORITY:
        return minority_summary(manifest)
    if manifest.get("experiment") == EFFORT:
        return (
            f"{len(manifest['games'])} games: effort sweep, {manifest['effort_per_level']} Mirror "
            f"Matches per Model at each of {', '.join(manifest['effort_levels'])} effort; "
            f"pool of {len(manifest['pool'])}"
        )
    kinds = Counter(g["kind"] for g in manifest["games"])
    return (
        f"{len(manifest['games'])} games: {manifest['blocks']} blocks x "
        f"{manifest['block_size']} ({kinds['lobby']} Balanced Lobby, {kinds['benign']} Benign), "
        f"{kinds['mirror']} Mirror; pool of {len(manifest['pool'])}; Impostor Pairings balanced "
        f"every {manifest['pairing_window']} blocks"
    )


def _is_prime(n: int) -> bool:
    return n >= 2 and all(n % k for k in range(2, math.isqrt(n) + 1))


# v1.3 Minority Disadvantage: Lopsided Lobbies ------------------------------------------

LOPSIDED = "lopsided"  # game kind
MAJORITY_SEATS = 8
MINORITY_SEATS = 2
GAMES_PER_PAIR = 2
# Style Mask conditions: every ordered pair is played once in each per wave.
CONDITIONS = ("off", "mask")


def setup_id(pool: Sequence[str], majority: str, minority: str, wave: int) -> str:
    """The id shared by a setup's mask-off and mask-on games (and the seed source)."""
    return f"lop-m{pool.index(majority)}-m{pool.index(minority)}-{wave}"


def make_minority_manifest(
    pool: Sequence[str],
    master_seed: int,
    *,
    games_per_pair: int = GAMES_PER_PAIR,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The v1.3 plan: Lopsided Lobbies for every ordered pair of Models, mask off and on.

    For each ordered pair (majority, minority) of different Models (42 with a pool
    of 7, so both directions of every pair) and each of ``games_per_pair``
    **waves**, one *setup* is played twice: once without and once with the Style
    Mask (``config: {"style_mask": true}``). 7 x 6 x 2 x 2 = 168 games.

    - **Seats**: 8 of the majority Model, 2 of the minority (``lineup`` lists all
      10 Seats, so ``seat_lineup`` reads it as one Seat per entry).
    - **Roles**: both Impostors are majority Seats (``impostors`` names the
      majority Model twice, which ``runs.game_setup`` turns into fixed Impostor
      Seats). The minority are always Crewmates, so any minority Ejection is wrong.
    - **Matched seeds**: a setup's two games share one seed, derived from the
      setup id, so names, seat order, map and Impostor Seats are identical and the
      Style Mask is the only difference between them. Setups have distinct seeds.
    - **Order**: each wave is shuffled on its own and waves run in order, so a run
      stopped after wave 1 holds every ordered pair in both conditions.
    """
    if games_per_pair < 1:
        raise ValueError("games_per_pair must be >= 1")
    pool = list(pool)
    if len(set(pool)) != len(pool) or len(pool) < 2:
        raise ValueError("the Minority Disadvantage plan needs 2+ distinct Models")
    root = GameRng(master_seed)
    games: list[dict[str, Any]] = []
    for wave in range(1, games_per_pair + 1):
        planned = []
        for majority in pool:
            for minority in pool:
                if majority == minority:
                    continue
                setup = setup_id(pool, majority, minority, wave)
                for condition in CONDITIONS:
                    mask = condition == "mask"
                    planned.append(
                        {
                            "id": f"{setup}-{condition}",
                            "block": None,
                            "kind": LOPSIDED,
                            "wave": wave,
                            "setup": setup,
                            "lineup_id": None,
                            "majority": majority,
                            "minority": minority,
                            "lineup": [majority] * MAJORITY_SEATS + [minority] * MINORITY_SEATS,
                            "impostors": [majority] * IMPOSTORS,
                            "style_mask": mask,
                            "config": {"style_mask": True} if mask else {},
                            "seed": game_seed(master_seed, setup),
                        }
                    )
        games += root.fork(f"order/wave-{wave}").shuffled(planned)
    for index, game in enumerate(games):
        game["index"] = index
    if len({g["seed"] for g in games}) != len(games) // len(CONDITIONS):
        raise ValueError("seed collision; choose another master seed")
    manifest = {
        "manifest_version": MANIFEST_VERSION,
        "experiment": MINORITY,
        "master_seed": master_seed,
        "seed_derivation": SEED_DERIVATION.replace("<game_id>", "<setup>"),
        "pool": pool,
        "games_per_pair": games_per_pair,
        "majority_seats": MAJORITY_SEATS,
        "minority_seats": MINORITY_SEATS,
        "conditions": list(CONDITIONS),
        "engine": engine_versions(),
        "config": dict(config or {}),
        "games": games,
    }
    problems = verify(manifest)
    if problems:
        raise AssertionError(f"scheduler produced an invalid plan: {problems[:5]}")
    return manifest


def verify_minority(manifest: dict[str, Any]) -> list[str]:
    """Every invariant of a Minority Disadvantage plan (empty = valid)."""
    pool = manifest["pool"]
    games = manifest["games"]
    per_pair = manifest["games_per_pair"]
    out: list[str] = []
    if "style_mask" in manifest.get("config", {}):
        out.append("the run-wide config must not set style_mask; each game sets its condition")
    want = len(pool) * (len(pool) - 1) * per_pair * len(CONDITIONS)
    if len(games) != want:
        out.append(f"{len(games)} games, expected {want}")
    if len({g["id"] for g in games}) != len(games):
        out.append("duplicate game ids")
    if [g.get("index") for g in games] != list(range(len(games))):
        out.append("game indexes are not the run order")
    cells: Counter[tuple[str, str, str]] = Counter()
    setups: dict[str, list[dict[str, Any]]] = {}
    waves: dict[int, Counter[tuple[str, str, str]]] = {}
    for g in games:
        gid, maj, mino = g["id"], g.get("majority"), g.get("minority")
        if g.get("kind") != LOPSIDED:
            out.append(f"{gid}: kind is not {LOPSIDED}")
            continue
        if maj not in pool or mino not in pool or maj == mino:
            out.append(f"{gid}: majority/minority must be two different pool Models")
            continue
        if g["lineup"] != [maj] * MAJORITY_SEATS + [mino] * MINORITY_SEATS:
            out.append(
                f"{gid}: lineup is not {MAJORITY_SEATS} majority + {MINORITY_SEATS} minority"
            )
        if g["impostors"] != [maj] * IMPOSTORS:
            out.append(f"{gid}: Impostors must be {IMPOSTORS} majority Seats")
        entries, positions = seat_lineup(g)
        if len(entries) != MAJORITY_SEATS + MINORITY_SEATS or any(
            entries[p] != maj for p in positions
        ):
            out.append(f"{gid}: an Impostor position is not a majority Seat")
        if mino in g["impostors"]:
            out.append(f"{gid}: the minority must always be Crewmates")
        mask = g.get("style_mask")
        condition = "mask" if mask is True else "off" if mask is False else None
        if condition is None:
            out.append(f"{gid}: style_mask must be true or false")
            continue
        if g["config"] != ({"style_mask": True} if mask else {}):
            out.append(f"{gid}: config does not match its Style Mask condition")
        setup = (
            setup_id(pool, maj, mino, g["wave"])
            if g.get("wave") in range(1, per_pair + 1)
            else None
        )
        if setup is None or g.get("setup") != setup or gid != f"{setup}-{condition}":
            out.append(f"{gid}: wave, setup or id does not match its pair and condition")
            continue
        if g["seed"] != game_seed(manifest["master_seed"], setup):
            out.append(f"{gid}: seed is not derived from the master seed and the setup")
        cells[(maj, mino, condition)] += 1
        setups.setdefault(setup, []).append(g)
        waves.setdefault(g["wave"], Counter())[(maj, mino, condition)] += 1
    ordered = [(a, b) for a in pool for b in pool if a != b]
    expected = {(a, b, c): per_pair for a, b in ordered for c in CONDITIONS}
    if cells != expected:
        missing = sorted(set(expected) - {k for k, n in cells.items() if n == per_pair})
        out.append(f"ordered pairs x conditions not each played {per_pair} times: {missing[:3]}")
    seeds = [gs[0]["seed"] for gs in setups.values()]
    if len(set(seeds)) != len(seeds):
        out.append("two setups share a seed")
    for setup, gs in setups.items():
        if sorted(g["style_mask"] for g in gs) != [False, True]:
            out.append(f"{setup}: needs exactly one mask-off and one mask-on game")
    for wave, counts in sorted(waves.items()):
        if counts != {(a, b, c): 1 for a, b in ordered for c in CONDITIONS}:
            out.append(f"wave {wave}: not every ordered pair once in each condition")
    order = [g.get("wave") for g in games]
    if order != sorted(order, key=lambda w: (w is None, w)):
        out.append("waves are not played in order")
    return out


def minority_summary(manifest: dict[str, Any]) -> str:
    pool = manifest["pool"]
    pairs = len(pool) * (len(pool) - 1)
    return (
        f"{len(manifest['games'])} games: Lopsided Lobbies ({manifest['majority_seats']} majority "
        f"vs {manifest['minority_seats']} minority Seats) for {pairs} ordered pairs x "
        f"{manifest['games_per_pair']} waves x Style Mask off/on; Impostors only on majority "
        f"Seats; pool of {len(pool)}"
    )
