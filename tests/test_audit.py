"""Audit Tasks: split specs, independent checks, private feedback and replayable records."""

from typing import Any

import pytest

from helpers import events, make_game, obs_of
from zantine.config import GameConfig
from zantine.engine import Game
from zantine.harness.neutral import action_tool
from zantine.rng import GameRng
from zantine.rules.actions import CallMeeting, Kill
from zantine.rules.audit import (
    AuditModule,
    CheckAudit,
    ShareSpec,
    audit_board,
    audits,
    place_audit,
)
from zantine.rules.registry import GameModule, RoundContext, Rules
from zantine.rules.tasks import GRADED, WorkOnTask, board
from zantine.runner import play
from zantine.seats.bots import ScriptedBot
from zantine.tasks import (
    Task,
    TaskContent,
    TaskKind,
    register_task_kind,
    run_solo_trials,
    task_kind,
)
from zantine.tasks.audit import APPROVE, REJECT, audit_kind, merge_spec, verdict_for


def act(game: Game, seat: int, action: Any) -> None:
    game.controllers[seat].actions[game.state.round + 1] = action


def setup(**config: Any) -> tuple[Game, Task, Task]:
    game = make_game(
        audit_tasks=0,
        relay_tasks=0,
        splitkey_tasks=0,
        task_dependency_chance=0,
        task_kinds=("sort_list",),
        **config,
    )
    game.start()
    target = board(game.state).graph.of(2)[0]
    audit = place_audit(game, target.id, 3, 4, "Weapons")
    for seat in (2, 3, 4):
        game.state.player(seat).room = target.room if seat == 2 else audit.room
    return game, target, audit


def prepare(game: Game, target: Task, audit: Task, correct: bool = True) -> None:
    act(game, 2, WorkOnTask(task=target.id, answer=target.content.answer if correct else "wrong"))
    act(game, 4, ShareSpec(task=audit.id))
    game.play_round()


def test_specs_are_split_and_only_the_auditor_gets_the_reading() -> None:
    game, target, audit = setup()
    tb = board(game.state)
    a, b = [tb.views[s][audit.id]["inputs"]["spec"] for s in (3, 4)]
    assert set(a) == {"1", "3", "5"} and set(b) == {"2", "4", "6"}
    assert list(merge_spec(a, b)[str(i)] for i in range(1, 7)) == [
        str(x) for x in target.content.answer
    ]
    with pytest.raises(ValueError, match="both halves"):
        verdict_for(a, 6, [str(x) for x in target.content.answer])
    act(game, 2, WorkOnTask(task=target.id, answer=target.content.answer))
    game.play_round()
    [reading] = obs_of(game, 3, "audit.reading")
    assert reading["answer"] == target.content.answer and reading["submission"] == 1
    assert "correct" not in reading and "outcome" not in reading
    for seat in range(10):
        if seat != 3:
            assert not obs_of(game, seat, "audit.reading")
    assert (
        "spec_received" not in game.action_context(game.state.player(4)).extra["audit"]["audits"][0]
    )
    assert (
        game.action_context(game.state.player(3)).extra["audit"]["audits"][0]["spec_received"]
        is None
    )


def test_a_check_requires_a_reading_and_a_share_from_the_other_player() -> None:
    game, target, audit = setup()
    check = CheckAudit(task=audit.id, verdict=APPROVE)
    assert "nothing has been submitted" in check.validate(game.state, game.state.player(3))
    act(game, 2, WorkOnTask(task=target.id, answer=target.content.answer))
    game.play_round()
    assert "half of the spec" in check.validate(game.state, game.state.player(3))
    # The share and reading must be known at the start of a Round, even if the
    # holder shares during the Round in which the auditor tries to check.
    act(game, 3, check)
    act(game, 4, ShareSpec(task=audit.id))
    game.play_round()
    assert len(obs_of(game, 3, "invalid_action")) == 2
    assert not events(game, "audit.checked")
    assert obs_of(game, 3, "audit.spec_shared")
    act(game, 3, check)
    game.play_round()
    assert audit.done


def test_only_the_holder_can_share_and_only_when_colocated() -> None:
    game, _, audit = setup()
    share = ShareSpec(task=audit.id)
    assert "not the spec holder" in share.validate(game.state, game.state.player(3))
    game.state.player(4).room = "Cafeteria"
    assert "not in your room" in share.validate(game.state, game.state.player(4))
    assert not obs_of(game, 3, "audit.spec_shared")


@pytest.mark.parametrize(("auditor", "holder"), [(2, 4), (3, 2), (3, 3)])
def test_placement_rejects_self_audits_and_duplicate_assignees(auditor: int, holder: int) -> None:
    game, target, _ = setup()
    with pytest.raises(ValueError):
        place_audit(game, target.id, auditor, holder, "Weapons")


def test_check_rejects_own_submission_even_after_target_reassignment() -> None:
    game, target, audit = setup()
    prepare(game, target, audit)
    ab = audit_board(game.state)
    # A later rule reassigns the target and audit: submission authorship remains
    # authoritative, even though the author no longer holds the target.
    target.assignees = [5]
    audit.assignees[0] = 2
    ab.shown[(audit.id, 2)] = ab.shown[(audit.id, 3)]
    ab.received[(audit.id, 2)] = ab.received[(audit.id, 3)]
    game.state.player(2).room = audit.room
    assert "own submission" in CheckAudit(audit.id, APPROVE).validate(
        game.state, game.state.player(2)
    )
    with pytest.raises(ValueError, match="own submission"):
        place_audit(game, target.id, 2, 6, "Weapons")


@pytest.mark.parametrize("reading_correct", [False, True])
@pytest.mark.parametrize("verdict", [APPROVE, REJECT])
def test_ground_truth_outcome_is_private_and_every_check_is_recorded(
    reading_correct: bool, verdict: str
) -> None:
    game, target, audit = setup()
    prepare(game, target, audit, reading_correct)
    act(game, 3, CheckAudit(task=audit.id, verdict=verdict))
    game.play_round()
    correct = (verdict == APPROVE) == reading_correct
    [record] = events(game, "audit.checked")
    assert record == audit_board(game.state).checks[0]
    assert record["target"] == target.id and record["submission"] == 1
    assert record["submitter"] == 2 and record["auditor"] == 3
    assert record["spec_from"] == 4
    assert record["spec_received"] == audit.content.inputs["halves"][1]
    assert record["answer"] == (target.content.answer if reading_correct else "wrong")
    assert record["outcome"] == ("pass" if reading_correct else "fail")
    assert record["correct"] is correct and audit.done is correct
    [told] = obs_of(game, 3, "audit.result")
    assert told == {
        k: record[k]
        for k in ("task", "target", "submitter", "submission", "verdict", "outcome", "correct")
    }
    for seat in range(10):
        if seat != 3:
            assert not obs_of(game, seat, "audit.result")
            for o in game.observations_of(seat):
                if o.type.startswith("audit."):
                    assert not {"correct", "outcome", "verdict"} & o.data.keys()
    if not correct:
        act(game, 3, CheckAudit(task=audit.id, verdict=REJECT if verdict == APPROVE else APPROVE))
        game.play_round()
        assert audit.done and len(events(game, "audit.checked")) == 2


def test_a_check_uses_the_reading_known_before_simultaneous_resubmission() -> None:
    game, target, audit = setup()
    prepare(game, target, audit, correct=False)
    act(game, 2, WorkOnTask(task=target.id, answer=target.content.answer))
    act(game, 3, CheckAudit(task=audit.id, verdict=REJECT))
    game.play_round()
    [record] = events(game, "audit.checked")
    assert record["submission"] == 1 and record["outcome"] == "fail" and record["correct"]
    assert audit_board(game.state).latest(target.id).n == 2
    assert audit_board(game.state).latest(target.id).correct
    assert audit.done and target.done


def test_a_new_reading_is_private_and_has_a_new_submission_number() -> None:
    game, target, audit = setup()
    prepare(game, target, audit, correct=False)
    act(game, 2, WorkOnTask(task=target.id, answer=target.content.answer))
    game.play_round()
    assert [r["submission"] for r in obs_of(game, 3, "audit.reading")] == [1, 2]
    assert len(obs_of(game, 4, "audit.spec_shared")) == 0
    act(game, 3, CheckAudit(task=audit.id, verdict=APPROVE))
    game.play_round()
    assert events(game, "audit.checked")[0]["submission"] == 2


@pytest.mark.parametrize("victim", [2, 3, 4])
def test_kills_cancel_work_checks_and_spec_sharing(victim: int) -> None:
    game, target, audit = setup(kill_cooldown=0)
    if victim == 3:
        prepare(game, target, audit)
        act(game, 3, CheckAudit(task=audit.id, verdict=APPROVE))
    else:
        act(game, 2, WorkOnTask(task=target.id, answer=target.content.answer))
        act(game, 4, ShareSpec(task=audit.id))
    game.state.player(0).room = game.state.player(victim).room
    act(game, 0, Kill(victim))
    game.play_round()
    assert not events(game, "audit.checked")
    if victim == 2:
        assert not obs_of(game, 3, "audit.reading")
    elif victim == 4:
        assert not obs_of(game, 3, "audit.spec_shared")


def test_a_share_is_not_delivered_to_a_simultaneously_killed_auditor() -> None:
    game, _, audit = setup(kill_cooldown=0)
    game.state.player(0).room = audit.room
    act(game, 0, Kill(3))
    act(game, 4, ShareSpec(task=audit.id))
    game.play_round()
    assert not events(game, "audit.shared")
    assert not obs_of(game, 3, "audit.spec_shared")


@pytest.mark.parametrize("victim", [3, 4])
def test_meetings_reassign_audit_duties_without_self_audits_or_stale_shares(victim: int) -> None:
    game, target, audit = setup(kill_cooldown=0)
    prepare(game, target, audit, correct=False)
    game.state.player(0).room = audit.room
    act(game, 0, Kill(victim))
    act(game, 6, CallMeeting())
    game.play_round()
    assert victim not in audit.assignees and len(set(audit.assignees)) == 2
    assert not set(audit.assignees) & set(target.assignees)
    assert 2 not in audit.assignees
    assert all(game.state.player(s).alive for s in audit.assignees)
    assert events(game, "audit.reassigned")
    new_auditor, new_holder = audit.assignees
    ab = audit_board(game.state)
    assert (audit.id, new_auditor) not in ab.received
    assert ab.shown[(audit.id, new_auditor)].seat == 2
    for seat, partner in [(new_auditor, new_holder), (new_holder, new_auditor)]:
        view = game.action_context(game.state.player(seat)).extra["audit"]["audits"][0]
        assert view["partner"] == partner
    # The replacement holder must share again before a check can be made.
    for seat in audit.assignees:
        game.state.player(seat).room = audit.room
    assert "half of the spec" in CheckAudit(audit.id, REJECT).validate(
        game.state, game.state.player(new_auditor)
    )
    act(game, new_holder, ShareSpec(audit.id))
    game.play_round()
    act(game, new_auditor, CheckAudit(audit.id, REJECT))
    game.play_round()
    assert audit.done


def test_reassignment_moves_an_auditor_who_now_holds_the_target() -> None:
    game, target, audit = setup()
    # Simulate a target reassignment by the Tasks hook before the audit hook.
    target.assignees = [3]
    AuditModule().on_meeting_end(game, 1)
    assert 3 not in audit.assignees
    assert obs_of(game, 3, "audit.unassigned") == [{"task": audit.id}]
    assert "audit" not in game.action_context(game.state.player(3)).extra


def test_optional_audits_do_not_gate_task_completion() -> None:
    game, _, audit = setup(tasks_per_crewmate=1)
    for task in board(game.state).graph.counted():
        seat = task.assignees[0]
        game.state.player(seat).room = task.room
        act(game, seat, WorkOnTask(task.id, task.content.answer))
    game.play_round()
    assert game.state.win_reason == "tasks_done" and not audit.done
    assert game.transcript.records[-1]["tasks"]["total"] == 8


def test_invalid_checks_and_actions_cannot_bypass_the_audit_rules() -> None:
    game, target, audit = setup()
    prepare(game, target, audit)
    for action, seat, reason in [
        (CheckAudit(audit.id, "perhaps"), 3, "verdict must"),
        (CheckAudit(audit.id, APPROVE), 4, "not the auditor"),
        (CheckAudit(target.id, APPROVE), 3, "not an audit"),
        (CheckAudit([], APPROVE), 3, "no such Task"),
        (WorkOnTask(audit.id, APPROVE), 3, "worked with audit.check"),
    ]:
        assert reason in game.rules.validate(action, game.state, game.state.player(seat))
    game.state.player(3).room = "Cafeteria"
    assert "not Cafeteria" in CheckAudit(audit.id, APPROVE).validate(
        game.state, game.state.player(3)
    )
    for action in [ShareSpec(audit.id), CheckAudit(audit.id, APPROVE)]:
        assert game.rules.parse_action(action.to_dict()) == action
        schema = action_tool(type(action)).parameters
        assert set(schema["properties"]) == set(action.to_dict()) - {"kind"}


def test_seeded_placement_is_independent_private_and_uses_unused_random_ids() -> None:
    for impostors in (0, 1, 2):
        game = make_game(impostors=tuple(range(impostors)), num_impostors=impostors)
        game.start()
        tb = board(game.state)
        assert len(audits(tb)) == 4
        assert len({t.id for t in tb.graph}) == len(tb.graph)
        for audit in audits(tb):
            target = tb.graph.get(audit.content.inputs["target"])
            assert not set(audit.assignees) & set(target.assignees)
            assert len(set(audit.assignees)) == 2
            assert all(not game.state.player(s).is_impostor for s in audit.assignees)
            assert audit not in tb.graph.counted()
            assert audit.id.startswith("T") and len(audit.id) == 3
            recipients = [
                s
                for s in range(10)
                if any(o["task"]["task"] == audit.id for o in obs_of(game, s, "audit.assigned"))
            ]
            assert recipients == sorted(audit.assignees)


def test_scripted_bots_share_and_check_from_their_observed_contexts() -> None:
    game, target, audit = setup()
    prepare(game, target, audit, correct=False)
    bot = ScriptedBot(GameRng(1), diligence=1, mistakes=0)
    # Finish equally close required work first, then check the nearby reading.
    for _ in range(2):
        chosen = bot.choose_action(game.action_context(game.state.player(3)))
        assert isinstance(chosen, WorkOnTask)
        act(game, 3, chosen)
        game.play_round()
    assert bot.choose_action(game.action_context(game.state.player(3))) == CheckAudit(
        audit.id, REJECT
    )
    holder_bot = ScriptedBot(GameRng(2), diligence=1, mistakes=0)
    ab = audit_board(game.state)
    ab.shared.clear()
    assert holder_bot.choose_action(game.action_context(game.state.player(4))) == ShareSpec(
        audit.id
    )
    first = play(GameConfig(seed=7, num_impostors=0)).transcript.to_jsonl()
    second = play(GameConfig(seed=7, num_impostors=0)).transcript.to_jsonl()
    assert first == second
    assert '"type":"audit.checked"' in first.replace(" ", "")


def test_audit_solo_calibration_has_both_halves_and_both_outcomes() -> None:
    results = run_solo_trials("audit", range(60), audit_kind().solve)
    assert all(r.correct for r in results)
    assert {r.expected for r in results} == {APPROVE, REJECT}


@register_task_kind
class SensorReading(TaskKind):
    """A different kind with its own work resolver can opt into audits."""

    name = "test_auditable_sensor"
    auditable = True

    def generate(self, rng: GameRng) -> TaskContent:
        return TaskContent(self.name, "Sensor reading", "Read two digits", {}, "42")

    def grade(self, content: TaskContent, answer: Any) -> bool:
        return answer == content.answer

    def solve(self, brief: dict[str, Any]) -> Any:
        return "42"


class SensorModule(GameModule):
    name = "test_sensor"

    def install(self, rules: Rules) -> None:
        rules.add_resolver(420, self.name, self.resolve)

    def resolve(self, game: Game, rc: RoundContext) -> None:
        rc.scratch.setdefault(GRADED, []).append((2, "T00", "41", False))


def test_other_task_resolvers_can_publish_submissions_for_generic_audits() -> None:
    game, _, _ = setup()
    target = Task("T00", "Weapons", [2], task_kind(SensorReading.name).generate(GameRng(0)))
    board(game.state).graph.add(target)
    audit = place_audit(game, target.id, 5, 6, "Weapons")
    game.rules.install(SensorModule())
    for seat in (5, 6):
        game.state.player(seat).room = audit.room
    act(game, 6, ShareSpec(audit.id))
    game.play_round()
    [reading] = obs_of(game, 5, "audit.reading")
    assert reading["items"] == ["4", "1"] and "correct" not in reading
    act(game, 5, CheckAudit(audit.id, REJECT))
    game.play_round()
    assert audit.done and events(game, "audit.checked")[0]["outcome"] == "fail"


@pytest.mark.parametrize("kind", ["sort_list", "lookup_value"])
def test_audit_item_normalization_agrees_with_the_target_grader(kind: str) -> None:
    k = task_kind(kind)
    content = k.generate(GameRng(3))
    answers = [content.answer, str(content.answer), True, [content.answer], "wrong"]
    if kind == "sort_list":
        answers.extend([", ".join(map(str, content.answer)), list(map(str, content.answer))])
    else:
        answers.extend([f" {content.answer} ", content.answer + 1])
    expected = k.audit_items(content.answer)
    for answer in answers:
        assert (k.audit_items(answer) == expected) == k.grade(content, answer)


def test_audit_placement_respects_task_id_capacity() -> None:
    game = make_game(
        impostors=(), num_impostors=0, tasks_per_crewmate=9, relay_tasks=0, splitkey_tasks=0
    )
    game.start()
    assert len(board(game.state).graph) == 90
    assert audits(board(game.state)) == []
    with pytest.raises(ValueError, match="bad task settings"):
        GameConfig(audit_tasks=-1)
