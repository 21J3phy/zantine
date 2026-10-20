"""The demo replay viewer: transcript → view model → static HTML."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from zantine.cli import main
from zantine.config import GameConfig
from zantine.engine import Game
from zantine.replay import build_view, load_records, render_html, write_replay
from zantine.runner import play

SEEDS = range(12)


def scripted(seed: int) -> Game:
    return play(GameConfig(seed=seed), "scripted")


def view_of(game: Game) -> dict[str, Any]:
    return build_view(json.loads(json.dumps(game.transcript.records)))


def items(view: dict[str, Any]) -> list[dict[str, Any]]:
    return [it for frame in view["rounds"] for it in frame["items"]]


def statuses(frame: dict[str, Any]) -> dict[int, str]:
    return {s["seat"]: s["status"] for s in frame["status"]}


# Round by Round ---------------------------------------------------------------


@pytest.mark.parametrize("seed", SEEDS)
def test_scripted_game_replays_every_round(seed: int) -> None:
    game = scripted(seed)
    view = view_of(game)
    assert [f["round"] for f in view["rounds"]] == list(range(game.state.round + 1))
    assert len(view["meetings"]) == game.state.meetings_held
    assert view["result"]["winner"] == game.state.winner.value
    # Every record of the game shows up in exactly one frame.
    seqs = sorted(it["seq"] for it in items(view))
    expected = [r["seq"] for r in game.transcript.records if r["kind"] in ("event", "observation")]
    assert seqs == expected
    # The final frame's status matches the result record.
    final = statuses(view["rounds"][-1])
    result = game.transcript.records[-1]
    assert sorted(s for s, st in final.items() if st == "killed") == result["killed"]
    assert sorted(s for s, st in final.items() if st == "ejected") == result["ejected"]
    assert sorted(s for s, st in final.items() if st == "alive") == result["survivors"]


@pytest.mark.parametrize("seed", SEEDS)
def test_positions_and_bodies_follow_the_game(seed: int) -> None:
    game = scripted(seed)
    view = view_of(game)
    kills = {r["data"]["victim"]: r for r in game.transcript.events("kill")}
    for frame in view["rounds"]:
        alive = {s for s, st in statuses(frame).items() if st == "alive"}
        placed = {p["seat"] for p in frame["positions"]}
        # Players ejected in this frame's Meeting are still on the pre-Meeting map.
        ejected_now = {
            view["meetings"][m - 1]["ejection"]["seat"]
            for m in frame["meetings"]
            if view["meetings"][m - 1]["ejection"]
        }
        assert placed == alive | ejected_now
        for body in frame["bodies"]:
            assert kills[body["victim"]]["data"]["room"] == body["room"]
            assert kills[body["victim"]]["round"] <= frame["round"]
    last = view["rounds"][-1]
    if not last["meetings"]:
        rooms = {p.seat: p.room for p in game.state.living()}
        assert {p["seat"]: p["room"] for p in last["positions"]} == rooms


@pytest.mark.parametrize("seed", SEEDS)
def test_events_alone_give_the_same_map(seed: int) -> None:
    """Positions come from ground-truth events; observations only confirm them."""
    game = scripted(seed)
    full = view_of(game)
    events_only = build_view(r for r in game.transcript.records if r["kind"] != "observation")
    for a, b in zip(full["rounds"], events_only["rounds"], strict=True):
        assert (a["positions"], a["bodies"], a["status"], a["moves"]) == (
            b["positions"],
            b["bodies"],
            b["status"],
            b["moves"],
        )


def test_meetings_carry_discussion_votes_and_ejection() -> None:
    games = [scripted(seed) for seed in SEEDS]
    game = next(g for g in games if any(True for _ in g.transcript.events("ejection")))
    view = view_of(game)
    ejection = next(game.transcript.events("ejection"))["data"]
    meeting = view["meetings"][ejection["meeting"] - 1]
    assert meeting["ejection"] == {"seat": ejection["seat"], "role": ejection["role"]}
    assert meeting["tally"]["ejected"] == ejection["seat"]
    statements = [
        r["data"]
        for r in game.transcript.events("statement")
        if r["data"]["meeting"] == ejection["meeting"]
    ]
    assert [s["text"] for s in meeting["statements"]] == [s["text"] for s in statements]
    assert {v["seat"] for v in meeting["votes"]} == set(meeting["orders"][0]["order"])
    frame = next(f for f in view["rounds"] if ejection["meeting"] in f["meetings"])
    assert meeting["round"] == frame["round"]


# Role reveal --------------------------------------------------------------------


@pytest.mark.parametrize("seed", SEEDS)
def test_hidden_roles_never_leak(seed: int) -> None:
    game = scripted(seed)
    view = view_of(game)
    impostor_names = {p.name for p in game.state.players if p.is_impostor}
    for it in items(view):
        public = it["public"]
        if public is None or it["type"] == "game_over":
            continue
        assert "impostor" not in public.lower(), it
        if it["type"] in ("kill", "kill_witnessed", "killed"):
            assert not any(name in public.split(" was killed")[1] for name in impostor_names)
            assert not public.startswith(tuple(impostor_names))
    hidden = {it["type"] for it in items(view) if it["public"] is None}
    assert "role_assigned" in hidden
    # Roles are in the view model so the toggle can reveal them.
    assert {s["name"] for s in view["seats"] if s["role"] == "impostor"} == impostor_names


def test_reveal_text_names_the_killer() -> None:
    game = scripted(7)
    kill = next(game.transcript.events("kill"))
    item = next(it for it in items(view_of(game)) if it["seq"] == kill["seq"])
    killer = game.state.player(kill["data"]["killers"][0]).name
    assert killer in item["text"] and killer not in item["public"]


# Robustness ---------------------------------------------------------------------


def future_transcript() -> list[dict[str, Any]]:
    return [
        {
            "kind": "header",
            "seq": 0,
            "seed": 3,
            "map": {"rooms": ["Hub", "Lab"], "edges": [["Hub", "Lab"]], "meeting_room": "Hub"},
            "seats": [
                {"seat": 0, "name": "Ada", "role": "crewmate", "controller": {"kind": "x"}},
                {"seat": 1, "name": "Bo", "role": "impostor", "controller": {"model": "m-1"}},
            ],
        },
        {"kind": "event", "seq": 1, "round": 1, "type": "move", "data": {"seat": 0}},
        {
            "kind": "event",
            "seq": 2,
            "round": 1,
            "type": "room_chat.message",
            "data": {"seat": 1, "room": "Hub", "text": "</script><b>hi</b>"},
        },
        {
            "kind": "event",
            "seq": 3,
            "round": 2,
            "type": "crisis.started",
            "data": {"seat": 1, "rooms": ["Hub", "Lab"], "deadline": 3},
        },
        {
            "kind": "observation",
            "seq": 4,
            "round": 2,
            "seat": 0,
            "type": "tasks.graded",
            "data": {"task": "t1", "correct": True},
        },
        {
            "kind": "observation",
            "seq": 5,
            "round": 2,
            "seat": 1,
            "type": "tasks.partner",
            "data": {"role": "impostor"},
        },
        {"kind": "event", "seq": 6, "type": "weird", "data": "not a dict"},
        {
            "kind": "event",
            "seq": 7,
            "round": 4,
            "type": "move",
            "data": {"seat": 0, "to_room": "Lab"},
        },
        {"kind": "mystery", "seq": 8},
    ]


def test_unknown_types_render_generically() -> None:
    view = build_view(future_transcript())
    assert [f["round"] for f in view["rounds"]] == [0, 1, 2, 3, 4]
    by_type = {it["type"]: it for it in items(view)}
    chat = by_type["room_chat.message"]
    assert chat["category"] == "speech" and chat["speaker"] == 1
    assert chat["text"] == "Bo in Hub (room_chat.message): </script><b>hi</b>"
    crisis = by_type["crisis.started"]
    assert "Bo" in crisis["text"] and "deadline=3" in crisis["text"]
    assert "Bo" not in crisis["public"] and "deadline=3" in crisis["public"]
    assert "correct=true" in by_type["tasks.graded"]["text"]
    assert by_type["tasks.partner"]["public"] is None
    assert by_type["weird"]["text"] == "weird: not a dict"
    # The malformed move (no destination) is logged generically, not fatal.
    assert by_type["move"]["category"] in ("move", "other")
    assert (
        view["rounds"][0]["items"] == view["rounds"][3]["items"] == []
    )  # an empty Round still gets a frame
    assert view["rounds"][-1]["positions"] == [
        {"seat": 0, "room": "Lab"},
        {"seat": 1, "room": "Hub"},
    ]
    assert view["seats"][1]["controller"] == "m-1"


def test_transcript_without_header_still_replays() -> None:
    records = [r for r in future_transcript() if r["kind"] != "header"]
    view = build_view(records)
    assert [s["name"] for s in view["seats"]] == ["Seat 0", "Seat 1"]
    assert "Lab" in view["map"]["rooms"] and "Hub" in view["map"]["rooms"]
    assert set(view["map"]["layout"]) == set(view["map"]["rooms"])
    assert build_view([])["rounds"][0]["round"] == 0


def test_load_skips_blank_and_broken_lines(tmp_path: Path) -> None:
    path = tmp_path / "t.jsonl"
    path.write_text('{"kind": "header"}\n\nnot json\n[1, 2]\n{"kind": "result"}\n')
    assert [r["kind"] for r in load_records(path)] == ["header", "result"]


# HTML ----------------------------------------------------------------------------


def embedded(html: str) -> dict[str, Any]:
    match = re.search(
        r'<script type="application/json" id="replay-data">(.*?)</script>', html, re.S
    )
    assert match
    return json.loads(match.group(1))


def test_html_is_self_contained_and_hides_roles_by_default() -> None:
    view = build_view(future_transcript())
    html = render_html(view)
    for pattern in (r"<script[^>]+src=", r"<link\b", r"@import", r"url\(\s*['\"]?https?:"):
        assert not re.search(pattern, html, re.I), pattern
    assert re.search(r'<input id="reveal" type="checkbox">', html)
    assert "</script><b>" not in html  # player text can't break out of the data block
    assert embedded(html) == json.loads(json.dumps(view))


def test_smoke_scripted_game_to_html(tmp_path: Path) -> None:
    game = scripted(7)
    transcript = tmp_path / "game.jsonl"
    game.transcript.write(transcript)
    out = tmp_path / "replay" / "game.html"
    view = write_replay(transcript, out)
    html = out.read_text(encoding="utf-8")
    assert html.startswith("<!doctype html>")
    assert "<title>Zantine replay: seed 7</title>" in html
    assert embedded(html)["rounds"] == view["rounds"]
    assert len(view["rounds"]) == game.state.round + 1


def test_cli_replay(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    transcript = tmp_path / "g.jsonl"
    assert main(["play", "--seed", "2", "--quiet", "--out", str(transcript)]) == 0
    assert main(["replay", str(transcript)]) == 0
    assert (tmp_path / "g.html").is_file()
    assert main(["replay", str(transcript), "--out", str(tmp_path / "x.html")]) == 0
    assert "replay:" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        main(["replay", str(tmp_path / "missing.jsonl")])
