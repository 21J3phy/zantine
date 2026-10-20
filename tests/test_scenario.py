"""Seeded generation: map, names, seat order and roles all come from the seed."""

import re
from collections import Counter

import pytest

from zantine.config import GameConfig
from zantine.rng import GameRng
from zantine.roles import Role
from zantine.runner import new_game, play
from zantine.scenario import (
    MAX_CORRIDORS,
    NAME_POOL,
    ROOM_POOL,
    SCENARIO_VERSION,
    build_scenario,
    generate_map,
)


def scenario(seed: int, **config):
    return build_scenario(GameConfig(seed=seed, **config), GameRng(seed).fork("scenario"))


def test_same_seed_gives_the_same_scenario() -> None:
    for seed in range(10):
        assert scenario(seed) == scenario(seed)


def test_different_seeds_give_different_maps_and_names() -> None:
    scenarios = [scenario(seed) for seed in range(20)]
    assert len({frozenset(s.map.rooms) for s in scenarios}) == 20
    assert len({s.map.edges for s in scenarios}) == 20
    assert len({frozenset(s.names) for s in scenarios}) == 20
    assert len({s.seat_order for s in scenarios}) == 20
    assert len({s.roles for s in scenarios}) > 10
    # Not just reshuffled: two seeds share few rooms and names.
    a, b = scenarios[0], scenarios[1]
    assert set(a.map.rooms) != set(b.map.rooms)
    assert set(a.names) != set(b.names)


@pytest.mark.parametrize("seed", range(30))
def test_generated_map_is_a_connected_map_of_about_8_rooms(seed: int) -> None:
    m = scenario(seed).map  # GameMap itself rejects disconnected maps
    assert len(m.rooms) == 8
    assert set(m.rooms) <= set(ROOM_POOL)
    assert m.meeting_room in m.rooms
    assert len(m.edges) >= len(m.rooms) - 1
    assert len(set(m.edges)) == len(m.edges)
    assert all(a < b for a, b in m.edges)
    degrees = [len(m.neighbors(r)) for r in m.rooms]
    assert all(1 <= d <= MAX_CORRIDORS for d in degrees)
    assert len(m.neighbors(m.meeting_room)) == max(degrees)


def test_room_count_is_configurable() -> None:
    for n in (2, 5, 12, len(ROOM_POOL)):
        assert len(generate_map(n, GameRng(3)).rooms) == n
    for n in (1, len(ROOM_POOL) + 1):
        with pytest.raises(ValueError):
            generate_map(n, GameRng(3))


def test_names_are_unique_single_words_distinct_from_rooms() -> None:
    assert len(set(NAME_POOL)) == len(NAME_POOL)
    assert len(set(ROOM_POOL)) == len(ROOM_POOL)
    assert not set(NAME_POOL) & set(ROOM_POOL)
    assert all(re.fullmatch(r"\w+", n) for n in NAME_POOL + ROOM_POOL)
    for seed in range(10):
        names = scenario(seed).names
        assert len(set(names)) == len(names) == 10


def test_roles_come_from_the_seed_unless_fixed() -> None:
    for seed in range(10):
        roles = scenario(seed).roles
        assert Counter(roles) == {Role.CREWMATE: 8, Role.IMPOSTOR: 2}
    fixed = scenario(4, impostor_seats=(3, 7)).roles
    assert [s for s, r in enumerate(fixed) if r is Role.IMPOSTOR] == [3, 7]


def test_parts_use_independent_streams() -> None:
    # Changing the map size must not reshuffle names, seats or roles.
    a, b = scenario(9), scenario(9, num_rooms=12)
    assert a.map != b.map
    assert (a.names, a.seat_order, a.roles) == (b.names, b.seat_order, b.roles)


def test_seat_order_places_the_lineup_into_seats() -> None:
    s = scenario(5)
    assert sorted(s.seat_order) == list(range(10))
    lineup = [f"entry-{i}" for i in range(10)]
    seated = s.seat(lineup)
    assert [seated[seat] for seat in range(10)] == [lineup[i] for i in s.seat_order]
    with pytest.raises(ValueError):
        s.seat(lineup[:9])


def test_new_game_seats_the_bot_lineup_by_seed() -> None:
    idle_seats = set()
    for seed in range(10):
        game = new_game(GameConfig(seed=seed), "scripted*8,idle*2")
        kinds = [c.describe()["kind"] for c in game.controllers]
        # Lineup positions 8 and 9 are the idle bots.
        expected = ["idle" if i >= 8 else "scripted" for i in game.scenario.seat_order]
        assert kinds == expected
        idle_seats.add(frozenset(s for s, k in enumerate(kinds) if k == "idle"))
    assert len(idle_seats) > 5


@pytest.mark.parametrize("bots", ["scripted", "scripted*8,idle*2", "idle*3,scripted*7"])
def test_rerunning_a_seed_gives_a_byte_identical_transcript(bots: str) -> None:
    for seed in (0, 7, 123456789):
        a = play(GameConfig(seed=seed), bots).transcript.to_jsonl()
        b = play(GameConfig(seed=seed), bots).transcript.to_jsonl()
        assert a == b
        assert a != play(GameConfig(seed=seed + 1), bots).transcript.to_jsonl()


@pytest.mark.parametrize("seed", [0, 1, 42, 2**40])
def test_seed_and_generated_setup_are_recorded_in_the_transcript(seed: int) -> None:
    game = play(GameConfig(seed=seed))
    header = game.transcript.records[0]
    assert header["kind"] == "header"
    assert header["seed"] == seed
    assert header["config"]["seed"] == seed
    assert header["scenario_version"] == SCENARIO_VERSION
    s = scenario(seed)
    assert header["map"] == s.map.to_dict()
    assert [p["name"] for p in header["seats"]] == list(s.names)
    assert [p["role"] for p in header["seats"]] == [r.value for r in s.roles]
