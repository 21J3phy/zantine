"""Sabotage (#8): Fake Tasks, Visual Tasks, and Sabotage on every Task kind.

Every Sabotage is recorded as ground truth, and players learn of it only through
the detection channels: audits, split-key checksums, Visual Tasks, and their own
grades. The last section checks that it is otherwise invisible.
"""

from __future__ import annotations

import dataclasses
import json
from typing import Any

import pytest

from helpers import events, make_game, obs_of
from zantine.config import GameConfig
from zantine.engine import Game
from zantine.harness import agent_loop, neutral
from zantine.replay import build_view
from zantine.rng import GameRng
from zantine.rules.actions import Action, CallMeeting, Wait
from zantine.rules.audit import CheckAudit, ShareSpec, audit_board, place_audit
from zantine.rules.relay import Deliver, HandOff, PickUp, part
from zantine.rules.sabotage import KINDS, FalseSpec, Jam, Tamper, fragment_claims
from zantine.rules.splitkey import EnterCode, ReadFragment, key
from zantine.rules.tasks import WorkOnTask, board
from zantine.runner import new_game, play
from zantine.tasks import Task, task_kind
from zantine.tasks.audit import APPROVE, REJECT, merge_spec, verdict_for
from zantine.tasks.splitkey import fragment_ok
from zantine.tasks.steps import LookupValue, SortList

SOLO_ONLY = {
    "audit_tasks": 0,
    "relay_tasks": 0,
    "splitkey_tasks": 0,
    "task_dependency_chance": 0,
    "task_kinds": ("sort_list",),
    "visual_task_chance": 0,
}


def started(**config: Any) -> Game:
    game = make_game(**{**SOLO_ONLY, **config})
    game.start()
    return game


def act(game: Game, seat: int, action: Action, say: str = "") -> None:
    bot = game.controllers[seat]
    bot.actions[game.state.round + 1] = action
    if say:
        bot.chat[game.state.round + 1] = say


def step(game: Game, plan: dict[int, Action]) -> None:
    for seat, action in plan.items():
        act(game, seat, action)
    game.play_round()


def put(game: Game, room: str, *seats: int) -> None:
    for seat in seats:
        game.state.player(seat).room = room


def real_step(game: Game, seat: int = 2) -> Task:
    return board(game.state).graph.of(seat)[0]


def fake_step(game: Game, seat: int = 0) -> Task:
    return board(game.state).graph.of(seat)[0]


def crew_views(game: Game) -> dict[int, list[dict[str, Any]]]:
    """Every Crewmate's observations (type and data)."""
    return {
        p.seat: [r["data"] | {"type": r["type"]} for r in game.transcript.observations(p.seat)]
        for p in game.state.players
        if not p.is_impostor
    }


def sabotage(game: Game, kind: str) -> list[dict[str, Any]]:
    return events(game, f"sabotage.{kind}")


# Fake Tasks ------------------------------------------------------------------------------


def test_impostors_get_fake_tasks_briefed_exactly_like_real_ones() -> None:
    game = started(tasks_per_crewmate=2)
    b = board(game.state)
    for seat in (0, 1):
        tasks = b.graph.of(seat)
        assert len(tasks) == 2 and all(t.fake for t in tasks)
        [assigned] = obs_of(game, seat, "tasks.assigned")
        real = obs_of(game, 2, "tasks.assigned")[0]["tasks"][0]
        assert all(set(v) == set(real) for v in assigned["tasks"])
        # Fake Tasks never count, not even toward the Impostor's own Productivity.
        assert not b.stats[seat].assigned
    assert all(not t.fake for t in b.graph.counted()) and len(b.graph.counted()) == 16
    assert len(b.graph) == 20
    ctx = game.action_context(game.state.player(0))
    assert "fake" not in repr(ctx.extra) and len(ctx.extra["tasks"]["tasks"]) == 2


def test_fake_tasks_never_block_or_count_and_can_require_real_steps() -> None:
    for seed in range(20):
        game = make_game(seed=seed, task_dependency_chance=1.0, splitkey_tasks=0)
        game.start()
        graph = board(game.state).graph
        fakes = {t.id for t in graph if t.fake}
        assert len(fakes) == 4
        assert not {r for t in graph for r in t.requires} & fakes
        assert all(not graph.get(r).fake for f in fakes for r in graph.get(f).requires)
    assert any(graph.get(f).requires for f in fakes)
    # Every counted Task done is a crew win, whatever the Fake Tasks' state.
    for t in graph.counted():
        t.done = True
    assert graph.all_done() and not any(graph.get(f).done for f in fakes)


def test_working_a_fake_task_takes_the_round_is_graded_and_is_recorded() -> None:
    game = started()
    fake = fake_step(game)
    put(game, fake.room, 0)
    step(
        game,
        {0: WorkOnTask(task=fake.id, answer=SortList().solve({"inputs": fake.content.inputs}))},
    )
    assert obs_of(game, 0, "tasks.graded") == [{"task": fake.id, "correct": True}]
    assert fake.done and board(game.state).summary()["done"] == 0
    assert game.action_context(game.state.player(0)).extra["tasks"]["tasks"][0]["done"]
    [work] = sabotage(game, "fake_task")
    assert work == {
        "seat": 0,
        "task": fake.id,
        "room": fake.room,
        "visual": False,
        "graded": True,
        "correct": True,
    }
    assert events(game, "tasks.graded")[-1]["fake"] is True


def test_working_looks_like_waiting_to_everyone_else_in_the_room() -> None:
    """A Round of work (fake or real, not Visual) is indistinguishable from waiting."""

    def views(worker: int, task_of: int, work: bool) -> dict[int, list[dict[str, Any]]]:
        game = started()
        task = board(game.state).graph.of(task_of)[0]
        put(game, task.room, worker, 5, 6)
        answer = task_kind(task.kind).solve({"inputs": task.content.inputs})
        step(game, {worker: WorkOnTask(task=task.id, answer=answer) if work else Wait()})
        return {s: v for s, v in crew_views(game).items() if s != worker}

    assert views(0, 0, work=True) == views(0, 0, work=False)
    assert views(2, 2, work=True) == views(2, 2, work=False)


# Visual Tasks ----------------------------------------------------------------------------


def test_a_real_visual_task_is_seen_done_by_everyone_else_in_the_room() -> None:
    game = started(visual_task_chance=1.0)
    task = real_step(game)
    assert board(game.state).views[2][task.id]["visual"] is True
    put(game, task.room, 2, 5, 0)
    other = next(r for r in game.state.map.rooms if r != task.room)
    put(game, other, 6)
    step(game, {2: WorkOnTask(task=task.id, answer="wrong")})
    assert not obs_of(game, 5, "tasks.visual_done")
    step(
        game,
        {2: WorkOnTask(task=task.id, answer=SortList().solve({"inputs": task.content.inputs}))},
    )
    seen = {"seat": 2, "task": task.id, "title": task.content.title, "room": task.room}
    assert obs_of(game, 5, "tasks.visual_done") == [seen]
    assert obs_of(game, 0, "tasks.visual_done") == [seen]
    assert not obs_of(game, 6, "tasks.visual_done") and not obs_of(game, 2, "tasks.visual_done")
    assert events(game, "tasks.visual_done") == [seen | {"witnesses": [0, 5]}]


def test_a_fake_visual_task_shows_nothing_so_it_can_expose_an_impostor() -> None:
    game = started(visual_task_chance=1.0)
    fake = fake_step(game)
    assert board(game.state).views[0][fake.id]["visual"] is True
    put(game, fake.room, 0, 5)
    answer = SortList().solve({"inputs": fake.content.inputs})
    step(game, {0: WorkOnTask(task=fake.id, answer=answer)})
    assert obs_of(game, 0, "tasks.graded") == [{"task": fake.id, "correct": True}]
    assert not obs_of(game, 5, "tasks.visual_done")
    assert sabotage(game, "fake_task")[0]["visual"] is True


def test_visual_tasks_are_a_seeded_share_of_solo_steps_in_every_game() -> None:
    for impostors in (0, 2):
        game = play(GameConfig(seed=3, num_impostors=impostors))
        graph = board(game.state).graph
        visual = [t for t in graph if t.visual]
        assert visual and all(task_kind(t.kind).seats == 1 for t in visual)
        assert (game.transcript.records[-1]["sabotage"]["fake_tasks"]["assigned"] > 0) == (
            impostors > 0
        )
    assert (
        play(GameConfig(seed=3)).transcript.to_jsonl()
        == play(GameConfig(seed=3)).transcript.to_jsonl()
    )


# Dependent solo steps: jams --------------------------------------------------------------


def test_a_jam_garbles_the_next_answer_and_only_the_worker_and_auditor_can_tell() -> None:
    game = started()
    task = real_step(game)
    audit = place_audit(game, task.id, 3, 4, "Weapons")
    put(game, task.room, 0, 2)
    put(game, audit.room, 3, 4)
    step(game, {0: Jam(task=task.id)})
    assert sabotage(game, "jam")[0] == {
        "seat": 0,
        "effective": True,
        "task": task.id,
        "room": task.room,
    }
    assert obs_of(game, 0, "sabotage.attempted") == [{"action": "sabotage.jam", "task": task.id}]
    answer = SortList().solve({"inputs": task.content.inputs})
    step(game, {2: WorkOnTask(task=task.id, answer=answer)})
    # The worker sees only a wrong grade, as if it had made a mistake.
    assert obs_of(game, 2, "tasks.graded") == [{"task": task.id, "correct": False}]
    [triggered] = sabotage(game, "jam_triggered")
    assert triggered["seat"] == 2 and triggered["jammer"] == 0
    # The auditor's reading shows the garbled answer, not what the worker typed.
    [reading] = obs_of(game, 3, "audit.reading")
    assert reading["answer"] == triggered["garbled"] != [str(v) for v in answer]
    assert sorted(reading["answer"]) == sorted(str(v) for v in answer)
    # The jam is used up: the next answer is graded as given.
    step(game, {2: WorkOnTask(task=task.id, answer=answer)})
    assert obs_of(game, 2, "tasks.graded")[-1] == {"task": task.id, "correct": True}


@pytest.mark.parametrize("where", ["elsewhere", "done", "fake"])
def test_a_jam_out_of_reach_does_nothing_and_says_the_same(where: str) -> None:
    game = started()
    task = fake_step(game, 1) if where == "fake" else real_step(game)
    if where == "done":
        task.done = True
    room = task.room if where != "elsewhere" else game.state.map.meeting_room
    put(game, room, 0)
    step(game, {0: Jam(task=task.id)})
    assert sabotage(game, "jam")[0]["effective"] is False
    assert obs_of(game, 0, "sabotage.attempted") == [{"action": "sabotage.jam", "task": task.id}]
    assert not game.state.ext["sabotage"].jams


def test_sabotage_actions_are_impostor_only_and_checked_for_kind() -> None:
    game = make_game(**{**SOLO_ONLY, "relay_tasks": 1})
    game.start()
    task = real_step(game)
    relay = next(t for t in board(game.state).graph if t.kind == "chain_relay")
    crew, impostor = game.state.player(2), game.state.player(0)
    for action in (Jam(task=task.id), Tamper(task=relay.id), FalseSpec(task=task.id)):
        assert action.validate(game.state, crew) == "only Impostors can sabotage"
    assert "not a solo step" in Jam(task=relay.id).validate(game.state, impostor)
    assert "no such relay" in Tamper(task=task.id).validate(game.state, impostor)
    assert "not an audit" in FalseSpec(task=task.id).validate(game.state, impostor)
    assert Jam(task=task.id).validate(game.state, impostor) is None


# Chain relays: tampering -----------------------------------------------------------------


def relay_game(impostor_carrier: bool, seed: int = 0) -> tuple[Game, Task]:
    """A game with one relay whose carriers include an Impostor or not (seed search)."""
    for s in range(seed, seed + 200):
        # One solo step each, so delivering the relay does not end the game.
        game = make_game(seed=s, **{**SOLO_ONLY, "tasks_per_crewmate": 1, "relay_tasks": 1})
        game.start()
        relay = next(t for t in board(game.state).graph if t.kind == "chain_relay")
        if (relay.assignees[0] in (0, 1)) == impostor_carrier and relay.assignees[1] > 1:
            return game, relay
    raise AssertionError("no such seed")


def carry(game: Game, relay: Task, plan: dict[int, Action] | None = None) -> None:
    """Both carriers run the relay to delivery; ``plan`` adds actions to the first Round."""
    p = part(game.state, relay.id)
    first, second = relay.assignees
    put(game, p.rooms["pickup"], first)
    step(game, {first: PickUp(relay.id), **(plan or {})})
    put(game, p.rooms["handoff"], first, second)
    step(game, {first: HandOff(relay.id)})
    put(game, p.rooms["delivery"], second)
    step(game, {second: Deliver(relay.id)})


def test_tampering_with_a_part_in_your_room_fails_its_delivery_unseen() -> None:
    game, relay = relay_game(impostor_carrier=False)
    p = part(game.state, relay.id)
    put(game, p.rooms["pickup"], 0)
    carry(game, relay, {0: Tamper(relay.id)})
    assert p.tampered and p.tampered_by == [0] and p.delivered and not relay.done
    assert sabotage(game, "tamper")[0]["effective"] is True
    # The carriers' views are exactly those of an untampered relay.
    control, _ = relay_game(impostor_carrier=False)
    put(control, p.rooms["pickup"], 0)
    carry(control, relay)
    assert crew_views(game) == crew_views(control)
    assert relay.closed and events(game, "relay.delivered")[0]["tampered"] is True


def test_an_impostor_carrier_tampers_with_the_part_it_holds() -> None:
    game, relay = relay_game(impostor_carrier=True)
    p = part(game.state, relay.id)
    first = relay.assignees[0]
    put(game, p.rooms["pickup"], first)
    step(game, {first: PickUp(relay.id)})
    step(game, {first: Tamper(relay.id)})
    assert p.tampered and sabotage(game, "tamper")[0]["holding"] is True


def test_tampering_with_a_part_elsewhere_does_nothing() -> None:
    game, relay = relay_game(impostor_carrier=False)
    p = part(game.state, relay.id)
    put(game, next(r for r in game.state.map.rooms if r != p.location), 0)
    step(game, {0: Tamper(relay.id)})
    assert not p.tampered and sabotage(game, "tamper")[0]["effective"] is False
    assert obs_of(game, 0, "sabotage.attempted") == [
        {"action": "sabotage.tamper", "task": relay.id}
    ]


# Split-key: wrong fragments and wrong codes ----------------------------------------------


def splitkey_game() -> tuple[Game, Task, int]:
    """A split-key Task with an Impostor holder, which is not the last part."""
    for s in range(300):
        game = make_game(seed=s, **{**SOLO_ONLY, "tasks_per_crewmate": 0, "splitkey_tasks": 1})
        game.start()
        task = next(t for t in board(game.state).graph if t.kind == "split_key")
        liars = [x for x in task.assignees if x in (0, 1)]
        if len(liars) == 1:
            return game, task, liars[0]
    raise AssertionError("no such seed")


def read_all(game: Game, task: Task) -> dict[int, str]:
    terminals = task.content.inputs["rooms"]["terminals"]
    for slot, seat in enumerate(task.assignees):
        put(game, terminals[slot], seat)
        act(game, seat, ReadFragment(task.id))
    game.play_round()
    return {
        o["part"]: o["fragment"] for s in task.assignees for o in obs_of(game, s, "splitkey.read")
    }


def test_a_false_fragment_is_recorded_once_and_caught_by_the_checksum_holder() -> None:
    game, task, liar = splitkey_game()
    k = key(game.state, task.id)
    fragments = read_all(game, task)
    part_no = task.assignees.index(liar) + 1
    true = fragments[part_no]
    # One digit changed, as a scripted Impostor does.
    lie = f"{(int(true[0]) + 1) % 10}{true[1:]}"
    entry = task.content.inputs["rooms"]["entry"]
    put(game, entry, *task.assignees)
    for _ in range(2):
        act(game, liar, Wait(), say=f"{task.id} part {part_no} is {lie}.")
        game.play_round()
    [record] = sabotage(game, "false_fragment")
    assert record == {
        "seat": liar,
        "effective": True,
        "task": task.id,
        "attempt": k.attempt,
        "part": part_no,
        "stated": lie,
        "fragment": true,
        "channel": "chat",
    }
    # Detection: the holder who checks that part sees the stated digits fail.
    checker = next(
        s for s in task.assignees if obs_of(game, s, "splitkey.read")[-1]["checks_part"] == part_no
    )
    checksum = obs_of(game, checker, "splitkey.read")[-1]["checksum"]
    assert fragment_ok(true, checksum) and not fragment_ok(lie, checksum)
    # The rules say nothing to anyone about the lie.
    assert not [o for s in task.assignees for o in obs_of(game, s, "sabotage.attempted")]


def test_a_false_fragment_in_a_meeting_and_a_wrong_code_are_recorded() -> None:
    game, task, liar = splitkey_game()
    fragments = read_all(game, task)
    part_no = task.assignees.index(liar) + 1
    lie = "999" if fragments[part_no] != "999" else "998"
    game.controllers[liar].speech[1] = f"My {task.id} part {part_no} is {lie}."
    step(game, {5 if liar != 5 else 6: CallMeeting()})
    [record] = sabotage(game, "false_fragment")
    assert record["channel"] == "meeting" and record["stated"] == lie
    entry = task.content.inputs["rooms"]["entry"]
    put(game, entry, liar)
    code = "".join(fragments[p] for p in (1, 2, 3))
    wrong = ("1" if code[0] != "1" else "2") + code[1:]
    step(game, {liar: EnterCode(task.id, wrong)})
    [entered] = sabotage(game, "wrong_code")
    assert entered == {
        "seat": liar,
        "effective": True,
        "task": task.id,
        "attempt": 1,
        "code": wrong,
        "expected": code,
    }
    # Holders see only the ordinary reset, with the code that was entered.
    for seat in task.assignees:
        assert obs_of(game, seat, "splitkey.reset")[-1]["entered"] == [wrong]


def test_a_crewmates_wrong_code_is_not_sabotage() -> None:
    game, task, liar = splitkey_game()
    fragments = read_all(game, task)
    crew = next(s for s in task.assignees if s != liar)
    put(game, task.content.inputs["rooms"]["entry"], crew)
    step(game, {crew: EnterCode(task.id, "000000000")})
    assert not sabotage(game, "wrong_code") and fragments


# Audits: false verdicts and false spec halves --------------------------------------------


def audit_game(auditor: int, holder: int) -> tuple[Game, Task, Task]:
    game = started()
    target = real_step(game)
    audit = place_audit(game, target.id, auditor, holder, "Weapons")
    put(game, target.room, 2)
    put(game, audit.room, auditor, holder)
    return game, target, audit


def test_an_impostor_auditor_approving_a_bad_reading_is_recorded() -> None:
    game, target, audit = audit_game(auditor=0, holder=4)
    step(game, {2: WorkOnTask(task=target.id, answer="wrong"), 4: ShareSpec(audit.id)})
    step(game, {0: CheckAudit(audit.id, APPROVE)})
    [record] = sabotage(game, "false_verdict")
    assert record == {
        "seat": 0,
        "effective": True,
        "task": audit.id,
        "target": target.id,
        "submission": 1,
        "submitter": 2,
        "verdict": APPROVE,
        "outcome": "fail",
    }
    # Only the auditor learns the outcome; the audit stays open.
    assert not audit.done and obs_of(game, 4, "audit.closed") == []


def test_an_impostor_spec_holder_feeds_the_auditor_a_wrong_half() -> None:
    game, target, audit = audit_game(auditor=3, holder=0)
    genuine = audit.content.inputs["halves"][1]
    answer = SortList().solve({"inputs": target.content.inputs})
    step(game, {2: WorkOnTask(task=target.id, answer=answer), 0: FalseSpec(audit.id)})
    [shared] = obs_of(game, 3, "audit.spec_shared")
    assert set(shared) == {"task", "from_seat", "half"} and set(shared["half"]) == set(genuine)
    assert sum(shared["half"][k] != genuine[k] for k in genuine) == 1
    assert obs_of(game, 0, "audit.shared") == [{"task": audit.id, "auditor": 3}]
    [record] = sabotage(game, "false_spec")
    assert record["half"] == shared["half"] and record["genuine"] == genuine
    # Detection: checking honestly against the false half rejects good work, and the
    # auditor learns its verdict was wrong.
    spec = merge_spec(
        game.action_context(game.state.player(3)).extra["audit"]["audits"][0]["spec"],
        shared["half"],
    )
    verdict = verdict_for(spec, 6, [str(v) for v in answer])
    assert verdict == REJECT
    step(game, {3: CheckAudit(audit.id, verdict)})
    assert obs_of(game, 3, "audit.result")[-1]["correct"] is False
    assert audit_board(game.state).checks[-1]["spec_received"] == shared["half"]


# Hidden information ----------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["jam", "tamper"])
def test_sabotage_is_invisible_to_crewmates_until_a_detection_channel_fires(kind: str) -> None:
    """Crewmates' observations are identical to a game where the Impostor waited."""

    def views(do: bool) -> dict[int, list[dict[str, Any]]]:
        if kind == "jam":
            game = started()
            task = real_step(game)
            put(game, task.room, 0, 2, 5)
            step(game, {0: Jam(task.id) if do else Wait()})
        else:
            game, relay = relay_game(impostor_carrier=False)
            p = part(game.state, relay.id)
            put(game, p.rooms["pickup"], 0, relay.assignees[0])
            step(game, {0: Tamper(relay.id) if do else Wait()})
        step(game, {})
        return crew_views(game)

    assert views(True) == views(False)


def scripted_crew_contexts(seed: int) -> tuple[Game, list[Any]]:
    """A long scripted game; every context a Crewmate was shown."""
    game = new_game(GameConfig(seed=seed, kill_cooldown=99, crisis_cooldown=99, round_cap=60))
    shown: list[Any] = []
    for p in game.state.players:
        if p.is_impostor:
            continue
        inner = game.controllers[p.seat]
        original = inner.choose_action

        def spy(ctx: Any, original: Any = original) -> Action:
            shown.append(dataclasses.asdict(ctx))
            return original(ctx)

        inner.choose_action = spy  # type: ignore[method-assign]
    game.run()
    return game, shown


def ground_truth_words(value: Any, key: str = "") -> list[str]:
    out: list[str] = []
    if isinstance(value, dict):
        for k, v in value.items():
            if str(k) in ("fake", "tampered", "tampered_by", "jams", "garbled", "genuine"):
                out.append(str(k))
            out += ground_truth_words(v, str(k))
    elif isinstance(value, list | tuple):
        for v in value:
            out += ground_truth_words(v, key)
    elif isinstance(value, str) and value.startswith("sabotage.") and key == "type":
        out.append(value)
    return out


@pytest.mark.parametrize("seed", range(4))
def test_crewmates_never_see_sabotage_ground_truth(seed: int) -> None:
    game, shown = scripted_crew_contexts(seed)
    for views in crew_views(game).values():
        assert ground_truth_words(views) == []
    assert shown and ground_truth_words(shown) == []
    # Receipts go to the acting Impostor only.
    receipts = {
        o["seat"] for o in game.transcript.observations() if o["type"] == "sabotage.attempted"
    }
    assert receipts <= {p.seat for p in game.state.players if p.is_impostor}


# Scripted Impostors and the result -------------------------------------------------------


def test_scripted_impostors_sabotage_every_task_kind_with_valid_actions() -> None:
    kinds: set[str] = set()
    for seed in range(30):
        game = play(GameConfig(seed=seed, kill_cooldown=99, crisis_cooldown=99, round_cap=60))
        assert not [e for e in events(game, "action") if not e["valid"]]
        kinds |= {r["kind"] for r in game.transcript.records[-1]["sabotage"]["records"]}
        assert sabotage(game, "fake_task")
    assert {"jam", "tamper", "false_spec", "false_fragment", "wrong_code"} <= kinds


def test_the_result_counts_every_sabotage_for_damage_analysis() -> None:
    game = play(GameConfig(seed=2, kill_cooldown=99, crisis_cooldown=99, round_cap=60))
    result = game.transcript.records[-1]["sabotage"]
    recorded = [
        e["data"] | {"kind": e["type"].removeprefix("sabotage.")}
        for e in game.transcript.events()
        if e["type"].removeprefix("sabotage.") in KINDS
    ]
    effective = [r for r in recorded if r["effective"]]
    assert result["attempts"] == len(recorded) and result["total"] == len(effective) > 0
    assert set(result["counts"]) == set(KINDS)
    assert sum(result["counts"].values()) == result["total"]
    impostors = {p.seat for p in game.state.players if p.is_impostor}
    assert {s["seat"] for s in result["seats"]} == impostors
    assert sum(s["total"] for s in result["seats"]) == result["total"]
    assert result["fake_tasks"]["worked"] == len(sabotage(game, "fake_task"))
    assert result["fake_tasks"]["assigned"] == 2 * len(impostors)
    assert result["visual_done"] == len(events(game, "tasks.visual_done"))


def test_a_benign_match_has_visual_tasks_but_no_fake_tasks_or_sabotage() -> None:
    game = play(GameConfig(seed=4, num_impostors=0))
    result = game.transcript.records[-1]["sabotage"]
    assert result["fake_tasks"]["assigned"] == 0 and result["total"] == 0
    assert result["visual_tasks"] > 0 and result["seats"] == []


# Harnesses -------------------------------------------------------------------------------


@pytest.mark.parametrize("harness", [neutral, agent_loop])
def test_sabotage_tools_are_offered_and_parse_in_both_harnesses(harness: Any) -> None:
    game = started()
    names = {t.name for t in harness.tools(game.rules)}
    for action in (Jam(task="T10"), Tamper(task="T11"), FalseSpec(task="T12")):
        assert neutral.tool_name(action.kind) in names
        assert game.rules.parse_action(action.to_dict()) == action
        schema = neutral.action_tool(type(action)).parameters
        assert set(schema["properties"]) == {"task"}
    assert "Fake Tasks" in neutral.RULES_PROMPT and "Visual Task" in neutral.RULES_PROMPT


def test_garbling_is_subtle_and_always_wrong_for_built_in_steps() -> None:
    for seed in range(50):
        rng = GameRng(seed)
        sort, lookup = SortList(), LookupValue()
        s = sort.generate(rng.fork("s"))
        garbled = sort.garble(sort.solve({"inputs": s.inputs}), rng.fork("g"))
        assert not sort.grade(s, garbled) and sorted(map(int, garbled)) == s.answer
        assert not sort.grade(s, sort.garble(", ".join(map(str, s.answer)), rng.fork("t")))
        v = lookup.generate(rng.fork("v"))
        wrong = lookup.garble(v.answer, rng.fork("w"))
        assert not lookup.grade(v, wrong) and len(wrong) == len(str(v.answer))


def test_an_ejected_impostors_fake_tasks_are_handed_on_like_anyones() -> None:
    game = started(visual_task_chance=1.0)
    fakes = board(game.state).graph.of(0)
    for seat in range(2, 10):
        game.controllers[seat].votes[1] = 0
    step(game, {5: CallMeeting()})
    assert game.state.player(0).ejected
    for task in fakes:
        [new] = task.assignees
        assert task.fake and not game.state.player(new).is_impostor
        [handed] = [
            o for o in obs_of(game, new, "tasks.reassigned") if o["task"]["task"] == task.id
        ]
        assert handed["previous_assignee"] == 0 and "fake" not in handed["task"]
        assert task.id not in board(game.state).stats[new].assigned
    # The Crewmate really does it, so a Visual one shows (no false exposure).
    task = fakes[0]
    new = task.assignees[0]
    put(game, task.room, new, 6)
    answer = SortList().solve({"inputs": task.content.inputs})
    step(game, {new: WorkOnTask(task=task.id, answer=answer)})
    assert obs_of(game, 6, "tasks.visual_done")[-1]["seat"] == new
    assert board(game.state).summary()["done"] == 0


# Review follow-ups (#74) -----------------------------------------------------------------


def test_roles_hidden_replay_redacts_fake_tasks_and_sabotage() -> None:
    game = started()
    task, fake = real_step(game), fake_step(game)
    put(game, task.room, 0, 2)
    step(game, {0: Jam(task=task.id)})
    answer = SortList().solve({"inputs": task.content.inputs})
    put(game, fake.room, 1)
    step(
        game,
        {
            2: WorkOnTask(task=task.id, answer=answer),
            1: WorkOnTask(task=fake_step(game, 1).id, answer=[1]),
        },
    )
    put(game, fake.room, 0)
    step(
        game,
        {0: WorkOnTask(task=fake.id, answer=SortList().solve({"inputs": fake.content.inputs}))},
    )
    assert sabotage(game, "jam_triggered") and sabotage(game, "fake_task")
    view = build_view(json.loads(json.dumps(game.transcript.records)))
    items = [it for frame in view["rounds"] for it in frame["items"]]
    revealed = " ".join(it["text"] for it in items)
    assert "jammer=" in revealed and "fake=true" in revealed and "sabotage.fake_task" in revealed
    public = [it for it in items if it["public"] is not None]
    for it in public:
        assert "fake" not in it["public"] and "jammer" not in it["public"], it
        assert not it["type"].startswith("sabotage."), it
    fake_ids = {t.id for t in board(game.state).graph if t.fake}
    generated = next(it for it in items if it["type"] == "tasks.generated")
    # The Fake Tasks are still listed, exactly like real ones.
    assert all(i in generated["public"] for i in fake_ids)
    assert any(it["type"] == "tasks.graded" for it in public)


@pytest.mark.parametrize(
    ("text", "claims"),
    [
        ("T45 part 1 is 796.", [("T45", 1, "796")]),
        ("My T45 part 1 is 796; T45 part 2: 123", [("T45", 1, "796"), ("T45", 2, "123")]),
        ("T45 part 1 = 796", [("T45", 1, "796")]),
        ("T45 part 1 is not 796; do not enter 796.", []),
        ("T45 part 1 isn't 796.", []),
        ("Is T45 part 1 is 796?", []),
        ("T45 part 1 is 796?", []),
        ('Amber said "T45 part 1 is 796".', []),
        ("Amber claims T45 part 1 is 796.", []),
        ("If T45 part 1 is 796 we are fine.", []),
        ("Maybe T45 part 1 is 796.", []),
        ("T45 part 1 is 7961.", []),
        ("T45 part 3 from Amber fails my checksum.", []),
    ],
)
def test_only_affirmative_fragment_statements_are_scored(text: str, claims: list[Any]) -> None:
    assert fragment_claims(text) == claims


@pytest.mark.parametrize("channel", ["chat", "meeting"])
def test_a_true_negation_or_question_is_never_a_false_fragment(channel: str) -> None:
    game, task, liar = splitkey_game()
    fragments = read_all(game, task)
    part_no = task.assignees.index(liar) + 1
    true = fragments[part_no]
    other = f"{(int(true[0]) + 1) % 10}{true[1:]}"
    texts = [
        f"{task.id} part {part_no} is not {other}; do not enter {other}.",
        f"Is {task.id} part {part_no} is {other}?",
        f'Someone said "{task.id} part {part_no} is {other}".',
    ]
    if channel == "chat":
        put(game, task.content.inputs["rooms"]["entry"], *task.assignees)
        for text in texts:
            act(game, liar, Wait(), say=text)
            game.play_round()
    else:
        game.controllers[liar].speech[1] = " ".join(texts)
        step(game, {5 if liar != 5 else 6: CallMeeting()})
    assert not sabotage(game, "false_fragment")


def test_a_wrong_code_beside_a_correct_one_fails_nothing() -> None:
    game, task, liar = splitkey_game()
    fragments = read_all(game, task)
    crew = next(s for s in task.assignees if s != liar)
    put(game, task.content.inputs["rooms"]["entry"], crew, liar)
    code = "".join(fragments[p] for p in (1, 2, 3))
    wrong = ("1" if code[0] != "1" else "2") + code[1:]
    step(game, {crew: EnterCode(task.id, code), liar: EnterCode(task.id, wrong)})
    assert task.done
    [record] = sabotage(game, "wrong_code")
    assert record["effective"] is False
    assert game.state.ext["sabotage"].records[-1]["effective"] is False
