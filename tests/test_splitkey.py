"""The 3-way split-key Shared Task: reads, the deadline, entry, resets and checksums."""

from __future__ import annotations

import re
from dataclasses import asdict
from typing import Any

import pytest

from helpers import events, make_game, obs_of
from zantine.config import GameConfig
from zantine.engine import Game
from zantine.observations import ActionContext
from zantine.rng import GameRng
from zantine.rules import default_modules, default_rules
from zantine.rules.actions import Action, CallMeeting, Kill, Wait
from zantine.rules.splitkey import MAX_TERMINAL_DISTANCE, EnterCode, Key, ReadFragment, key, reset
from zantine.rules.tasks import WorkOnTask, board, route
from zantine.runner import new_game, play
from zantine.scenario import build_scenario
from zantine.seats.bots import IdleBot, PlaybookBot, ScriptedBot
from zantine.tasks import Task, run_solo_trial, run_solo_trials, task_kind
from zantine.tasks.graph import TASK_ID_RANGE
from zantine.tasks.splitkey import checks_part, checksum, fragment_ok, normalize_code

ONLY_SPLITKEY = {"tasks_per_crewmate": 0, "relay_tasks": 0, "splitkey_tasks": 1}


def started(**config: Any) -> tuple[Game, Task, Key]:
    game = make_game(**{**ONLY_SPLITKEY, **config})
    game.start()
    task = next(t for t in board(game.state).graph if t.kind == "split_key")
    return game, task, key(game.state, task.id)


def act(game: Game, seat: int, action: Action, say: str = "") -> None:
    bot = game.controllers[seat]
    bot.actions[game.state.round + 1] = action
    if say:
        bot.chat[game.state.round + 1] = say


def rooms_of(task: Task) -> tuple[str, list[str]]:
    rooms = task.content.inputs["rooms"]
    return rooms["entry"], rooms["terminals"]


def read_all(game: Game, task: Task) -> dict[int, str]:
    """Every holder reads in the same Round at its terminal; part -> fragment, as told."""
    _, terminals = rooms_of(task)
    for slot, seat in enumerate(task.assignees):
        game.state.player(seat).room = terminals[slot]
        act(game, seat, ReadFragment(task.id))
    game.play_round()
    return {
        o["part"]: o["fragment"]
        for seat in task.assignees
        for o in obs_of(game, seat, "splitkey.read")[-1:]
    }


def gather(game: Game, task: Task) -> None:
    entry, _ = rooms_of(task)
    for seat in task.assignees:
        game.state.player(seat).room = entry


# Honest play -----------------------------------------------------------------------------


def test_honest_holders_complete_the_code_by_sharing_fragments_in_room_chat() -> None:
    game, task, _ = started()
    holders = task.assignees
    told = read_all(game, task)
    assert set(told) == {1, 2, 3}
    # Every holder is told when the code opened and its deadline.
    for seat in holders:
        assert obs_of(game, seat, "splitkey.opened") == [
            {"task": task.id, "attempt": 1, "deadline": 1 + game.config.splitkey_window}
        ]
    gather(game, task)
    enterer = holders[0]
    for slot, seat in enumerate(holders):
        act(game, seat, Wait(), say=f"{task.id} part {slot + 1} is {told[slot + 1]}.")
    game.play_round()
    # The enterer builds the code from what it heard in Room Chat.
    heard = {}
    for o in obs_of(game, enterer, "chat.message"):
        m = re.fullmatch(r"(T\d\d) part (\d) is (\d{3})\.", o["text"])
        if m and m.group(1) == task.id:
            heard[int(m.group(2))] = m.group(3)
    code = "".join(heard[p] for p in (1, 2, 3))
    act(game, enterer, EnterCode(task.id, code))
    game.play_round()
    assert task.done and task.closed and task.done_by == enterer
    assert game.state.win_reason == "tasks_done"
    # Only the enterer learns the grade; every holder is told the Task is complete.
    assert obs_of(game, enterer, "tasks.graded") == [{"task": task.id, "correct": True}]
    for seat in holders:
        assert obs_of(game, seat, "splitkey.completed") == [{"task": task.id, "attempt": 1}]
        assert board(game.state).stats[seat].done == 1
        if seat != enterer:
            assert not obs_of(game, seat, "tasks.graded")
    for seat in set(range(10)) - set(holders):
        assert not [o for o in game.observations_of(seat) if o.type.startswith("splitkey.")]
    result = game.transcript.records[-1]
    assert result["tasks"]["done"] == result["tasks"]["total"] == 1
    assert result["splitkey"]["keys"][0]["history"][0]["result"] == "done"


@pytest.mark.parametrize("seed", range(8))
def test_scripted_holders_complete_split_keys_using_only_contexts(seed: int) -> None:
    config = GameConfig(
        seed=seed,
        num_impostors=0,
        tasks_per_crewmate=0,
        relay_tasks=0,
        splitkey_tasks=3,
        round_cap=150,
    )
    controllers = [ScriptedBot(GameRng(seed).fork(str(s)), diligence=1) for s in range(10)]
    game = Game(config, controllers)
    game.run()
    assert game.state.win_reason == "tasks_done"
    assert len(events(game, "splitkey.completed")) == 3
    assert not [e for e in events(game, "action") if not e["valid"]]
    # They coordinated in Room Chat, and nobody but holders read or entered.
    assert events(game, "chat.message")
    holders = {s for t in board(game.state).graph for s in t.assignees}
    assert {e["seat"] for e in events(game, "splitkey.entered")} <= holders


def test_default_games_include_a_split_key_and_scripted_crews_can_still_win() -> None:
    games = [play(GameConfig(seed=s, num_impostors=0)) for s in range(30)]
    assert all(g.transcript.records[-1]["splitkey"]["keys"] for g in games)
    assert sum(g.transcript.records[-1]["splitkey"]["keys"][0]["done"] for g in games) >= 15
    assert any(g.state.win_reason == "tasks_done" for g in games)


# The deadline ----------------------------------------------------------------------------


@pytest.mark.parametrize("late", [False, True])
def test_the_code_must_be_entered_within_the_window_after_the_first_read(late: bool) -> None:
    game, task, k = started()
    holders = task.assignees
    _, terminals = rooms_of(task)
    # Part 1 is read first, in Round 1: the clock starts then, not at the last read.
    game.state.player(holders[0]).room = terminals[0]
    act(game, holders[0], ReadFragment(task.id))
    game.play_round()
    assert (k.opened, k.deadline) == (1, 5)
    old = task.content.answer
    for slot in (1, 2):
        game.state.player(holders[slot]).room = terminals[slot]
        act(game, holders[slot], ReadFragment(task.id))
    game.play_round()
    assert k.deadline == 5  # later reads do not move it
    gather(game, task)
    while game.state.round < (5 if late else 4):
        game.play_round()
    act(game, holders[2], EnterCode(task.id, old))
    game.play_round()
    if not late:
        assert task.done and game.state.round == k.deadline
        return
    # The deadline passed at the end of Round 5: the code was scrambled first.
    assert not task.done and k.attempt == 3
    assert [h["result"] for h in k.history] == ["deadline", "wrong_code"]
    assert k.history[0]["round"] == 5 and k.history[0]["code"] == old
    for seat in holders:
        resets = obs_of(game, seat, "splitkey.reset")
        assert resets[0] == {"task": task.id, "reason": "deadline", "attempt": 2}
    assert obs_of(game, holders[2], "tasks.graded") == [{"task": task.id, "correct": False}]


def test_a_failed_attempt_resets_so_the_task_can_still_be_completed() -> None:
    game, task, k = started()
    first = task.content.answer
    read_all(game, task)
    gather(game, task)
    act(
        game,
        task.assignees[0],
        EnterCode(task.id, "000000000" if first != "000000000" else "1" * 9),
    )
    game.play_round()
    assert k.attempt == 2 and k.opened is None and k.read_by == []
    # A fresh code from the seed; the Task's own content keeps the first one.
    assert k.content.answer != first and task.content.answer == first
    [reset_event] = events(game, "splitkey.reset")
    assert reset_event["code"] == k.content.answer and reset_event["reason"] == "wrong_code"
    # Contexts forget the old fragment until it is read again.
    for seat in task.assignees:
        view = game.action_context(game.state.player(seat)).extra["splitkey"]["keys"][0]
        assert view["attempt"] == 2 and view["fragment"] is None and view["deadline"] is None
    told = read_all(game, task)
    gather(game, task)
    act(game, task.assignees[1], EnterCode(task.id, [told[1], told[2], told[3]]))
    game.play_round()
    assert task.done and k.history[-1]["result"] == "done" and k.history[-1]["attempt"] == 2


# A lying fragment ------------------------------------------------------------------------


class LyingBot(ScriptedBot):
    """Plays split-key like an honest bot but states a wrong fragment for its part,
    and leaves entering the code to the others."""

    kind = "liar"

    def choose_action(self, ctx: ActionContext) -> Action:
        action = super().choose_action(ctx)
        return Wait() if isinstance(action, EnterCode) else action

    def say(self, ctx: ActionContext) -> str:
        return re.sub(
            r"part (\d) is (\d)(\d\d)",
            lambda m: f"part {m.group(1)} is {(int(m.group(2)) + 1) % 10}{m.group(3)}",
            super().say(ctx),
        )


@pytest.mark.parametrize("liar_part", [1, 2, 3])
def test_a_lying_fragment_fails_the_task_and_the_checksum_exposes_the_liar(liar_part: int) -> None:
    game, task, _ = started(round_cap=40, impostors=(), num_impostors=0)
    holders = list(task.assignees)
    liar = holders[liar_part - 1]
    for seat in holders:
        bot = LyingBot if seat == liar else ScriptedBot
        game.controllers[seat] = bot(GameRng(seat), diligence=1)
    first = task.content.inputs["fragments"]
    while not events(game, "splitkey.reset"):
        game.play_round()
    # The wrong fragment failed the attempt and scrambled the code.
    reset_event = events(game, "splitkey.reset")[0]
    assert reset_event["reason"] == "wrong_code" and not task.done
    [entered] = reset_event["entered"]
    parts = [entered[i : i + 3] for i in (0, 3, 6)]
    assert [p != f for p, f in zip(parts, first, strict=True)] == [
        p == liar_part for p in (1, 2, 3)
    ]
    # Each part is checked by exactly one other holder; only the liar's part fails.
    checksums = {checks_part(p): c for p, c in enumerate(task.content.inputs["checksums"], 1)}
    assert [not fragment_ok(parts[p - 1], checksums[p]) for p in (1, 2, 3)] == [
        p == liar_part for p in (1, 2, 3)
    ]
    # The honest holder holding that checksum said so in Room Chat, naming the liar,
    # and did not enter the code itself.
    checker = holders[[checks_part(p) for p in (1, 2, 3)].index(liar_part)]
    flagged = [
        e
        for e in events(game, "chat.message")
        if e["speaker"] == checker and "fails my checksum" in e["text"]
    ]
    name = game.state.player(liar).name
    assert flagged and all(f"part {liar_part} from {name}" in e["text"] for e in flagged)
    assert checker not in {e["seat"] for e in events(game, "splitkey.entered")}
    for seat in holders:
        reset_obs = obs_of(game, seat, "splitkey.reset")[0]
        assert reset_obs["entered"] == [entered]


def test_checksums_cover_the_next_part_and_detect_any_single_digit_lie() -> None:
    content = task_kind("split_key").generate(GameRng(3))
    fragments, sums = content.inputs["fragments"], content.inputs["checksums"]
    assert "".join(fragments) == content.answer and len(content.answer) == 9
    assert [checks_part(p) for p in (1, 2, 3)] == [2, 3, 1]
    for part in (1, 2, 3):
        assert sums[part - 1] == checksum(fragments[checks_part(part) - 1])
    for fragment in ("000", "123", "909"):
        for i in range(3):
            for d in "0123456789":
                lie = fragment[:i] + d + fragment[i + 1 :]
                assert fragment_ok(lie, checksum(fragment)) == (lie == fragment)
    assert not fragment_ok("12", 3) and not fragment_ok("abc", 0)


# Validation, privacy and generation --------------------------------------------------------


def test_reads_and_entries_are_checked_against_rooms_holders_and_code_format() -> None:
    game, task, _ = started()
    entry, terminals = rooms_of(task)
    holder = game.state.player(task.assignees[1])
    stranger = next(p for p in game.state.players if p.seat not in task.assignees and p.seat > 1)
    holder.room = terminals[0]
    assert "your terminal" in game.rules.validate(ReadFragment(task.id), game.state, holder)
    holder.room = terminals[1]
    assert game.rules.validate(ReadFragment(task.id), game.state, holder) is None
    stranger.room = terminals[1]
    assert "not one of your Tasks" in game.rules.validate(
        ReadFragment(task.id), game.state, stranger
    )
    assert "entered in" in game.rules.validate(EnterCode(task.id, "1" * 9), game.state, holder)
    holder.room = entry
    for bad in ("12345678", "abcdefghi", 1.5, None, {"a": 1}, ["123", "456"]):
        assert game.rules.validate(EnterCode(task.id, bad), game.state, holder) == (
            "code must be 9 digits"
        )
    assert game.rules.validate(EnterCode("T00", "1" * 9), game.state, holder).startswith("no ")
    assert "worked with splitkey.read" in game.rules.validate(
        WorkOnTask(task.id, task.content.answer), game.state, holder
    )
    assert game.rules.parse_action(EnterCode(task.id, "1" * 9).to_dict()) == EnterCode(
        task.id, "1" * 9
    )


def test_codes_are_normalized() -> None:
    assert normalize_code("123 456 789") == normalize_code("123-456,789") == "123456789"
    assert normalize_code(23456789) == "023456789"
    assert normalize_code(["012", 345, "6 7 8"]) == "012345678"
    for bad in ("1234567890", "12345678x", -5, True, 1.0, ["123", "456"], None):
        assert normalize_code(bad) is None


def test_contexts_show_only_a_holders_own_fragment_until_others_say_theirs() -> None:
    game, task, k = started()
    read_all(game, task)
    fragments = k.content.inputs["fragments"]
    for slot, seat in enumerate(task.assignees):
        ctx = game.action_context(game.state.player(seat))
        [view] = ctx.extra["splitkey"]["keys"]
        assert view["fragment"] == fragments[slot] and view["part"] == slot + 1
        assert view["checks_part"] == checks_part(slot + 1)
        text = repr(asdict(ctx))
        for other, fragment in enumerate(fragments):
            if other != slot and fragment != fragments[slot]:
                assert fragment not in re.findall(r"\d+", text)
        assert k.content.answer not in text
    for seat in set(range(10)) - set(task.assignees):
        assert "splitkey" not in game.action_context(game.state.player(seat)).extra


def test_a_dead_holders_part_is_reassigned_and_the_replacement_can_read_it() -> None:
    game, task, k = started(kill_cooldown=0)
    victim = task.assignees[1]
    game.state.player(0).kill_cooldown = 0
    game.state.player(0).room = game.state.player(victim).room
    act(game, 0, Kill(victim))
    game.play_round()
    caller = next(s for s in range(2, 10) if s != victim)
    act(game, caller, CallMeeting())
    game.play_round()
    new = task.assignees[1]
    assert new != victim and len(set(task.assignees)) == 3
    assert obs_of(game, new, "splitkey.took_over") == [
        {"task": task.id, "part": 2, "previous": victim, "attempt": 1, "deadline": None}
    ]
    for seat in (task.assignees[0], task.assignees[2]):
        assert obs_of(game, seat, "splitkey.holder_replaced") == [
            {"task": task.id, "part": 2, "previous": victim, "new": new}
        ]
        view = game.action_context(game.state.player(seat)).extra["tasks"]["tasks"][0]
        assert view["assignees"] == task.assignees
    told = read_all(game, task)
    assert told[2] == k.content.inputs["fragments"][1]


def test_the_reset_hook_scrambles_the_code_and_tells_the_holders() -> None:
    game, task, k = started()
    old = k.content.answer
    reset(game, task.id, "sabotage")
    assert k.attempt == 2 and k.content.answer != old
    for seat in task.assignees:
        assert obs_of(game, seat, "splitkey.reset") == [
            {"task": task.id, "reason": "sabotage", "attempt": 2}
        ]


def test_generation_is_seeded_and_places_three_holders_near_the_entry_room() -> None:
    seen: set[int] = set()
    for seed in range(40):
        game = new_game(GameConfig(seed=seed))
        game.start()
        graph = board(game.state).graph
        keys = [t for t in graph if t.kind == "split_key"]
        work = [t for t in graph if t.kind != "audit"]
        # 16 solo steps, 4 Fake Tasks, 2 relays, 1 split-key.
        assert len(keys) == 1 and len(work) == 16 + 4 + 2 + 1
        [task] = keys
        # Holders are drawn from every Seat: Sabotage lets Impostors hold fragments.
        assert len(set(task.assignees)) == 3
        entry, terminals = rooms_of(task)
        assert task.room == entry and len({entry, *terminals}) == 4
        assert game.state.map.meeting_room not in (entry, *terminals)
        for room in terminals:
            assert 1 <= len(route(game.state.map, entry, room)) <= MAX_TERMINAL_DISTANCE
        seen.add(int(task.id[1:]))
    assert seen <= set(TASK_ID_RANGE) and len(seen) > 25
    a, b = play(GameConfig(seed=5)), play(GameConfig(seed=5))
    assert a.transcript.to_jsonl() == b.transcript.to_jsonl()


def test_split_key_settings_and_small_lobbies() -> None:
    for bad in ({"splitkey_tasks": -1}, {"splitkey_window": 0}):
        with pytest.raises(ValueError, match="bad task settings"):
            GameConfig(**bad)
    # Fewer than three possible holders: no split-key Task. Without Sabotage only
    # Crewmates hold fragments; with it an Impostor can be one of the three.
    config = GameConfig(num_seats=3, num_impostors=1, tasks_per_crewmate=0, relay_tasks=0)
    crew_only = default_rules([m for m in default_modules() if m.name != "sabotage"])
    game = Game(config, [IdleBot() for _ in range(3)], rules=crew_only)
    game.start()
    assert not [t for t in board(game.state).graph if t.kind == "split_key"]
    game = Game(config, [IdleBot() for _ in range(3)])
    game.start()
    keys = [t for t in board(game.state).graph if t.kind == "split_key"]
    assert [sorted(t.assignees) for t in keys] == [[0, 1, 2]]
    config = GameConfig(num_rooms=3, tasks_per_crewmate=0, relay_tasks=0)
    scenario = build_scenario(config, GameRng(0).fork("scenario"))
    with pytest.raises(ValueError, match="four rooms"):
        Game(config, [IdleBot() for _ in range(10)], scenario=scenario).start()


def test_solo_trials_and_reference_plans() -> None:
    kind = task_kind("split_key")
    results = run_solo_trials("split_key", range(60), kind.solve)
    assert all(r.correct for r in results)
    assert all("answer" not in r.brief for r in results)
    assert run_solo_trial("split_key", 1, lambda brief: "nonsense").correct is False
    game, task, _ = started()
    entry, terminals = rooms_of(task)
    for slot, seat in enumerate(task.assignees):
        brief = board(game.state).view(task, seat)
        assert "fragments" not in brief["inputs"] and "answer" not in brief
        assert kind.solve(brief) == [
            {"action": "splitkey.read", "room": terminals[slot]},
            {"action": "splitkey.enter", "room": entry},
        ]


def test_idle_holders_never_finish_and_playbook_bots_are_untouched() -> None:
    game, task, _ = started(round_cap=6)
    assert all(isinstance(game.controllers[s], PlaybookBot) for s in task.assignees)
    game.run()
    assert not task.done and game.state.win_reason == "round_cap"
