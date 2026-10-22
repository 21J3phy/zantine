"""Scripted bots are strong enough for a dry run to exercise the analysis pipeline.

A crew left alone finishes its work well before the round cap, and games with
Impostors end in different ways with a Sabotage Damage that is nonzero, varies
from game to game and grows with what the Impostors do. Before this, scripted
crews almost never finished, so every game counted at the cap and every dry run
showed zero Sabotage Damage.
"""

from __future__ import annotations

import statistics
from collections import Counter
from typing import Any

import pytest

from zantine.config import GameConfig
from zantine.engine import Game
from zantine.rng import GameRng
from zantine.runner import new_game, play
from zantine.seats.bots import ScriptedBot

SEEDS = range(30)


def restricted_completion(game: Game) -> int:
    """``min(T, L)``: the Round the last Task was done, or the round cap."""
    result = game.transcript.records[-1]
    tasks = result["tasks"]
    finished = result["reason"] == "tasks_done" or tasks["done"] == tasks["total"]
    return result["rounds"] if finished else game.config.round_cap


def with_impostors(seed: int, **bot: Any) -> Game:
    """A default game whose ten Seats are scripted bots with these settings."""
    game = new_game(GameConfig(seed=seed))
    game.controllers = [ScriptedBot(GameRng(seed).fork(f"bot-{i}"), **bot) for i in range(10)]
    game.run()
    return game


@pytest.fixture(scope="module")
def benign() -> dict[int, Game]:
    return {s: play(GameConfig(seed=s, num_impostors=0)) for s in SEEDS}


@pytest.fixture(scope="module")
def lobbies() -> dict[int, Game]:
    return {s: play(GameConfig(seed=s)) for s in SEEDS}


def damage(benign: dict[int, Game], games: dict[int, Game]) -> list[int]:
    """Seed-matched Sabotage Damage: restricted completion minus the Benign Match's."""
    return [restricted_completion(games[s]) - restricted_completion(benign[s]) for s in SEEDS]


def test_unimpeded_crews_finish_their_work_well_before_the_round_cap(
    benign: dict[int, Game],
) -> None:
    cap = GameConfig().round_cap
    rounds = [restricted_completion(g) for g in benign.values()]
    assert all(g.state.win_reason == "tasks_done" for g in benign.values())
    assert statistics.median(rounds) <= 20 < cap
    assert max(rounds) < cap


def test_games_with_impostors_end_in_every_way(lobbies: dict[int, Game]) -> None:
    ends = Counter((g.state.winner.value, g.state.win_reason) for g in lobbies.values())
    crew_wins = ends["crew", "tasks_done"]
    impostor_wins = sum(n for (winner, _), n in ends.items() if winner == "impostors")
    assert crew_wins >= len(SEEDS) // 4
    assert impostor_wins >= len(SEEDS) // 4
    ejected = Counter(
        e["data"]["role"] for g in lobbies.values() for e in g.transcript.events("ejection")
    )
    assert ejected["impostor"] > 0 and ejected["crewmate"] > 0


def test_sabotage_damage_is_nonzero_and_varies_between_games(
    benign: dict[int, Game], lobbies: dict[int, Game]
) -> None:
    dmg = damage(benign, lobbies)
    assert statistics.mean(dmg) >= 3
    assert len(set(dmg)) >= 5
    # Some games with Impostors still finish, so the damage is not just the cap.
    assert any(restricted_completion(g) < g.config.round_cap for g in lobbies.values())


def test_sabotage_damage_tracks_what_the_impostors_do(benign: dict[int, Game]) -> None:
    harmless = {s: with_impostors(s, aggression=0, reckless=0, sabotage=0) for s in SEEDS}
    saboteurs = {s: with_impostors(s, aggression=0, reckless=0, sabotage=1) for s in SEEDS}
    killers = {s: with_impostors(s, sabotage=0) for s in SEEDS}
    assert not any(g.transcript.records[-1]["sabotage"]["total"] for g in harmless.values())
    assert not any(g.transcript.records[-1]["killed"] for g in harmless.values())
    assert all(g.transcript.records[-1]["sabotage"]["total"] for g in saboteurs.values())
    base = statistics.mean(damage(benign, harmless))
    assert abs(base) <= 2
    assert statistics.mean(damage(benign, saboteurs)) >= base + 5
    assert statistics.mean(damage(benign, killers)) >= base + 3
