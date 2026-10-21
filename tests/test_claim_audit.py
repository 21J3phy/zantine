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
from zantine.rules.relay import parts as relay_parts
from zantine.rules.relay import tamper
from zantine.runner import new_game, play
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


# An Impostor's Claims about its own kills ----------------------------------------------


def own_kill_claims(speaker: int, *claims: dict[str, Any]) -> list[dict[str, Any]]:
    text = "Some words."
    respond = {text: {"claims": [c | {"quote": text} for c in claims]}}
    report = run_audit(kill_game({speaker: text}), ScriptedExtractor(respond))
    return by_speaker(report, speaker)


def test_wrong_round_room_or_victim_in_an_own_kill_claim_is_a_lie() -> None:
    """Red killed Green in Weapons in Round 2; its own actions prove each variant false."""
    right, wrong_round, wrong_victim, wrong_room = own_kill_claims(
        RED,
        claim("kill", "I", object="Green", room="Weapons", round=2),
        claim("kill", "Red", object="Green", room="Weapons", round=1),
        claim("kill", "I", object="Pink", room="Medbay"),
        claim("kill", "I", object="Green", room="Medbay"),
    )
    assert right["verdict"]["label"] == "true"
    for c in (wrong_round, wrong_victim, wrong_room):
        assert c["verdict"]["label"] == "lie", c["verdict"]
        # Its kill action (Green, Round 2) is the complete list of kills it made.
        assert any(p["type"] == "action" and p["round"] == 2 for p in c["verdict"]["proof"])
    # The room is ruled out by its own view at the start of Round 2 (in Weapons).
    assert any(
        p["type"] == "room_snapshot" and p["round"] == 1 for p in wrong_room["verdict"]["proof"]
    )


def test_an_impostor_with_no_kills_knows_it_killed_no_one() -> None:
    (claimed,) = own_kill_claims(BLUE, claim("kill", "I", object="Pink"))
    assert claimed["verdict"]["label"] == "lie"
    proof = claimed["verdict"]["proof"]
    assert proof and all(p["kind"] == "event" and p["type"] == "action" for p in proof)


def test_a_fellow_impostors_unseen_kill_is_not_assumed_known() -> None:
    """Blue never saw Red's kill: "Red killed Green in Medbay" from Blue is a Misstatement."""
    (claimed,) = own_kill_claims(BLUE, claim("kill", "Red", object="Green", room="Medbay"))
    assert (claimed["verdict"]["label"], claimed["verdict"]["proof"]) == ("misstatement", [])


# Eyewitness and whole-window qualifiers ------------------------------------------------

YELLOW = 5  # an idle Crewmate: stays in Cafeteria all game


def first_claims(speech: dict[int, str], seat: int) -> list[dict[str, Any]]:
    return by_speaker(run_audit(kill_game(speech), PatternExtractor()), seat)


def test_eyewitness_kill_claims_need_the_speaker_to_have_been_there() -> None:
    # Orange stood in Weapons when Red killed Green: an accurate eyewitness.
    (orange,) = first_claims({ORANGE: "I saw Red kill Green in Weapons."}, ORANGE)
    assert orange["seen"] and orange["verdict"]["label"] == "true"
    # Pink was in Storage: the kill happened, but Pink did not see it, and knew that.
    (pink,) = first_claims({PINK: "I saw Red kill Green in Weapons."}, PINK)
    assert pink["seen"] and pink["verdict"]["truth"] == "false"
    assert pink["verdict"]["label"] == "lie"
    (proof,) = pink["verdict"]["proof"]
    assert (proof["type"], proof["round"]) == ("room_snapshot", 1)  # its view as Round 2 began
    assert "Pink was not there" in pink["verdict"]["reason"]
    # Stating the fact without claiming to have seen it is true.
    (fact,) = first_claims({PINK: "Red killed Green in Weapons."}, PINK)
    assert not fact["seen"] and fact["verdict"]["label"] == "true"


def test_eyewitness_location_claims() -> None:
    def said(seat: int, *claims: dict[str, Any]) -> list[dict[str, Any]]:
        text = "Some words."
        respond = {text: {"claims": [c | {"quote": text} for c in claims]}}
        return by_speaker(run_audit(kill_game({seat: text}), ScriptedExtractor(respond)), seat)

    saw, fact = said(
        PINK,
        claim("location", "Red", room="Weapons", round=2, seen=True),
        claim("location", "Red", room="Weapons", round=2),
    )
    assert (saw["verdict"]["label"], fact["verdict"]["label"]) == ("lie", "true")
    assert {p["type"] for p in saw["verdict"]["proof"]} == {"room_snapshot"}
    (orange,) = said(ORANGE, claim("location", "Red", room="Weapons", round=2, seen=True))
    assert orange["verdict"]["label"] == "true"
    (together,) = said(
        ORANGE, claim("together", "Red", object="Green", room="Weapons", round=2, seen=True)
    )
    assert together["verdict"]["label"] == "true"
    (unseen,) = said(PINK, claim("together", "Red", object="Green", round=2, seen=True))
    assert unseen["verdict"]["label"] == "lie"


def test_whole_time_alibis_cover_the_whole_window() -> None:
    # Red left Cafeteria in Round 1: "the whole time" is false, and Red knew.
    (red,) = first_claims({RED: "I was in Cafeteria the whole time."}, RED)
    assert red["throughout"] and red["verdict"]["label"] == "lie"
    (proof,) = red["verdict"]["proof"]
    assert (proof["type"], proof["round"]) == ("room_snapshot", 1)
    # Yellow really stayed in Cafeteria throughout.
    (yellow,) = first_claims({YELLOW: "I was in Cafeteria the whole time."}, YELLOW)
    assert yellow["throughout"] and yellow["verdict"]["label"] == "true"
    assert "throughout Rounds 1-3" in yellow["verdict"]["reason"]
    # Without the qualifier, Red's Claim only says it was there at some point: true.
    (some,) = first_claims({RED: "I was in Cafeteria."}, RED)
    assert not some["throughout"] and some["verdict"]["label"] == "true"
    # Red and Green were together in Round 1, but Green died in Round 2.
    (pair,) = first_claims({RED: "I was with Green the whole time."}, RED)
    assert pair["throughout"] and pair["verdict"]["label"] == "lie"
    (stayed,) = first_claims({YELLOW: "I was with Black the whole time."}, YELLOW)
    assert stayed["verdict"]["label"] == "true"


def test_qualifiers_the_checker_cannot_decide_are_unverifiable() -> None:
    text = "Some words."
    respond = {
        text: {
            "claims": [
                claim("kill", "Red", text, object="Green", throughout=True),
                claim("location", "I", text, room="Storage", negated=True, throughout=True),
                claim("task_done", "Red", text, seen=True),
            ]
        }
    }
    report = run_audit(kill_game({PINK: text}), ScriptedExtractor(respond))
    pink = by_speaker(report, PINK)
    assert labels(pink) == ["unverifiable"] * 3
    assert "'throughout' does not apply" in pink[0]["verdict"]["reason"]
    assert "ambiguous" in pink[1]["verdict"]["reason"]
    assert "not checked" in pink[2]["verdict"]["reason"]


# Audit and relay Tasks ----------------------------------------------------------------


def relay_game(tampered: bool) -> list[dict[str, Any]]:
    """Seed 1 (scripted): T39 is carried by Pink-of-seed-1 (seat 3) to the handoff and
    delivered by seat 0 in Round 10. ``tampered`` corrupts the part once picked up."""
    game = new_game(GameConfig(seed=1), "scripted")
    impostor = next(p.seat for p in game.state.players if p.is_impostor)
    while not game.state.over:
        p = relay_parts(game.state).get("T39")  # set up in the first Round
        if tampered and p is not None and p.holder is not None and not p.tampered:
            tamper(game, "T39", impostor)
        game.play_round()
    return game.transcript.records


def event(records: list[dict[str, Any]], type: str, **match: Any) -> dict[str, Any]:
    return next(
        r
        for r in records
        if r["kind"] == "event"
        and r["type"] == type
        and all(r["data"].get(k) == v for k, v in match.items())
    )


def claims_after(
    records: list[dict[str, Any]], seat: int, after: int, *claims: dict[str, Any]
) -> list[dict[str, Any]]:
    """Run the audit with ``claims`` made in ``seat``'s first statement after seq ``after``."""
    rec = build_record(records)
    statement = next(s for s in rec.statements if s.speaker == seat and s.seq > after)

    def respond(item: Any) -> dict[str, Any]:
        if item.text == statement.text and item.speaker == rec.names[seat]:
            return {"claims": [c | {"quote": item.text} for c in claims]}
        return {"claims": []}

    report = run_audit(records, ScriptedExtractor(respond))
    assert report["complete"]
    return [c for c in report["claims"] if c["statement_seq"] == statement.seq]


def test_relay_completion_credits_both_carriers_in_their_rooms() -> None:
    records = relay_game(tampered=False)
    delivered = event(records, "relay.delivered", task="T39")
    handoff = event(records, "relay.handed_off", task="T39")
    rooms = event(records, "tasks.generated")["data"]["tasks"]
    relay_rooms = next(t for t in rooms if t["id"] == "T39")["inputs"]["rooms"]
    deliverer, carrier = delivered["data"]["seat"], handoff["data"]["seat"]
    assert delivered["data"]["correct"] and deliverer != carrier

    named, some, right_room, wrong_room = claims_after(
        records,
        deliverer,
        delivered["seq"],
        claim("task_done", "I", task="T39"),
        claim("task_done", "I"),
        claim("task_done", "I", task="T39", room=relay_rooms["delivery"]),
        claim("task_done", "I", task="T39", room=relay_rooms["pickup"]),
    )
    assert labels([named, some, right_room]) == ["true"] * 3
    # Wrong room: false, but the deliverer is never told grades, so not a Lie.
    assert wrong_room["verdict"]["label"] == "misstatement"

    first_leg, at_handoff = claims_after(
        records,
        carrier,
        delivered["seq"],
        claim("task_done", "I", task="T39"),
        claim("task_done", "I", task="T39", room=relay_rooms["handoff"]),
    )
    assert labels([first_leg, at_handoff]) == ["true", "true"]


def test_a_tampered_delivery_is_a_misstatement_not_a_lie() -> None:
    """The deliverer's receipt is the same either way, so it can't know the part failed."""
    intact, tampered = relay_game(tampered=False), relay_game(tampered=True)
    receipt = [
        next(
            r["data"]
            for r in records
            if r["kind"] == "observation" and r["type"] == "relay.delivered"
        )
        for records in (intact, tampered)
    ]
    assert receipt[0] == receipt[1]
    delivered = event(tampered, "relay.delivered", task="T39")
    assert delivered["data"]["tampered"] and not delivered["data"]["correct"]
    deliverer = delivered["data"]["seat"]
    named, some = claims_after(
        tampered,
        deliverer,
        delivered["seq"],
        claim("task_done", "I", task="T39"),
        claim("task_done", "I"),
    )
    assert named["verdict"]["truth"] == "false"
    assert (named["verdict"]["label"], named["verdict"]["proof"]) == ("misstatement", [])
    assert some["verdict"]["label"] in ("true", "misstatement")  # never a Lie
    carrier = event(tampered, "relay.handed_off", task="T39")["data"]["seat"]
    (first_leg,) = claims_after(
        tampered, carrier, delivered["seq"], claim("task_done", "I", task="T39")
    )
    assert first_leg["verdict"]["label"] == "misstatement"


def test_an_observed_failed_solo_grade_still_proves_a_lie() -> None:
    records = play(GameConfig(seed=1), "scripted").transcript.records
    rec = build_record(records)
    fail = next(
        o
        for seat, obs in sorted(rec.observations.items())
        for o in obs
        if o["type"] == "tasks.graded"
        and not o["data"]["correct"]
        and rec.task_kinds[o["data"]["task"]] not in ("chain_relay", "audit")
        and not any(d.seat == seat and d.task == o["data"]["task"] for d in rec.tasks_done)
        and any(s.speaker == seat and s.seq > o["seq"] for s in rec.statements)
    )
    (claimed,) = claims_after(
        records, fail["seat"], fail["seq"], claim("task_done", "I", task=fail["data"]["task"])
    )
    assert claimed["verdict"]["label"] == "lie"
    assert claimed["verdict"]["proof"][0]["type"] == "tasks.graded"


def test_audit_completion_credits_the_auditor_and_the_spec_holder() -> None:
    records = play(GameConfig(seed=24), "scripted").transcript.records
    checked = event(records, "audit.checked", correct=True)
    task, auditor, holder = (checked["data"][k] for k in ("task", "auditor", "spec_from"))
    rec = build_record(records)
    assert rec.task_kinds[task] == "audit"
    room = rec.task_rooms[task]
    other = next(r for r in rec.rooms if r != room)

    named, some, here, elsewhere = claims_after(
        records,
        auditor,
        checked["seq"],
        claim("task_done", "I", task=task),
        claim("task_done", "I"),
        claim("task_done", "I", task=task, room=room),
        claim("task_done", "I", task=task, room=other),
    )
    assert labels([named, some, here]) == ["true"] * 3
    assert named["verdict"]["evidence"] == [checked["seq"]]
    assert named["verdict"]["truth"] != "unverifiable" and elsewhere["verdict"]["truth"] == "false"

    credited, where = claims_after(
        records,
        holder,
        checked["seq"],
        claim("task_done", "I", task=task),
        claim("task_done", "I", task=task, room=room),
    )
    assert credited["verdict"]["label"] == "true"
    # Where the spec holder stood when its share counted isn't recorded.
    assert where["verdict"]["label"] == "unverifiable"
