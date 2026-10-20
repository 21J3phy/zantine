"""Game setup: map, player names and role assignment.

Everything here is drawn from the game seed. Issue #2 replaces the hard-coded
map and names with procedural generation; keep that work inside this module so
the engine only ever sees a finished ``Scenario``.
"""

from __future__ import annotations

from dataclasses import dataclass

from zantine.config import GameConfig
from zantine.map import GameMap, tiny_map
from zantine.rng import GameRng
from zantine.roles import Role

DEFAULT_NAMES = (
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


@dataclass(frozen=True)
class Scenario:
    map: GameMap
    names: tuple[str, ...]
    roles: tuple[Role, ...]


def build_scenario(config: GameConfig, rng: GameRng) -> Scenario:
    if config.num_seats > len(DEFAULT_NAMES):
        raise ValueError(f"only {len(DEFAULT_NAMES)} names available")
    names = DEFAULT_NAMES[: config.num_seats]
    impostors = config.impostor_seats
    if impostors is None:
        impostors = tuple(rng.fork("roles").sample(range(config.num_seats), config.num_impostors))
    roles = tuple(
        Role.IMPOSTOR if seat in impostors else Role.CREWMATE for seat in range(config.num_seats)
    )
    return Scenario(map=tiny_map(), names=names, roles=roles)
