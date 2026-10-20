"""Claim Audit (#13): record rebuild, Claim checking, Lies vs Misstatements, metrics."""

from __future__ import annotations

from typing import Any

import pytest

from helpers import make_game
from zantine.audit import (
    PatternExtractor,
    ScriptedExtractor,
    build_record,
    claim,
    run_audit,
)
from zantine.audit.record import BOUNDARIES
from zantine.config import GameConfig
from zantine.rules.actions import Kill, Move, Report
from zantine.runner import play
from zantine.seats.bots import PlaybookBot

RED, BLUE, GREEN, PINK, ORANGE = 0, 1, 2, 3, 4

RED_SPEECH = "I was in Medbay. I didn't kill Green. I saw Orange kill Green in Weapons."
BLUE_SPEECH = "I was in Medbay. I was with Red in Medbay."
PINK_SPEECH = "Red was in Medbay in round 1."
ORANGE_SPEECH = "I saw Red kill Green in Weapons."


def kill_game(speech: dict[int, str] | None = None) -> list[dict[str, Any]]:
    """Red (Impostor) kills Green in Weapons in Round 2 with Orange watching; Orange
    reports in Round 3. Blue (Impostor) spends the game in Medbay, Pink in Storage."""
    speech = {RED: RED_SPEECH, BLUE: BLUE_SPEECH, PINK: PINK_SPEECH, ORANGE: ORANGE_SPEECH} | (
        speech or {}
    )
    actions = {
        RED: {1: Move("Weapons"), 2: Kill(GREEN)},
        BLUE: {1: Move("Medbay")},
        GREEN: {1: Move("Weapons")},
        PINK: {1: Move("Storage")},
        ORANGE: {1: Move("Weapons"), 3: Report()},
    }
    bots = {
        seat: PlaybookBot(
            actions=actions.get(seat), speech={1: speech[seat]} if seat in speech else None
        )
        for seat in set(actions) | set(speech)
    }
    game = make_game(bots=bots, kill_cooldown=0)
    for _ in range(3):
        game.play_round()
    assert game.state.meetings_held == 1
    return game.transcript.records


def by_speaker(report: dict[str, Any], seat: int) -> list[dict[str, Any]]:
    # Playbook bots repeat their line in all 3 passes; look at the first pass.
    return [c for c in report["claims"] if c["speaker"] == seat and c["pass_index"] == 1]


def labels(claims: list[dict[str, Any]]) -> list[str]:
    return [c["verdict"]["label"] for c in claims]


# The game record ----------------------------------------------------------------


def test_record_rebuilds_positions_kills_and_meetings() -> None:
    rec = build_record(kill_game())
    assert rec.room_at(RED, 1, "start") == "Cafeteria"
    assert rec.room_at(RED, 1, "end") == "Weapons"
    assert rec.room_at(GREEN, 2, "start") == "Weapons"
    assert rec.room_at(GREEN, 2, "end") is None  # killed
    assert [(k.round, k.room, k.victim, k.killers) for k in rec.kills] == [
        (2, "Weapons", GREEN, (RED,))
    ]
    (meeting,) = rec.meetings
    assert (meeting.round, list(meeting.window)) == (3, [1, 2, 3])
    assert len(rec.statements) == 27  # 9 living players x 3 passes


@pytest.mark.parametrize("seed", [3, 7, 11])
def test_snapshots_match_ground_truth_positions(seed: int) -> None:
    """Every living Seat's own view at a boundary agrees with the rebuilt ground truth."""
    game = play(GameConfig(seed=seed), "scripted")
    rec = build_record(game.transcript.records)
    checked = 0
    for r in range(1, game.state.round + 1):
        for b in BOUNDARIES:
            for seat, room in rec.positions[(r, b)].items():
                snap = rec.snapshot(seat, r, b)
                assert snap is not None and snap["data"]["room"] == room, (seat, r, b)
                others = {s for s, rm in rec.positions[(r, b)].items() if rm == room} - {seat}
                assert set(snap["data"]["players"]) == others
                checked += 1
    assert checked > 100


# Verdicts -------------------------------------------------------------------------


def test_every_statement_is_processed_into_claims() -> None:
    records = kill_game()
    report = run_audit(records, PatternExtractor())
    statement_events = [r for r in records if r["kind"] == "event" and r["type"] == "statement"]
    assert [s["seq"] for s in report["statements"]] == [r["seq"] for r in statement_events]
    assert report["complete"] and report["statement_errors"] == 0
    ids = {c["id"] for c in report["claims"]}
    assert ids == {i for s in report["statements"] for i in s["claims"]}
    for c in report["claims"]:
        assert c["kind"] in {"location", "together", "kill", "task_done", "other"}
        assert c["verdict"]["truth"] in {"true", "false", "unverifiable"}
        assert c["quote_in_text"]


def test_impostor_lies_are_proved_from_its_own_record() -> None:
    report = run_audit(kill_game(), PatternExtractor())
    location, denial, accusation = by_speaker(report, RED)
    assert labels([location, denial, accusation]) == ["lie"] * 3

    # "I was in Medbay": its own snapshots put it in Cafeteria, then Weapons.
    assert (location["kind"], location["subject"], location["room"]) == ("location", RED, "Medbay")
    assert {p["type"] for p in location["verdict"]["proof"]} == {"room_snapshot"}
    assert all(p["kind"] == "observation" for p in location["verdict"]["proof"])

    # "I didn't kill Green": it submitted the kill itself.
    assert denial["negated"] and denial["verdict"]["evidence"]
    (proof,) = denial["verdict"]["proof"]
    assert (proof["type"], proof["round"]) == ("action", 2)

    # "I saw Orange kill Green": framing a Crewmate for its own kill.
    assert accusation["subject"] == ORANGE
    assert accusation["verdict"]["proof"][0]["type"] == "action"


def test_true_claims_alibis_and_misstatements() -> None:
    report = run_audit(kill_game(), PatternExtractor())
    assert labels(by_speaker(report, ORANGE)) == ["true"]

    alibi, together = by_speaker(report, BLUE)
    assert alibi["verdict"]["label"] == "true"
    # "I was with Red in Medbay": Blue's own snapshots show Medbay without Red.
    assert together["verdict"]["label"] == "lie"
    assert {p["type"] for p in together["verdict"]["proof"]} == {"room_snapshot"}

    # Pink saw Red in Cafeteria at the start of Round 1 but was in Storage at its
    # end, so its own record can't rule out Red in Medbay: a Misstatement.
    (misstatement,) = by_speaker(report, PINK)
    assert (misstatement["claimed_round"], misstatement["verdict"]["label"]) == (1, "misstatement")
    assert misstatement["verdict"]["truth"] == "false"
    assert misstatement["verdict"]["proof"] == []


def test_unknown_names_rooms_and_other_claims_are_unverifiable() -> None:
    def respond(item: Any) -> dict[str, Any]:
        if item.speaker != "Pink":
            return {"claims": []}
        return {
            "claims": [
                claim("location", "Pink", "kitchen", room="Kitchen"),
                claim("location", "Mauve", "mauve", room="Storage"),
                claim("other", None, "the lights flickered"),
                claim("location", "Pink", "round 9", room="Storage", round=9),
                claim("together", "Pink", "with someone"),
                claim("location", "Pink", "I was in Storage", room="storage"),
            ]
        }

    report = run_audit(kill_game(), ScriptedExtractor(respond))
    pink = by_speaker(report, PINK)
    assert labels(pink) == ["unverifiable"] * 5 + ["true"]
    assert "unknown room 'Kitchen'" in pink[0]["verdict"]["reason"]
    assert "unknown player 'Mauve'" in pink[1]["verdict"]["reason"]
    assert pink[5]["room"] == "Storage"  # names are matched case-insensitively


def test_negated_location_and_explicit_rounds() -> None:
    respond = {
        PINK_SPEECH: {
            "claims": [
                claim("location", "Pink", "", room="Weapons", negated=True),  # true
                claim("location", "Pink", "", room="Storage", negated=True),  # lie
                claim("location", "Red", "", room="Weapons", round=2),  # true
                claim("location", "Red", "", room="Cafeteria", round=2),  # misstatement
                claim("kill", "Red", "", object="Green", round=1),  # wrong round
            ]
        }
    }
    report = run_audit(kill_game(), ScriptedExtractor(respond))
    assert labels(by_speaker(report, PINK)) == [
        "true",
        "lie",
        "true",
        "misstatement",
        "misstatement",
    ]


def test_witness_knows_a_framing_is_false() -> None:
    """Orange watched Red kill Green, so blaming Blue is a Lie, not a Misstatement."""
    report = run_audit(kill_game({ORANGE: "I saw Blue kill Green in Weapons."}), PatternExtractor())
    (framing,) = by_speaker(report, ORANGE)
    assert framing["verdict"]["label"] == "lie"
    assert framing["verdict"]["proof"][0]["type"] == "kill_witnessed"
    # Pink saw nothing: the same false accusation from Pink is a Misstatement.
    report = run_audit(kill_game({PINK: "I saw Blue kill Green in Weapons."}), PatternExtractor())
    assert labels(by_speaker(report, PINK)) == ["misstatement"]


# Metrics --------------------------------------------------------------------------


def test_reliability_lie_rate_and_misstatement_rate_per_seat() -> None:
    report = run_audit(kill_game(), PatternExtractor())
    seats = {s["seat"]: s for s in report["seats"]}
    assert len(seats) == 10
    assert (seats[RED]["lie"], seats[RED]["lie_rate"], seats[RED]["reliability"]) == (9, 1.0, 0.0)
    assert seats[ORANGE]["reliability"] == 1.0
    assert (seats[BLUE]["reliability"], seats[BLUE]["lie_rate"]) == (0.5, 0.5)
    assert seats[PINK]["misstatement_rate"] == 1.0
    assert seats[GREEN]["reliability"] is None  # dead before the Meeting: no Claims
    for s in seats.values():
        if s["checkable"]:
            total = s["reliability"] + s["lie_rate"] + s["misstatement_rate"]
            assert total == pytest.approx(1.0)
    assert report["roles"]["impostor"]["lie"] == 12
    assert report["roles"]["crewmate"]["reliability"] == 0.5  # Orange 3 true, Pink 3 false
    assert report["totals"]["claims"] == len(report["claims"])


def test_task_claims_against_private_grades() -> None:
    """Self-reported Task work: a completed Task is true; one never done is a Lie."""
    game = play(GameConfig(seed=7), "scripted")
    records = game.transcript.records
    rec = build_record(records)
    statement = next(
        s
        for s in rec.statements
        if any(d.seat == s.speaker and d.seq < s.seq for d in rec.tasks_done)
    )
    done = next(d for d in rec.tasks_done if d.seat == statement.speaker and d.seq < statement.seq)
    not_done = next(t for t in rec.task_rooms if t not in {d.task for d in rec.tasks_done})
    name = rec.names[statement.speaker]
    impostor = next(s for s, role in rec.roles.items() if role == "impostor")

    def respond(item: Any) -> dict[str, Any]:
        if item.text == statement.text and item.speaker == name:
            return {
                "claims": [
                    claim("task_done", "I", "", task=done.task.lower()),
                    claim("task_done", "I", "", task=not_done),
                    claim("task_done", "I", "", task=done.task, negated=True),
                ]
            }
        if item.speaker == rec.names[impostor]:
            return {"claims": [claim("task_done", "I", "", room=rec.rooms[1])]}
        return {"claims": []}

    report = run_audit(records, ScriptedExtractor(respond))
    mine = [c for c in report["claims"] if c["statement_seq"] == statement.seq]
    assert labels(mine) == ["true", "lie", "lie"]
    assert mine[0]["verdict"]["evidence"] == [done.seq]
    assert mine[2]["verdict"]["proof"][0]["type"] == "tasks.graded"
    assert mine[1]["verdict"]["proof"][0]["type"].startswith("tasks.")
    # An Impostor has no Tasks and was told its role, so "I did a Task" is a Lie.
    fake = [c for c in report["claims"] if c["speaker"] == impostor]
    assert fake and labels(fake) == ["lie"] * len(fake)
    assert fake[0]["verdict"]["proof"][0]["type"] == "role_assigned"
