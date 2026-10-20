"""Convenience entry points: build a game from a config and a bot spec."""

from __future__ import annotations

from zantine.config import GameConfig
from zantine.engine import Game
from zantine.rng import GameRng
from zantine.seats.registry import make_controllers


def new_game(config: GameConfig, bots: str = "scripted") -> Game:
    rng = GameRng(config.seed)
    controllers = make_controllers(bots, config.num_seats, rng.fork("seats"))
    return Game(config, controllers, rng=rng)


def play(config: GameConfig, bots: str = "scripted") -> Game:
    game = new_game(config, bots)
    game.run()
    return game
