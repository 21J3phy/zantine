"""Convenience entry points: build a game from a config and a bot spec."""

from __future__ import annotations

from typing import TYPE_CHECKING

from zantine.config import GameConfig
from zantine.engine import Game
from zantine.rng import GameRng
from zantine.scenario import build_scenario
from zantine.seats.registry import make_controller, parse_spec

if TYPE_CHECKING:
    from zantine.stylemask import StyleMask


def new_game(
    config: GameConfig, bots: str = "scripted", style_mask: StyleMask | None = None
) -> Game:
    """A game whose setup, including who sits where, comes from the seed.

    The spec lists the lineup; the scenario's seat order decides which seat
    each entry fills, so ``scripted*8,idle*2`` does not always idle seats 8-9.
    With ``config.style_mask`` on and no ``style_mask``, the dry-run mask is used.
    """
    rng = GameRng(config.seed)
    scenario = build_scenario(config, rng.fork("scenario"))
    seated = scenario.seat(parse_spec(bots, config.num_seats))
    seats_rng = rng.fork("seats")
    controllers = [
        make_controller(entry, seats_rng.fork(f"seat-{seat}")) for seat, entry in enumerate(seated)
    ]
    return Game(config, controllers, rng=rng, scenario=scenario, style_mask=style_mask)


def play(config: GameConfig, bots: str = "scripted", style_mask: StyleMask | None = None) -> Game:
    game = new_game(config, bots, style_mask)
    game.run()
    return game
