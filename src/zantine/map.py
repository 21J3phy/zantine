"""Rooms and the corridors between them."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class GameMap:
    rooms: tuple[str, ...]
    # Undirected corridors, stored once each as (a, b).
    edges: tuple[tuple[str, str], ...]
    # Everyone starts here and returns here after each Meeting.
    meeting_room: str

    def __post_init__(self) -> None:
        if len(set(self.rooms)) != len(self.rooms):
            raise ValueError("duplicate room names")
        if self.meeting_room not in self.rooms:
            raise ValueError(f"unknown meeting room {self.meeting_room!r}")
        for a, b in self.edges:
            if a not in self.rooms or b not in self.rooms or a == b:
                raise ValueError(f"bad corridor {a!r}-{b!r}")
        if not self._connected():
            raise ValueError("map is not connected")

    def neighbors(self, room: str) -> tuple[str, ...]:
        out = [b for a, b in self.edges if a == room] + [a for a, b in self.edges if b == room]
        return tuple(sorted(out))

    def is_adjacent(self, a: str, b: str) -> bool:
        return (a, b) in self.edges or (b, a) in self.edges

    def to_dict(self) -> dict[str, Any]:
        return {
            "rooms": list(self.rooms),
            "edges": [list(e) for e in self.edges],
            "meeting_room": self.meeting_room,
        }

    def _connected(self) -> bool:
        seen = {self.rooms[0]}
        frontier: list[str] = [self.rooms[0]]
        while frontier:
            for n in self.neighbors(frontier.pop()):
                if n not in seen:
                    seen.add(n)
                    frontier.append(n)
        return seen == set(self.rooms)


def make_map(rooms: Iterable[str], edges: Iterable[tuple[str, str]], meeting_room: str) -> GameMap:
    return GameMap(tuple(rooms), tuple(edges), meeting_room)


def tiny_map() -> GameMap:
    """The hard-coded 6-room map used until seeded generation (issue #2)."""
    return make_map(
        rooms=["Cafeteria", "Weapons", "Navigation", "Storage", "Electrical", "Medbay"],
        edges=[
            ("Cafeteria", "Weapons"),
            ("Cafeteria", "Medbay"),
            ("Cafeteria", "Storage"),
            ("Weapons", "Navigation"),
            ("Navigation", "Storage"),
            ("Storage", "Electrical"),
            ("Electrical", "Medbay"),
        ],
        meeting_room="Cafeteria",
    )
