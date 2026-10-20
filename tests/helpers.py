"""Shared test helpers."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from zantine.config import GameConfig
from zantine.engine import Game
from zantine.rng import GameRng
from zantine.scenario import classic_scenario
from zantine.seats.base import SeatController
from zantine.seats.bots import PlaybookBot


def make_game(
    bots: Mapping[int, SeatController] | None = None,
    rooms: Mapping[int, str] | None = None,
    impostors: tuple[int, ...] = (0, 1),
    **config: Any,
) -> Game:
    """A 10-seat game with fixed Impostors; unlisted seats are idle PlaybookBots.

    Uses the classic setup (``tiny_map()``, colour names) so tests can name rooms.
    """
    cfg = GameConfig(seed=config.pop("seed", 1), impostor_seats=impostors, **config)
    bots = dict(bots or {})
    controllers = [bots.get(seat) or PlaybookBot() for seat in range(cfg.num_seats)]
    rng = GameRng(cfg.seed)
    scenario = classic_scenario(cfg, rng.fork("scenario"))
    game = Game(cfg, controllers, rng=rng, scenario=scenario)
    for seat, room in (rooms or {}).items():
        game.state.player(seat).room = room
    return game


def obs_of(game: Game, seat: int, type: str) -> list[dict[str, Any]]:
    return [o.data for o in game.observations_of(seat) if o.type == type]


def events(game: Game, type: str) -> list[dict[str, Any]]:
    return [r["data"] for r in game.transcript.events(type)]
