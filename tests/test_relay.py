"""Chain relay timing, recovery, private progress, and the Sabotage/Audit hooks."""

from dataclasses import asdict
from typing import Any

import pytest

from helpers import events, make_game, obs_of
from zantine.config import GameConfig
from zantine.engine import Game
from zantine.rng import GameRng
from zantine.rules.actions import Action, CallMeeting, Kill, Move
from zantine.rules.relay import Deliver, HandOff, Part, PickUp, part, parts, tamper
from zantine.rules.tasks import WorkOnTask, board, route
from zantine.runner import new_game
from zantine.seats.bots import IdleBot, ScriptedBot
from zantine.tasks import Task, run_solo_trial, task_kind
from zantine.tasks.graph import TASK_ID_RANGE, task_ids


def started(**config: Any) -> tuple[Game, Task, Part]:
    game = make_game(
        **{
            "relay_tasks": 1,
            "tasks_per_crewmate": 0,
            "audit_tasks": 0,
            "splitkey_tasks": 0,
            **config,
        }
    )
    game.start()
    task = next(t for t in board(game.state).graph if t.kind == "chain_relay")
    return game, task, part(game.state, task.id)


def act(game: Game, seat: int, action: Action) -> None:
    game.controllers[seat].actions[game.state.round + 1] = action


def step(game: Game, seat: int, action: Action) -> None:
    act(game, seat, action)
    game.play_round()


def travel(game: Game, seat: int, room: str) -> None:
    for stop in route(game.state.map, game.state.player(seat).room, room):
        step(game, seat, Move(stop))


def pick_up(game: Game, task: Task, p: Part) -> int:
    first = task.assignees[0]
    travel(game, first, p.rooms["pickup"])
    step(game, first, PickUp(task.id))
    assert p.holder == first
    return first


def hand_off(game: Game, task: Task, p: Part) -> tuple[int, int]:
    first = pick_up(game, task, p)
    second = task.assignees[1]
    travel(game, first, p.rooms["handoff"])
    travel(game, second, p.rooms["handoff"])
    step(game, first, HandOff(task.id))
    assert p.holder == second
    return first, second


def test_carriers_complete_a_relay_across_three_rooms_and_both_get_credit() -> None:
    game, task, p = started()
    first, second = hand_off(game, task, p)
    assert not task.done and p.leg == 1
    assert task.id in board(game.state).known_done[first]
    travel(game, second, p.rooms["delivery"])
    step(game, second, Deliver(task.id))
    assert task.done and task.closed and task.done_by == second
    assert game.state.win_reason == "tasks_done"
    assert len(set(p.rooms.values())) == 3
    assert p.location == p.rooms["delivery"] and p.holder is None
    assert board(game.state).stats[first].done == board(game.state).stats[second].done == 1
    result = game.transcript.records[-1]
    assert result["tasks"]["done"] == result["tasks"]["total"] == 1
    assert result["relay"]["relays"][0]["tampered"] is False
    for seat in set(range(10)) - {first, second}:
        assert not [o for o in game.observations_of(seat) if o.type.startswith("relay.")]


@pytest.mark.parametrize("receiver_move", ["arrive", "leave", "absent", "stay"])
def test_handoff_uses_receiver_end_of_round_room(receiver_move: str) -> None:
    game, task, p = started()
    first = pick_up(game, task, p)
    second = task.assignees[1]
    room = p.rooms["handoff"]
    neighbor = game.state.map.neighbors(room)[0]
    game.state.player(first).room = room
    game.state.player(second).room = room if receiver_move in ("leave", "stay") else neighbor
    if receiver_move in ("arrive", "leave"):
        act(game, second, Move(room if receiver_move == "arrive" else neighbor))
    step(game, first, HandOff(task.id))
    success = receiver_move in ("arrive", "stay")
    assert p.holder == (second if success else first)
    assert p.leg == int(success)
    assert bool(events(game, "relay.handed_off")) == success
    assert bool(obs_of(game, second, "relay.received")) == success
    assert bool(obs_of(game, first, "relay.handoff_failed")) != success
    assert not obs_of(game, first, "invalid_action")


@pytest.mark.parametrize("victim_role", ["sender", "receiver"])
def test_kill_cancels_handoff_and_leaves_part_recoverable(victim_role: str) -> None:
    game, task, p = started(kill_cooldown=0)
    first = pick_up(game, task, p)
    second = task.assignees[1]
    room = p.rooms["handoff"]
    for seat in (0, first, second):
        game.state.player(seat).room = room
    victim = first if victim_role == "sender" else second
    act(game, 0, Kill(victim))
    step(game, first, HandOff(task.id))
    assert not events(game, "relay.handed_off") and p.leg == 0
    assert p.holder == (None if victim == first else first)
    if victim == first:
        assert p.location == room
        assert events(game, "relay.dropped")[-1]["reason"] == "killed"
    step(game, 8 if victim != 8 else 9, CallMeeting())
    assert victim not in task.assignees and len(set(task.assignees)) == 2
    new = task.assignees[0 if victim == first else 1]
    assert obs_of(game, new, "relay.took_over")
    survivor = second if victim == first else first
    ctx = game.action_context(game.state.player(survivor))
    view = next(t for t in ctx.extra["tasks"]["tasks"] if t["task"] == task.id)
    assert view["assignees"] == task.assignees
    initial = obs_of(game, survivor, "tasks.assigned")[0]["tasks"][0]
    assert victim in initial["assignees"]  # Past observations are immutable.
    if victim == first:
        travel(game, new, room)
        step(game, new, PickUp(task.id))
        assert p.holder == new
    first, second = task.assignees
    travel(game, first, room)
    travel(game, second, room)
    step(game, first, HandOff(task.id))
    travel(game, second, p.rooms["delivery"])
    step(game, second, Deliver(task.id))
    assert task.done


def test_killed_deliverer_drops_part_and_replacement_finishes_second_leg() -> None:
    game, task, p = started(kill_cooldown=0)
    _, second = hand_off(game, task, p)
    room = p.rooms["delivery"]
    travel(game, second, room)
    game.state.player(0).room = room
    act(game, 0, Kill(second))
    step(game, second, Deliver(task.id))
    assert not p.delivered and not task.done
    assert p.holder is None and p.location == room and p.leg == 1
    step(game, next(s for s in range(2, 10) if s != second), CallMeeting())
    new = task.assignees[1]
    assert new != second and new != task.assignees[0]
    travel(game, new, room)
    step(game, new, PickUp(task.id))
    step(game, new, Deliver(task.id))
    assert task.done and task.done_by == new


@pytest.mark.parametrize("leg", [0, 1])
def test_ejected_carrier_leaves_part_in_meeting_room_for_replacement(leg: int) -> None:
    game, task, p = started()
    if leg:
        hand_off(game, task, p)
    else:
        pick_up(game, task, p)
    holder = p.holder
    assert holder is not None
    for bot in game.controllers:
        bot.votes[1] = holder
    step(game, next(s for s in range(2, 10) if s != holder), CallMeeting())
    assert game.state.player(holder).ejected
    assert p.holder is None and p.location == game.state.map.meeting_room
    assert events(game, "relay.dropped")[-1]["reason"] == "ejected"
    new = task.assignees[leg]
    assert new != holder
    view = game.action_context(game.state.player(new)).extra["relay"]["relays"][0]
    assert view["next"] == {"action": PickUp.kind, "room": p.location, "path": []}
    step(game, new, PickUp(task.id))
    assert p.holder == new and p.leg == leg


def test_tamper_state_survives_a_carriers_death_and_recovery() -> None:
    game, task, p = started(kill_cooldown=0)
    first = pick_up(game, task, p)
    tamper(game, task.id, 0)
    serial = p.serial
    room = game.state.player(first).room
    game.state.player(0).room = room
    step(game, 0, Kill(first))
    step(game, next(s for s in range(2, 10) if s != first), CallMeeting())
    new = task.assignees[0]
    travel(game, new, room)
    step(game, new, PickUp(task.id))
    assert p.holder == new and p.serial == serial and p.tampered
    assert p.tampered_by == [0]
    assert "tampered" not in repr(game.action_context(game.state.player(new)))


def test_meeting_retains_a_living_carriers_part() -> None:
    game, task, p = started()
    first = pick_up(game, task, p)
    step(game, first, CallMeeting())
    assert p.holder == first and p.room(game.state) == game.state.map.meeting_room
    assert not events(game, "relay.dropped")


def test_tampered_and_intact_relays_are_indistinguishable_to_every_player() -> None:
    def run(corrupt: bool) -> Game:
        game, task, p = started(round_cap=29)
        if corrupt:
            before = [game.observations_of(s) for s in range(10)]
            tamper(game, task.id, 0)
            assert [game.observations_of(s) for s in range(10)] == before
            assert p.tampered and p.serial != task.content.answer
        _, second = hand_off(game, task, p)
        travel(game, second, p.rooms["delivery"])
        step(game, second, Deliver(task.id))
        return game

    clean, corrupt = run(False), run(True)
    for seat in range(10):
        # The crew win itself is observable only after play ends.
        assert [o for o in clean.observations_of(seat) if o.type != "game_over"] == [
            o for o in corrupt.observations_of(seat) if o.type != "game_over"
        ]
        assert (
            asdict(clean.action_context(clean.state.player(seat)))["extra"]
            == asdict(corrupt.action_context(corrupt.state.player(seat)))["extra"]
        )
        assert "tampered" not in repr(corrupt.action_context(corrupt.state.player(seat)).extra)
    task = next(iter(board(corrupt.state).graph))
    assert not task.done and task.closed
    assert part(corrupt.state, task.id).tampered_by == [0]
    assert events(corrupt, "relay.tampered")[0]["tampered"] is True
    assert events(corrupt, "relay.delivered")[0]["correct"] is False
    assert events(clean, "relay.delivered")[0]["correct"] is True
    # A Meeting cannot reveal the failed delivery through reassignment.
    deliverer = task.assignees[1]
    for bot in corrupt.controllers:
        bot.votes[1] = deliverer
    step(corrupt, task.assignees[0], CallMeeting())
    assert not events(corrupt, "tasks.reassigned")
    corrupt.run()
    assert corrupt.transcript.records[-1]["relay"]["relays"][0]["tampered"] is True
    with pytest.raises(ValueError, match="already delivered"):
        tamper(corrupt, task.id, 0)


def test_relay_context_changes_only_when_the_carrier_is_told() -> None:
    game, task, p = started()
    second = task.assignees[1]
    before = game.action_context(game.state.player(second)).extra["relay"]
    pick_up(game, task, p)
    # Pickup elsewhere is not disclosed to the waiting carrier.
    assert game.action_context(game.state.player(second)).extra["relay"] == before
    tamper(game, task.id, 0)
    assert game.action_context(game.state.player(second)).extra["relay"] == before


def test_pickup_waits_for_prerequisites_completed_in_an_earlier_round() -> None:
    game, task, p = started(tasks_per_crewmate=1, task_dependency_chance=0)
    first = task.assignees[0]
    pre = next(
        t for t in board(game.state).graph if t.kind != task.kind and first not in t.assignees
    )
    task.requires = (pre.id,)
    game.state.player(first).room = p.location
    worker = pre.assignees[0]
    game.state.player(worker).room = pre.room
    act(game, worker, WorkOnTask(pre.id, pre.content.answer))
    step(game, first, PickUp(task.id))
    assert pre.done and p.holder is None
    assert obs_of(game, first, "tasks.blocked") == [{"task": task.id, "waiting_on": [pre.id]}]
    step(game, first, PickUp(task.id))
    assert p.holder == first


@pytest.mark.parametrize("action_type", [PickUp, HandOff, Deliver])
def test_relay_actions_parse_and_reject_unknown_malformed_or_unassigned_tasks(action_type) -> None:
    game, task, _ = started()
    actor = game.state.player(task.assignees[0])
    assert game.rules.parse_action(action_type(task.id).to_dict()) == action_type(task.id)
    for value in ("missing", [], None):
        assert game.rules.validate(action_type(value), game.state, actor)
    assert game.rules.validate(action_type(task.id), game.state, game.state.player(0))


def test_relay_cannot_be_finished_by_submitting_the_hidden_serial_to_tasks_work() -> None:
    game, task, _ = started()
    first, second = task.assignees
    game.state.player(first).room = task.room
    assert "worked with relay." in game.rules.validate(
        WorkOnTask(task.id, task.content.answer), game.state, game.state.player(first)
    )
    assert PickUp(task.id).validate(game.state, game.state.player(second))
    assert HandOff(task.id).validate(game.state, game.state.player(first))
    assert Deliver(task.id).validate(game.state, game.state.player(first))
    pick_up(game, task, part(game.state, task.id))
    assert PickUp(task.id).validate(game.state, game.state.player(first))
    assert HandOff(task.id).validate(game.state, game.state.player(first))
    assert Deliver(task.id).validate(game.state, game.state.player(first))


@pytest.mark.parametrize("seed", range(10))
def test_scripted_carriers_can_finish_multiple_relays_using_only_contexts(seed: int) -> None:
    config = GameConfig(
        seed=seed, num_impostors=0, tasks_per_crewmate=0, relay_tasks=6, round_cap=120
    )
    controllers = [ScriptedBot(GameRng(seed).fork(str(s)), diligence=1) for s in range(10)]
    game = Game(config, controllers)
    game.run()
    assert game.state.win_reason == "tasks_done"
    assert len(events(game, "relay.delivered")) == 6
    assert not [e for e in events(game, "action") if not e["valid"]]


def test_relay_generation_is_seeded_private_and_uses_noncolliding_ids() -> None:
    seen: set[int] = set()
    for seed in range(60):
        game, _, _ = started(seed=seed, tasks_per_crewmate=2, relay_tasks=2)
        graph = board(game.state).graph
        assert len(graph) == 18
        for p in parts(game.state).values():
            task = graph.get(p.task)
            assert len(set(task.assignees)) == 2
            assert all(not game.state.player(s).is_impostor for s in task.assignees)
            assert len(set(p.rooms.values())) == 3
            assert game.state.map.meeting_room not in p.rooms.values()
            seen.add(int(task.id[1:]))
            for seat in task.assignees:
                ctx = game.action_context(game.state.player(seat))
                assert p.serial not in repr(ctx)
        assert all(not p["tampered"] for p in events(game, "relay.generated")[0]["parts"])
    assert seen <= set(TASK_ID_RANGE) and len(seen) > 50
    assert min(seen) < 20 and max(seen) > 90
    a = new_game(GameConfig(seed=7), "scripted")
    b = new_game(GameConfig(seed=7), "scripted")
    a.run()
    b.run()
    assert a.transcript.to_jsonl() == b.transcript.to_jsonl()


def test_task_id_allocator_excludes_all_existing_tasks_and_enforces_capacity() -> None:
    ids = task_ids(GameRng(0), 89)
    remaining = task_ids(GameRng(1), 1, exclude=ids)
    assert len(set(ids + remaining)) == 90
    with pytest.raises(ValueError, match="at most 90 Tasks"):
        task_ids(GameRng(2), 2, exclude=ids)


def test_relay_settings_and_small_lobbies() -> None:
    with pytest.raises(ValueError, match="bad task settings"):
        GameConfig(relay_tasks=-1)
    config = GameConfig(num_seats=2, num_impostors=1, tasks_per_crewmate=0)
    game = Game(config, [IdleBot(), IdleBot()])
    game.start()
    assert not parts(game.state)
    config = GameConfig(num_rooms=2, tasks_per_crewmate=0)
    with pytest.raises(ValueError, match="three rooms"):
        Game(config, [IdleBot() for _ in range(10)]).start()


def test_reference_plan_matches_carrier_briefs_and_has_no_answer_key() -> None:
    game, task, p = started()
    kind = task_kind("chain_relay")
    for slot, seat in enumerate(task.assignees):
        brief = board(game.state).view(task, seat)
        assert "answer" not in brief and p.serial not in repr(brief)
        plan = kind.solve(brief)
        assert plan[0]["room"] == p.rooms["pickup" if slot == 0 else "handoff"]
        assert plan[1]["action"] == (HandOff.kind if slot == 0 else Deliver.kind)
    with pytest.raises(NotImplementedError, match="two carriers"):
        run_solo_trial("chain_relay", 0, kind.solve)
