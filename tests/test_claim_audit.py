"""Claim Audit (#13): record rebuild, Claim checking, Lies vs Misstatements, metrics."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

import zantine.audit
from helpers import make_game
from zantine.audit import (
    PatternExtractor,
    ScriptedExtractor,
    build_record,
    claim,
    run_audit,
)
from zantine.audit.record import BOUNDARIES
from zantine.cli import main
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
        q = item.text
        return {
            "claims": [
                claim("location", "Pink", q, room="Kitchen"),
                claim("location", "Mauve", q, room="Storage"),
                claim("other", None, q),
                claim("location", "Pink", q, room="Storage", round=9),
                claim("together", "Pink", q),
                claim("location", "Pink", q, room="storage"),
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
                claim("location", "Pink", PINK_SPEECH, room="Weapons", negated=True),  # true
                claim("location", "Pink", PINK_SPEECH, room="Storage", negated=True),  # lie
                claim("location", "Red", PINK_SPEECH, room="Weapons", round=2),  # true
                claim("location", "Red", PINK_SPEECH, room="Cafeteria", round=2),  # misstatement
                claim("kill", "Red", PINK_SPEECH, object="Green", round=1),  # wrong round
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
                    claim("task_done", "I", item.text, task=done.task.lower()),
                    claim("task_done", "I", item.text, task=not_done),
                    claim("task_done", "I", item.text, task=done.task, negated=True),
                ]
            }
        if item.speaker == rec.names[impostor]:
            return {"claims": [claim("task_done", "I", item.text, room=rec.rooms[1])]}
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


# Quotes ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("quote", "problem"),
    [
        ("", "no quote from the statement"),
        ("   \n\t", "no quote from the statement"),
        ("I was in Medbay.", "quote is not in the statement"),
    ],
)
def test_claims_without_a_quote_from_the_statement_are_never_scored(
    quote: str, problem: str
) -> None:
    """An extractor can't put words in a player's mouth: "Let's vote." makes no Claim,
    so an invented (or empty) quote is unverifiable and stays out of every rate."""
    respond = {"Let's vote.": {"claims": [claim("location", "I", quote, room="Medbay")]}}
    report = run_audit(kill_game({PINK: "Let's vote."}), ScriptedExtractor(respond))
    pink = [c for c in report["claims"] if c["speaker"] == PINK]
    assert pink and labels(pink) == ["unverifiable"] * len(pink)
    assert all(problem in c["problems"] and not c["quote_in_text"] for c in pink)
    seat = report["seats"][PINK]
    assert (seat["checkable"], seat["lie"], seat["lie_rate"], seat["reliability"]) == (
        0,
        0,
        None,
        None,
    )
    assert report["totals"]["unverifiable"] == len(pink)


def test_a_quote_matches_up_to_case_and_whitespace() -> None:
    respond = {
        "I was in Storage.": {
            "claims": [claim("location", "I", "i  WAS\nin storage", room="Storage")]
        }
    }
    report = run_audit(kill_game({PINK: "I was in Storage."}), ScriptedExtractor(respond))
    (first, *_) = by_speaker(report, PINK)
    assert first["quote_in_text"] and first["verdict"]["label"] == "true"


# Malformed extractor output ---------------------------------------------------------


def pink_replies(*replies: dict[str, Any]) -> tuple[ScriptedExtractor, list[int]]:
    """An extractor answering Pink's statements with ``replies`` in turn (the last one
    repeats), and every other statement with no Claims. Returns it and Pink's call count."""
    calls = [0]

    def respond(item: Any) -> dict[str, Any]:
        if item.speaker != "Pink":
            return {"claims": []}
        calls[0] += 1
        return replies[min(calls[0], len(replies)) - 1]

    return ScriptedExtractor(respond), calls


VALID = claim("location", "Pink", PINK_SPEECH, room="Storage")


@pytest.mark.parametrize(
    "bad",
    [
        {"kind": "location"},  # no subject, no quote, ...
        {k: v for k, v in VALID.items() if k != "quote"},  # absent quote
        VALID | {"quote": None},
        VALID | {"negated": "no"},
    ],
)
def test_all_malformed_items_are_retried_then_make_the_audit_incomplete(
    bad: dict[str, Any],
) -> None:
    extractor, calls = pink_replies({"claims": [bad]})
    report = run_audit(kill_game(), extractor)
    pink = [s for s in report["statements"] if s["speaker"] == PINK]
    assert calls[0] == 2 * len(pink)  # one retry per statement
    assert all(s["rejected"] and "malformed" in s["error"] for s in pink)
    assert not report["complete"] and report["statement_errors"] == len(pink)
    assert report["seats"][PINK]["statement_errors"] == len(pink)
    assert not [c for c in report["claims"] if c["speaker"] == PINK]


def test_mixed_reply_keeps_the_valid_claims_but_is_not_complete() -> None:
    extractor, _ = pink_replies({"claims": [VALID, {"kind": "location"}]})
    report = run_audit(kill_game(), extractor)
    assert not report["complete"]
    pink = [c for c in report["claims"] if c["speaker"] == PINK]
    assert pink and labels(pink) == ["true"] * len(pink)  # partial, and flagged as such
    assert report["seats"][PINK]["statement_errors"] == 3


def test_a_clean_retry_replaces_a_malformed_reply() -> None:
    extractor, calls = pink_replies({"claims": [VALID, {"kind": "location"}]}, {"claims": [VALID]})
    report = run_audit(kill_game(), extractor)
    assert report["complete"] and report["statement_errors"] == 0
    first = next(s for s in report["statements"] if s["speaker"] == PINK)
    assert (first["rejected"], first["error"]) == ([], None)
    assert calls[0] == 4  # 2 for the first statement, then 1 each


def test_cli_fails_on_an_incomplete_audit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    transcript = tmp_path / "game.jsonl"
    transcript.write_text("".join(json.dumps(r) + "\n" for r in kill_game()))
    extractor, _ = pink_replies({"claims": [{"kind": "location"}]})
    monkeypatch.setattr(zantine.audit, "PatternExtractor", lambda: extractor)
    assert main(["audit", str(transcript)]) == 1
    assert "INCOMPLETE: 3 statement(s)" in capsys.readouterr().out
    assert json.loads((tmp_path / "game.audit.json").read_text())["complete"] is False
