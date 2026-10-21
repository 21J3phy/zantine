"""Game setup: map, player names, seat order and role assignment.

Everything here is drawn from the game seed, so the same seed and config
always give the same ``Scenario`` and there is no fixed map or name list to
memorize. Each part uses its own ``GameRng`` fork, so changing one generator
does not shift the others. The engine only ever sees a finished ``Scenario``.

Bump ``SCENARIO_VERSION`` whenever a change here makes an existing seed
generate a different setup; it is recorded in every transcript header.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TypeVar

from zantine.config import GameConfig
from zantine.map import GameMap, make_map, tiny_map
from zantine.rng import GameRng
from zantine.roles import Role

T = TypeVar("T")

SCENARIO_VERSION = 5

# Single words only: scripted bots and transcripts refer to players by name.
NAME_POOL = (
    "Amber",
    "Ash",
    "Azure",
    "Birch",
    "Blaze",
    "Cedar",
    "Cinder",
    "Clover",
    "Cobalt",
    "Coral",
    "Crimson",
    "Dune",
    "Ember",
    "Fern",
    "Flint",
    "Frost",
    "Garnet",
    "Hazel",
    "Indigo",
    "Ivory",
    "Jade",
    "Juniper",
    "Lilac",
    "Maple",
    "Marble",
    "Moss",
    "Ochre",
    "Onyx",
    "Opal",
    "Pebble",
    "Quartz",
    "Raven",
    "Ruby",
    "Russet",
    "Saffron",
    "Scarlet",
    "Slate",
    "Sterling",
    "Teal",
    "Topaz",
    "Umber",
    "Violet",
    "Willow",
    "Zinc",
)

ROOM_POOL = (
    "Admin",
    "Airlock",
    "Archive",
    "Armory",
    "Bridge",
    "Brig",
    "Cafeteria",
    "Cargo",
    "Comms",
    "Electrical",
    "Engine",
    "Galley",
    "Greenhouse",
    "Hangar",
    "Laboratory",
    "Library",
    "Lounge",
    "Medbay",
    "Navigation",
    "Observatory",
    "Oxygen",
    "Reactor",
    "Security",
    "Shields",
    "Storage",
    "Vault",
    "Weapons",
    "Workshop",
)

# The fixed setup used by unit tests that place players in named rooms.
CLASSIC_NAMES = (
    "Red",
    "Blue",
    "Green",
    "Pink",
    "Orange",
    "Yellow",
    "Black",
    "White",
    "Purple",
    "Brown",
)

# Corridors per room are capped so the map stays a map, not a hub.
MAX_CORRIDORS = 4


@dataclass(frozen=True)
class Scenario:
    map: GameMap
    names: tuple[str, ...]
    roles: tuple[Role, ...]
    # seat_order[seat] = the lineup position that sits in that seat. A lineup is
    # the list of controllers as given (e.g. a ``--bots`` spec); see ``seat()``.
    seat_order: tuple[int, ...]

    def seat(self, lineup: Sequence[T]) -> list[T]:
        """Rearrange a lineup (in spec order) into seat order."""
        if len(lineup) != len(self.seat_order):
            raise ValueError(f"lineup has {len(lineup)} entries, need {len(self.seat_order)}")
        return [lineup[i] for i in self.seat_order]


def build_scenario(config: GameConfig, rng: GameRng) -> Scenario:
    """The whole setup, drawn from ``rng`` (the game's ``"scenario"`` fork)."""
    if config.num_seats > len(NAME_POOL):
        raise ValueError(f"only {len(NAME_POOL)} names available")
    return Scenario(
        map=generate_map(config.num_rooms, rng.fork("map")),
        names=tuple(rng.fork("names").sample(NAME_POOL, config.num_seats)),
        roles=_roles(config, rng.fork("roles")),
        seat_order=tuple(rng.fork("seats").shuffled(range(config.num_seats))),
    )


def classic_scenario(config: GameConfig, rng: GameRng) -> Scenario:
    """The hard-coded ``tiny_map()``, colour names and identity seat order.

    Only roles still come from the seed (unless ``impostor_seats`` is set). For
    tests that need known room names; real games use ``build_scenario``.
    """
    if config.num_seats > len(CLASSIC_NAMES):
        raise ValueError(f"only {len(CLASSIC_NAMES)} classic names available")
    return Scenario(
        map=tiny_map(),
        names=CLASSIC_NAMES[: config.num_seats],
        roles=_roles(config, rng.fork("roles")),
        seat_order=tuple(range(config.num_seats)),
    )


def generate_map(num_rooms: int, rng: GameRng) -> GameMap:
    """A connected map of ``num_rooms`` rooms with a few loops.

    A random spanning tree (each new room joins an earlier one) guarantees
    connectivity; extra corridors then add alternative routes. No room gets
    more than ``MAX_CORRIDORS`` corridors. The best-connected room is the
    meeting room, so nobody is too far from it.
    """
    if not 2 <= num_rooms <= len(ROOM_POOL):
        raise ValueError(f"num_rooms must be between 2 and {len(ROOM_POOL)}")
    rooms = rng.sample(ROOM_POOL, num_rooms)
    degree = dict.fromkeys(rooms, 0)
    edges: list[tuple[str, str]] = []

    def connect(a: str, b: str) -> None:
        edges.append((a, b) if a < b else (b, a))
        degree[a] += 1
        degree[b] += 1

    for i, room in enumerate(rooms[1:], start=1):
        open_rooms = [r for r in rooms[:i] if degree[r] < MAX_CORRIDORS]
        connect(room, rng.choice(open_rooms))
    candidates = [
        (a, b)
        for i, a in enumerate(rooms)
        for b in rooms[i + 1 :]
        if (a, b) not in edges and (b, a) not in edges
    ]
    for a, b in rng.sample(
        candidates, min(len(candidates), rng.randint(1, max(1, num_rooms // 2)))
    ):
        if degree[a] < MAX_CORRIDORS and degree[b] < MAX_CORRIDORS:
            connect(a, b)
    meeting_room = max(rooms, key=lambda r: degree[r])  # first among equals
    return make_map(rooms=rooms, edges=sorted(edges), meeting_room=meeting_room)


def _roles(config: GameConfig, rng: GameRng) -> tuple[Role, ...]:
    impostors = config.impostor_seats
    leader = config.leader
    if impostors is None and (leader is None or leader.compromised is None):
        impostors = tuple(rng.sample(range(config.num_seats), config.num_impostors))
    elif impostors is None:
        # A forced Leader role (v1.4): the other Impostors are drawn from the other seats.
        others = [s for s in range(config.num_seats) if s != leader.seat]
        if leader.compromised:
            impostors = (leader.seat, *rng.sample(others, config.num_impostors - 1))
        else:
            impostors = tuple(rng.sample(others, config.num_impostors))
    return tuple(
        Role.IMPOSTOR if seat in impostors else Role.CREWMATE for seat in range(config.num_seats)
    )
