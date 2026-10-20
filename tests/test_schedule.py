"""The lineup scheduler: block structure, Impostor balance, Benign rotation, seeds."""

from __future__ import annotations

import copy
from collections import Counter
from itertools import combinations
from typing import Any

import pytest

from zantine.harness.pricing import load_models
from zantine.schedule import (
    CyclicDesign,
    all_pairings,
    block_problems,
    game_seed,
    load_pool,
    lobby_pairings,
    make_manifest,
    seat_lineup,
    verify,
)

POOL = load_pool()


@pytest.fixture(scope="module")
def full() -> dict[str, Any]:
    """The v1 plan: 12 blocks plus Mirror Matches (378 games)."""
    return make_manifest(POOL, 20261020, blocks=12, mirror=True)


def block(manifest: dict[str, Any], b: int) -> list[dict[str, Any]]:
    return [g for g in manifest["games"] if g["block"] == b]


def test_the_pool_is_the_seven_models_in_models_toml() -> None:
    assert list(load_models()) == POOL
    assert len(POOL) == 7


def test_v1_plan_has_378_games_and_passes_every_invariant(full: dict[str, Any]) -> None:
    kinds = Counter(g["kind"] for g in full["games"])
    assert kinds == {"lobby": 252, "benign": 84, "mirror": 42}
    assert full["block_size"] == 28 and full["pairing_window"] == 4
    assert full["benign_period"] == 3
    assert verify(full) == []


@pytest.mark.parametrize("b", [1, 2, 7, 12])
def test_a_block_has_all_21_lobbies_and_7_benign_matches(full: dict[str, Any], b: int) -> None:
    games = block(full, b)
    assert len(games) == 28
    lobbies = [g for g in games if g["kind"] == "lobby"]
    benign = [g for g in games if g["kind"] == "benign"]
    assert sorted(tuple(g["lineup"]) for g in lobbies) == sorted(combinations(POOL, 5))
    assert len(benign) == 7 and all(g["impostors"] == [] for g in benign)
    assert all(g["config"] == {"num_impostors": 0} for g in benign)
    plays = Counter(m for g in lobbies for m in g["lineup"])
    meets = Counter(p for g in lobbies for p in combinations(g["lineup"], 2))
    impostor_seats = Counter(m for g in lobbies for m in g["impostors"])
    assert plays == dict.fromkeys(POOL, 15)
    assert len(meets) == 21 and set(meets.values()) == {10}
    assert impostor_seats == dict.fromkeys(POOL, 6)
    for g in lobbies:
        assert len(g["impostors"]) == 2 and set(g["impostors"]) <= set(g["lineup"])
    assert block_problems(games, POOL) == []


def test_each_impostor_pairing_appears_3_times_in_every_4_block_window(
    full: dict[str, Any],
) -> None:
    counts = [lobby_pairings(block(full, b), POOL) for b in range(1, 13)]
    assert len(all_pairings(POOL)) == 28
    for start in range(12 - 4 + 1):  # sliding windows, not just blocks 1-4, 5-8, 9-12
        window = sum(counts[start : start + 4], Counter())
        assert window == dict.fromkeys(all_pairings(POOL), 3), f"window at block {start + 1}"
        same = sum(n for (a, b), n in window.items() if a == b)
        assert same == 21  # 7 same-Model pairings x 3; both of that Model's Seats
        assert sum(window.values()) - same == 63  # 21 different-Model pairings x 3


def test_benign_matches_rotate_through_all_21_lineups_every_3_blocks(
    full: dict[str, Any],
) -> None:
    benign = [[g for g in block(full, b) if g["kind"] == "benign"] for b in range(1, 13)]
    for start in range(12 - 3 + 1):
        lineups = [tuple(g["lineup"]) for bl in benign[start : start + 3] for g in bl]
        assert sorted(lineups) == sorted(combinations(POOL, 5))
    for bl in benign:  # and each block's 7 are themselves balanced across Models
        assert Counter(m for g in bl for m in g["lineup"]) == dict.fromkeys(POOL, 5)
        lobby_ids = {g["lineup_id"] for g in bl}
        assert len(lobby_ids) == 7


def test_mirror_matches_are_6_per_model_with_2_impostors_after_the_blocks(
    full: dict[str, Any],
) -> None:
    mirrors = [g for g in full["games"] if g["kind"] == "mirror"]
    assert Counter(g["lineup"][0] for g in mirrors) == dict.fromkeys(POOL, 6)
    assert all(g["impostors"] == g["lineup"] * 2 and g["block"] is None for g in mirrors)
    assert full["games"][-42:] == mirrors
    entries, impostors = seat_lineup(mirrors[0])
    assert entries == mirrors[0]["lineup"] * 10 and len(impostors) == 2


def test_every_game_has_its_own_seed_derived_from_the_master_seed(full: dict[str, Any]) -> None:
    seeds = [g["seed"] for g in full["games"]]
    assert len(set(seeds)) == len(seeds) == 378
    assert all(0 <= s < 2**63 for s in seeds)
    for g in full["games"]:
        assert g["seed"] == game_seed(20261020, g["id"])
    assert [g["index"] for g in full["games"]] == list(range(378))


def test_same_master_seed_gives_the_same_manifest() -> None:
    a = make_manifest(POOL, 99, blocks=4, mirror=True)
    assert make_manifest(POOL, 99, blocks=4, mirror=True) == a
    b = make_manifest(POOL, 100, blocks=4, mirror=True)
    assert not {g["seed"] for g in a["games"]} & {g["seed"] for g in b["games"]}
    # The plan differs too (which pairing each lobby gets, and the run order) but
    # every invariant still holds.
    assert [g["id"] for g in a["games"]] != [g["id"] for g in b["games"]]
    assert verify(b) == []


def test_seat_lineup_puts_the_scheduled_models_on_impostor_positions() -> None:
    a, b, c, d, e = POOL[:5]
    game = {"lineup": [a, b, c, d, e], "impostors": [b, d]}
    entries, impostors = seat_lineup(game)
    assert entries == [a, a, b, b, c, c, d, d, e, e]
    assert sorted(entries[i] for i in impostors) == [b, d]
    entries, impostors = seat_lineup({"lineup": [a, b, c, d, e], "impostors": [c, c]})
    assert impostors == {4, 5}  # both of that Model's Seats
    assert seat_lineup({"lineup": [a, b, c, d, e], "impostors": []})[1] == set()


def test_verify_catches_a_broken_plan(full: dict[str, Any]) -> None:
    broken = copy.deepcopy(full)
    lobby = next(g for g in broken["games"] if g["kind"] == "lobby")
    lobby["impostors"] = [lobby["lineup"][0], lobby["lineup"][0]]
    if lobby["impostors"] == full["games"][lobby["index"]]["impostors"]:
        lobby["impostors"] = [lobby["lineup"][1], lobby["lineup"][1]]
    problems = verify(broken)
    assert any("Impostor Seats per Model" in p for p in problems)
    assert any("unbalanced" in p for p in problems)
    broken = copy.deepcopy(full)
    broken["games"][0]["seed"] += 1
    assert any("not derived" in p for p in verify(broken))


def test_the_7_model_orbit_plan_is_the_closed_form() -> None:
    design = CyclicDesign(POOL)
    assert (design.window, design.per_window, len(design.orbits)) == (4, 3, 3)
    assert design.plan == [[(3 * b + j) % 4 for j in range(3)] for b in range(4)]


def test_other_prime_pools_are_balanced_too_and_others_are_refused() -> None:
    pool = [f"m{i}" for i in range(11)]
    manifest = make_manifest(pool, 1, blocks=2)
    assert verify(manifest) == []
    for n in (5, 6, 8):
        with pytest.raises(ValueError, match="prime pool size"):
            CyclicDesign([f"m{i}" for i in range(n)])
