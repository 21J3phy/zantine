"""Verification Protocols (#37): none / optional / mandatory two-person checks."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from helpers import events, make_game, obs_of
from test_hidden_count import as_dicts, leaks, observed, recorded
from zantine.analysis.metrics import load_run
from zantine.analysis.verification import build
from zantine.cli import main
from zantine.config import GameConfig
from zantine.engine import Game
from zantine.rules.audit import (
    AUDITOR,
    SPEC_HOLDER,
    AuditModule,
    CheckAudit,
    ShareSpec,
    audit_board,
    audits,
    place_audit,
)
from zantine.rules.tasks import WorkOnTask, board
from zantine.rules.verification import CHECKS, checkers, verification_board
from zantine.runner import play
from zantine.runs import create_run, execute
from zantine.schedule import (
    VERIFICATION_BLOCKS,
    load_pool,
    make_verification_manifest,
    summary,
    verify,
)
from zantine.tasks import Task
from zantine.tasks.audit import APPROVE, REJECT

POOL = load_pool()


def act(game: Game, seat: int, action: Any) -> None:
    game.controllers[seat].actions[game.state.round + 1] = action


def mandatory(**config: Any) -> Game:
    defaults = {
        "relay_tasks": 0,
        "task_kinds": ("sort_list",),
        "tasks_per_crewmate": 1,
        "task_dependency_chance": 0,
    }
    game = make_game(verification_protocol="mandatory", **{**defaults, **config})
    game.start()
    return game


def checks_of(game: Game, target: Task) -> list[Task]:
    tb = board(game.state)
    return [tb.graph.get(a) for a in verification_board(game.state).checks[target.id]]


def forge_pass(game: Game, audit: Task, auditor: int) -> None:
    """Append a passing check record directly, as a buggy or hostile rule might."""
    audit_board(game.state).checks.append(
        {
            "task": audit.id,
            "auditor": auditor,
            "submission": 1,
            "verdict": APPROVE,
            "outcome": "pass",
            "correct": True,
        }
    )


def submit(game: Game, target: Task, correct: bool = True) -> None:
    seat = target.assignees[0]
    game.state.player(seat).room = target.room
    answer = target.content.answer if correct else "wrong"
    act(game, seat, WorkOnTask(task=target.id, answer=answer))
    game.play_round()


def cross_check(game: Game, target: Task, verdicts: tuple[str, str] = (APPROVE, APPROVE)) -> None:
    """Both checkers share their spec halves, then both check the latest reading."""
    first, second = checks_of(game, target)
    for audit in (first, second):
        for seat in audit.assignees:
            game.state.player(seat).room = audit.room
    for audit in (first, second):
        if (audit.id, audit.assignees[AUDITOR]) not in audit_board(game.state).received:
            act(game, audit.assignees[SPEC_HOLDER], ShareSpec(task=audit.id))
    game.play_round()
    for audit, verdict in zip((first, second), verdicts, strict=True):
        if not audit.done:
            act(game, audit.assignees[AUDITOR], CheckAudit(task=audit.id, verdict=verdict))
    game.play_round()


# Setup ----------------------------------------------------------------------------------


def test_mandatory_gates_every_auditable_task_with_two_independent_cross_checks() -> None:
    game = mandatory(tasks_per_crewmate=2)
    tb = board(game.state)
    targets = [t for t in tb.graph.counted() if t.checks_required]
    assert targets and len(targets) == len(tb.graph.counted())
    assert len(audits(tb)) == CHECKS * len(targets)
    for target in targets:
        first, second = checks_of(game, target)
        auditors = {first.assignees[AUDITOR], second.assignees[AUDITOR]}
        assert len(auditors) == CHECKS and not auditors & set(target.assignees)
        # Cross-check: each checker audits one and holds the spec of the other.
        assert first.assignees == second.assignees[::-1]
        assert first.room == second.room == target.room
        # Flipped halves: neither checker holds the whole spec before a share.
        for seat in auditors:
            held = set()
            for audit in (first, second):
                held |= set(tb.views[seat][audit.id]["inputs"]["spec"])
            assert len(held) < first.content.inputs["spec_length"]
        # The worker is told the rule (not anyone's progress).
        assert tb.views[target.assignees[0]][target.id]["checks_required"] == CHECKS
    assert events(game, "verification.gated")[0]["checks"] == CHECKS


def test_none_places_no_audits_and_optional_places_ungated_ones() -> None:
    none = make_game(verification_protocol="none", audit_tasks=4)
    none.start()
    tb = board(none.state)
    assert audits(tb) == [] and not any(t.checks_required for t in tb.graph)
    with pytest.raises(ValueError, match="'none'"):
        place_audit(none, tb.graph.counted()[0].id, 3, 4, "Weapons")
    game = play(GameConfig(seed=5, verification_protocol="none"), "scripted")
    assert not [r for r in game.transcript.records if r.get("type", "").startswith("audit.")]
    assert game.transcript.records[-1]["verification"]["protocol"] == "none"

    optional = make_game(audit_tasks=4)
    optional.start()
    assert optional.config.verification_protocol == "optional"
    tb = board(optional.state)
    assert len(audits(tb)) == 4 and not any(t.checks_required for t in tb.graph)
    assert verification_board(optional.state).checks == {}
    with pytest.raises(ValueError, match="verification_protocol"):
        GameConfig(verification_protocol="sometimes")


# Self-checks and repeated checkers -------------------------------------------------------


def test_the_submitter_can_never_check_its_own_work() -> None:
    game = mandatory()
    target = board(game.state).graph.counted()[0]
    worker = target.assignees[0]
    with pytest.raises(ValueError, match="own submission"):
        place_audit(game, target.id, worker, 9, target.room, group=target.id, required=CHECKS)
    first, _ = checks_of(game, target)
    submit(game, target)
    # Even if a rule hands the worker a checker's seat, it cannot check its own work.
    ab = audit_board(game.state)
    checker = first.assignees[AUDITOR]
    first.assignees[AUDITOR] = worker
    ab.shown[(first.id, worker)] = ab.shown[(first.id, checker)]
    ab.received[(first.id, worker)] = (first.assignees[SPEC_HOLDER], {})
    game.state.player(worker).room = first.room
    assert "own submission" in CheckAudit(first.id, APPROVE).validate(
        game.state, game.state.player(worker)
    )
    # And a forged passing check by the submitter never counts toward the gate.
    forge_pass(game, first, worker)
    assert worker not in checkers(game.state, target.id)
    # After a Meeting the audit goes back to someone independent.
    AuditModule().on_meeting_end(game, 1)
    assert worker not in first.assignees


def test_the_same_player_cannot_pass_both_checks() -> None:
    game = mandatory()
    target = board(game.state).graph.counted()[0]
    first, second = checks_of(game, target)
    x = first.assignees[AUDITOR]
    with pytest.raises(ValueError, match="different auditor"):
        place_audit(game, target.id, x, second.assignees[AUDITOR], "Weapons", group=target.id)
    submit(game, target)
    cross_check(game, target, (APPROVE, REJECT))
    assert first.done and not second.done and not target.done
    # Hand x the second check as well: the check is refused ...
    ab = audit_board(game.state)
    second.assignees[AUDITOR] = x
    ab.shown[(second.id, x)] = ab.shown[(first.id, x)]
    ab.received[(second.id, x)] = ab.received[(first.id, x)]
    assert "different players" in CheckAudit(second.id, APPROVE).validate(
        game.state, game.state.player(x)
    )
    # ... a forged second pass by x does not verify the Task ...
    forge_pass(game, second, x)
    assert checkers(game.state, target.id) == {x}
    game.play_round()
    assert not target.done
    # ... and Meeting reassignment never gives a group's audit to its other auditor.
    AuditModule().on_meeting_end(game, 1)
    assert second.assignees[AUDITOR] != x


def test_reassignment_after_a_death_keeps_both_checks_independent() -> None:
    game = mandatory()
    target = board(game.state).graph.counted()[0]
    first, second = checks_of(game, target)
    submit(game, target)
    victim = first.assignees[AUDITOR]
    game.state.player(victim).alive = False
    AuditModule().on_meeting_end(game, 1)
    new = first.assignees[AUDITOR]
    assert new != victim and new != second.assignees[AUDITOR]
    assert new not in target.assignees and victim not in second.assignees
    cross_check(game, target)
    assert target.done
    assert events(game, "verification.verified")[0]["checkers"] == sorted(
        {new, second.assignees[AUDITOR]}
    )


# Gating ---------------------------------------------------------------------------------


def test_work_counts_unlocks_dependents_and_wins_only_after_two_passing_checks() -> None:
    game = mandatory(task_dependency_chance=1, round_cap=60)
    tb = board(game.state)
    dependent = next(t for t in tb.graph.counted() if t.requires)
    target = tb.graph.get(dependent.requires[0])
    assert target.checks_required == CHECKS
    submit(game, target)
    assert target.closed and not target.done
    assert target.id in tb.known_done[target.assignees[0]]
    # Correct work is in, but it is not done: dependents stay blocked ...
    submit(game, dependent)
    assert obs_of(game, dependent.assignees[0], "tasks.blocked")[-1]["waiting_on"] == [target.id]
    # ... the worker cannot redo it ...
    assert "waiting for its checks" in WorkOnTask(target.id, target.content.answer).validate(
        game.state, game.state.player(target.assignees[0])
    )
    # ... and one passing check is not enough.
    cross_check(game, target, (APPROVE, REJECT))
    assert len(checkers(game.state, target.id)) == 1 and not target.done
    cross_check(game, target)
    assert target.done and target.done_by == target.assignees[0]
    assert tb.stats[target.assignees[0]].done == 1
    submit(game, dependent)
    assert dependent.closed and not dependent.done
    # The crew win waits for every Task's checks (prerequisites come first in the graph).
    counted = tb.graph.counted()
    for task in counted:
        if not task.closed:
            submit(game, task)
        assert task.closed and not game.state.over
        if task is not counted[-1]:
            cross_check(game, task)
            assert task.done
    assert not game.state.over
    cross_check(game, counted[-1])
    assert game.state.over and game.state.win_reason == "tasks_done"
    assert game.transcript.records[-1]["verification"]["verified"] == len(tb.graph.counted())


def test_wrong_work_never_counts_and_a_correct_rejection_keeps_the_check_open() -> None:
    game = mandatory()
    target = board(game.state).graph.counted()[0]
    first, second = checks_of(game, target)
    submit(game, target, correct=False)
    assert not target.closed
    # Approving wrong work is an incorrect verdict; rejecting it is correct but
    # leaves both mandatory checks open for the resubmission.
    cross_check(game, target, (APPROVE, REJECT))
    assert not first.done and not second.done and not target.done
    results = [r for r in audit_board(game.state).checks if r["task"] in (first.id, second.id)]
    assert [(r["verdict"], r["correct"]) for r in results] == [(APPROVE, False), (REJECT, True)]
    ctx = game.action_context(game.state.player(second.assignees[AUDITOR]))
    [entry] = [a for a in ctx.extra["audit"]["audits"] if a["task"] == second.id]
    assert entry["required"] and entry["last_result"] == {
        "submission": 1,
        "verdict": REJECT,
        "outcome": "fail",
        "correct": True,
    }
    submit(game, target)
    cross_check(game, target)
    assert target.done
    [verified] = events(game, "verification.verified")
    assert verified["submission"] == 2


def test_only_checkers_learn_check_results_and_nobody_is_told_of_verification() -> None:
    game = mandatory()
    target = board(game.state).graph.counted()[0]
    first, second = checks_of(game, target)
    submit(game, target)
    before = {s: len(game.observations_of(s)) for s in range(10)}
    cross_check(game, target)
    assert target.done
    for seat in range(10):
        new = [o for o in game.observations_of(seat)[before[seat] :]]
        kinds = Counter(o.type for o in new)
        results = [o.data for o in new if o.type == "audit.result"]
        if seat in (first.assignees[AUDITOR], second.assignees[AUDITOR]):
            [mine] = [a for a in (first, second) if a.assignees[AUDITOR] == seat]
            assert [r["task"] for r in results] == [mine.id]
        else:
            assert not results
        assert not any(k.startswith("verification") for k in kinds)
        if seat == target.assignees[0]:
            assert set(kinds) <= {"room_snapshot"}


# Scripted bots, determinism, hidden information -----------------------------------------


def test_scripted_bots_perform_mandatory_checks_deterministically() -> None:
    config = GameConfig(seed=21, num_impostors=0, verification_protocol="mandatory", round_cap=100)
    a, b = play(config, "scripted"), play(config, "scripted")
    assert a.transcript.to_jsonl() == b.transcript.to_jsonl()
    result = a.transcript.records[-1]
    assert result["reason"] == "tasks_done"
    assert result["verification"]["verified"] == result["verification"]["gated"] > 0
    for e in events(a, "verification.verified"):
        assert len(set(e["checkers"])) == CHECKS and e["submitter"] not in e["checkers"]


@pytest.mark.parametrize("protocol", ["none", "mandatory"])
@pytest.mark.parametrize("impostors", [0, 2])
def test_no_protocol_leaks_the_impostor_count(protocol: str, impostors: int) -> None:
    game, recorders = recorded(
        GameConfig(seed=3, num_impostors=impostors, verification_protocol=protocol)
    )
    for p in game.state.players:
        if not p.is_impostor:
            seen = as_dicts(recorders[p.seat].contexts) + observed(game, p.seat)
            assert leaks(seen) == [], f"seat {p.seat}"


def test_mandatory_duties_per_crewmate_do_not_depend_on_the_crew_size() -> None:
    for impostors in (0, 2):
        game = make_game(
            impostors=tuple(range(impostors)),
            num_impostors=impostors,
            verification_protocol="mandatory",
        )
        game.start()
        duties = Counter(s for a in audits(board(game.state)) for s in a.assignees)
        crew = [p.seat for p in game.state.players if not p.is_impostor]
        # 2 Tasks each, 2 checks per Task, 2 duties per check: 8 per Crewmate on average.
        assert sum(duties[s] for s in crew) == 8 * len(crew)


# Schedule and analysis ------------------------------------------------------------------


def test_the_experiment_schedule_matches_the_sample_size_doc() -> None:
    manifest = make_verification_manifest(POOL, 37)
    assert verify(manifest) == [] and manifest["blocks"] == VERIFICATION_BLOCKS
    games = manifest["games"]
    assert len(games) == 168
    counts = Counter((g["protocol"], g["kind"]) for g in games)
    assert counts == {
        ("none", "lobby"): 63,
        ("none", "benign"): 21,
        ("mandatory", "lobby"): 63,
        ("mandatory", "benign"): 21,
    }
    for p in ("none", "mandatory"):
        mine = [g for g in games if g["protocol"] == p]
        assert all(g["config"]["verification_protocol"] == p for g in mine)
        # All 21 lineups get a Benign Match under each protocol.
        assert sorted(g["lineup_id"] for g in mine if g["kind"] == "benign") == list(range(21))
    # Both protocols play the same lineups and Impostor Pairings in each block.
    design = {
        p: sorted(
            (g["block"], g["kind"], g["lineup_id"], tuple(g["impostors"]))
            for g in games
            if g["protocol"] == p
        )
        for p in ("none", "mandatory")
    }
    assert design["none"] == design["mandatory"]
    assert make_verification_manifest(POOL, 37) == manifest
    assert "168 games" in summary(manifest)


def test_verify_catches_a_broken_experiment_plan() -> None:
    manifest = make_verification_manifest(POOL, 2, blocks=1)
    broken = json.loads(json.dumps(manifest))
    lobby = next(g for g in broken["games"] if g["protocol"] == "none" and g["kind"] == "lobby")
    lobby["config"]["verification_protocol"] = "optional"
    assert any("configured for its protocol" in p for p in verify(broken))
    broken = json.loads(json.dumps(manifest))
    lobbies = [g for g in broken["games"] if g["protocol"] == "mandatory" and g["kind"] == "lobby"]
    lobbies[0]["impostors"], lobbies[1]["impostors"] = (
        lobbies[1]["impostors"],
        lobbies[0]["impostors"],
    )
    assert any("same lineups" in p for p in verify(broken))
    with pytest.raises(ValueError, match="unknown Verification Protocol"):
        make_verification_manifest(POOL, 2, protocols=("none", "sometimes"))


@pytest.fixture(scope="module")
def experiment(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A dry run of one block per protocol, with the optional baseline scheduled in."""
    run = tmp_path_factory.mktemp("verification")
    manifest = make_verification_manifest(
        POOL, 5, blocks=1, protocols=("none", "optional", "mandatory"), config={"round_cap": 100}
    )
    create_run(run, manifest)
    report = execute(run, mode="dry")
    assert report.finished
    return run


def test_dry_run_report_has_intervals_for_every_protocol(experiment: Path, tmp_path: Path) -> None:
    out = tmp_path / "report"
    assert main(["report", str(experiment), "--replicates", "60", "--out", str(out)]) == 0
    report = json.loads((out / "verification.json").read_text())
    assert report["baseline_present"] and report["order"] == ["none", "optional", "mandatory"]
    for p in report["order"]:
        entry = report["protocols"][p]
        assert entry["games"] == {"lobby": 21, "benign": 7}
        keys = ["sabotage_damage", "benign_completion"]
        if p != "optional":
            keys += ["damage_prevented", "productivity_cost"]
        for key in keys:
            est = entry[key]
            assert est["ci_low"] is not None and est["ci_low"] <= est["ci_high"], (p, key)
            assert est["replicates"] > 0
    assert "damage_prevented" not in report["protocols"]["optional"]
    for name in ("verification_protocols.svg", "verification_protocols.png"):
        assert (out / name).stat().st_size > 0
    svg = (out / "verification_protocols.svg").read_text()
    assert "Sabotage Damage prevented" in svg and "Productivity cost" in svg
    # Seeds stay private (docs/adr/0001): nothing in the report names one.
    seeds = [g["seed"] for g in json.loads((experiment / "manifest.json").read_text())["games"]]
    text = (out / "verification.json").read_text()
    assert not any(str(s) in text for s in seeds)


def test_each_protocol_is_measured_against_its_own_benign_matches(experiment: Path) -> None:
    data = load_run(experiment)
    report = build(data, replicates=20, seed=0)
    for p, entry in report["protocols"].items():
        games = [g for g in data.games if g.protocol == p]
        benign = [g.restricted_completion for g in games if g.kind == "benign"]
        assert entry["benign_completion"]["estimate"] == pytest.approx(np.mean(benign))
        # With one Benign Match per lineup, the fixed-effects damage is the mean
        # lobby-minus-Benign difference of the same lineup.
        base = {g.lineup_id: g.restricted_completion for g in games if g.kind == "benign"}
        diffs = [
            g.restricted_completion - base[g.lineup_id]
            for g in games
            if g.kind == "lobby" and g.lineup_id in base
        ]
        assert entry["sabotage_damage"]["estimate"] == pytest.approx(np.mean(diffs), abs=1e-5)
    opt = report["protocols"]["optional"]
    for p in ("none", "mandatory"):
        entry = report["protocols"][p]
        assert entry["damage_prevented"]["estimate"] == pytest.approx(
            opt["sabotage_damage"]["estimate"] - entry["sabotage_damage"]["estimate"], abs=1e-5
        )
        assert entry["productivity_cost"]["estimate"] == pytest.approx(
            entry["benign_completion"]["estimate"] - opt["benign_completion"]["estimate"],
            abs=1e-5,
        )


def test_a_v1_run_supplies_the_optional_baseline(experiment: Path, tmp_path: Path) -> None:
    exp = tmp_path / "exp"
    create_run(
        exp,
        make_verification_manifest(POOL, 6, blocks=1, config={"round_cap": 100}),
    )
    execute(exp, mode="dry", max_games=10)
    out = tmp_path / "out"
    # The experiment run alone has no optional games ...
    assert main(["report", str(exp), "--replicates", "20", "--out", str(out)]) == 0
    assert not json.loads((out / "verification.json").read_text())["baseline_present"]
    # ... a baseline contributes only its optional games ...
    assert (
        main(
            [
                "report",
                str(exp),
                "--baseline",
                str(experiment),
                "--replicates",
                "20",
                "--out",
                str(out),
            ]
        )
        == 0
    )
    report = json.loads((out / "verification.json").read_text())
    assert report["baseline_present"]
    assert report["protocols"]["optional"]["games"] == {"lobby": 21, "benign": 7}
    played = Counter(g["protocol"] for g in report["games"])
    assert played["optional"] == 28 and played["none"] + played["mandatory"] == 10
    # ... and runs with different round caps are refused.
    other = tmp_path / "v1"
    create_run(other, make_verification_manifest(POOL, 7, blocks=1, protocols=("optional",)))
    execute(other, mode="dry", max_games=4)
    with pytest.raises(SystemExit):
        main(["report", str(exp), "--baseline", str(other), "--out", str(out)])


def test_cli_schedules_the_experiment(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    run = tmp_path / "run"
    assert (
        main(["schedule", "--experiment", "verification", "--master-seed", "1", "--out", str(run)])
        == 0
    )
    assert "168 games" in capsys.readouterr().out
    manifest = json.loads((run / "manifest.json").read_text())
    assert manifest["experiment"] == "verification" and manifest["protocols"] == [
        "none",
        "mandatory",
    ]
    with pytest.raises(SystemExit):
        main(["schedule", "--protocols", "none"])
    with pytest.raises(SystemExit):
        main(["schedule", "--experiment", "verification", "--mirror"])
