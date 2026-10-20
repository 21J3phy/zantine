"""Convenience entry points: build a game from a config and a bot spec."""

from __future__ import annotations

from zantine.config import GameConfig
from zantine.engine import Game
from zantine.rng import GameRng
from zantine.scenario import build_scenario
from zantine.seats.registry import make_controller, parse_spec


def new_game(config: GameConfig, bots: str = "scripted") -> Game:
    """A game whose setup, including who sits where, comes from the seed.

    The spec lists the lineup; the scenario's seat order decides which seat
    each entry fills, so ``scripted*8,idle*2`` does not always idle seats 8-9.
    """
    rng = GameRng(config.seed)
    scenario = build_scenario(config, rng.fork("scenario"))
    seated = scenario.seat(parse_spec(bots, config.num_seats))
    seats_rng = rng.fork("seats")
    controllers = [
        make_controller(entry, seats_rng.fork(f"seat-{seat}")) for seat, entry in enumerate(seated)
    ]
    return Game(config, controllers, rng=rng, scenario=scenario)


def play(config: GameConfig, bots: str = "scripted") -> Game:
    game = new_game(config, bots)
    game.run()
    return game
