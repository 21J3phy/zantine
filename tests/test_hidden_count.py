"""Hidden Impostor Count and zero-Impostor games (Benign Matches).

Players are told only "0 to 2 Impostors". These tests check that nothing a
player can see (contexts, observations, Meeting info, vote tallies, Crisis
notices, Task ids) gives the exact number away.
"""

from __future__ import annotations

import dataclasses
import re
from collections import Counter
from typing import Any

import pytest

from helpers import make_game
from zantine.cli import main
from zantine.config import GameConfig
from zantine.harness import agent_loop, neutral
from zantine.observations import ActionContext, MeetingContext
from zantine.rng import GameRng
from zantine.rules.actions import Action, CallMeeting
from zantine.rules.crisis import TriggerCrisis
from zantine.runner import new_game, play
from zantine.seats.base import SeatController
from zantine.seats.bots import PlaybookBot
from zantine.tasks import build_task_graph
from zantine.tasks.graph import TASK_ID_RANGE

COUNT = "0 to 2 Impostors"


class Recorder(SeatController):
    """Passes every call through to ``inner`` and keeps every context it was shown."""

    kind = "recorder"

    def __init__(self, inner: SeatController) -> None:
        self.inner = inner
        self.contexts: list[ActionContext | MeetingContext] = []

    def choose_action(self, ctx: ActionContext) -> Action:
        self.contexts.append(ctx)
        return self.inner.choose_action(ctx)

    def speak(self, ctx: MeetingContext) -> str:
        self.contexts.append(ctx)
        return self.inner.speak(ctx)

    def vote(self, ctx: MeetingContext) -> int | None:
        self.contexts.append(ctx)
        return self.inner.vote(ctx)


def recorded(config: GameConfig) -> tuple[Any, list[Recorder]]:
    game = new_game(config)
    recorders = [Recorder(c) for c in game.controllers]
    game.controllers = list(recorders)
    game.run()
    return game, recorders


def observed(game: Any, seat: int) -> list[dict[str, Any]]:
    """Every observation the seat was given during play.

    ``game_over`` is left out: it arrives after the last decision, and its
    winner may say "none" in a Benign Match.
    """
    return [
        r["data"] | {"type": r["type"]}
        for r in game.transcript.observations(seat)
        if r["type"] != "game_over"
    ]


def as_dicts(contexts: list[Any]) -> list[dict[str, Any]]:
    return [dataclasses.asdict(c) for c in contexts]


def leaks(value: Any, key: str = "") -> list[str]:
    """Anything that could state the number of Impostors."""
    out: list[str] = []
    if isinstance(value, dict):
        for k, v in value.items():
            k = str(k)
            if "impostor" in k.lower() and k not in ("fellow_impostors", "impostor_count"):
                out.append(f"key {k}")
            out += leaks(v, k)
    elif isinstance(value, list | tuple):
        for v in value:
            out += leaks(v, key)
    elif isinstance(value, str):
        if key == "impostor_count" and value != COUNT:
            out.append(f"impostor_count {value!r}")
        text = value.replace(COUNT, "")
        if re.search(r"\d\s*impostor|impostors?\W{0,3}\d", text, re.IGNORECASE):
            out.append(f"text {value!r}")
        if key == "task" and not re.fullmatch(r"T\d\d", value):
            out.append(f"task id {value!r}")
    return out


@pytest.mark.parametrize("impostors", [0, 1, 2])
@pytest.mark.parametrize("seed", range(6))
def test_nothing_a_crewmate_sees_states_the_impostor_count(impostors: int, seed: int) -> None:
    game, recorders = recorded(GameConfig(seed=seed, num_impostors=impostors))
    for p in game.state.players:
        if p.is_impostor:
            continue
        seen = as_dicts(recorders[p.seat].contexts) + observed(game, p.seat)
        assert seen
        assert leaks(seen) == [], f"seat {p.seat}"
        # The fellow-Impostor list is empty for every Crewmate in every game.
        for item in seen:
            if item.get("type") == "role_assigned":
                assert item["fellow_impostors"] == [] and item["impostor_count"] == COUNT


@pytest.mark.parametrize("harness", [neutral, agent_loop])
@pytest.mark.parametrize("impostors", [0, 2])
def test_harness_prompts_say_0_to_2_and_never_the_count(impostors: int, harness: Any) -> None:
    game, recorders = recorded(GameConfig(seed=4, num_impostors=impostors))
    assert COUNT in harness.SYSTEM_PROMPT
    for p in game.state.players:
        if p.is_impostor:
            continue
        for ctx in recorders[p.seat].contexts[::5]:
            if isinstance(ctx, ActionContext):
                request = harness.action_request(game.rules, ctx)
            elif ctx.pass_index is None:
                request = harness.vote_request(game.rules, ctx)
            else:
                request = harness.speak_request(game.rules, ctx)
            prompt = "\n".join([request.system, *request.memory, request.tail])
            assert leaks(prompt) == [], f"seat {p.seat}"


def test_every_context_and_role_notice_says_0_to_2() -> None:
    game, recorders = recorded(GameConfig(seed=2, num_impostors=2))
    for p in game.state.players:
        contexts = recorders[p.seat].contexts
        assert contexts and all(c.impostor_count == COUNT for c in contexts)
        roles = [o for o in observed(game, p.seat) if o["type"] == "role_assigned"]
        assert [r["impostor_count"] for r in roles] == [COUNT]


def test_a_crewmates_view_is_identical_with_0_1_or_2_impostors() -> None:
    """Same play by everyone: a Crewmate's whole view, including Meeting info,
    vote tallies and Crisis state, does not depend on the Impostor count."""

    def views(impostors: tuple[int, ...]) -> list[Any]:
        bots = {seat: PlaybookBot(votes={1: 2, 2: None}) for seat in range(10)}
        bots[3].actions[1] = CallMeeting()
        bots[4].actions[3] = CallMeeting()
        game = make_game(
            bots=bots,
            impostors=impostors,
            num_impostors=len(impostors),
            tasks_per_crewmate=0,
            relay_tasks=0,
            splitkey_tasks=0,
            round_cap=4,
        )
        game.run()
        assert game.state.meetings_held == 2
        crewmate = bots[5]
        return [
            as_dicts(crewmate.action_contexts),
            as_dicts(crewmate.meeting_contexts),
            observed(game, 5),
        ]

    baseline = views(())
    assert views((0,)) == baseline
    assert views((0, 1)) == baseline


def test_crisis_notices_do_not_say_how_many_impostors_there_are() -> None:
    bots = {0: PlaybookBot({1: TriggerCrisis()})}
    game = make_game(bots=bots, crisis_cooldown=0, round_cap=2)
    game.run()
    for seat in range(2, 10):
        crisis = [
            o.data | {"type": o.type} for o in game.observations_of(seat) if "crisis" in o.type
        ]
        assert crisis and leaks(crisis) == []
        ctx = game.action_context(game.state.player(seat))
        assert set(ctx.extra["crisis"]) == {"active", "stations", "deadline"}


def test_task_ids_do_not_depend_on_how_many_tasks_there_are() -> None:
    rooms = ["A", "B", "C"]
    kinds = ["sort_list", "lookup_value"]
    seen: dict[int, set[int]] = {}
    for crew in (8, 10):
        seen[crew] = set()
        for seed in range(60):
            graph = build_task_graph(GameRng(seed), range(crew), rooms, kinds, 2, 0.5)
            ids = [t.id for t in graph]
            assert len(ids) == len(set(ids)) == 2 * crew
            seen[crew] |= {int(i[1:]) for i in ids}
    # Both crew sizes draw from the whole range, so an id like T19 says nothing.
    assert seen[8] == seen[10] == set(TASK_ID_RANGE)


# Zero-Impostor games ------------------------------------------------------------------


@pytest.mark.parametrize("seed", range(10))
def test_a_zero_impostor_game_runs_end_to_end_with_scripted_bots(seed: int) -> None:
    game = play(GameConfig(seed=seed, num_impostors=0))
    state = game.state
    result = game.transcript.records[-1]
    header = game.transcript.records[0]
    assert Counter(s["role"] for s in header["seats"]) == {"crewmate": 10}
    assert (result["winner"], result["reason"]) in (("crew", "tasks_done"), ("none", "round_cap"))
    assert result["reason"] == "tasks_done" or state.round == game.config.round_cap
    assert result["containment"] == {"contained": False, "round": None, "meeting": None}
    assert result["tasks"]["total"] == 20 + game.config.relay_tasks + game.config.splitkey_tasks
    assert not list(game.transcript.events("kill"))
    assert not list(game.transcript.events("crisis.started"))
    assert not list(game.transcript.events("containment"))


def test_zero_impostor_games_can_finish_their_tasks() -> None:
    reasons = {play(GameConfig(seed=s, num_impostors=0)).state.win_reason for s in range(10)}
    assert "tasks_done" in reasons


@pytest.mark.parametrize(
    "config",
    [
        {"num_impostors": 3},
        {"num_impostors": -1},
        {"num_impostors": 1, "impostor_seats": (0, 1)},
        {"num_impostors": 2, "max_impostors": 2, "num_seats": 2},
    ],
)
def test_bad_impostor_counts_are_rejected(config: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        GameConfig(**config)


def test_cli_plays_a_benign_match(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["play", "--seed", "3", "--impostors", "0", "--quiet"]) == 0
    out = capsys.readouterr().out
    assert "impostors win" not in out
    assert re.search(r"(crew win|no winner) by (tasks_done|round_cap)", out)
