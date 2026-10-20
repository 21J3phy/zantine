import pytest

from zantine.map import make_map, tiny_map
from zantine.rng import GameRng


def test_same_seed_same_stream() -> None:
    a, b = GameRng(42), GameRng(42)
    assert [a.random() for _ in range(5)] == [b.random() for _ in range(5)]
    assert GameRng(42).random() != GameRng(43).random()


def test_forks_are_independent_of_other_consumers() -> None:
    root1, root2 = GameRng(7), GameRng(7)
    root1.fork("noise").random()  # another consumer draws first
    assert root1.fork("bots").random() == root2.fork("bots").random()
    assert GameRng(7).fork("a").random() != GameRng(7).fork("b").random()


def test_tiny_map_is_connected_and_symmetric() -> None:
    m = tiny_map()
    assert m.meeting_room in m.rooms
    for room in m.rooms:
        assert m.neighbors(room)
        for n in m.neighbors(room):
            assert room in m.neighbors(n)
            assert m.is_adjacent(room, n)


def test_map_rejects_disconnected_or_bad_edges() -> None:
    with pytest.raises(ValueError):
        make_map(["A", "B", "C"], [("A", "B")], "A")
    with pytest.raises(ValueError):
        make_map(["A", "B"], [("A", "Z")], "A")
