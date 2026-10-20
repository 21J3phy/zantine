"""Mutable ground-truth game state. Only the engine and rule modules mutate it."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from zantine.config import GameConfig
from zantine.map import GameMap
from zantine.roles import Role, Team


@dataclass
class PlayerState:
    seat: int
    name: str
    role: Role
    room: str
    alive: bool = True
    ejected: bool = False
    kill_cooldown: int = 0
    emergency_meetings_left: int = 0

    @property
    def is_impostor(self) -> bool:
        return self.role is Role.IMPOSTOR


@dataclass
class Body:
    victim: int
    room: str
    round: int


@dataclass
class GameState:
    config: GameConfig
    map: GameMap
    players: list[PlayerState]
    round: int = 0
    meetings_held: int = 0
    bodies: list[Body] = field(default_factory=list)
    winner: Team | None = None
    win_reason: str | None = None
    # Per-module state, keyed by module name (e.g. ext["tasks"]). Plug-ins keep
    # their state here instead of adding fields to this class.
    ext: dict[str, Any] = field(default_factory=dict)

    @property
    def over(self) -> bool:
        return self.winner is not None

    def player(self, seat: int) -> PlayerState:
        return self.players[seat]

    def living(self) -> list[PlayerState]:
        return [p for p in self.players if p.alive]

    def living_in(self, room: str) -> list[PlayerState]:
        return [p for p in self.players if p.alive and p.room == room]

    def bodies_in(self, room: str) -> list[Body]:
        return [b for b in self.bodies if b.room == room]

    def roster(self) -> dict[int, str]:
        return {p.seat: p.name for p in self.players}
