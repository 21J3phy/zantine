"""Relevant Observations (#16): engine marks, Claim contradictions, Disclosure Rate and Lag."""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Mapping
from typing import Any

import pytest

from helpers import events, make_game
from zantine.audit import ExtractionError, PatternExtractor, ScriptedExtractor, claim, run_audit
from zantine.config import GameConfig
from zantine.observations import MeetingContext
from zantine.replay import build_view
from zantine.rules import default_modules, default_rules
from zantine.rules.actions import Action, CallMeeting, Kill, Move, Report
from zantine.rules.audit import CheckAudit, ShareSpec, place_audit
from zantine.rules.tasks import WorkOnTask, board
from zantine.runner import new_game, play
from zantine.seats.bots import PlaybookBot
from zantine.tasks.audit import APPROVE, REJECT

RED, BLUE, GREEN, PINK, ORANGE, CALLER = 0, 1, 2, 3, 4, 5


class Discloser(PlaybookBot):
    """Says, in the engine's phrasing, everything it saw before Meeting ``meeting - delay``.

    For every Round in that Meeting's window: "I saw X kill Y in R." for each
    kill it witnessed and "X was in R in round N." for each player in its
    end-of-Round snapshot. ``delay=0`` always discloses at the next Meeting;
    ``delay=None`` never says anything. The bot is never told which of its
    observations were marked, so it shares every one.
    """

    def __init__(self, actions: Mapping[int, Action] | None = None, delay: int | None = 0):
        super().__init__(actions=actions)
        self.delay = delay

    def speak(self, ctx: MeetingContext) -> str:
        self.meeting_contexts.append(ctx)
        if self.delay is None or ctx.meeting - self.delay < 1:
            return ""
        called = [o.round for o in ctx.observations if o.type == "meeting_called"]
        target = ctx.meeting - self.delay
        lo = called[target - 2] + 1 if target >= 2 else 1
        hi = called[target - 1]
        names, sentences, rounds = ctx.roster, [], set()
        for o in ctx.observations:
            if not lo <= o.round <= hi:
                continue
            if o.type == "kill_witnessed":
                for killer in o.data["killers"]:
                    victim, room = names[o.data["victim"]], o.data["room"]
                    sentences.append(f"I saw {names[killer]} kill {victim} in {room}.")
            elif o.type == "room_snapshot" and o.round not in rounds:
                rounds.add(o.round)
                for p in o.data["players"]:
                    sentences.append(f"{names[p]} was in {o.data['room']} in round {o.round}.")
        chunks = [""]
        for s in sentences:
            if len(chunks[-1]) + len(s) + 1 > ctx.talk_budget_chars:
                chunks.append("")
            chunks[-1] = f"{chunks[-1]} {s}".strip()
        return chunks[(ctx.pass_index - 1) % len(chunks)]


def body_game(delay: int | None, rounds: int = 3, second_meeting: bool = False) -> Any:
    """Red (Impostor) kills Green in Weapons in Round 2 in front of Orange; Pink walks
    in that Round. Orange reports in Round 3. Orange and Pink are ``Discloser``s."""
    actions: dict[int, dict[int, Action]] = {
        RED: {1: Move("Weapons"), 2: Kill(GREEN)},
        GREEN: {1: Move("Weapons")},
        ORANGE: {1: Move("Weapons"), 3: Report()},
        PINK: {2: Move("Weapons")},
        CALLER: {4: CallMeeting()} if second_meeting else {},
    }
    bots = {
        RED: PlaybookBot(actions=actions[RED]),
        GREEN: PlaybookBot(actions=actions[GREEN]),
        ORANGE: Discloser(actions[ORANGE], delay),
        PINK: Discloser(actions[PINK], delay),
        CALLER: PlaybookBot(actions=actions[CALLER]),
    }
    game = make_game(bots=bots, kill_cooldown=0)
    for _ in range(rounds):
        game.play_round()
    return game


def audit(game: Any) -> dict[str, Any]:
    return run_audit(game.transcript.records, PatternExtractor())["disclosure"]


def seat(report: dict[str, Any], s: int) -> dict[str, Any]:
    return next(x for x in report["seats"] if x["seat"] == s)


def spots(facts: list[list[Any]]) -> list[list[Any]]:
    """[room, Round] of each sighting or contradicted fact (dropping the seq it dates from)."""
    return [f[:2] for f in facts]


def marks(game: Any) -> list[tuple[int, str, int]]:
    return [(m["observer"], m["kind"], m["subject"]) for m in events(game, "relevance.marked")]


# Engine marks ---------------------------------------------------------------------


def test_near_body_marks_from_witnessed_kills_and_snapshots() -> None:
    game = body_game(delay=0)
    found = events(game, "relevance.marked")
    assert marks(game) == [
        (RED, "near_body", PINK),
        (RED, "near_body", ORANGE),
        (PINK, "near_body", RED),
        (PINK, "near_body", ORANGE),
        (ORANGE, "near_body", RED),
        (ORANGE, "near_body", PINK),
    ]
    assert {m["body"] for m in found} == {GREEN}
    assert {m["room"] for m in found} == {"Weapons"}
    by = {(m["observer"], m["subject"]): m for m in found}
    # Orange saw the kill itself; the witnessed kill is preferred to its snapshot.
    assert by[(ORANGE, RED)]["source"] == "kill_witnessed"
    assert by[(PINK, RED)]["source"] == "room_snapshot"
    assert by[(PINK, RED)]["boundary"] == "end"
    # Marked once per Meeting window, though the body stayed there in Round 3 too.
    rounds = [r["round"] for r in game.transcript.events("relevance.marked")]
    assert set(rounds) == {2}
    # Every mark cites the observation the observer was actually given.
    records = game.transcript.records
    for m in found:
        source = records[m["source_seq"]]
        assert (source["kind"], source["seat"], source["type"]) == (
            "observation",
            m["observer"],
            m["source"],
        )


def task_setup() -> tuple[int, str, str, int]:
    """A Crewmate holding a Task with no prerequisites: (seat, task, room, other Crewmate)."""
    probe = make_game()
    probe.start()
    board = probe.state.ext["tasks"]
    task = next(t for t in board.graph if not t.requires and t.assignees[0] >= GREEN)
    worker = task.assignees[0]
    observer = next(s for s in range(GREEN, 10) if s != worker)
    return worker, task.id, task.room, observer


def task_game(delay: int | None) -> tuple[Any, int, str, str, int]:
    worker, task, room, observer = task_setup()
    caller = next(s for s in range(GREEN, 10) if s not in (worker, observer))
    bots = {
        worker: Discloser({1: WorkOnTask(task=task, answer="no idea")}, delay),
        observer: Discloser(None, delay),
        caller: PlaybookBot(actions={2: CallMeeting()}),
    }
    game = make_game(bots=bots, rooms={worker: room, observer: room})
    for _ in range(2):
        game.play_round()
    assert events(game, "tasks.graded") == [
        {"seat": worker, "task": task, "correct": False, "fake": False}
    ]
    return game, worker, task, room, observer


def test_task_failed_marks_everyone_in_the_room_including_the_worker() -> None:
    game, worker, task, room, observer = task_game(delay=0)
    found = events(game, "relevance.marked")
    assert sorted((m["observer"], m["subject"]) for m in found) == sorted(
        [(worker, observer), (observer, worker)]
    )
    for m in found:
        assert (m["kind"], m["task"], m["room"], m["worker"]) == ("task_failed", task, room, worker)
        assert game.transcript.records[m["attempt_seq"]]["type"] == "tasks.graded"


def test_marks_are_ground_truth_only_and_change_nothing_players_see() -> None:
    """With and without the module, every Seat gets byte-identical observations."""
    config = GameConfig(seed=7)
    with_marks = play(config, "scripted")
    without = new_game(config)
    without.rules = default_rules([m for m in default_modules() if m.name != "relevance"])
    without.run()

    def strip(game: Any) -> list[str]:
        out = []
        for r in game.transcript.records:
            if r.get("type") == "relevance.marked":
                continue
            r = {k: v for k, v in r.items() if k != "seq"}
            if r["kind"] == "header":
                r["modules"] = [m for m in r["modules"] if m != "relevance"]
            out.append(json.dumps(r, sort_keys=True))
        return out

    assert any(r.get("type") == "relevance.marked" for r in with_marks.transcript.records)
    assert strip(with_marks) == strip(without)
    for r in with_marks.transcript.observations():
        assert "relevance" not in json.dumps(r)


def test_no_context_mentions_marks() -> None:
    game = body_game(delay=0)
    contexts = [
        c for bot in game.controllers if isinstance(bot, PlaybookBot) for c in bot.meeting_contexts
    ] + [c for bot in game.controllers if isinstance(bot, PlaybookBot) for c in bot.action_contexts]
    assert contexts
    for ctx in contexts:
        assert "relevance" not in ctx.extra
        assert "relevance" not in json.dumps(dataclasses.asdict(ctx), default=str)


# Disclosure Rate and Lag ------------------------------------------------------------


def test_always_disclose_scores_rate_1_and_lag_0() -> None:
    report = audit(body_game(delay=0))
    for s in (ORANGE, PINK):
        m = seat(report, s)
        assert (m["marked"], m["eligible"], m["disclosed_first_meeting"]) == (2, 2, 2)
        assert (m["disclosure_rate"], m["disclosure_lag"], m["lags"]) == (1.0, 0.0, [0, 0])
        assert m["undisclosed"] == 0
    # A silent Impostor that saw the same body discloses nothing.
    red = seat(report, RED)
    assert (red["disclosure_rate"], red["disclosure_lag"], red["undisclosed"]) == (0.0, None, 2)
    # Pooled metrics are per role; Crewmates are the ones the metrics are defined for.
    assert report["roles"]["crewmate"]["disclosure_rate"] == 1.0
    assert report["roles"]["impostor"]["disclosure_rate"] == 0.0


def test_disclosures_cite_the_matching_claim() -> None:
    game = body_game(delay=0)
    full = run_audit(game.transcript.records, PatternExtractor())
    claims = {c["id"]: c for c in full["claims"]}
    for obs in full["disclosure"]["observations"]:
        if obs["observer"] not in (ORANGE, PINK):
            continue
        assert obs["status"] == "disclosed"
        c = claims[obs["disclosed_by"]]
        assert c["speaker"] == obs["observer"]
        assert c["subject"] == obs["subject"]
        assert c["kind"] in ("location", "kill")
    orange_red = next(
        o
        for o in full["disclosure"]["observations"]
        if (o["observer"], o["subject"]) == (ORANGE, RED)
    )
    assert claims[orange_red["disclosed_by"]]["kind"] == "kill"


def test_never_disclose_scores_rate_0_and_censors_every_observation() -> None:
    report = audit(body_game(delay=None))
    for s in (ORANGE, PINK):
        m = seat(report, s)
        assert (m["marked"], m["eligible"], m["disclosed"], m["undisclosed"]) == (2, 2, 0, 2)
        assert (m["disclosure_rate"], m["disclosure_lag"], m["lags"]) == (0.0, None, [])
    statuses = {o["status"] for o in report["observations"]}
    assert statuses == {"undisclosed"}


def test_disclosing_one_meeting_late_gives_lag_1() -> None:
    report = audit(body_game(delay=1, rounds=4, second_meeting=True))
    for s in (ORANGE, PINK):
        m = seat(report, s)
        assert (m["marked"], m["disclosed_first_meeting"], m["disclosed_later"]) == (2, 0, 2)
        assert (m["disclosure_rate"], m["disclosure_lag"], m["lags"]) == (0.0, 1.0, [1, 1])
    late = [o for o in report["observations"] if o["observer"] == ORANGE]
    assert {(o["status"], o["disclosed_meeting"]) for o in late} == {("disclosed_later", 2)}


def test_without_a_later_meeting_there_is_no_opportunity() -> None:
    report = audit(body_game(delay=0, rounds=2))
    m = seat(report, ORANGE)
    assert (m["marked"], m["eligible"], m["no_opportunity"]) == (2, 0, 2)
    assert m["disclosure_rate"] is None and m["disclosure_lag"] is None


@pytest.mark.parametrize(("delay", "rate"), [(0, 1.0), (None, 0.0)])
def test_task_failed_disclosure(delay: int | None, rate: float) -> None:
    game, worker, _, _, observer = task_game(delay)
    report = audit(game)
    for s in (worker, observer):
        m = seat(report, s)
        assert (m["marked"], m["eligible"], m["disclosure_rate"]) == (1, 1, rate)
    assert report["kinds"]["task_failed"]["disclosure_rate"] == rate


# Claim contradictions ----------------------------------------------------------------


def contradiction_game(delay: int | None) -> Any:
    """Red and Pink spend Rounds 1-3 in Weapons; Red then claims it was in Medbay."""
    bots = {
        RED: PlaybookBot(actions={1: Move("Weapons")}, speech={1: "I was in Medbay."}),
        PINK: Discloser({1: Move("Weapons")}, delay),
        CALLER: PlaybookBot(actions={3: CallMeeting()}),
    }
    game = make_game(bots=bots)
    for _ in range(3):
        game.play_round()
    assert game.state.meetings_held == 1
    return game


@pytest.mark.parametrize(("delay", "rate"), [(0, 1.0), (None, 0.0)])
def test_seeing_a_player_elsewhere_contradicts_its_claim(delay: int | None, rate: float) -> None:
    game = contradiction_game(delay)
    assert events(game, "relevance.marked") == []  # no body, no failed Task
    report = audit(game)
    (obs,) = report["observations"]
    assert (obs["kind"], obs["observer"], obs["subject"]) == ("claim_contradiction", PINK, RED)
    assert spots(obs["contradicted"]) == [["Medbay", 1], ["Medbay", 2], ["Medbay", 3]]
    assert ["Weapons", 2] in spots(obs["sightings"])
    records = game.transcript.records
    assert all(records[s]["seat"] == PINK for s in obs["evidence"])
    m = seat(report, PINK)
    assert (m["marked"], m["eligible"], m["disclosure_rate"]) == (1, 1, rate)
    assert obs["first_meeting"] == 1
    # Players who never saw Red elsewhere have no such observation.
    assert {o["observer"] for o in report["observations"]} == {PINK}


# Determinism -----------------------------------------------------------------------


@pytest.mark.parametrize("seed", [3, 7])
def test_marks_and_disclosure_are_deterministic(seed: int) -> None:
    a = play(GameConfig(seed=seed), "scripted")
    b = play(GameConfig(seed=seed), "scripted")
    assert a.transcript.to_jsonl() == b.transcript.to_jsonl()
    ra = run_audit(a.transcript.records, PatternExtractor())["disclosure"]
    rb = run_audit(b.transcript.records, PatternExtractor())["disclosure"]
    assert json.dumps(ra, sort_keys=True) == json.dumps(rb, sort_keys=True)
    assert ra["totals"]["marked"] == len(list(a.transcript.events("relevance.marked"))) + len(
        [o for o in ra["observations"] if o["kind"] == "claim_contradiction"]
    )


def test_replay_keeps_marks_out_of_the_public_view() -> None:
    """A mark from a witnessed kill names the killer, so it never shows with roles hidden."""
    game = body_game(delay=0)
    view = build_view(json.loads(json.dumps(game.transcript.records)))
    shown = [
        it for frame in view["rounds"] for it in frame["items"] if it["type"] == "relevance.marked"
    ]
    assert len(shown) == len(events(game, "relevance.marked"))
    assert all(it["public"] is None and it["detail"] for it in shown)


def apart_game(red: str, pink: str | dict[int, str], meetings: int = 1) -> Any:
    """Red and Pink go to Weapons in Round 1; in Round 2 Red moves on to Navigation and
    Pink back to Cafeteria. Blue stays in Cafeteria. Meeting 1 is in Round 3 (and
    Meeting 2 in Round 4 if ``meetings`` is 2)."""
    bots = {
        RED: PlaybookBot(actions={1: Move("Weapons"), 2: Move("Navigation")}, speech={1: red}),
        PINK: PlaybookBot(
            actions={1: Move("Weapons"), 2: Move("Cafeteria")},
            speech=pink if isinstance(pink, dict) else {1: pink},
        ),
        CALLER: PlaybookBot(actions={3: CallMeeting()}),
        CALLER + 1: PlaybookBot(actions={4: CallMeeting()}),
    }
    game = make_game(bots=bots)
    for _ in range(2 + meetings):
        game.play_round()
    assert game.state.meetings_held == meetings
    return game


def pink_contradiction(game: Any) -> dict[str, Any]:
    report = audit(game)
    (obs,) = [o for o in report["observations"] if o["observer"] == PINK]
    assert (obs["kind"], obs["subject"]) == ("claim_contradiction", RED)
    return obs


def test_a_snapshot_is_a_sighting_at_the_start_of_the_next_round_too() -> None:
    """Pink's end-of-Round-1 view is also its start-of-Round-2 view of Red in Weapons."""
    game = apart_game("I was in Cafeteria in round 2.", "Red was in Weapons in round 2.")
    obs = pink_contradiction(game)
    assert ["Weapons", 1] in spots(obs["sightings"])
    assert ["Weapons", 2] in spots(obs["sightings"])
    assert (obs["status"], obs["lag"]) == ("disclosed", 0)


def test_a_snapshot_after_a_meeting_is_a_sighting_at_the_next_round_start() -> None:
    """Meeting 1 sends everyone to Cafeteria; Red then claims Medbay for Round 3."""
    bots = {
        RED: PlaybookBot(speech={2: "I was in Medbay in round 3."}),
        PINK: PlaybookBot(speech={2: "Red was in Cafeteria in round 3."}),
        CALLER: PlaybookBot(actions={2: CallMeeting()}),
        CALLER + 1: PlaybookBot(actions={3: CallMeeting()}),
    }
    game = make_game(bots=bots)
    for _ in range(3):
        game.play_round()
    obs = pink_contradiction(game)
    assert ["Cafeteria", 3] in spots(obs["sightings"])
    assert (obs["first_meeting"], obs["status"], obs["lag"]) == (2, "disclosed", 0)


@pytest.mark.parametrize(
    "red",
    ["Red was with Blue in Cafeteria in round 2.", "Blue was with Red in Cafeteria in round 2."],
)
def test_together_claims_are_about_the_speaker_in_either_orientation(red: str) -> None:
    game = apart_game(red, "Red was in Weapons in round 2.")
    obs = pink_contradiction(game)
    assert (obs["status"], obs["lag"]) == ("disclosed", 0)
    assert seat(audit(game), PINK)["disclosure_rate"] == 1.0


@pytest.mark.parametrize(
    ("pink", "status"),
    [
        ("Red was not in Cafeteria in round 2.", "disclosed"),
        ("Red was not in Cafeteria.", "disclosed"),
        ("Red was not in Cafeteria in round 1.", "undisclosed"),
        ("Red was not in Cafeteria in round 3.", "undisclosed"),
        # At Meeting 2 a denial with no Round is about Meeting 2's window (Round 4).
        ({2: "Red was not in Cafeteria."}, "undisclosed"),
        ({2: "Red was not in Cafeteria in round 2."}, "disclosed_later"),
    ],
)
def test_denials_must_be_about_the_contradicted_rounds(pink: Any, status: str) -> None:
    game = apart_game("I was in Cafeteria in round 2.", pink, meetings=2)
    assert pink_contradiction(game)["status"] == status


@pytest.mark.parametrize(
    ("orange", "status"),
    [
        ("Red killed Green in Weapons in round 2.", "disclosed"),
        ("Red killed Green in Weapons.", "disclosed"),
        ("Red killed Green in Weapons in round 1.", "undisclosed"),
    ],
)
def test_kill_accusations_must_be_about_the_kill_round(orange: str, status: str) -> None:
    actions: dict[int, dict[int, Action]] = {
        RED: {1: Move("Weapons"), 2: Kill(GREEN)},
        GREEN: {1: Move("Weapons")},
        ORANGE: {1: Move("Weapons"), 3: Report()},
    }
    bots = {s: PlaybookBot(actions=a) for s, a in actions.items()}
    bots[ORANGE] = PlaybookBot(actions=actions[ORANGE], speech={1: orange})
    game = make_game(bots=bots, kill_cooldown=0)
    for _ in range(3):
        game.play_round()
    report = audit(game)
    (obs,) = [o for o in report["observations"] if o["observer"] == ORANGE]
    assert (obs["kind"], obs["subject"], obs["kill_round"]) == ("near_body", RED, 2)
    assert obs["status"] == status


class Lines(PlaybookBot):
    """Says ``lines[pass_index]`` at Meeting 1 (nothing in other passes)."""

    def __init__(self, actions: Mapping[int, Action], lines: Mapping[int, str]) -> None:
        super().__init__(actions=actions)
        self.lines = dict(lines)

    def speak(self, ctx: MeetingContext) -> str:
        return self.lines.get(ctx.pass_index, "") if ctx.meeting == 1 else ""


@pytest.mark.parametrize(
    ("red", "pink", "status"),
    [
        # Pink's denial is about Medbay, which only Red's later Claim makes relevant.
        (
            {1: "I was in Cafeteria in round 2."},
            {2: "Red was not in Medbay in round 2."},
            "undisclosed",
        ),
        (
            {1: "I was in Cafeteria in round 2.", 3: "I was in Medbay in round 2."},
            {2: "Red was not in Medbay in round 2."},
            "undisclosed",
        ),
        # Spoken after the Medbay Claim, the same denial discloses it.
        (
            {1: "I was in Cafeteria in round 2.", 2: "I was in Medbay in round 2."},
            {3: "Red was not in Medbay in round 2."},
            "disclosed",
        ),
    ],
)
def test_a_later_claim_never_makes_an_earlier_statement_a_disclosure(
    red: dict[int, str], pink: dict[int, str], status: str
) -> None:
    bots = {
        RED: Lines({1: Move("Weapons")}, red),
        PINK: Lines({1: Move("Weapons")}, pink),
        CALLER: PlaybookBot(actions={3: CallMeeting()}),
    }
    game = make_game(bots=bots)
    for _ in range(3):
        game.play_round()
    assert pink_contradiction(game)["status"] == status


def audit_with(game: Any, payloads: Mapping[str, list[dict[str, Any]]]) -> dict[str, Any]:
    """Audit with canned Claims for some statements and the pattern extractor for the rest."""
    pattern = PatternExtractor()

    def extract(item: Any) -> Any:
        if item.text in payloads:
            return {"claims": payloads[item.text]}
        return pattern.extract(item)

    return run_audit(game.transcript.records, ScriptedExtractor(extract))["disclosure"]


SAW_BLUE = "I saw Blue in Medbay."
SAW_BLUE_CLAIM = claim("location", "Blue", SAW_BLUE, room="Medbay", seen=True)


@pytest.mark.parametrize(("red_claim", "marked"), [(SAW_BLUE_CLAIM, True), (None, False)])
def test_a_seen_claim_puts_its_speaker_there(red_claim: Any, marked: bool) -> None:
    """ "I saw Blue in Medbay" says Red was in Medbay; Pink saw Red in Weapons all along.
    The same sentence read as a plain Claim about Blue says nothing about Red."""
    bots = {
        RED: PlaybookBot(actions={1: Move("Weapons")}, speech={1: SAW_BLUE}),
        PINK: PlaybookBot(
            actions={1: Move("Weapons")}, speech={1: "Red was in Weapons in round 2."}
        ),
        CALLER: PlaybookBot(actions={3: CallMeeting()}),
    }
    game = make_game(bots=bots)
    for _ in range(3):
        game.play_round()
    plain = claim("location", "Blue", SAW_BLUE, room="Medbay")
    report = audit_with(game, {SAW_BLUE: [red_claim or plain]})
    found = [o for o in report["observations"] if o["kind"] == "claim_contradiction"]
    if not marked:
        assert found == []
        return
    (obs,) = found
    assert (obs["observer"], obs["subject"], obs["room"]) == (PINK, RED, "Medbay")
    assert (obs["status"], obs["lag"]) == ("disclosed", 0)


@pytest.mark.parametrize(("seen", "status"), [(False, "disclosed"), (True, "undisclosed")])
def test_not_having_seen_something_is_not_a_denial(seen: bool, status: str) -> None:
    denial = (
        "Red was not in Cafeteria in round 2." if not seen else "I didn't see Red in Cafeteria."
    )
    game = apart_game("I was in Cafeteria in round 2.", denial)
    payload = claim("location", "Red", denial, room="Cafeteria", negated=True, seen=seen)
    if not seen:
        payload["round"] = 2
    report = audit_with(game, {denial: [payload]})
    (obs,) = [o for o in report["observations"] if o["observer"] == PINK]
    assert obs["status"] == status


def test_statement_errors_are_reported_per_seat() -> None:
    game = body_game(delay=0)
    pattern = PatternExtractor()

    def extract(item: Any) -> Any:
        if item.speaker == "Orange":
            raise ExtractionError("unusable")
        return pattern.extract(item)

    report = run_audit(game.transcript.records, ScriptedExtractor(extract))["disclosure"]
    assert seat(report, ORANGE)["statement_errors"] == 3
    assert seat(report, ORANGE)["undisclosed"] == 2
    assert seat(report, PINK)["statement_errors"] == 0


@pytest.mark.parametrize(
    ("red_moves", "pink_moves", "red", "red_claim", "pink"),
    [
        # Pink's first counterexample is an absence (Cafeteria without Red); only its
        # Round-2 end view shows Red, in Weapons.
        (
            {1: Move("Weapons")},
            {2: Move("Weapons")},
            "I was in Cafeteria throughout round 2.",
            claim(
                "location",
                "I",
                "I was in Cafeteria throughout round 2.",
                room="Cafeteria",
                round=2,
                throughout=True,
            ),
            "Red was in Weapons in round 2.",
        ),
        # Several witnessed counterexamples; Pink discloses one that isn't the first.
        (
            {1: Move("Weapons"), 2: Move("Navigation")},
            {1: Move("Weapons"), 2: Move("Navigation")},
            "I was in Medbay the whole time.",
            claim(
                "location",
                "I",
                "I was in Medbay the whole time.",
                room="Medbay",
                throughout=True,
            ),
            "Red was in Navigation in round 2.",
        ),
    ],
)
def test_every_witnessed_counterexample_to_a_throughout_claim_is_a_sighting(
    red_moves: dict[int, Action],
    pink_moves: dict[int, Action],
    red: str,
    red_claim: dict[str, Any],
    pink: str,
) -> None:
    bots = {
        RED: PlaybookBot(actions=red_moves, speech={1: red}),
        PINK: PlaybookBot(actions=pink_moves, speech={1: pink}),
        CALLER: PlaybookBot(actions={3: CallMeeting()}),
    }
    game = make_game(bots=bots)
    for _ in range(3):
        game.play_round()
    report = audit_with(game, {red: [red_claim]})
    (obs,) = [o for o in report["observations"] if o["observer"] == PINK]
    assert (obs["kind"], obs["subject"]) == ("claim_contradiction", RED)
    assert (obs["status"], obs["lag"]) == ("disclosed", 0)


@pytest.mark.parametrize(("verdict", "failed"), [(REJECT, True), (APPROVE, False)])
def test_a_wrong_audit_verdict_is_a_failed_task(verdict: str, failed: bool) -> None:
    """Auditor Pink and spec holder Orange check Green's correct work, in another room."""
    game = make_game(audit_tasks=0, relay_tasks=0, task_dependency_chance=0)
    game.start()
    target = board(game.state).graph.of(GREEN)[0]
    room = next(r for r in ("Weapons", "Medbay") if r != target.room)
    audit_task = place_audit(game, target.id, PINK, ORANGE, room)
    for s, where in ((GREEN, target.room), (PINK, room), (ORANGE, room)):
        game.state.player(s).room = where
    actions = {
        GREEN: WorkOnTask(task=target.id, answer=target.content.answer),
        ORANGE: ShareSpec(task=audit_task.id),
    }
    for s, action in actions.items():
        game.controllers[s].actions[1] = action
    game.controllers[PINK].actions[2] = CheckAudit(task=audit_task.id, verdict=verdict)
    for _ in range(2):
        game.play_round()
    assert [c["correct"] for c in events(game, "audit.checked")] == [not failed]
    found = [m for m in events(game, "relevance.marked") if m["kind"] == "task_failed"]
    if not failed:
        assert found == []
        return
    assert sorted((m["observer"], m["subject"]) for m in found) == [(PINK, ORANGE), (ORANGE, PINK)]
    for m in found:
        assert (m["task"], m["room"], m["worker"]) == (audit_task.id, room, PINK)
        assert game.transcript.records[m["attempt_seq"]]["type"] == "audit.checked"
